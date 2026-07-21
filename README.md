# NISS Endoscopy — Device Node (Pi)

Script Python yang berjalan di Raspberry Pi sebagai node perangkat endoskopi. Mengontrol kamera USB, menyiarkan live stream MJPEG, merekam video/foto, mengupload ke Supabase Storage, dan berkomunikasi lewat MQTT.

## Arsitektur Sistem

Pi (device kamera) dan Docker Compose (backend + broker) bisa jalan di
**jaringan/lokasi yang berbeda**. Semua koneksi antar keduanya lewat Cloudflare
Tunnel, tidak bergantung pada IP LAN:

```
Browser / Frontend (Vercel)
        │  HTTPS
        ▼
  app.satsetin.com  (tunnel: NISS)
        │
        ▼
  Docker Compose (PC Lab)
  ├── backend (Node.js :3000)
  ├── mosquitto (MQTT :1883)
  ├── cloudflared        → tunnel "NISS"        (expose backend)
  └── cloudflared-mqtt   → tunnel "niss-mqtt"   (expose mosquitto:1883)
        │                         ▲
        │ PI_STREAM_URL           │ mqtt.satsetin.com
        ▼                         │
  pi-stream.satsetin.com   cloudflared access tcp (Pi)
  (tunnel: niss-pi-stream)        │ → localhost:1883
        ▲                         ▼
        │                  mqtt_server.py (MQTT_HOST=localhost:1883)
  cloudflared (Pi, expose :5000)
        │
        ▼
  mqtt_server.py + Flask Stream (:5000)  (jalan native di Pi)
```

Setup lengkap tunnel untuk deployment Pi di jaringan berbeda ada di
[`pi-tunnel-setup/README.md`](./pi-tunnel-setup/README.md).

## Fitur

- **Live stream** MJPEG di `http://<ip-pi>:5000/stream`
- **Snapshot** tunggal di `http://<ip-pi>:5000/snapshot`
- **Rekam video** ke MP4 (mp4v) via perintah MQTT
- **Ambil foto** JPEG via perintah MQTT
- **Upload otomatis** ke Supabase Storage setelah rekam/foto selesai
- **Publikasi event** ke broker MQTT (Mosquitto lokal) untuk notifikasi backend
<<<<<<< HEAD
- **Enkripsi AES-128-GCM** pada semua payload MQTT (command, event, status) — kerahasiaan + integritas data dalam satu operasi kriptografi
=======
>>>>>>> dec8b9da5af95d891e1185203720ae0f9ebb5ae6

## Prasyarat

```bash
# Python 3.9+
python3 --version

# Dependensi
<<<<<<< HEAD
pip3 install opencv-python-headless paho-mqtt flask requests python-dotenv pycryptodome
=======
pip3 install opencv-python-headless paho-mqtt flask requests python-dotenv
>>>>>>> dec8b9da5af95d891e1185203720ae0f9ebb5ae6
```

> **Catatan:** Gunakan `opencv-python-headless` (bukan `opencv-python`) di Raspberry Pi tanpa display.

<<<<<<< HEAD
## Konfigurasi (`.env` Device Node / Pi)
=======
## Konfigurasi
>>>>>>> dec8b9da5af95d891e1185203720ae0f9ebb5ae6

Salin template dan isi dengan nilai yang sesuai:

```bash
cp .env.example .env
```

<<<<<<< HEAD
Isi `.env` dengan konfigurasi berikut agar script `mqtt_server.py` dan layanan di Pi dapat berjalan:
=======
Isi `.env`:
>>>>>>> dec8b9da5af95d891e1185203720ae0f9ebb5ae6

```env
# Pi & broker satu LAN yang sama:
MQTT_HOST=localhost
MQTT_PORT=1883

# Pi & broker beda jaringan/lokasi (lihat pi-tunnel-setup/README.md):
# jalankan `cloudflared access tcp --hostname mqtt.satsetin.com --url localhost:1883`
# lalu MQTT_HOST tetap localhost:1883 (proxy lokal yang meneruskan ke tunnel)
MQTT_USERNAME=
MQTT_PASSWORD=
DEVICE_ID=endoskop-01

<<<<<<< HEAD
# Konfigurasi Kamera & Live Stream
=======
>>>>>>> dec8b9da5af95d891e1185203720ae0f9ebb5ae6
CAMERA_INDEX=0
FRAME_WIDTH=1280
FRAME_HEIGHT=720
VIDEO_FPS=20
JPEG_QUALITY=80
MEDIA_DIR=/path/to/media
<<<<<<< HEAD
STREAM_PORT=5000

# Konfigurasi Compressive Sensing (CS)
CS_BLOCK_SIZE=8
CS_MR_PERCENT=50

# Konfigurasi Supabase Storage & Database
SUPABASE_URL=https://yourproject.supabase.co
SUPABASE_KEY=your_service_role_key
SUPABASE_BUCKET=endoskop-media

# Enkripsi AES-128-GCM
NISS_AES_KEY=
```

