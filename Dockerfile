# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# poppler + tesseract cover PDF text extraction and the OCR fallback.
# Force IPv4: many hosts resolve Debian to IPv6 first but have no IPv6 route,
# which makes apt sit in "Ign:" retries forever.
RUN apt-get -o Acquire::ForceIPv4=true update && \
    apt-get -o Acquire::ForceIPv4=true install -y --no-install-recommends \
        curl \
        poppler-utils \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home appuser && \
    mkdir -p /data && \
    chown -R appuser:appuser /data /app

USER appuser

EXPOSE 5000
ENV FLASK_ENV=production \
    TALLY_HOST=0.0.0.0 \
    TALLY_ALLOW_REMOTE=1 \
    DATA_DIR=/data \
    PORT=5000

VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:5000/api/v1/health || exit 1

CMD ["python", "run.py"]

LABEL org.opencontainers.image.source="https://github.com/cryptobredda/tallylocal" \
      org.opencontainers.image.description="Tally Local - local-first personal finance with MCP, CLI and API" \
      org.opencontainers.image.title="Tally Local" \
      org.opencontainers.image.vendor="Tally Local"
