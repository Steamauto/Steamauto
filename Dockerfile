ARG BASE_IMAGE=mcr.microsoft.com/playwright/python:v1.49.0-jammy
FROM ${BASE_IMAGE}

WORKDIR /app

COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt \
    && python -m playwright install --with-deps chromium

COPY . .

RUN mkdir -p /app/config /app/log \
    && chown -R pwuser:pwuser /app

USER pwuser

EXPOSE 28472

ENV AETHERSWAP_MODE=server \
    AETHERSWAP_AGREE_DISCLAIMER=1 \
    AETHERSWAP_HOST=0.0.0.0 \
    AETHERSWAP_PORT=28472

CMD ["python", "run.py"]