| Variabel | Default | Keterangan |
|---|---|---|
| `MQTT_HOST` | `localhost` | Host/IP broker MQTT Mosquitto |
| `MQTT_PORT` | `1883` | Port broker MQTT |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | - | Kredensial login ke broker MQTT (kosongkan jika broker *allow_anonymous*) |
| `DEVICE_ID` | `endoskop-01` | Identifier unik perangkat endoskop ini |
| `CAMERA_INDEX` | `0` | Indeks kamera V4L2 USB (0 = kamera pertama) |
| `FRAME_WIDTH` / `FRAME_HEIGHT` | `1280` / `720` | Target resolusi kamera |
| `VIDEO_FPS` | `20` | Frame rate video saat merekam & stream |
| `JPEG_QUALITY` | `80` | Kualitas kompresi JPEG snapshot/stream (1-100) |
| `MEDIA_DIR` | `./media` | Direktori lokal untuk menyimpan sementara file foto/video sebelum di-upload |
| `STREAM_PORT` | `5000` | Port server Flask untuk live stream MJPEG & snapshot |
| `CS_BLOCK_SIZE` | `8` | Ukuran blok piksel (NxN) untuk Compressive Sensing |
| `CS_MR_PERCENT` | `50` | Persentase *measurement rate* Compressive Sensing |
| `SUPABASE_URL` | - | URL project Supabase |
| `SUPABASE_KEY` | - | Kunci `service_role` Supabase untuk otorisasi upload |
| `SUPABASE_BUCKET` | `endoskop-media` | Nama bucket storage di Supabase |
| `NISS_AES_KEY` | *(auto-generate)* | Key AES-128 hex (32 char). Jika kosong, baca dari `aes_key.bin`. Jika file belum ada, generate otomatis saat startup pertama |

=======

SUPABASE_URL=https://yourproject.supabase.co
SUPABASE_KEY=your_service_role_key
SUPABASE_BUCKET=endoskop-media
```

>>>>>>> dec8b9da5af95d891e1185203720ae0f9ebb5ae6
> Resolusi aktual kamera terdeteksi otomatis saat startup. Cek log untuk melihat resolusi yang digunakan.
>
> `MQTT_USERNAME`/`MQTT_PASSWORD` dikosongkan karena broker Mosquitto lokal
> dikonfigurasi `allow_anonymous true` (lihat `mosquitto/config/mosquitto.conf`).

## Menjalankan

### Manual

```bash
python3 mqtt_server.py
```

### Dengan PM2 (direkomendasikan, auto-restart)

Gunakan `ecosystem.config.js` di root repo ini:

```bash
pm2 start ecosystem.config.js --only niss-camera
pm2 save
pm2 startup  # ikuti instruksi untuk auto-start saat boot
```

## Docker Compose (Backend + Mosquitto + Tunnel)

Untuk menjalankan backend, broker MQTT, dan Cloudflare Tunnel sekaligus:

```bash
# Salin dan isi file .env
cp .env.example .env

# Jalankan semua service
docker compose up -d

# Cek status
docker compose ps
docker logs niss-cloudflared
```

<<<<<<< HEAD
### Konfigurasi `.env` (root repo untuk Docker Compose / Microservice)

Jika menjalankan seluruh infrastruktur (Backend, Mosquitto, Cloudflare Tunnel, dan Microservice Faringitis) via `docker compose up`, pastikan file `.env` di root memuat:

```env
# Token Cloudflare Tunnel untuk expose layanan ke internet
CLOUDFLARE_TOKEN=<token dari Cloudflare Zero Trust dashboard>

# Konfigurasi Microservice Pharyngitis (opsional jika dikustomisasi)
MODEL_PATH=model_scripted.pt
IMG_SIZE=224
API_TOKEN=
```

| Variabel | Default | Keterangan |
|---|---|---|
| `CLOUDFLARE_TOKEN` | - | Token tunnel dari Cloudflare Zero Trust (**Networks → Tunnels → Configure**) |
| `MODEL_PATH` | `model_scripted.pt` | Path file model TorchScript untuk klasifikasi faringitis |
| `IMG_SIZE` | `224` | Ukuran input citra ke model |
| `API_TOKEN` | - | Token autentikasi opsional untuk WebSocket `/ws/predict` (kosongkan untuk tanpa auth) |
=======
### Konfigurasi `.env` (root repo)

```env
CLOUDFLARE_TOKEN=<token dari Cloudflare Zero Trust dashboard>
```

Token didapat dari: **Cloudflare Dashboard → Zero Trust → Networks → Tunnels → klik tunnel → Configure**
>>>>>>> dec8b9da5af95d891e1185203720ae0f9ebb5ae6

## Menjalankan Pi di Jaringan/Lokasi Berbeda

Kalau Pi tidak berada di LAN yang sama dengan PC lab (tempat `docker compose up`
dijalankan), broker MQTT dan Flask stream perlu diekspos lewat Cloudflare Tunnel
supaya tetap saling terhubung dari jaringan manapun. Ada 2 tunnel tambahan yang
perlu di-setup di sisi Pi:

1. **`niss-pi-stream`** — expose Flask stream Pi (`:5000`) ke
   `https://pi-stream.satsetin.com`, supaya backend di PC lab bisa akses live
   stream Pi walau beda jaringan.
