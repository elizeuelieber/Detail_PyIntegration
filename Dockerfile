FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    APP_CONFIG=/config/config.json

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium chromium-driver ca-certificates fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/downloads /data/logs \
    && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
