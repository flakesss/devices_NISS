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
from flask import Flask, request, Response

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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6000, threaded=True)
