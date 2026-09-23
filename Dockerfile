FROM python:3.11-slim

WORKDIR /app

# Install minimal system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

COPY . .

RUN mkdir -p /app/config /app/log \
    && useradd -u 1000 -m appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 28472

ENV AETHERSWAP_MODE=server \
    AETHERSWAP_AGREE_DISCLAIMER=1 \
    AETHERSWAP_HOST=0.0.0.0 \
    AETHERSWAP_PORT=28472

CMD ["python", "run.py"]
