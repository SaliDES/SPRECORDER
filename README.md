# SPRECORDER v4.0 — application indépendante « tout-en-un »

**Une application, une interface Web, un conteneur, un seul port (18083).**

### Interfaces de la même page

- **Enregistreur** : démarrage/arrêt manuel, progression en temps réel, prévisualisation HLS.
- **Auto-record** : ajout de modèles, surveillance de leur direct public, démarrage automatique.
- **Bibliothèque** : MP4 de SPRECORDER + anciens fichiers 2.1 et v3 en lecture seule,
  lecture des MP4 dans un lecteur intégré et conversion manuelle des nouveaux TS.
- **Journal/réglages** : événements d'erreur, conversion, qualité vidéo.

Aucune dépendance à un conteneur, un binaire ou un service ChanTrail. La logique
réseau et l'enregistrement sont implémentés dans `app/streaming.py` et
`app/engine.py` ; l'interface HTML+JS réside dans `app/static/index.html`.
L'interpréteur Python, FFmpeg et une copie locale de hls.js sont embarqués dans
**notre propre image** Docker.

## Enregistrement

1. `https://stripchat.com/api/front/v1/broadcasts/<pseudo>` fournit le `streamName`.
2. `.../api/front/v2/models/<streamName>/cam` fournit les statuts public/live et
   l'identifiant numérique du modèle.
3. `_auto.m3u8` fournit les renditions et les couples `psch/pkey` Mouflon.
4. La qualité choisie récupère la playlist média. La bonne clé est sélectionnée
   parmi les clés configurées dans `config/mouflon_keys.json`.
5. Les balises `#EXT-X-MOUFLON:URI` sont traitées ; le segment d'initialisation
   fMP4 est associé à chaque fragment MP4 téléchargé et converti en TS via FFmpeg.
6. Des fragments `.ts` sont conservés dans un dossier `.work_...` jusqu'à l'arrêt.
   Après arrêt, FFmpeg crée un TS complet, puis un MP4 sans réencodage.
7. **Le TS final est supprimé uniquement si FFprobe confirme le MP4, avec durée
   cohérente, et si le TS n'a pas changé.** Si une étape échoue, le TS et/ou les
   fragments sont conservés.
8. Si un dossier de fragments d'une session interrompue existe au redémarrage,
   SPRECORDER tente de récupérer le TS et de le convertir avant de relancer le
   monitoring automatique.

Le polling automatique s'effectue toutes les 45 secondes. Si aucun fragment
n'arrive pendant 10 minutes, la session s'arrête et les fragments déjà présents
sont finalisés. Un bouton Stop arrête aussi l'auto-record pour ce modèle afin
qu'il ne redémarre pas au cycle suivant.

### Clés Mouflon

Le fichier `config/mouflon_keys.json` peut contenir :

```json
{"pkey2pdkey":{"CLE_PKEY":"CLE_PDKEY"}}
```

Ces valeurs sont des **exemples**, pas des clés réelles. Une capture peut échouer
si la liste ne contient pas de clé adaptée au flux actuel. Tu peux copier ton
fichier de clés existant depuis ton ancienne installation si tu en as les droits.

## Publication dans le dépôt GitHub

L'archive ZIP est prévue pour le dépôt :
https://github.com/SaliDES/SPRECORDER

Sur Mac (dans un dossier de travail vierge) :

```bash
cd ~/Downloads
git clone https://github.com/SaliDES/SPRECORDER.git SPRECORDER-v4-upload
cd SPRECORDER-v4-upload
# Attention : enlève les anciennes sources suivies, uniquement dans ce nouveau clone.
git rm -r .
unzip -o ../SPRECORDER-v4-independant.zip -d .
git add -A
git commit -m "SPRECORDER v4 independent all-in-one"
git push origin main
```

**Important :** si Git garde d'anciens workflows dans `.github/workflows`, supprime
les workflows v3 pour ne pas publier les deux images en parallèle. Le ZIP contient
`.github/workflows/build.yml`, qui publie :

`ghcr.io/salides/sprecorder:4.0.0` et `ghcr.io/salides/sprecorder:latest`.

Dans GitHub Actions : vérifier que le workflow **Publish SPRECORDER v4** est vert.
Dans GitHub Packages : mettre l'image en visibilité publique si nécessaire.

## Installation TrueNAS SCALE

**1. Préparer le dossier sans modifier les anciens enregistrements :**

```bash
sudo mkdir -p /mnt/services/SPRECORDER-v4/{config,recordings,state}
```

**2. Copier les clés existantes, si présentes :**

```bash
if sudo test -f /mnt/services/SPRECORDER/config-chantrail-0.4/mouflon_keys.json; then
  sudo cp -n /mnt/services/SPRECORDER/config-chantrail-0.4/mouflon_keys.json \
    /mnt/services/SPRECORDER-v4/config/mouflon_keys.json
fi
```

**3. Vérifier que l'image existe :**

```bash
sudo docker pull ghcr.io/salides/sprecorder:4.0.0
```

**4. TrueNAS → Apps → Custom App → Install via YAML :**

Coller le contenu du fichier `TRUENAS-APPS.yml` inclus dans le ZIP.

**5. Ouvrir :** `http://IP-DU-NAS:18083`.

La v4 cohabite avec les installations 2.1, 2.2 et v3. Elle ne les remplace pas.
Les anciens dossiers d'enregistrement montés sous `/history` et `/history-v3`
le sont **en lecture seule**. Les nouvelles captures vont exclusivement dans
`/mnt/services/SPRECORDER-v4/recordings`.

Ne pas utiliser `docker compose down -v`. Ne jamais supprimer
`/mnt/services/SPRECORDER/recordings` ni les données de la v3.

## Tests

Pour le code source, depuis le dossier contenant `app/` :

```bash
python3 -m unittest discover -s tests -v
```

Ces tests génèrent un exemple fMP4 avec FFmpeg, testent la conversion réelle en TS
puis en MP4, une session simulée de bout en bout, les playlists et la sécurité
HTTP. Il faut FFmpeg et FFprobe installés. L'image Docker n'a pas été construite
ici et **aucun vrai live Stripchat n'a été testé ici** : ce sont des validations
hors ligne et non une garantie de fonctionnement de toutes les diffusions.

## Licence

GPL-3.0 pour SPRECORDER. Voir `LICENSE` et `SOURCE-NOTES.md` pour les crédits
ChanTrail et hls.js. N'enregistre que des diffusions dont tu es autorisé à
conserver les images et respecte les conditions du service.
