#!/bin/sh
set -eu
APP=/app/stripchat-recorder
DATA=/data
# Keep the runtime libc environment deterministic. A stray LD_PRELOAD from the host
# must never inject an incompatible library into FFmpeg or the Rust backend.
unset LD_PRELOAD || true
export LD_PRELOAD=
mkdir -p "$DATA/config" "$DATA/recordings" "$DATA/modules" "$DATA/logs"

log_runtime() {
  echo "[RUNTIME] uname=$(uname -a)"
  echo "[RUNTIME] arch=$(uname -m)"
  echo "[RUNTIME] dpkg_arch=$(dpkg --print-architecture 2>/dev/null || true)"
  echo "[RUNTIME] ffmpeg_path=$(command -v ffmpeg || true)"
  echo "[RUNTIME] ffmpeg_version=$(ffmpeg -hide_banner -version 2>&1 | head -1 || true)"
  echo "[RUNTIME] LD_PRELOAD=${LD_PRELOAD:-}"
  echo "[RUNTIME] LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-}"
  if command -v file >/dev/null 2>&1; then file /usr/bin/ffmpeg || true; fi
}
log_runtime > "$DATA/logs/runtime.log" 2>&1
if ! ffmpeg -hide_banner -version >/dev/null 2>&1; then
  echo "FFmpeg preflight failed" >&2
  cat "$DATA/logs/runtime.log" >&2 || true
  exit 1
fi
if [ ! -f "$DATA/config/settings.json" ]; then
cat > "$DATA/config/settings.json" <<JSON
{
  "output_dir": "/data/recordings",
  "poll_interval": 30,
  "auto_record": false,
  "api_proxy": null,
  "cdn_proxy": null,
  "mirror": null,
  "max_concurrent": 0,
  "merge_format": "mp4",
  "preferred_resolution": 0,
  "resolution_preference": "lower",
  "max_tmp_dir_gb": 50.0,
  "language": "en-US",
  "server_port": 3030,
  "mouflon_sync_url": "https://mouflon.chantrail.com",
  "setup_done": true
}
JSON
fi
if [ ! -f "$DATA/config/mouflon_keys.json" ]; then
  printf '%s\n' '{"keys":{},"auto_synced_at":null,"manual_updated_at":null}' > "$DATA/config/mouflon_keys.json"
fi
rm -rf "$APP/config" "$APP/recordings" "$APP/modules" "$APP/logs"
ln -s "$DATA/config" "$APP/config"
ln -s "$DATA/recordings" "$APP/recordings"
ln -s "$DATA/modules" "$APP/modules"
ln -s "$DATA/logs" "$APP/logs"
cd "$APP/backend"
"$APP/backend/target/release/stripchat-recorder" > /data/logs/chantrail.log 2>&1 &
BACK_PID=$!
PLAYER_PID=""
cleanup(){
  kill "$PLAYER_PID" 2>/dev/null || true
  kill "$BACK_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT
READY=0
for i in $(seq 1 90); do
  if python3 - <<'PY'
import socket
s=socket.socket(); s.settimeout(1)
try:
    s.connect(('127.0.0.1',3030)); raise SystemExit(0)
except OSError: raise SystemExit(1)
finally: s.close()
PY
  then READY=1; break; fi
  if ! kill -0 "$BACK_PID" 2>/dev/null; then
    cat /data/logs/chantrail.log || true
    exit 1
  fi
  sleep 1
done
if [ "$READY" -ne 1 ]; then
  echo "ChanTrail backend did not become ready on port 3030" >&2
  cat /data/logs/chantrail.log || true
  exit 1
fi
python3 /app/sprecorder/player.py > /data/logs/sprecorder.log 2>&1 &
PLAYER_PID=$!
wait "$PLAYER_PID"
