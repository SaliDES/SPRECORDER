# SPRECORDER v3 — moteur ChanTrail + conversion TS → MP4 fiable

**Version :** `3.0.0-chantrail-engine-converter`  
**NAS cible :** TrueNAS SCALE x86_64 / Docker Compose  
**Dépôt prévu :** https://github.com/SaliDES/SPRECORDER

## Ce qui change

Cette version ne recrée **pas** le proxy HLS Python expérimental de la version 2.2.
Elle utilise directement l'image officielle `chantrail/stripchat-recorder:beta` pour :

- vérifier si les modèles sont en direct et lancer/arrêter automatiquement l'enregistrement ;
- résoudre les playlists HLS et télécharger les segments depuis les CDN ;
- gérer le déchiffrement Mouflon `pkey -> pdkey` ;
- enregistrer les segments en `.ts` et les fusionner avec FFmpeg ;
- fournir une interface Web pour ajouter les modèles et gérer le monitoring.

SPRECORDER ajoute le service `library` (code Python fourni dans `app/`) :

- détecte **uniquement les TS finaux** de type `nom_YYYYMMDD_HHMMSS.ts` ;
- ignore les fragments de travail et les `.ts.part` ;
- attend au moins 180 secondes depuis la dernière écriture et deux scans stables ;
- remuxe en MP4 avec `ffmpeg -c copy` (pas de réencodage) ;
- contrôle les pistes vidéo et les durées avec `ffprobe` ;
- publie un MP4 complet sans écraser un fichier existant ;
- supprime le TS **uniquement après validation et publication** du MP4 ;
- garde le TS en cas d'échec, de manque de place ou de changement du fichier ;
- propose une bibliothèque Web lisant les MP4, avec recherche, lecture et prise en charge du seeking HTTP Range ;
- affiche les MP4 historiques en lecture seule, sans jamais les convertir/supprimer.

**Attention :** le moteur ChanTrail est un projet externe. Son code Rust **n'est pas recopié dans cette archive** : il est utilisé via son image Docker publiée. Le code complet de l'intégration SPRECORDER et du convertisseur, lui, est inclus dans le ZIP. Pour disposer aussi du code de capture, consulter le dépôt officiel cité ci-dessous. Cela évite de prétendre fournir un moteur indépendant qui n'aurait pas été testé.

## Ce que fait vraiment ChanTrail (code source consulté)

Références officielles :

- https://github.com/ChanTrail/StripchatRecorder/blob/master/backend/src/streaming/stripchat.rs
- https://github.com/ChanTrail/StripchatRecorder/blob/master/backend/src/recording/hls.rs
- https://github.com/ChanTrail/StripchatRecorder/blob/master/backend/src/recording/recorder.rs

Dans `streaming/stripchat.rs`, le client Rust résout l'état du modèle, l'URL de playlist et utilise plusieurs domaines CDN possibles. `recording/hls.rs` analyse les playlists et la directive `#EXT-X-MOUFLON:PSCH`; si une clé est reconnue, le token d'URL dans `#EXT-X-MOUFLON:URI` est déchiffré par Base64 inverse puis XOR avec SHA256 de la clé `pdkey`. `recording/recorder.rs` télécharge les fragments fMP4, les transforme en `.ts` avec FFmpeg, puis les fusionne en fin de session au format choisi dans les paramètres (`ts`, `mp4`, `mkv`). Le moteur supprime le dossier de fragments seulement après une fusion réussie. **Nous ne substituons pas FFmpeg directement aux URL Mouflon**, ce qui évite l'erreur HTTP 404 rencontrée avec l'ancien relais maison.

## Déploiement sur TrueNAS

### 1. Publier le ZIP sur GitHub

Le contenu du ZIP est **la racine du nouveau dépôt SPRECORDER** : `docker-compose.yml`, `docker-compose.truenas-apps.yml`, `Dockerfile`, `app/`, `tests/`, `.github/workflows/`, etc.

