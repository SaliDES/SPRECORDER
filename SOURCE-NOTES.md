# Origine et licences

SPRECORDER v4 est une application Python distincte (un serveur HTTP, un moteur HLS et
une interface développés pour ce projet). Aucun exécutable, conteneur, API locale,
frontend ou service ChanTrail n'est utilisé à l'exécution.

Le fonctionnement HLS/Mouflon a été étudié dans les sources publiques :

- https://github.com/ChanTrail/StripchatRecorder/blob/master/backend/src/streaming/stripchat.rs
  (broadcasts -> cam -> master HLS, sélection qualité et pkey, CDN alternatifs)
- https://github.com/ChanTrail/StripchatRecorder/blob/master/backend/src/recording/hls.rs
  (balises EXT-X-MOUFLON, URLs fMP4, SHA256/XOR)
- https://github.com/ChanTrail/StripchatRecorder/blob/master/backend/src/recording/recorder.rs
  (fragments fMP4 + init -> MPEG-TS avec FFmpeg, puis fusion)

Projet de référence : ChanTrail/StripchatRecorder, GPL-3.0
https://github.com/ChanTrail/StripchatRecorder

SPRECORDER est distribué sous GPL-3.0 ; voir LICENSE. Ce code n'est ni un
produit affilié ni une redistribution de l'image officielle de ChanTrail.

Le lecteur HLS intégré utilise hls.js 1.6.13 (Apache-2.0) :
https://github.com/video-dev/hls.js — installé au moment de construire l'image,
servi localement et non téléchargé depuis un CDN lors de l'utilisation.

Conditions techniques et légales : enregistrez uniquement les diffusions pour
lesquelles vous disposez des droits et autorisations nécessaires. Le logiciel
n'accède pas aux shows privés et ne garantit pas l'accès à un CDN qui bloque
les requêtes ou qui change de protocole.
