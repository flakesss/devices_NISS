module.exports = {
  apps: [
    {
      name: "niss-camera",
      script: "mqtt_server.py",
      cwd: __dirname,
      interpreter: "./venv/bin/python",
      restart_delay: 5000,
      max_restarts: 10,
      watch: false,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
    },
    // niss-tunnel (Cloudflare Quick Tunnel via PM2) dihapus — digantikan tunnel
    // "NISS" (app.satsetin.com) yang jalan sebagai service "cloudflared" di
    // docker-compose.yml, auto-restart via restart: unless-stopped.
  ],
};