Si vous remplacez la branche `main` de votre dépôt existant, remplacez aussi les fichiers et l'ancien workflow `.github` qui ciblait l'image expérimentale, sinon il continuera à tourner inutilement. Conservez l'historique Git pour pouvoir revenir en arrière.

### 2. Créer les nouveaux dossiers, isolés des anciens

```sh
sudo mkdir -p /mnt/services/SPRECORDER-v3/{config,recordings,modules,logs,state}
```

Si votre ancien moteur ChanTrail fonctionnait avec un fichier Mouflon, réutilisez **uniquement** ce fichier (ne recopiez pas `settings.json` : incompatibilités entre versions possibles) :

```sh
if [ -f /mnt/services/SPRECORDER/config-chantrail-0.4/mouflon_keys.json ]; then
  sudo install -m 600 \
    /mnt/services/SPRECORDER/config-chantrail-0.4/mouflon_keys.json \
    /mnt/services/SPRECORDER-v3/config/mouflon_keys.json
fi
```

### 3A. Installation directe dans TrueNAS Apps — recommandée si vous utilisez l'éditeur YAML

1. Pousser le contenu du ZIP (y compris `.github/workflows/sprecorder-v3.yml`) dans la branche `main` de `https://github.com/SaliDES/SPRECORDER`.
2. Vérifier l'exécution réussie de l'action GitHub `Build SPRECORDER v3 converter`. Elle publie l'image `ghcr.io/salides/sprecorder:3.0.0`. Si le paquet GHCR est privé, rendre le paquet **public** dans GitHub Packages, ou configurer une authentification GHCR sur TrueNAS.
3. Dans TrueNAS **Apps > Custom App > Install via YAML**, coller **`docker-compose.truenas-apps.yml`**. Ce fichier n'a pas de `build:` ; TrueNAS télécharge les deux images.
4. Ouvrir l'interface moteur `http://IP-DU-NAS:13032` et appliquer l'étape 4 ci-dessous.

Ne collez **pas** le `docker-compose.yml` de développement (contenant `build:`) dans TrueNAS Apps. Vous pouvez le lancer en SSH comme décrit juste après.

### 3B. Installation en SSH / Docker Compose (alternative)

Après avoir poussé le ZIP dans `https://github.com/SaliDES/SPRECORDER` :

```sh
cd /mnt/services
sudo git clone --branch main --single-branch \
  https://github.com/SaliDES/SPRECORDER.git SPRECORDER-v3-app
cd /mnt/services/SPRECORDER-v3-app
sudo docker compose -f docker-compose.yml config
sudo docker compose -f docker-compose.yml up -d --build
```

Si le dossier `SPRECORDER-v3-app` existe déjà, ne le reclonez pas : utiliser `sudo git -C /mnt/services/SPRECORDER-v3-app pull --ff-only` depuis la copie existante.

### 4. Configuration à faire UNE FOIS dans l'interface du moteur

- Ouvrir `http://IP-DU-NAS:13032` (ChanTrail dédié à SPRECORDER v3).
- Dans **Settings**, choisir impérativement **Merge format = `ts`**, afin de produire les fichiers TS finaux que le convertisseur SPRECORDER prendra en charge. Vérifier également le répertoire de sortie `recordings`.
- Ajouter les modèles à surveiller et activer leur enregistrement automatique.
- Si la liste de clés Mouflon ne fonctionne pas, vérifier sa configuration/synchronisation dans cette même interface.

**Important :** si vous laissez **Merge format = `mp4`** (valeur par défaut de ChanTrail), le moteur produira déjà directement des MP4. La bibliothèque les verra, mais la conversion TS → MP4 supplémentaire ne sera simplement pas nécessaire.

### 5. Utilisation

- **Monitoring / démarrer / arrêter** : `http://IP-DU-NAS:13032`
- **Bibliothèque SPRECORDER MP4** : `http://IP-DU-NAS:18082`

La bibliothèque analyse les MP4 du nouveau dossier `/mnt/services/SPRECORDER-v3/recordings` et **lit aussi** les MP4 historiques de `/mnt/services/SPRECORDER/recordings` grâce à un montage `:ro`.

