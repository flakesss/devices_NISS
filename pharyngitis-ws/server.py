"""
server.py - Pharyngitis detector over WebSocket (realtime streaming).

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

import torch
import torch.nn.functional as F
from PIL import Image
from torchvision import transforms

from fastapi import FastAPI, File, UploadFile, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import JSONResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool
import uvicorn

# ── config ────────────────────────────────────────────────────────────────
MODEL_PATH  = Path(os.environ.get("MODEL_PATH", "model_scripted.pt"))
API_TOKEN   = os.environ.get("API_TOKEN")            # None = no auth
IMG_SIZE    = int(os.environ.get("IMG_SIZE", "224"))
CLASS_NAMES = ["no_pharyngitis", "pharyngitis"]
DEVICE      = torch.device("cpu")
STATIC_DIR  = Path(__file__).parent / "static"

app = FastAPI(title="Pharyngitis Detector (WebSocket)", version="1.0.0")
model = None

preprocess = transforms.Compose([
    transforms.Resize((IMG_SIZE, IMG_SIZE)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
])


@app.on_event("startup")
def load_model():
    global model
    if not MODEL_PATH.exists():
        raise RuntimeError(f"Model not found at {MODEL_PATH}. Export a scripted model first.")
    model = torch.jit.load(str(MODEL_PATH), map_location=DEVICE)
    model.eval()
    print(f"Model loaded from {MODEL_PATH}")


def predict_bytes(img_bytes: bytes) -> dict:
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    tensor = preprocess(img).unsqueeze(0).to(DEVICE)
    t0 = time.perf_counter()
    with torch.no_grad():
        probs = F.softmax(model(tensor), dim=1)[0]
    latency_ms = (time.perf_counter() - t0) * 1000
    idx = int(probs.argmax().item())
    return {
        "prediction": CLASS_NAMES[idx],
        "confidence": round(probs[idx].item(), 4),
        "probabilities": {CLASS_NAMES[i]: round(probs[i].item(), 4) for i in range(len(CLASS_NAMES))},
        "latency_ms": round(latency_ms, 2),
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
