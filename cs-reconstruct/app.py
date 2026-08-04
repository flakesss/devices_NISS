"""
cs-reconstruct — service internal Docker Compose.
Menerima payload biner CS (format cs_codec) dari backend, merekonstruksi
citra RGB via OMP+Wavelet(haar), lalu membalas sebagai JPEG.
Tidak diekspos ke host/publik — cuma diakses backend lewat jaringan Docker.
"""

import io
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

import cv2
import numpy as np
from flask import Flask, request, Response, jsonify
from skimage.metrics import structural_similarity as ssim

import cs_codec

app = Flask(__name__)


@app.route("/health")
def health():
    return {"ok": True}


# ── Rekonstruksi CS untuk video (job asinkron) ───────────────────────────────
# Video yang tersimpan adalah rekaman H.264 biasa (bukan hasil sensing CS asli
# di hardware) -- fitur ini mensimulasikan encode+rekonstruksi CS per-frame di
# atas video yang sudah ada, sama seperti "Foto via CS" tapi frame demi frame.
# OMP ~3.4 detik/frame di resolusi produksi (lihat cs_codec.py) sehingga video
# beberapa detik saja bisa butuh beberapa menit -- job jalan di background
# thread, klien poll status lewat /reconstruct-video/<job_id>/status.
_video_jobs = {}
_video_jobs_lock = threading.Lock()


def _run_video_job(job_id, in_path, mr_percent):
    job = _video_jobs[job_id]
    tmp_dir = tempfile.mkdtemp(prefix=f"csvid_{job_id}_")
    try:
        cap = cv2.VideoCapture(in_path)
        if not cap.isOpened():
            raise RuntimeError("gagal membuka video sumber")
        fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        with _video_jobs_lock:
            job["total"] = total

        idx = 0
        while True:
            ok, bgr = cap.read()
            if not ok:
                break
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            payload = cs_codec.encode_frame_ycbcr(rgb, mr_percent=mr_percent)
            recon = cs_codec.reconstruct_frame_ycbcr(payload)
            recon_bgr = cv2.cvtColor(recon, cv2.COLOR_RGB2BGR)
            cv2.imwrite(os.path.join(tmp_dir, f"f{idx:06d}.jpg"), recon_bgr,
                        [cv2.IMWRITE_JPEG_QUALITY, 92])
            idx += 1
            with _video_jobs_lock:
                job["progress"] = idx
                job["percent"] = round(idx / total * 100, 1) if total else None
        cap.release()

        if idx == 0:
            raise RuntimeError("video sumber tidak punya frame yang bisa dibaca")

        out_path = os.path.join(tmp_dir, "out.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-framerate", str(fps),
            "-i", os.path.join(tmp_dir, "f%06d.jpg"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-movflags", "faststart",
            out_path,
        ], check=True)

        with _video_jobs_lock:
            job["result_path"] = out_path
            job["status"] = "done"
    except Exception as e:
        with _video_jobs_lock:
            job["status"] = "error"
            job["error"] = str(e)
        shutil.rmtree(tmp_dir, ignore_errors=True)
    finally:
        try:
            os.remove(in_path)
        except OSError:
            pass


@app.route("/reconstruct-video", methods=["POST"])
def reconstruct_video_start():
    payload = request.get_data()
    if not payload:
        return jsonify({"error": "video kosong"}), 400

    mr_percent = cs_codec.CS_MR_PERCENT
    mr_arg = request.args.get("mr")
    if mr_arg is not None:
        try:
            mr_percent = int(mr_arg)
        except ValueError:
            return jsonify({"error": "parameter mr harus berupa angka"}), 400
        if not (1 <= mr_percent <= 100):
            return jsonify({"error": "parameter mr harus di antara 1-100"}), 400

    job_id = uuid.uuid4().hex
    fd, in_path = tempfile.mkstemp(suffix=".mp4", prefix=f"csvidin_{job_id}_")
    with os.fdopen(fd, "wb") as f:
        f.write(payload)

    with _video_jobs_lock:
        _video_jobs[job_id] = {
            "status": "processing", "progress": 0, "total": 0, "percent": 0.0,
            "result_path": None, "error": None, "mrPercent": mr_percent,
        }
    t = threading.Thread(target=_run_video_job, args=(job_id, in_path, mr_percent), daemon=True)
    t.start()
    return jsonify({"jobId": job_id, "mrPercent": mr_percent}), 202


