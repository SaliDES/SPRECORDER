# SPRECORDER

Notre application SPRECORDER pour macOS / TrueNAS, basée sur le moteur original ChanTrail pour le relay HLS/Stripchat.

## Fonctions

- Monitoring de plusieurs modèles.
- Statut Online / Offline.
- Photo de profil Stripchat.
- Lecteur indépendant par modèle.
- Enregistrement manuel.
- Liste séparée « Enregistrement automatique ».
- Démarrage automatique quand un modèle passe Online.
- Arrêt automatique et finalisation MP4 quand il repasse Offline.
- Enregistrement sans transcodage (`-c copy`).

## TrueNAS

Le conteneur expose :

- `8080` : interface SPRECORDER.
- `3030` : backend ChanTrail / relay.

Les données persistantes sont montées depuis `/mnt/services/SPRECORDER/`.

## Important

Le code du moteur ChanTrail n'est pas copié dans ce dépôt : le Dockerfile le récupère depuis le dépôt officiel au moment du build. Le dépôt officiel est GPL-3.0.

## Build local

```bash
docker compose build
docker compose up -d
```

Puis ouvrir `http://IP_DU_SERVEUR:8080`.
