# Update Pi Fisik — Compressive Sensing + Enkripsi AES-128-GCM

Dokumen ini khusus untuk **perangkat Raspberry Pi fisik** yang menjalankan
`mqtt_server.py`. PC lab (Docker Compose: backend, `cs-reconstruct`,
`pharyngitis-ws`, dst.) **sudah diperbarui dan berjalan** — dokumen ini
melengkapi sisi Pi supaya kedua sisi sinkron.

Tanpa langkah-langkah ini, fitur berikut akan **gagal/503** meski kode di PC
lab sudah siap:
- Toggle "Mode: Compressive Sensing" di frontend (`/stream/snapshot/cs`)
- Endpoint `/stream/info` (label resolusi/FPS dinamis di live view)
- Payload MQTT (status/event) akan **gagal didekripsi backend** kalau key AES
  tidak disamakan — device akan terlihat seperti mengirim data rusak

## 1. Tarik perubahan kode terbaru

```bash
cd /path/ke/devices_NISS   # folder tempat mqtt_server.py berjalan di Pi
git pull origin main
```

Perubahan yang akan masuk:
- `cs_codec.py` — codec Compressive Sensing (OMP+DCT, YCbCr)
- `aes_utils.py` — enkripsi AES-128-GCM
- `mqtt_server.py` — endpoint baru (`/info`, `/snapshot_cs`, `/stream_cs`) +
  enkripsi payload MQTT & CS

## 2. Install dependency baru

```bash
pip3 install opencv-python-headless paho-mqtt flask requests python-dotenv pycryptodome
```

Yang baru dibanding sebelumnya: **`pycryptodome`** (untuk AES-128-GCM).
`cs_codec.py` hanya butuh `numpy` + `opencv-python-headless` (sudah ada).

## 3. Samakan key AES-128 dengan backend

Backend di PC lab **sudah punya key** tersimpan di `backend/.env`
(variabel `NISS_AES_KEY`, file ini **tidak** di-commit ke git — minta
langsung ke yang pegang akses PC lab, jangan disebar lewat channel publik).

Key ini **harus identik** di Pi. Pilih salah satu cara (ganti
`<HEX_KEY_DARI_BACKEND>` dengan nilai asli dari `backend/.env`):

### Opsi A — set lewat env var (lebih simpel)

Tambahkan ke `.env` Pi (`devices_NISS/.env`):

```env
NISS_AES_KEY=<HEX_KEY_DARI_BACKEND>
```

### Opsi B — set lewat file `aes_key.bin`

Kalau `mqtt_server.py` di Pi membaca key dari file biner (bukan env var),
generate file itu dari hex di atas:

```bash
python3 -c "open('aes_key.bin','wb').write(bytes.fromhex('<HEX_KEY_DARI_BACKEND>'))"
```

> **JANGAN** commit `NISS_AES_KEY` atau `aes_key.bin` ke git — keduanya
> sudah ada di `.gitignore`. Kirim hex key ke rekan lewat channel privat
> (bukan Slack/GitHub issue publik).

> **PENTING:** Kalau key di Pi dan backend tidak sama persis, SEMUA payload
> MQTT (status/event) dan payload CS akan gagal didekripsi backend —
> muncul log `[SECURITY] Dekripsi/autentikasi gagal` di backend, dan device
> akan terlihat offline/rusak di frontend walau Pi-nya menyala normal.

## 3b. (Opsional) Fitur "Foto via CS" — foto tersimpan = hasil rekonstruksi CS

Fitur baru: toggle "Foto via CS" di frontend, kalau diaktifkan sebelum
menekan tombol Foto, hasil foto yang disimpan & diupload adalah **hasil
rekonstruksi OMP+DCT** di MR yang dipilih (bukan JPEG mentah kamera) —
supaya efek Compressive Sensing benar-benar terlihat pada foto asli, bukan
cuma simulasi di panel "Info Kompresi".

Ini butuh dependency tambahan (sebelumnya cuma dipakai oleh service
`cs-reconstruct` di PC, sekarang juga dipakai langsung di Pi):

```bash
pip3 install scipy scikit-learn
```

**Catatan performa:** OMP (Orthogonal Matching Pursuit) cukup berat untuk
CPU Raspberry Pi — rekonstruksi 1 foto bisa makan waktu beberapa detik
sampai puluhan detik tergantung resolusi & MR (jauh lebih lambat dari PC
lab). Ini hanya dipakai untuk **foto** (aksi sekali klik), bukan video —
menerapkannya ke rekaman video real-time tidak memungkinkan karena OMP
tidak cukup cepat untuk diproses per-frame pada frame rate video.

Kalau toggle "Foto via CS" **tidak pernah** diaktifkan, foto tetap tersimpan
seperti biasa (JPEG langsung dari kamera) dan `scipy`/`scikit-learn` tidak
akan pernah diimpor (lazy import, hanya dipanggil saat rekonstruksi
benar-benar dijalankan) — tapi tetap disarankan diinstal dari awal supaya
toggle langsung siap dipakai kapan saja tanpa error `ModuleNotFoundError`.

## 4. (Opsional) Sesuaikan parameter Compressive Sensing

Default sudah aman dipakai apa adanya (`CS_BLOCK_SIZE=64`,
`CS_MR_PERCENT=100` — kualitas terbaik, payload tetap 8.6× lebih kecil dari
data mentah). Kalau mau override, tambahkan ke `.env` Pi:

```env
CS_BLOCK_SIZE=64
CS_MR_PERCENT=100
```

## 5. Restart service

```bash
# Kalau pakai PM2:
pm2 restart niss-camera

# Kalau dijalankan manual:
# Ctrl+C proses lama, lalu:
python3 mqtt_server.py
```

## 6. Verifikasi

Cek dari Pi sendiri (ganti `localhost` kalau perlu):

```bash
curl http://localhost:5000/info
# harus balas JSON: {"width":1280,"height":720,"fps":20}

curl -o /dev/null -w "%{http_code}\n" http://localhost:5000/snapshot_cs
# harus balas 200 (bukan 404) -- payload biner terenkripsi
```

Cek dari sisi backend (PC lab) — log harus bersih tanpa `[SECURITY]` error:

```bash
docker logs niss-backend --tail 30
```

Cek dari frontend: buka live view, tunggu beberapa detik — label resolusi
harus berubah dari `— × — · — FPS` menjadi angka asli (mis. `1280×720 · 20
FPS`), dan toggle "Mode: Compressive Sensing" harus menampilkan gambar
(bukan macet di placeholder).

## Troubleshooting

| Gejala | Penyebab | Solusi |
|---|---|---|
| `/info` masih 404 | Pi belum `git pull` atau service belum di-restart | Ulangi langkah 1 & 5 |
| `/snapshot_cs` 503 | Kamera belum sempat mengisi frame CS pertama (baru start) | Tunggu beberapa detik, coba lagi |
| Backend log `[SECURITY] Dekripsi/autentikasi gagal` | Key AES di Pi ≠ backend | Cek ulang langkah 3, pastikan hex key sama persis (32 karakter) |
| `ModuleNotFoundError: No module named 'Crypto'` | `pycryptodome` belum terinstall | `pip3 install pycryptodome` |
| Device jadi terlihat offline padahal menyala | Payload status MQTT gagal didekripsi (key beda) | Sama seperti dekripsi gagal di atas — cek key |
