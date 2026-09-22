FROM --platform=linux/amd64 python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    SPREC_HOST=0.0.0.0 \
    SPREC_PORT=8080 \
    SPREC_BACKEND=http://chantrail:3030 \
    SPREC_DATA_DIR=/data \
    SPREC_RECORDING_START_TIMEOUT=30 \
    SPREC_FFMPEG_STALL_TIMEOUT=45 \
    SPREC_FFMPEG_RECONNECT_DELAY=5 \
    SPREC_FFMPEG_MAX_RECONNECTS=0

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl file \
    && rm -rf /var/lib/apt/lists/*

COPY app/player.py /app/sprecorder/player.py
RUN mkdir -p /data/config /data/recordings /data/modules /data/logs

EXPOSE 8080

CMD ["python3", "/app/sprecorder/player.py"]
