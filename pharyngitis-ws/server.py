"""
server.py - Pharyngitis detector over WebSocket (realtime streaming).

Model: YOLO object-detection (ultralytics), 2 classes "normal" / "phar".
Diporting dari repo model terpisah (Pharyngitis, branch `yolo`) -- yolov8n
dipilih sebagai model produksi (mAP50 0.978, ~4ms/gambar di CPU, tercepat
di antara 6 varian yang diuji, lihat metrics/yolo_comparison.csv di repo
model). Karena ini detector (bukan classifier), gambar tanpa area
tenggorokan menghasilkan 0 deteksi di atas threshold -> built-in rejection
("no_throat_detected"), bukan dipaksa masuk ke salah satu dari 2 kelas.

Keeps a persistent connection so a client (browser / Raspberry Pi) can push
frames continuously and receive predictions back with low overhead - better
suited to realtime than one HTTP request per frame.

Endpoints:
    GET  /            -> browser webcam demo page
    GET  /health      -> health check
    POST /predict     -> single-image HTTP inference (parity with the REST API)
    WS   /ws/predict  -> stream frames (binary JPEG or base64 text), get JSON back

Optional auth: set API_TOKEN env var; clients must then pass ?token=... on connect.
"""

import base64
import io
import os
import time
from pathlib import Path

import numpy as np
from PIL import Image
from ultralytics import YOLO

from fastapi import FastAPI, File, UploadFile, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import JSONResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool
import uvicorn

# ── config ────────────────────────────────────────────────────────────────
MODEL_PATH   = Path(os.environ.get("MODEL_PATH", "models/yolov8n.pt"))
API_TOKEN    = os.environ.get("API_TOKEN")            # None = no auth
CONF_THRESH  = float(os.environ.get("YOLO_CONF", "0.45"))
IMG_SIZE     = int(os.environ.get("IMG_SIZE", "640"))
# Nama kelas dataset: "normal" (tenggorokan sehat) / "phar" (pharyngitis) --
# dipetakan ke istilah lama supaya frontend (yang cek prediction === 'pharyngitis')
# tetap kompatibel tanpa perlu diubah.
CLASS_MAP    = {"normal": "no_pharyngitis", "phar": "pharyngitis"}
STATIC_DIR   = Path(__file__).parent / "static"

app = FastAPI(title="Pharyngitis Detector (YOLO, WebSocket)", version="2.0.0")
model = None


@app.on_event("startup")
def load_model():
    global model
    if not MODEL_PATH.exists():
        raise RuntimeError(f"Model not found at {MODEL_PATH}. Taruh file .pt YOLO di path ini.")
    model = YOLO(str(MODEL_PATH))
    print(f"Model loaded from {MODEL_PATH} | classes: {model.names}")


def predict_bytes(img_bytes: bytes) -> dict:
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    arr = np.array(img)

    t0 = time.perf_counter()
    results = model.predict(arr, imgsz=IMG_SIZE, conf=CONF_THRESH, verbose=False)
    latency_ms = (time.perf_counter() - t0) * 1000

    boxes = results[0].boxes
    if boxes is None or len(boxes) == 0:
        return {
            "prediction": "no_throat_detected",
            "confidence": 0.0,
            "probabilities": {"no_pharyngitis": 0.0, "pharyngitis": 0.0},
            "latency_ms": round(latency_ms, 2),
            "detections": 0,
        }

    # Ambil deteksi dengan confidence tertinggi sebagai hasil utama.
    best_idx = int(boxes.conf.argmax())
    best_cls_name = model.names[int(boxes.cls[best_idx])]
    best_conf = float(boxes.conf[best_idx])

    # Probabilitas per kelas: confidence deteksi terbaik dari tiap kelas yang
    # muncul di frame (bukan softmax -- YOLO tidak menghasilkan distribusi
    # tunggal per-gambar seperti classifier, jadi ini pendekatan terdekat).
    probs_by_raw_class = {}
    for i in range(len(boxes)):
        raw_name = model.names[int(boxes.cls[i])]
        conf = float(boxes.conf[i])
        if conf > probs_by_raw_class.get(raw_name, 0.0):
            probs_by_raw_class[raw_name] = conf
    probabilities = {
        CLASS_MAP.get(raw, raw): round(conf, 4) for raw, conf in probs_by_raw_class.items()
    }
    for mapped in CLASS_MAP.values():
        probabilities.setdefault(mapped, 0.0)

    return {
        "prediction": CLASS_MAP.get(best_cls_name, best_cls_name),
        "confidence": round(best_conf, 4),
        "probabilities": probabilities,
        "latency_ms": round(latency_ms, 2),
        "detections": int(len(boxes)),
    }


def _decode_message(msg: dict):
    """Extract raw image bytes from a WebSocket message (binary or base64 text)."""
    data = msg.get("bytes")
    if data is not None:
        return data
    text = msg.get("text")
    if text:
        if text.startswith("data:"):          # data URL from a browser canvas
            text = text.split(",", 1)[1]
        try:
            return base64.b64decode(text)
        except Exception:
            return None
    return None


# ── routes ──────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": model is not None}


@app.get("/", response_class=HTMLResponse)
def index():
    page = STATIC_DIR / "index.html"
    if page.exists():
        return HTMLResponse(page.read_text(encoding="utf-8"))
    return HTMLResponse("<h3>Pharyngitis WebSocket server</h3><p>Connect to /ws/predict</p>")


@app.post("/predict")
async def predict(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    img_bytes = await file.read()
    try:
        return JSONResponse(await run_in_threadpool(predict_bytes, img_bytes))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/predict")
async def ws_predict(websocket: WebSocket, token: str | None = Query(default=None)):
    if API_TOKEN and token != API_TOKEN:
        await websocket.close(code=1008)         # policy violation
        return
    await websocket.accept()
    frames = 0
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            img_bytes = _decode_message(msg)
            if not img_bytes:
                await websocket.send_json({"error": "no image data"})
                continue
            try:
                result = await run_in_threadpool(predict_bytes, img_bytes)
                frames += 1
                result["frame"] = frames
                await websocket.send_json(result)
            except Exception as e:
                await websocket.send_json({"error": str(e)})
    except WebSocketDisconnect:
        pass
    print(f"WebSocket closed after {frames} frames")


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
