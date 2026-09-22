#!/bin/sh
set -eu
mkdir -p /data/config /data/recordings /data/modules /data/logs
exec python3 /app/sprecorder/player.py
