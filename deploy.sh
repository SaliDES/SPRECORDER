#!/bin/sh
set -eu
BASE=/mnt/services/SPRECORDER
mkdir -p "$BASE/config-chantrail-0.4" \
         "$BASE/recordings" \
         "$BASE/modules-chantrail-0.4" \
         "$BASE/logs-2.1.0" \
         "$BASE/ts_fragment-chantrail-0.4"

echo '===== Validation du docker-compose ====='
docker compose config

echo '===== Téléchargement de ChanTrail 0.4.8-beta ====='
docker compose pull chantrail

echo '===== Construction et démarrage de SPRECORDER ====='
docker compose up -d --build

echo '===== État des services ====='
docker compose ps
