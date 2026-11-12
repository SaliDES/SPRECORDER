FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 LD_PRELOAD= \
    SPREC_HOST=0.0.0.0 SPREC_PORT=8080 SPREC_BACKEND=http://127.0.0.1:13031 \
    SPREC_RELAY_PORT=13031 SPREC_DATA_DIR=/data SPREC_RECORDING_START_TIMEOUT=30 \
    SPREC_FFMPEG_STALL_TIMEOUT=45 SPREC_FFMPEG_RECONNECT_DELAY=5 SPREC_FFMPEG_MAX_RECONNECTS=0
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/sprecorder
COPY VERSION ./VERSION
COPY app/player.py ./player.py
COPY app/relay.py ./relay.py
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /data/config /data/recordings /data/logs
EXPOSE 8080 13031
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD curl -fsS http://127.0.0.1:8080/health >/dev/null || exit 1
ENTRYPOINT ["/entrypoint.sh"]
