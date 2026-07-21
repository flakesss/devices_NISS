"""
Endoskop Node — MQTT control + Live Stream + Upload Supabase
- Live stream MJPEG di http://<ip-pi>:5000/stream  (pasang di <img src="...">)
- Perintah MQTT: rekam / stop / foto
- File hasil di-upload ke Supabase Storage (terenkripsi AES-128-GCM)
- Payload MQTT dienkripsi dengan AES-128-GCM (kerahasiaan + integritas)

Jalankan: python3 mqtt_server.py
"""

import os
import json
import time
import threading
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

import cv2
import requests
import paho.mqtt.client as mqtt
from flask import Flask, Response

import cs_codec
import aes_utils

# ====== KONFIGURASI BROKER MQTT ======
BROKER_HOST = os.environ["MQTT_HOST"]
BROKER_PORT = int(os.getenv("MQTT_PORT", "1883"))
USERNAME    = os.getenv("MQTT_USERNAME")   # opsional — kosongkan jika broker lokal
PASSWORD    = os.getenv("MQTT_PASSWORD")   # opsional — kosongkan jika broker lokal
DEVICE_ID   = os.getenv("DEVICE_ID", "endoskop-01")

# ====== KONFIGURASI KAMERA ======
CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "0"))
FRAME_WIDTH  = int(os.getenv("FRAME_WIDTH",  "1280"))
FRAME_HEIGHT = int(os.getenv("FRAME_HEIGHT", "720"))
VIDEO_FPS    = int(os.getenv("VIDEO_FPS",    "20"))
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "80"))
MEDIA_DIR    = os.getenv("MEDIA_DIR", os.path.join(os.path.dirname(__file__), "media"))

# ====== KONFIGURASI COMPRESSIVE SENSING (opsional, endpoint _cs) ======
CS_BLOCK_SIZE = int(os.getenv("CS_BLOCK_SIZE", str(cs_codec.CS_BLOCK_SIZE)))
CS_MR_PERCENT = int(os.getenv("CS_MR_PERCENT", str(cs_codec.CS_MR_PERCENT)))

# ====== KONFIGURASI STREAM ======
STREAM_PORT = int(os.getenv("STREAM_PORT", "5000"))

# ====== INISIALISASI AES-128-GCM ======
# Key dimuat sekali saat startup — persisten dari env var atau aes_key.bin.
# Raspberry Pi 4 (BCM2711) tidak punya hardware AES accelerator;
# semua operasi AES berjalan software-only (tetap <1ms untuk data kecil).
aes_utils.load_key()

# ====== KONFIGURASI SUPABASE ======
SUPABASE_URL    = os.environ["SUPABASE_URL"]
SUPABASE_KEY    = os.environ["SUPABASE_KEY"]
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "endoskop-media")

TOPIC_STATUS  = f"endoskop/{DEVICE_ID}/status"
TOPIC_COMMAND = f"endoskop/{DEVICE_ID}/command"
TOPIC_EVENT   = f"endoskop/{DEVICE_ID}/event"


def upload_to_supabase(local_path):
    """Upload file ke Supabase Storage — dienkripsi AES-128-GCM sebelum upload.

    File disimpan di Supabase sebagai JSON terenkripsi dengan suffix .enc.
    Backend akan mendekripsinya secara transparan saat diminta oleh frontend.
    Ini menghasilkan arsitektur Zero-Knowledge Storage: Supabase hanya
    menyimpan ciphertext, data medis pasien tidak pernah terekspos di cloud.
    """
    filename = os.path.basename(local_path)
    # Tambahkan suffix .enc pada storage path — menandakan file terenkripsi
    storage_path = f"{DEVICE_ID}/{filename}.enc"
    url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{storage_path}"
    try:
        with open(local_path, "rb") as f:
            raw_data = f.read()

        # Enkripsi file dengan AES-128-GCM sebelum upload
        enc_packet = aes_utils.encrypt_bytes(raw_data)
        enc_payload = json.dumps(enc_packet, separators=(",", ":")).encode("utf-8")

        print(f"-> Enkripsi file: {len(raw_data)} bytes -> {len(enc_payload)} bytes (AES-128-GCM)")

        headers = {
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "apikey": SUPABASE_KEY,
            "Content-Type": "application/octet-stream",
            "x-upsert": "true",
        }
        r = requests.post(url, headers=headers, data=enc_payload, timeout=60)
        if r.status_code in (200, 201):
            print(f"-> Upload sukses (encrypted): {storage_path}")
            return storage_path
        print(f"-> Upload GAGAL ({r.status_code}): {r.text}")
        return None
    except Exception as e:
        print(f"-> Upload error: {e}")
        return None


