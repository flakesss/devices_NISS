# Setup Cloudflare Tunnel di Raspberry Pi (live stream)

Folder ini berisi kredensial tunnel `niss-pi-stream` yang meng-expose Flask stream
Pi (`localhost:5000`) ke `https://pi-stream.satsetin.com`, supaya backend di PC lab
tetap bisa akses live stream walau Pi ada di jaringan/lokasi berbeda.

## Langkah di Raspberry Pi

1. Install cloudflared:
   ```bash
   curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
   sudo dpkg -i cloudflared.deb
   ```

2. Salin folder ini (`pi-tunnel-setup/`) ke Pi, misal ke `/etc/cloudflared/`:
   ```bash
   sudo mkdir -p /etc/cloudflared
   sudo cp credentials.json /etc/cloudflared/credentials.json
   sudo cp config.yml /etc/cloudflared/config.yml
   ```

3. Install sebagai service supaya auto-start & auto-restart:
   ```bash
   sudo cloudflared service install --config /etc/cloudflared/config.yml
   sudo systemctl enable cloudflared
   sudo systemctl start cloudflared
   sudo systemctl status cloudflared
   ```

4. Pastikan `mqtt_server.py` (Flask stream) sudah berjalan di port 5000 (lihat
   `STREAM_PORT` di `.env` root repo `mqtt/`), baru tunnel ini bisa meneruskan trafik.

5. Setelah aktif, cek dari PC lab / browser manapun:
   ```
   https://pi-stream.satsetin.com/health
   https://pi-stream.satsetin.com/stream
   ```

## Setup kedua: koneksi Pi → broker MQTT (`mqtt.satsetin.com`)

Bagian di atas untuk live stream (Pi jadi *server*, expose service ke internet).
Untuk MQTT arahnya kebalik: Pi jadi *client* yang perlu konek ke broker Mosquitto
di PC lab lewat tunnel `niss-mqtt` (`mqtt.satsetin.com`). Karena MQTT adalah
protokol TCP mentah (bukan HTTP), Pi tidak bisa langsung
`connect("mqtt.satsetin.com", ...)` — perlu **cloudflared client-side proxy**
(`cloudflared access tcp`) yang jalan di Pi, membuka port lokal yang meneruskan
ke tunnel tersebut.

1. Pastikan cloudflared sudah terinstall (langkah 1 di atas).

2. Test manual dulu (opsional, buat memastikan tunnel MQTT reachable):
   ```bash
   cloudflared access tcp --hostname mqtt.satsetin.com --url localhost:1883
   ```
   Biarkan berjalan, lalu di terminal lain coba:
   ```bash
   mosquitto_pub -h localhost -p 1883 -t test -m "halo"
   ```
   Kalau tidak error, tunnel MQTT sudah tersambung dengan benar. Tekan Ctrl+C untuk hentikan test ini.

3. Install sebagai service permanen (auto-start & auto-restart), supaya proxy ini
   selalu jalan di background dan `mqtt_server.py` bisa connect ke
   `localhost:1883` seperti biasa:
   ```bash
   sudo cp cloudflared-mqtt-proxy.service /etc/systemd/system/cloudflared-mqtt-proxy.service
   sudo systemctl daemon-reload
   sudo systemctl enable cloudflared-mqtt-proxy
   sudo systemctl start cloudflared-mqtt-proxy
   sudo systemctl status cloudflared-mqtt-proxy
   ```
   > Sesuaikan `ExecStart` di file `.service` kalau path `cloudflared` binary di Pi
   > kamu berbeda dari `/usr/local/bin/cloudflared` (cek dengan `which cloudflared`),
   > dan `User=pi` kalau username Pi kamu bukan `pi`.

4. Di `mqtt/.env` (folder repo ini di Pi), **`MQTT_HOST` dan `MQTT_PORT` TIDAK perlu
   diubah** — tetap `MQTT_HOST=localhost` dan `MQTT_PORT=1883`, karena
   `mqtt_server.py` tetap connect ke proxy lokal ini, bukan langsung ke
   `mqtt.satsetin.com`. Proxy inilah yang meneruskannya ke broker di PC lab.

5. Kalau `docker compose ps` di PC lab menunjukkan `niss-mosquitto` dan
   `niss-cloudflared-mqtt` keduanya `Up`, dan service
   `cloudflared-mqtt-proxy` di Pi juga aktif, jalankan `mqtt_server.py` — dia akan
   otomatis terhubung ke broker walau Pi ada di jaringan yang sepenuhnya berbeda.

## Catatan keamanan

`credentials.json` adalah rahasia — setara dengan kunci akses ke tunnel ini.
Jangan commit ke git (sudah di-gitignore) dan jangan share ke luar tim.
