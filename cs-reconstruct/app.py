"""
cs-reconstruct — service internal Docker Compose.
Menerima payload biner CS (format cs_codec) dari backend, merekonstruksi
citra RGB via OMP+DCT, lalu membalas sebagai JPEG.
Tidak diekspos ke host/publik — cuma diakses backend lewat jaringan Docker.
"""

import io
import time

import cv2
import numpy as np
from flask import Flask, request, Response, jsonify
from skimage.metrics import structural_similarity as ssim

import cs_codec

app = Flask(__name__)


@app.route("/health")
def health():
    return {"ok": True}


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

    arr = np.frombuffer(raw, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        return jsonify({"error": "gagal decode gambar (format tidak didukung)"}), 400

    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    orig_size = rgb.shape[0] * rgb.shape[1] * rgb.shape[2]

    t0 = time.time()
    try:
        cs_payload = cs_codec.encode_frame_ycbcr(rgb)
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
        "csType": "OMP+DCT (YCbCr, CS di channel Y)",
        "mrPercent": cs_codec.CS_MR_PERCENT,
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
