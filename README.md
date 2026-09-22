# SPRECORDER — TrueNAS / Docker — Robust 24/7 recording

Cette version conserve le backend original ChanTrail pour le relay MPEG-TS et ajoute un superviseur FFmpeg robuste pour les enregistrements.

## Enregistrement

- H.264/AAC conservés en `-c copy` : aucun réencodage.
- Sortie active : `model_YYYYMMDD_HHMMSS.ts.part`.
- Le `.ts.part` est alimenté directement par la sortie MPEG-TS de FFmpeg.
- Tant que FFmpeg produit des données, la taille du `.ts.part` augmente.
- Après un arrêt normal, `os.replace()` renomme atomiquement `.ts.part` en `.ts`.
- Après une panne, le `.ts.part` reste volontairement présent.
- Une perte du flux déclenche automatiquement une nouvelle instance FFmpeg vers le même `.ts.part`; aucun second enregistrement parallèle n'est créé pour le même modèle.
- Les reconnexions FFmpeg HTTP sont également activées lorsqu'elles sont supportées par la version de FFmpeg.

## Tolérance aux erreurs

Le recorder utilise notamment :

- `-fflags +discardcorrupt+genpts`
- `-err_detect ignore_err`
- `-avoid_negative_ts make_non_negative`
- `-muxpreload 0 -muxdelay 0`
- `-c copy`

Les erreurs comme `Packet corrupt`, `non-existing PPS`, `decode_slice_header`, `no frame` et les DTS non monotones sont conservées dans les logs. Elles ne sont pas masquées.

Un fichier réellement en `RECORDING` doit avoir reçu des octets de FFmpeg. Pendant le démarrage ou une reconnexion, l'interface affiche `CONNECTING`/`RECONNECTING`, pas `RECORDING`.

## Reconnexion

Variables Docker :

- `SPREC_RECORDING_START_TIMEOUT=30`
- `SPREC_FFMPEG_STALL_TIMEOUT=45`
- `SPREC_FFMPEG_RECONNECT_DELAY=5`
- `SPREC_FFMPEG_MAX_RECONNECTS=0` (`0` = illimité tant que l'enregistrement reste demandé)

## Récupération des `.ts.part`

Au démarrage, SPRECORDER tente de récupérer les `.ts.part` non vides avec FFmpeg vers `*.ts.recovered.ts`.

La récupération peut reconstruire uniquement les données que FFmpeg arrive encore à lire. Elle ne peut pas recréer des données H.264 réellement perdues ou des paquets absents.

Une récupération manuelle est également disponible dans l'interface via **Récupérer les .ts.part** ou :

`GET /record/recover`

## Logs / interface

Chaque enregistrement possède un log `logs/ffmpeg_*.log` contenant notamment :

- tentatives FFmpeg ;
- erreurs FFmpeg ;
- reconnexions ;
- taille écrite ;
- durée ;
- raison d'arrêt ;
- destination finale ou conservation du `.ts.part`.

L'interface affiche les mêmes compteurs pendant l'enregistrement.

## Chemins TrueNAS

Utiliser ces montages :

- `/mnt/services/SPRECORDER/config` → `/data/config`
- `/mnt/services/SPRECORDER/recordings` → `/data/recordings`
- `/mnt/services/SPRECORDER/modules` → `/data/modules`
- `/mnt/services/SPRECORDER/logs` → `/data/logs`

Ne pas utiliser `/app/data/...` avec cette version.

## Docker / architecture

Cette image est explicitement construite pour `linux/amd64`, correspondant au TrueNAS indiqué (Intel Core i5-8250U).

L'entrée du conteneur neutralise `LD_PRELOAD` et journalise l'architecture, l'architecture Debian, le chemin/version FFmpeg et `LD_LIBRARY_PATH` dans `logs/runtime.log`.

Le message :

`Inconsistency detected by ld.so: elf_machine_rela_relative...`

est un problème du chargeur dynamique/glibc, pas une erreur HLS. Des cas Debian documentés montrent que ce message peut venir d'une bibliothèque ELF incohérente/corrompue et affecter plusieurs programmes, dont FFmpeg. Le conteneur est donc rendu déterministe (amd64 + Debian Bookworm + `LD_PRELOAD` vide) et effectue un préflight `ffmpeg -version` avant de démarrer le service.

## Tests locaux inclus

Les tests du dossier `tests/` vérifient :

- croissance de `.ts.part` avant affichage de `RECORDING` ;
- arrêt normal `.ts.part` → `.ts` ;
- reconnexion après sortie FFmpeg ;
- conservation d'un `.ts.part` après épuisement des reconnexions ;
- absence de doublon pour un même modèle ;
- plusieurs modèles simultanés ;
- récupération d'un `.ts.part` lisible.

## Build

```bash
docker build --platform linux/amd64 -t sprecorder:robust .
```

## GitHub Actions

Le workflow publie `ghcr.io/salides/sprecorder:latest` pour `linux/amd64`.