class CameraController:
    def __init__(self, publish_event):
        self.publish_event = publish_event
        os.makedirs(MEDIA_DIR, exist_ok=True)

        self.cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

        # Baca resolusi aktual (kamera bisa saja tidak support FRAME_WIDTH x FRAME_HEIGHT)
        self.actual_width  = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))  or FRAME_WIDTH
        self.actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or FRAME_HEIGHT

        self.running = True
        self.recording = False
        self.writer = None
        self.record_path = None
        self.record_start = None

        # frame terbaru — dipakai bareng oleh stream server
        self.latest_jpeg = None
        self.latest_cs_payload = None
        self._frame_lock = threading.Lock()

        # flag perintah
        self._start_req = False
        self._stop_req = False
        self._snap_req = False
        self._lock = threading.Lock()

    def request_start(self):
        with self._lock: self._start_req = True
    def request_stop(self):
        with self._lock: self._stop_req = True
    def request_snapshot(self):
        with self._lock: self._snap_req = True

    def get_latest_jpeg(self):
        with self._frame_lock:
            return self.latest_jpeg

    def get_latest_cs_payload(self):
        with self._frame_lock:
            return self.latest_cs_payload

    def run(self):
        if not self.cap.isOpened():
            print("ERROR: kamera tidak bisa dibuka.")
            self.publish_event({"event": "error", "detail": "camera_open_failed"})
            return

        print(f"Kamera siap. Resolusi: {self.actual_width}x{self.actual_height} @ {VIDEO_FPS}fps")
        print("Live stream + menunggu perintah...")
        while self.running:
            ok, frame = self.cap.read()
            if not ok:
                continue

            # update frame terbaru untuk live stream
            ok_enc, buf = cv2.imencode(
                '.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
            )
            if ok_enc:
                with self._frame_lock:
                    self.latest_jpeg = buf.tobytes()

            # payload Compressive Sensing (measurement belum direkonstruksi) —
            # dikirim lewat /snapshot_cs & /stream_cs, opsional/paralel dengan JPEG.
            # Dienkripsi AES-128-GCM (format biner mentah, bukan JSON/base64,
            # supaya tidak menambah overhead di atas payload yang sudah
            # diperkecil habis-habisan) -- konsisten dengan file upload & MQTT.
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            cs_payload = cs_codec.encode_frame_ycbcr(frame_rgb, N=CS_BLOCK_SIZE, mr_percent=CS_MR_PERCENT)
            cs_payload_enc = aes_utils.encrypt_bytes_raw(cs_payload)
            with self._frame_lock:
                self.latest_cs_payload = cs_payload_enc

            # baca flag perintah
            with self._lock:
                start_req = self._start_req
                stop_req = self._stop_req
                snap_req = self._snap_req
                self._start_req = self._stop_req = self._snap_req = False

            if snap_req: self._do_snapshot(frame)
            if start_req: self._do_start()
            if stop_req: self._do_stop()

            if self.recording and self.writer is not None:
                self.writer.write(frame)

        if self.recording:
            self._do_stop()
        self.cap.release()
        print("Kamera dilepas.")

    def _do_snapshot(self, frame):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(MEDIA_DIR, f"foto_{ts}.jpg")
        cv2.imwrite(path, frame)
        print(f"-> Foto disimpan: {path}")
        storage_path = upload_to_supabase(path)
        self.publish_event({
            "event": "snapshot_taken",
            "file": path,
            "storage_path": storage_path,
        })

    def _do_start(self):
        if self.recording:
            print("-> Sudah merekam, perintah diabaikan.")
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.record_path = os.path.join(MEDIA_DIR, f"rekaman_{ts}.mp4")
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        self.writer = cv2.VideoWriter(
            self.record_path, fourcc, VIDEO_FPS, (self.actual_width, self.actual_height)
        )
        if not self.writer.isOpened():
            print(f"-> ERROR: VideoWriter gagal dibuka untuk {self.record_path}")
            self.writer = None
            return
        self.recording = True
        self.record_start = time.time()
        print(f"-> Mulai merekam: {self.record_path} ({self.actual_width}x{self.actual_height})")
        self.publish_event({"event": "recording_started", "file": self.record_path})

    def _do_stop(self):
        if not self.recording:
            print("-> Tidak sedang merekam, perintah diabaikan.")
            return
        self.recording = False
        if self.writer is not None:
            self.writer.release()
            self.writer = None
        durasi = round(time.time() - self.record_start, 1)
        print(f"-> Berhenti merekam. Durasi {durasi}s")
        storage_path = upload_to_supabase(self.record_path)
        self.publish_event({
            "event": "recording_stopped",
            "file": self.record_path,
            "storage_path": storage_path,
            "duration_sec": durasi,
        })

    def stop(self):
        self.running = False


# ====== Live Stream Server (Flask) ======
app = Flask(__name__)
camera = None  # diisi di main()