Le moteur officiel conserve ses propres métadonnées de fichiers TS dans son interface d'enregistrements ; après conversion externe, certains liens de cette interface peuvent donc désigner l'ancien TS. **La bibliothèque SPRECORDER sur le port 18082 est l'interface à utiliser pour lire les MP4 convertis.**

### 6. Vérifications

```sh
sudo docker ps --filter name=sprecorder-v3
curl -fsS http://127.0.0.1:18082/health
sudo docker logs --tail 80 sprecorder-v3-recorder
sudo docker logs --tail 80 sprecorder-v3-library
find /mnt/services/SPRECORDER-v3/recordings -type f \( -name '*.mp4' -o -name '*.ts' \) -printf '%p %s octets\n' | tail -20
```

Après la première session complète : vérifier que le TS final apparaît, puis que le MP4 est lisible et que **seul ce TS final** a disparu. Le moteur officiel gère le monitoring ; le convertisseur ne décide jamais de l'arrêt ou du redémarrage des streams.

## Nettoyage des essais 2.2

Le script `scripts/nettoyer-essais-2.2.sh` affiche d'abord ce qui sera supprimé :

```sh
bash scripts/nettoyer-essais-2.2.sh
```

Après avoir vérifié que les nouveaux services fonctionnent et que les enregistrements historiques sont toujours accessibles :

```sh
bash scripts/nettoyer-essais-2.2.sh --apply
```

**Ce nettoyage ne touche PAS :** `sprecorder-210`, `sprecorder-chantrail-048`, les dossiers `recordings` historiques, les clés d'origine ChanTrail, et les nouveaux dossiers `SPRECORDER-v3`.

Le script supprime seulement le conteneur expérimental `sprecorder-standalone-220`, son image si elle existe, le clone `SPRECORDER-standalone-2.2` et les **deux** dossiers distincts `config-standalone-2.2` et `logs-standalone-2.2`. Les clés d'essai contenues dans ce dossier de configuration sont donc supprimées. Sauvegarder ce dossier si besoin.

## Tests

Tests indépendants du NAS :

```sh
python3 -m unittest discover -s tests -v
```

Ils nécessitent `ffmpeg` et `ffprobe` installés. Ils vérifient la vraie conversion d'un TS H.264/AAC, la suppression uniquement après réussite, la conservation d'un TS invalide, la non-modification d'un MP4 existant et l'exclusion des fragments en cours.

**Limite de validation :** les tests de conversion ont été exécutés sur des médias synthétiques. Ni le moteur Docker `beta`, ni la capture d'un vrai stream depuis le NAS n'ont pu être exécutés dans l'environnement de création de cette archive. Il faudra valider une session de bout en bout sur le NAS avant d'effacer les essais précédents.

## Sécurité et stockage

- Déploiement à réserver au réseau local : les deux interfaces ne proposent pas d'authentification supplémentaire dans ce paquet.
- Ne pas publier les ports `13032` et `18082` directement sur Internet.
- Le fichier de clés Mouflon reste sur le NAS, **ne jamais l'ajouter à Git**.
- Le TS et le MP4 coexistent temporairement : prévoyez environ deux fois la taille d'un enregistrement le temps de sa conversion.
- Le convertisseur n'écrase aucun MP4 existant et ne touche pas aux enregistrements historiques.
- Ne jamais lancer `docker compose down -v`, `docker system prune --volumes` ou un `rm -rf` sur `/mnt/services/SPRECORDER/recordings`.

## Licence / attribution

Moteur de capture : [ChanTrail/StripchatRecorder](https://github.com/ChanTrail/StripchatRecorder), sous GPL-3.0, image Docker publiée séparément. Le projet SPRECORDER v3 ajoute son propre service Python. Pour étudier ou modifier directement le moteur, partir du dépôt source upstream, respecter ses obligations de licence et ne pas supposer que l'image Docker `beta` est une reproduction vérifiée de la branche `master`.
