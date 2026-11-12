#!/bin/sh
set -eu
unset LD_PRELOAD || true
export LD_PRELOAD=
DATA="${SPREC_DATA_DIR:-/data}"
mkdir -p "$DATA/config" "$DATA/recordings" "$DATA/logs"

{
  echo "[RUNTIME] version=$(cat /app/sprecorder/VERSION 2>/dev/null || echo unknown)"
  echo "[RUNTIME] uname=$(uname -a)"
  echo "[RUNTIME] arch=$(uname -m)"
  echo "[RUNTIME] ffmpeg=$(command -v ffmpeg || true)"
  ffmpeg -hide_banner -version 2>&1 | head -1 || true
  echo "[RUNTIME] LD_PRELOAD=${LD_PRELOAD:-}"
} >> "$DATA/logs/runtime.log" 2>&1

if ! ffmpeg -hide_banner -version >/dev/null 2>&1; then
  echo "FFmpeg preflight failed" >&2
  exit 1
fi

exec python3 /app/sprecorder/player.py >> "$DATA/logs/sprecorder.log" 2>&1