def mjpeg_generator():
    while True:
        jpeg = camera.get_latest_jpeg()
        if jpeg is None:
            time.sleep(0.05)
            continue
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg + b'\r\n')
        time.sleep(1 / 30)  # cap ~30 fps di stream


@app.route('/stream')
def stream():
    return Response(
        mjpeg_generator(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )


@app.route('/snapshot')
def snapshot():
    jpeg = camera.get_latest_jpeg()
    if jpeg is None:
        return Response(status=503)
    return Response(jpeg, mimetype='image/jpeg',
                    headers={'Cache-Control': 'no-cache, no-store'})

@app.route('/health')
def health():
    return {"ok": True, "device": DEVICE_ID}


def cs_generator():
    while True:
        payload = camera.get_latest_cs_payload()
        if payload is None:
            time.sleep(0.05)
            continue
        yield (b'--frame\r\nContent-Type: application/octet-stream\r\n\r\n' + payload + b'\r\n')
        time.sleep(1 / 30)


@app.route('/snapshot_cs')
def snapshot_cs():
    payload = camera.get_latest_cs_payload()
    if payload is None:
        return Response(status=503)
    return Response(payload, mimetype='application/octet-stream',
                    headers={'Cache-Control': 'no-cache, no-store'})


@app.route('/stream_cs')
def stream_cs():
    return Response(
        cs_generator(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )


@app.route('/info')
def info():
    return {
        "width": camera.actual_width,
        "height": camera.actual_height,
        "fps": VIDEO_FPS,
    }


def run_stream_server():
    # threaded=True biar bisa melayani banyak penonton sekaligus
    app.run(host='0.0.0.0', port=STREAM_PORT, threaded=True,
            debug=False, use_reloader=False)


# ====== MQTT ======
def publish_event(payload):
    # Enkripsi event sebelum publish — GCM auth tag menjamin integritas
    encrypted = aes_utils.encrypt_json(payload)
    client.publish(TOPIC_EVENT, encrypted, qos=1)


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Terhubung ke broker MQTT!")
        # Enkripsi status online — backend akan mendekripsinya
        encrypted_status = aes_utils.encrypt_json({"status": "online"})
        client.publish(TOPIC_STATUS, encrypted_status,
                       qos=1, retain=True)
        client.subscribe(TOPIC_COMMAND, qos=1)
        print(f"Mendengarkan perintah di: {TOPIC_COMMAND}")
    else:
        print(f"Gagal connect, kode error: {rc}")


def on_message(client, userdata, msg):
    payload_str = msg.payload.decode()
    print(f"Pesan masuk [{msg.topic}]: (encrypted, {len(msg.payload)} bytes)")
    try:
        # Dekripsi perintah yang diterima dari backend (AES-128-GCM)
        data = aes_utils.decrypt_json(payload_str)
        print(f"  -> Dekripsi OK: {data}")
        cmd = data.get("cmd")
        if cmd == "rekam": camera.request_start()
        elif cmd == "stop": camera.request_stop()
        elif cmd == "foto": camera.request_snapshot()
        else: print(f"-> Perintah tidak dikenal: {cmd}")
    except (ValueError, KeyError) as e:
        # Dekripsi/autentikasi gagal — data mungkin dirusak atau key salah
        print(f"[SECURITY] Dekripsi/autentikasi gagal: {e}")
        print("  -> Pesan DITOLAK (tidak dieksekusi)")
    except json.JSONDecodeError:
        print("-> Pesan bukan JSON valid, diabaikan.")


client = mqtt.Client(client_id=DEVICE_ID)
if USERNAME and PASSWORD:
    client.username_pw_set(USERNAME, PASSWORD)
    client.tls_set()
# Last Will tetap plaintext — MQTT broker mengirimnya saat device disconnect,
# dan kita tidak bisa menggunakan nonce unik karena payload di-set sekali saat connect.
# Backend menangani ini dengan fallback plaintext parse khusus untuk topic status.
client.will_set(TOPIC_STATUS, json.dumps({"status": "offline"}),
                qos=1, retain=True)
client.on_connect = on_connect
client.on_message = on_message


def main():
    global camera
    camera = CameraController(publish_event)

    # Thread 1: kamera loop
    threading.Thread(target=camera.run, daemon=True).start()
    # Thread 2: Flask live stream server
    threading.Thread(target=run_stream_server, daemon=True).start()
    print(f"Live stream: http://0.0.0.0:{STREAM_PORT}/stream")

    print("Menghubungkan ke broker...")
    client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\nMenutup...")
        camera.stop()
        time.sleep(1)
        # Status offline terakhir dienkripsi (beda dengan Last Will yang plaintext)
        encrypted_offline = aes_utils.encrypt_json({"status": "offline"})
        client.publish(TOPIC_STATUS, encrypted_offline,
                       qos=1, retain=True)
        client.disconnect()


if __name__ == "__main__":
    main()