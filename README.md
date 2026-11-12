# SPRECORDER 2.1.0 — TrueNAS + ChanTrail 0.4.8-beta

SPRECORDER reste l'application principale. Il conserve son interface de monitoring et son moteur d'enregistrement FFmpeg robuste. ChanTrail 0.4.8-beta est utilisé comme moteur amont pour résoudre et relayer les flux Stripchat via son endpoint HLS relay.

## Architecture

- `sprecorder` : interface SPRECORDER, monitoring, enregistrements FFmpeg, reconnexions, bibliothèque et conversion MP4.
- `chantrail` : résolution Stripchat et HLS relay officiel ChanTrail 0.4.8-beta.
- SPRECORDER récupère le flux via `http://chantrail:3030/stream/{model}`.
- Le navigateur utilise le relay publié sur le port hôte `13030`.
- Interface SPRECORDER : `http://IP_DU_NAS:18080`.

## Données et compatibilité

Les anciens fichiers ne sont pas supprimés. Les enregistrements sont conservés dans `/mnt/services/SPRECORDER/recordings`.

La configuration ChanTrail 0.4.x est isolée dans `config-chantrail-0.4` et les modules dans `modules-chantrail-0.4`, car ChanTrail signale des incompatibilités entre les branches 0.3 et 0.4. Les anciens dossiers `config` et `modules` ne sont pas réutilisés.

SPRECORDER conserve sa liste de modèles dans `config-chantrail-0.4/models.json`.

## Déploiement TrueNAS

Depuis le dossier contenant ce projet :

```bash
mkdir -p /mnt/services/SPRECORDER/config-chantrail-0.4
mkdir -p /mnt/services/SPRECORDER/recordings
mkdir -p /mnt/services/SPRECORDER/modules-chantrail-0.4
mkdir -p /mnt/services/SPRECORDER/logs-2.1.0
mkdir -p /mnt/services/SPRECORDER/ts_fragment-chantrail-0.4
docker compose config
docker compose pull chantrail
docker compose up -d --build
```

Ouvrir `http://IP_DU_NAS:18080`.

Au premier démarrage, vérifier dans les paramètres ChanTrail que le dossier de sortie est `/app/stripchat-recorder/recordings`.

## Diagnostic

Le bouton « Console » en bas à droite ouvre une console flottante. Elle affiche les dernières lignes de :

- `sprecorder.log`
- `chantrail.log`
- `runtime.log`
- `ffmpeg_*.log` et `recovery_*.log`

Endpoint JSON : `/debug/logs?limit=260`.

Journaux persistants : `/mnt/services/SPRECORDER/logs-2.1.0`.

## Enregistrement

- Les flux sont copiés sans réencodage (`-c copy`).
- Les fichiers actifs sont d'abord écrits en `.ts.part`.
- Un arrêt normal finalise le TS puis tente une conversion MP4 avec validation `ffprobe`.
- Le TS source n'est supprimé qu'après validation du MP4 ; en cas d'échec, le TS est conservé.
- Une interruption laisse le `.ts.part` pour récupération.
- Les reconnexions FFmpeg sont illimitées par défaut (`SPREC_FFMPEG_MAX_RECONNECTS=0`).

## Version moteur

- SPRECORDER : `2.1.0-chantrail-0.4.8-beta`
- ChanTrail : `0.4.8-beta` (image AMD64 épinglée par digest).