2. **`niss-mqtt` (client-side)** — proxy `cloudflared access tcp` di Pi supaya
   `mqtt_server.py` bisa connect ke broker Mosquitto di PC lab lewat
   `mqtt.satsetin.com`, meski MQTT adalah protokol TCP mentah yang tidak bisa
   langsung diakses seperti HTTP.

Instruksi lengkap instalasi & systemd service untuk keduanya ada di
**[`pi-tunnel-setup/README.md`](./pi-tunnel-setup/README.md)**.

Kalau Pi & PC lab satu LAN, langkah ini **tidak diperlukan** — cukup pakai IP
LAN langsung (`MQTT_HOST=<ip-lan-pc-lab>`, dan `PI_STREAM_URL=http://<ip-lan-pi>:5000/stream`
di `.env` backend).

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
| Tidak terhubung ke MQTT | Pastikan Mosquitto Docker jalan (`docker compose ps`) dan `MQTT_URL=mqtt://mosquitto:1883` di backend |
| Cloudflare tunnel tidak connect | Cek `docker logs niss-cloudflared` — pastikan `CLOUDFLARE_TOKEN` di `.env` sudah benar |
| Pi beda jaringan tidak connect ke MQTT | Cek service `cloudflared-mqtt-proxy` di Pi (`systemctl status cloudflared-mqtt-proxy`), dan `docker logs niss-cloudflared-mqtt` di PC lab — lihat [`pi-tunnel-setup/README.md`](./pi-tunnel-setup/README.md) |
| Live stream Pi (beda jaringan) tidak muncul di web | Cek tunnel `niss-pi-stream` aktif di Pi (`systemctl status cloudflared`) dan `PI_STREAM_URL=https://pi-stream.satsetin.com/stream` di `.env` backend |
<<<<<<< HEAD
| `[SECURITY] Dekripsi/autentikasi gagal` | Key di device dan backend tidak sama. Pastikan `NISS_AES_KEY` di `.env` backend identik dengan key di Pi |

## Enkripsi AES-128-GCM

Semua payload MQTT (command, event, status) dienkripsi menggunakan **AES-128-GCM** sebelum dikirim, menjamin:
- **Kerahasiaan** (confidentiality) — data tidak bisa dibaca pihak ketiga
- **Integritas + Autentikasi** (integrity/authentication) — GCM auth tag mendeteksi manipulasi data yang disengaja (lebih kuat dari CRC32 yang hanya mendeteksi kerusakan acak)

### Spesifikasi Teknis

| Parameter | Nilai |
|-----------|-------|
| Algoritma | AES-128 (key 16 byte / 128 bit) |
| Mode | GCM (Galois/Counter Mode) — AEAD |
| Nonce | 12 byte (96 bit), random per operasi, sesuai NIST SP 800-38D |
| Auth Tag | 16 byte (128 bit) |
| Library | pycryptodome (`pip3 install pycryptodome`) |
| Overhead per paket | 28 byte (nonce 12 + tag 16) |

### Kenapa AES-128, bukan AES-256?

Pengujian menunjukkan selisih performa antara AES-128 dan AES-256 di ukuran data kecil (8–150 KB) di bawah 0.01 ms — tidak signifikan. AES-128 sudah sangat aman untuk kasus penggunaan ini dan mengurangi beban komputasi di Pi 4 yang tidak punya hardware AES accelerator.

### Setup Key

1. **Pertama kali menjalankan `mqtt_server.py`** di Pi, key otomatis di-generate dan disimpan ke `aes_key.bin`. Hex key dicetak di log:
   ```
   [AES] Key baru di-generate dan disimpan ke: /path/aes_key.bin
   [AES]  SALIN HEX KEY INI KE .env BACKEND (NISS_AES_KEY):
   [AES]  a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6
   ```
2. **Salin hex key** ke `.env` backend: `NISS_AES_KEY=a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6`
3. Atau baca hex key dari file: `python3 -c "print(open('aes_key.bin','rb').read().hex())"`

> **PENTING:** Key HARUS identik di device (Pi) dan backend. Jika berbeda, semua pesan MQTT akan gagal didekripsi.
>
> File `aes_key.bin` dan `.env` sudah ditambahkan ke `.gitignore` — JANGAN commit key ke git.

### Catatan Hardware

Raspberry Pi 4 (BCM2711, Cortex-A72) **tidak memiliki hardware AES accelerator** (ARMv8 Crypto Extensions tidak tersedia di chip ini). Semua operasi AES berjalan secara software-only. Untuk ukuran data kecil (8–150 KB), ini bukan bottleneck — waktu enkripsi/dekripsi di bawah 1 ms.

### Pengukuran Performa

Jalankan benchmark di Pi 4 untuk mendapatkan angka performa asli:

```bash
python3 measure_aes_endoscope.py
```

### Testing

```bash
# Test interoperabilitas, persistensi key, penolakan data rusak
python3 test_aes_interop.py
```
=======
>>>>>>> dec8b9da5af95d891e1185203720ae0f9ebb5ae6
