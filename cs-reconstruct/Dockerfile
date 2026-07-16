FROM python:3.12-slim

WORKDIR /app

COPY cs-reconstruct/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cs_codec.py .
COPY cs-reconstruct/app.py .

EXPOSE 6000
CMD ["python", "app.py"]
