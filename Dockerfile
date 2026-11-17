# Single-build Debian runtime prevents cross-version Python/OpenSSL mismatches.
FROM node:22-alpine AS webassets
RUN mkdir /frontend && cd /frontend && npm init -y >/dev/null && npm install --no-audit --no-fund hls.js@1.6.13

FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 SPREC_DATA_DIR=/data SPREC_HISTORY=/history
RUN apt-get update && apt-get install -y --no-install-recommends python3 openssl libssl3 ffmpeg ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -c 'import ssl,http.server,urllib.request;print("SSL runtime OK:",ssl.OPENSSL_VERSION)'
WORKDIR /app
COPY app/ /app/
COPY --from=webassets /frontend/node_modules/hls.js/dist/hls.min.js /app/static/hls.min.js
COPY LICENSE LICENSE-HLSJS-APACHE-2.0 SOURCE-NOTES.md /app/licenses/
RUN mkdir -p /data/config /data/recordings /data/state \
    && python3 -m compileall -q /app \
    && python3 -c 'import ssl,engine,streaming,server;print("Application imports OK")'
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD curl -fsS http://127.0.0.1:8080/health >/dev/null || exit 1
CMD ["python3", "/app/server.py"]
