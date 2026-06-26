# NISS Endoscopy — Device Node (Pi)

Script Python yang berjalan di Raspberry Pi sebagai node perangkat endoskopi. Mengontrol kamera USB, menyiarkan live stream MJPEG, merekam video/foto, mengupload ke Supabase Storage, dan berkomunikasi lewat MQTT.

## Fitur

- **Live stream** MJPEG di `http://<ip-pi>:5000/stream`
- **Snapshot** tunggal di `http://<ip-pi>:5000/snapshot`
- **Rekam video** ke MP4 (mp4v) via perintah MQTT
- **Ambil foto** JPEG via perintah MQTT
- **Upload otomatis** ke Supabase Storage setelah rekam/foto selesai
- **Publikasi event** ke broker MQTT (HiveMQ) untuk notifikasi backend

## Prasyarat

```bash
# Python 3.9+
python3 --version

# Dependensi
pip3 install opencv-python-headless paho-mqtt flask requests python-dotenv
```

> **Catatan:** Gunakan `opencv-python-headless` (bukan `opencv-python`) di Raspberry Pi tanpa display.

## Konfigurasi

Salin template dan isi dengan nilai yang sesuai:

```bash
cp .env.example .env
```

Isi `.env`:

```env
MQTT_HOST=xxxxxxxx.s1.eu.hivemq.cloud
MQTT_PORT=8883
MQTT_USERNAME=your_username
MQTT_PASSWORD=your_password
DEVICE_ID=endoskop-01

CAMERA_INDEX=0
FRAME_WIDTH=1280
FRAME_HEIGHT=720
VIDEO_FPS=20
JPEG_QUALITY=80
MEDIA_DIR=/path/to/media

SUPABASE_URL=https://yourproject.supabase.co
SUPABASE_KEY=your_service_role_key
SUPABASE_BUCKET=endoskop-media
```

> Resolusi aktual kamera terdeteksi otomatis saat startup. Cek log untuk melihat resolusi yang digunakan.

## Menjalankan

### Manual

```bash
python3 mqtt_server.py
```

### Dengan PM2 (direkomendasikan, auto-restart)

Gunakan `ecosystem.config.js` dari repo backend NISS:

```bash
cd website/backend
pm2 start ecosystem.config.js --only niss-camera
pm2 save
```

## Perintah MQTT

Kirim pesan JSON ke topic `endoskop/<DEVICE_ID>/command`:

| Payload | Aksi |
|---------|------|
| `{"cmd": "rekam"}` | Mulai merekam video |
| `{"cmd": "stop"}` | Berhenti merekam & upload |
| `{"cmd": "foto"}` | Ambil foto & upload |

Contoh kirim via curl (lewat backend API):
```bash
curl -X POST http://localhost:3000/devices/endoskop-01/command \
  -H "Content-Type: application/json" \
  -d '{"cmd": "rekam"}'
```

## Event MQTT yang Dipublikasikan

Script mengirim event ke topic `endoskop/<DEVICE_ID>/event`:

```json
{ "event": "recording_started", "file": "/path/rekaman.mp4" }
{ "event": "recording_stopped", "file": "/path/rekaman.mp4", "storage_path": "endoskop-01/rekaman.mp4", "duration_sec": 12.3 }
{ "event": "snapshot_taken",    "file": "/path/foto.jpg",    "storage_path": "endoskop-01/foto.jpg" }
```

## Status Device

Status online/offline dikirim ke topic `endoskop/<DEVICE_ID>/status` dengan retain=true:

```json
{ "status": "online" }
{ "status": "offline" }   ← dikirim otomatis saat koneksi putus (Last Will)
```

## Live Stream

Setelah script berjalan:

| Endpoint | Keterangan |
|----------|------------|
| `http://<ip-pi>:5000/stream` | MJPEG stream (pasang di `<img src>`) |
| `http://<ip-pi>:5000/snapshot` | Satu frame JPEG |
| `http://<ip-pi>:5000/health` | Health check |

## Troubleshooting

| Masalah | Solusi |
|---------|--------|
| `ERROR: kamera tidak bisa dibuka` | Cek `CAMERA_INDEX`, coba `ls /dev/video*` untuk melihat device yang tersedia |
| Video rekaman 0 frame / file kecil | Resolusi VideoWriter tidak cocok dengan output kamera — resolusi kini auto-detect dari kamera aktual |
| Upload gagal | Pastikan `SUPABASE_KEY` adalah **service_role** key, cek koneksi internet |
| Flask port 5000 sudah dipakai | Ganti `STREAM_PORT` di konfigurasi |
| Tidak terhubung ke MQTT | Cek `BROKER_HOST`, `USERNAME`, `PASSWORD`, dan pastikan port 8883 tidak diblokir firewall |