@app.route("/reconstruct-video/<job_id>/status")
def reconstruct_video_status(job_id):
    with _video_jobs_lock:
        job = _video_jobs.get(job_id)
        if not job:
            return jsonify({"error": "job tidak ditemukan"}), 404
        return jsonify({
            "status": job["status"], "progress": job["progress"], "total": job["total"],
            "percent": job["percent"], "error": job["error"],
        })


@app.route("/reconstruct-video/<job_id>/result")
def reconstruct_video_result(job_id):
    with _video_jobs_lock:
        job = _video_jobs.get(job_id)
        if not job:
            return jsonify({"error": "job tidak ditemukan"}), 404
        if job["status"] != "done":
            return jsonify({"error": "job belum selesai", "status": job["status"]}), 409
        path = job["result_path"]

    with open(path, "rb") as f:
        data = f.read()
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)
    with _video_jobs_lock:
        del _video_jobs[job_id]
    return Response(data, mimetype="video/mp4")


@app.route("/reconstruct", methods=["POST"])
def reconstruct():
    payload = request.get_data()
    if not payload:
        return Response("payload kosong", status=400)

    bytes_in = len(payload)
    t0 = time.time()
    try:
        magic = payload[:4]
        if magic == cs_codec._MAGIC_YCC:
            frame = cs_codec.reconstruct_frame_ycbcr(payload)
        else:
            frame = cs_codec.reconstruct_frame(payload)
    except Exception as e:
        return Response(f"rekonstruksi gagal: {e}", status=400)
    elapsed_ms = round((time.time() - t0) * 1000, 1)

    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                            [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        return Response("encode JPEG gagal", status=500)

    jpeg_bytes = buf.tobytes()
    resp = Response(jpeg_bytes, mimetype="image/jpeg")
    resp.headers["X-CS-Bytes-In"] = str(bytes_in)
    resp.headers["X-CS-Bytes-Out"] = str(len(jpeg_bytes))
    resp.headers["X-CS-Reconstruct-Ms"] = str(elapsed_ms)
    return resp


@app.route("/cs-quality", methods=["POST"])
def cs_quality():
    """Terima 1 gambar (JPEG/PNG apa saja, mis. foto/thumbnail dari galeri),
    simulasikan encode+decode Compressive Sensing di atasnya, lalu balas
    metrik kualitas (PSNR, SSIM) + ukuran payload -- untuk ditampilkan di
    toggle "Info Kompresi" pada modal galeri. Ini simulasi demonstratif
    (bukan payload asli yang lewat jaringan Pi->server), karena media yang
    tersimpan sudah berupa JPEG hasil kompresi, bukan frame kamera mentah."""
    raw = request.get_data()
    if not raw:
        return jsonify({"error": "gambar kosong"}), 400

    # MR (measurement rate) opsional lewat query string, mis. ?mr=70
    # supaya bisa diuji-coba dari frontend tanpa restart service.
    mr_percent = cs_codec.CS_MR_PERCENT
    mr_arg = request.args.get("mr")
    if mr_arg is not None:
        try:
            mr_percent = int(mr_arg)
        except ValueError:
            return jsonify({"error": "parameter mr harus berupa angka"}), 400
        if not (1 <= mr_percent <= 100):
            return jsonify({"error": "parameter mr harus di antara 1-100"}), 400

    arr = np.frombuffer(raw, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        return jsonify({"error": "gagal decode gambar (format tidak didukung)"}), 400

    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    orig_size = rgb.shape[0] * rgb.shape[1] * rgb.shape[2]

    t0 = time.time()
    try:
        cs_payload = cs_codec.encode_frame_ycbcr(rgb, mr_percent=mr_percent)
        recon = cs_codec.reconstruct_frame_ycbcr(cs_payload)
    except Exception as e:
        return jsonify({"error": f"simulasi CS gagal: {e}"}), 400
    elapsed_ms = round((time.time() - t0) * 1000, 1)

    orig_f = rgb.astype(np.float32) / 255.0
    recon_f = recon.astype(np.float32) / 255.0
    mse = float(np.mean((orig_f - recon_f) ** 2))
    psnr = 10 * np.log10(1.0 / mse) if mse > 0 else 99.0
    s = float(ssim(orig_f, recon_f, channel_axis=2, data_range=1.0))

    return jsonify({
        "csType": "OMP+Wavelet(haar) (YCbCr, CS di channel Y)",
        "mrPercent": mr_percent,
        "blockSize": cs_codec.CS_BLOCK_SIZE,
        "originalBytes": int(len(raw)),
        "rawPixelBytes": int(orig_size),
        "csPayloadBytes": int(len(cs_payload)),
        "psnr": round(psnr, 2),
        "ssim": round(s, 4),
        "elapsedMs": elapsed_ms,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6000, threaded=True)
