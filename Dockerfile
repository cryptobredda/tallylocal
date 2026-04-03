# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        poppler-utils \
        tesseract-ocr \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home appuser && \
    mkdir -p /data && \
    chown -R appuser:appuser /data /app

USER appuser

EXPOSE 5000
ENV FLASK_ENV=production
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:5000/ || exit 1
CMD ["python", "run.py"]
