# Bundle hls.js inside SPRECORDER; browser needs no third-party CDN at runtime.
FROM node:22-alpine AS webassets
RUN mkdir /frontend && cd /frontend && npm init -y >/dev/null && npm install --no-audit --no-fund hls.js@1.6.13

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 SPREC_DATA_DIR=/data SPREC_HISTORY=/history
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY app/ /app/
COPY --from=webassets /frontend/node_modules/hls.js/dist/hls.min.js /app/static/hls.min.js
COPY LICENSE LICENSE-HLSJS-APACHE-2.0 SOURCE-NOTES.md /app/licenses/
RUN mkdir -p /data/config /data/recordings /data/state
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD curl -fsS http://127.0.0.1:8080/health >/dev/null || exit 1
CMD ["python", "/app/server.py"]
