FROM python:3.11-slim

WORKDIR /app

# ultralytics -> opencv-python butuh libGL/libglib runtime yang tidak ada di
# image slim -- tanpa ini import cv2 gagal dengan "libGL.so.1: cannot open
# shared object file".
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# install dependencies first (cached layer)
COPY requirements.txt .
# torch CPU-only dulu (~200MB) -- kalau tidak, pip menarik torch build CUDA
# penuh (~2GB+ termasuk paket nvidia-*) yang tidak perlu untuk service ini
# (inferensi CPU saja) dan rawan gagal di koneksi lambat.
RUN pip install --no-cache-dir torch==2.3.1 torchvision==0.18.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

# copy server, static assets and model
COPY server.py .
COPY static/ ./static/
COPY models/ ./models/

EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
