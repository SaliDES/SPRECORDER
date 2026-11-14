# SPRECORDER v4.1 — cartes modèles et bibliothèque illustrée

**Une application indépendante, une interface Web, un conteneur, un port (`18083`).**

Cette version **met à jour la v4 existante sans changer le moteur HLS qui fonctionne** (`app/streaming.py` et `app/engine.py` conservent leur processus de capture TS → MP4). Elle n'utilise aucun conteneur ChanTrail.

## Nouveautés

- Ajout d'un nombre libre de modèles, **sans lancer de capture et avec auto-record désactivé par défaut**.
- Cartes par modèle, chacune avec pseudo, photo de profil **si l'API publique fournit une URL de photo**, statut direct/hors ligne, aperçu vidéo HLS intégré sur la carte, bouton démarrer/arrêter l'enregistrement et interrupteur auto-record indépendant.
- Photo de profil : on utilise les champs `avatarUrl`/`avatar`/`profileImageUrl` et équivalents quand présents, et l'aperçu public comme solution de repli. La récupération se fait par un relais photo HTTPS interne (les restrictions d’affichage direct sont ainsi évitées). Si aucune image n'est fournie, une icône avec l'initiale du pseudo s'affiche ; nous n'inventons pas d'URL de photo.
- Prévisualisations ouvertes **à la demande**, pas à l'ajout ; l'actualisation du statut toutes les 5 s **ne reconstruit pas les lecteurs**.
- Monitoring de listes longues avec un nombre limité de requêtes concurrentes (8 au maximum).
- Bibliothèque en cartes : miniature 16:9 générée depuis les MP4/TS avec FFmpeg, puis nom, type, taille, date, lecture ou conversion. La génération est à la demande et le résultat mis en cache sous `/data/state/thumbnails`. Les anciens enregistrements en lecture seule ne sont jamais modifiés.
- Conversion TS → MP4 inchangée : le TS n'est supprimé qu'après vérification du MP4.

## Mise à jour depuis SPRECORDER v4.0 déjà installée

1. Télécharge et décompresse le ZIP **SPRECORDER-v4.1-independant.zip** sur ton Mac.
2. Publie les fichiers dans ta branche `main` du dépôt `https://github.com/SaliDES/SPRECORDER`. **Attention :** le dossier caché `.github/workflows` est indispensable à la publication de l'image.
3. Attends que le workflow **Publish SPRECORDER v4.1** soit vert dans GitHub Actions et que `ghcr.io/salides/sprecorder:4.1.0` soit disponible.
4. Sur TrueNAS, teste :
   ```bash
   sudo docker pull ghcr.io/salides/sprecorder:4.1.0
   ```
5. **Édite l'application existante `sprecorder-v4` dans TrueNAS Apps**, remplace `image: ghcr.io/salides/sprecorder:4.0.0` par `image: ghcr.io/salides/sprecorder:4.1.0`, et applique. Ne crée **pas** une deuxième application sur le même port `18083`.
6. Ouvre `http://IP-DU-NAS:18083`, recharge la page en ignorant le cache si nécessaire.

Le `TRUENAS-APPS.yml` fourni contient déjà l'image `4.1.0`. Il conserve les mêmes volumes :
`/mnt/services/SPRECORDER-v4:/data`, les anciens enregistrements `/history` et `/history-v3` en lecture seule, le même conteneur `sprecorder-v4` et le même port `18083:8080`.

### Publication Git via le terminal Mac

```bash
cd ~/Downloads
git clone https://github.com/SaliDES/SPRECORDER.git SPRECORDER-v41-upload
cd SPRECORDER-v41-upload
git rm -r .
unzip -o ../SPRECORDER-v4.1-independant.zip -d .
git add -A
git commit -m "SPRECORDER 4.1 - model cards preview and thumbnails"
git push origin main
```

**Ne supprime pas** `/mnt/services/SPRECORDER-v4` : il contient déjà les nouveaux enregistrements et préférences. Ne supprime pas les anciennes installations.

### Tests hors ligne

```bash
python3 -m unittest discover -s tests -v
```

L'archive inclut le test de génération réelle de thumbnail JPEG avec FFmpeg et la vérification que les nouveaux modèles n'ont pas l'auto-record activé. Le moteur HLS n'a pas été réécrit. L'affichage des photos dépend des données publiques de chaque profil et d'éventuelles restrictions du serveur d'images.

## Licence et sources

Projet GPL-3.0. Voir `LICENSE`, `SOURCE-NOTES.md` et `LICENSE-HLSJS-APACHE-2.0`. Ne conserve que les diffusions que tu es autorisé à enregistrer.
