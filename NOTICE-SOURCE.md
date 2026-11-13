# Provenance du moteur

Le moteur officiel de capture/déchiffrement, ses sources Rust, son interface Web Vue et son image Docker sont produits par ChanTrail :
https://github.com/ChanTrail/StripchatRecorder

L'image utilisée ici est `chantrail/stripchat-recorder:beta` (Docker Hub). Elle reste une dépendance d'exécution : **le ZIP ne contient pas le code ou binaire original de ChanTrail.**

Fichiers source consultés pour comprendre le fonctionnement :
- `backend/src/streaming/stripchat.rs` (résolution, CDN, HLS)
- `backend/src/recording/hls.rs` (parsing, Mouflon)
- `backend/src/recording/recorder.rs` (TS, fusion et nettoyage)
- `backend/src/relay/streamer.rs` (diffusion live en MPEG-TS)

Licence upstream : GPL-3.0. Ne pas supprimer les avis légaux si vous forkiez le code upstream.
