FROM python:3.11-slim

WORKDIR /app

# install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# copy server, static assets and model
COPY server.py .
COPY static/ ./static/
COPY model_scripted.pt .

EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
