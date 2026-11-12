#!/bin/sh
set -eu
BASE=/mnt/services/SPRECORDER
mkdir -p "$BASE/config-standalone-2.2" "$BASE/recordings" "$BASE/logs-standalone-2.2"
if [ -f "$BASE/config-chantrail-0.4/mouflon_keys.json" ] && [ ! -f "$BASE/config-standalone-2.2/mouflon_keys.json" ]; then
  sudo install -m 600 "$BASE/config-chantrail-0.4/mouflon_keys.json" "$BASE/config-standalone-2.2/mouflon_keys.json"
fi
echo '===== Validation du docker-compose ====='
sudo docker compose config
echo '===== Construction et démarrage du test autonome (sans arrêter ChanTrail/SPRECORDER actuel) ====='
sudo docker compose up -d --build
echo '===== État du service de test ====='
sudo docker compose ps
