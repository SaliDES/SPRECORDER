#!/bin/sh
set -eu
APP=/app/stripchat-recorder
DATA=/data
mkdir -p "$DATA/config" "$DATA/recordings" "$DATA/modules" "$DATA/logs"
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
cleanup(){ kill "$BACK_PID" 2>/dev/null || true; kill "$PLAYER_PID" 2>/dev/null || true; }
trap cleanup INT TERM EXIT
for i in $(seq 1 90); do
  if python3 - <<'PY'
import socket
s=socket.socket(); s.settimeout(1)
try: s.connect(('127.0.0.1',3030)); raise SystemExit(0)
except OSError: raise SystemExit(1)
finally: s.close()
PY
  then break; fi
  if ! kill -0 "$BACK_PID" 2>/dev/null; then
    cat /data/logs/chantrail.log || true
    exit 1
  fi
  sleep 1
done
python3 /app/sprecorder/player.py > /data/logs/sprecorder.log 2>&1 &
PLAYER_PID=$!
wait "$PLAYER_PID"
