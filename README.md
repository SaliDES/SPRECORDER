# SPRECORDER 2.2.0 — moteur HLS autonome pour TrueNAS

Cette édition conserve l'interface, le moteur d'enregistrement FFmpeg, les reconnexions, les fichiers `.ts.part`, la récupération, la bibliothèque et la conversion MP4 de SPRECORDER 2.1.0. Elle ajoute un résolveur/proxy HLS Python intégré. Elle ne lance et ne télécharge aucun binaire ChanTrail.

## Test parallèle — installation actuelle préservée

Cette configuration est volontairement isolée :
- interface de test : `http://IP_DU_NAS:18081` ;
- proxy HLS : port `13031` ;
- configuration dédiée : `/mnt/services/SPRECORDER/config-standalone-2.2` ;
- logs dédiés : `/mnt/services/SPRECORDER/logs-standalone-2.2` ;
- enregistrements : dossier actuel `/mnt/services/SPRECORDER/recordings`.

L'instance actuelle (`sprecorder-210`) et ChanTrail (`sprecorder-chantrail-048`) ne sont ni arrêtés ni supprimés. Le fichier de clés Mouflon est copié vers la configuration de test. La liste des modèles n'est pas copiée afin d'éviter qu'une deuxième instance démarre automatiquement les mêmes enregistrements.

Depuis le dossier extrait :

```bash
chmod +x deploy.sh
./deploy.sh
```

Ou manuellement :

```bash
sudo mkdir -p /mnt/services/SPRECORDER/config-standalone-2.2
sudo mkdir -p /mnt/services/SPRECORDER/logs-standalone-2.2
sudo install -m 600 /mnt/services/SPRECORDER/config-chantrail-0.4/mouflon_keys.json /mnt/services/SPRECORDER/config-standalone-2.2/mouflon_keys.json
sudo docker compose config
sudo docker compose up -d --build
```

## Vérification

```bash
sudo docker logs --tail 100 sprecorder-standalone-220
curl -fsS http://127.0.0.1:18081/health
curl -fsS http://127.0.0.1:13031/health
```

Ajoute un modèle manuellement dans l'interface de test, lance d'abord le monitoring, puis un enregistrement court. Vérifie que le `.ts.part` grossit et que le fichier final est lisible. N'arrête pas l'ancienne instance tant que ce test réel n'est pas concluant.

## Mouflon et HLS

Le relais lit le fichier `mouflon_keys.json` au format SPRECORDER `keys` ou au format brut `pkey2pdkey`. Il traite les marqueurs Mouflon v2, réécrit les URI HLS vers le proxy local et normalise les URL de segments `media-hls` vers le shard `b-hls` annoncé dans leur chemin.

**Limite de validation :** les tests automatiques couvrent la syntaxe Python, le déchiffrement v2 avec vecteur synthétique, la normalisation d'URL, la réécriture HLS, le démarrage HTTP du relais et les tests de robustesse de l'enregistreur. Je n'ai pas pu exécuter un build Docker ni un enregistrement Stripchat réel depuis cet environnement. Cette version est donc une candidate de test isolée, pas une version certifiée en production.
