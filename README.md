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


## Conversion automatique en MP4

À chaque arrêt normal (arrêt manuel ou arrêt automatique après confirmation hors ligne) :

1. le `.ts.part` est finalisé en `.ts` ;
2. FFmpeg remuxe le TS vers un fichier temporaire `.mp4.part` avec `-c copy` (pas de réencodage) ;
3. si FFmpeg réussit et que `ffprobe` confirme une piste vidéo et une durée supérieure à zéro, le MP4 est renommé atomiquement ;
4. le TS source est supprimé **uniquement après validation du MP4**.

Si la conversion échoue ou si le MP4 est invalide, le `.ts` source est conservé et l'interface affiche une erreur. Les fichiers `.ts.part` issus d'une panne ne sont pas convertis automatiquement tant qu'ils ne sont pas récupérés/finalisés.

## Arrêt automatique hors ligne

Le moniteur vérifie chaque modèle toutes les 10 secondes. Pour éviter qu'une réponse réseau isolée arrête un live, il exige par défaut 3 contrôles hors ligne consécutifs. Il attend ensuite que le fichier n'ait pas grossi pendant 15 secondes avant d'arrêter FFmpeg et de lancer la conversion MP4. Cela s'applique aux enregistrements manuels et automatiques.

Variables configurables dans `docker-compose.yml` :

- `SPREC_OFFLINE_CONFIRMATIONS=3` : nombre de contrôles hors ligne consécutifs.
- `SPREC_OFFLINE_NO_GROWTH_SECONDS=15` : délai sans nouvelles données avant l'arrêt.
- `SPREC_CONVERT_TIMEOUT=14400` : timeout de conversion (4 heures).

Un flux qui reste faussement détecté comme en ligne ou qui continue d'envoyer des données après le passage hors ligne peut retarder l'arrêt. Une vidéo réellement absente du flux ne peut pas être reconstruite par la conversion.


## Installation sur TrueNAS SCALE (via GitHub Container Registry)

Le dépôt contient aussi `docker-compose.truenas.yml`, destiné à l'écran **Install via YAML** de TrueNAS. Il utilise l'image publiée par GitHub Actions et conserve les données sur des chemins hôte persistants.

1. Pousse tout le contenu du dépôt sur la branche `main` de ton GitHub.
2. Dans GitHub, attends la réussite de l'action **Build and Publish SPRECORDER** dans l'onglet Actions. Elle publie `ghcr.io/<ton-utilisateur>/sprecorder:latest`.
3. Dans les paramètres du package GitHub Container Registry, rends le package public, ou configure les identifiants du registre dans TrueNAS si tu veux le garder privé.
4. Sur TrueNAS, crée un dataset/dossier pour les données, puis les sous-dossiers `config`, `recordings`, `modules` et `logs`. Le modèle fourni utilise `/mnt/services/SPRECORDER/...`; adapte ces chemins dans le YAML si ton dataset est ailleurs.
5. Dans TrueNAS : **Apps > Discover Apps > Custom App > ⋮ > Install via YAML**. Donne le nom `sprecorder`, colle le contenu de `docker-compose.truenas.yml`, remplace `YOUR_GITHUB_USERNAME` par ton nom GitHub, puis enregistre.
6. Ouvre `http://IP_DE_TON_NAS:8080`.

Pour un déploiement en ligne de commande depuis un clone du dépôt, utilise plutôt le `docker-compose.yml` local (il construit l'image sur le NAS) :

```bash
docker compose up -d --build
docker compose logs -f sprecorder
```

Avant toute mise à jour, arrête l'application mais **ne supprime jamais** les datasets `config` et `recordings`. Ils contiennent les modèles et les vidéos.

Les versions actuelles de TrueNAS SCALE proposent le déploiement de Compose via **Install via YAML**. Les chemins hôte doivent exister avant le déploiement ; voir la documentation TrueNAS citée dans la procédure d'installation.


**Limite importante concernant l'écran « OFFLINE » :** l'arrêt automatique utilise le statut public du profil (plusieurs contrôles consécutifs) et l'absence de croissance du fichier. Il ne fait pas d'OCR/analyse d'image du texte « OFFLINE » incrusté dans la vidéo. Si une image ou un segment OFFLINE a déjà été reçu avant l'arrêt, il peut donc se trouver dans le TS source et dans le MP4. Le TS est supprimé après conversion validée, pas avant.
