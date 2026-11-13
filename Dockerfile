FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY app/sprecorder.py /app/sprecorder.py
COPY app/index.html /app/index.html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
 CMD python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/health",timeout=3).read()' || exit 1
CMD ["python", "/app/sprecorder.py"]
