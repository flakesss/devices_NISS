FROM python:3.12-slim

WORKDIR /app

# ffmpeg -- dipakai untuk menyusun ulang frame hasil rekonstruksi CS (video)
# jadi mp4 H.264 (rekonstruksi per-frame lewat cs_codec, reassembly lewat CLI
# ffmpeg karena wheel opencv-python-headless tidak menyertakan encoder H.264).
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY cs-reconstruct/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cs_codec.py .
COPY cs-reconstruct/app.py .

EXPOSE 6000
CMD ["python", "app.py"]
