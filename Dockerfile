FROM rust:1-bookworm AS chantrail-build
RUN apt-get update && apt-get install -y --no-install-recommends git pkg-config libssl-dev ca-certificates file && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git clone --depth 1 https://github.com/ChanTrail/StripchatRecorder.git
WORKDIR /src/StripchatRecorder/backend
RUN cargo build --release

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    LD_PRELOAD= \
    SPREC_HOST=0.0.0.0 \
    SPREC_PORT=8080 \
    SPREC_BACKEND=http://127.0.0.1:3030 \
    SPREC_DATA_DIR=/data \
    SPREC_RECORDING_START_TIMEOUT=30 \
    SPREC_FFMPEG_STALL_TIMEOUT=45 \
    SPREC_FFMPEG_RECONNECT_DELAY=5 \
    SPREC_FFMPEG_MAX_RECONNECTS=0
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl file && rm -rf /var/lib/apt/lists/*
COPY --from=chantrail-build /src/StripchatRecorder /app/stripchat-recorder
COPY app/player.py /app/sprecorder/player.py
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh && mkdir -p /data/config /data/recordings /data/modules /data/logs
EXPOSE 8080 3030
ENTRYPOINT ["/app/entrypoint.sh"]
