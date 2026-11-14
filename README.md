# SPRECORDER v4.2 — Conversion globale des anciens TS

Une interface Web, un conteneur, port 18083. Le moteur d’enregistrement HLS et les cartes de modèles de la v4.1 sont conservés.

## Nouvelles fonctions

- Dans **Bibliothèque**, bouton **« Convertir tous les TS »**, en haut de page.
- Bouton **« Convertir en MP4 »** sur **chaque TS**, y compris les anciens fichiers `*.ts.recovered.ts` et `*.recovered.ts`.
- Conversion en **arrière-plan**, séquentielle, sans bloquer la navigation ni les captures vidéo.
- Barre de progression **globale visible en haut de l'application** : pourcentage pondéré par taille des TS, nombre de fichiers traités, succès, échecs et nom du fichier en cours.
- Échecs enregistrés et conversion relançable ; aucun MP4 existant n'est écrasé.
- Les dossiers anciens `/history` et `/history-v3` restent **en lecture seule**. Leurs nouveaux MP4 sont sauvegardés sous `/mnt/services/SPRECORDER-v4/recordings/imports/history/` ou `imports/history-v3/`. Les vieux TS ne sont pas supprimés.
- Les nouveaux TS situés dans `/mnt/services/SPRECORDER-v4/recordings` sont supprimés **uniquement après** validation du MP4 avec FFprobe et contrôle de la durée et de l'intégrité du TS source.
- Les fichiers encore en cours d'écriture et les fragments de sessions HLS sont ignorés. Un contrôle d’espace libre est effectué avant traitement.

## Installation / mise à jour

1. Extraire le ZIP v4.2 et remplacer les fichiers du dépôt `https://github.com/SaliDES/SPRECORDER` (y compris `.github/workflows/build.yml`).
2. Laisser GitHub Actions publier `ghcr.io/salides/sprecorder:4.2.0`.
3. TrueNAS : `sudo docker pull ghcr.io/salides/sprecorder:4.2.0`.
4. **Modifier** l'application existante `sprecorder-v4` via Apps > Edit > YAML. Remplacer seulement le tag de l'image par `4.2.0`, ou utiliser `TRUENAS-APPS.yml` inclus. Les volumes sont inchangés.
5. Ouvrir `http://IP-DU-NAS:18083`, onglet Bibliothèque, cliquer sur **Convertir tous les TS**.

Les fichiers historiques ne sont **pas** supprimés, car les dossiers historiques sont montés en lecture seule. Ainsi, tu peux vérifier les MP4 avant tout nettoyage manuel éventuel.

## Tests

`python3 -m unittest discover -s tests -v`

Les tests valident les conversions de `.ts`, `.recovered.ts`, l'import non destructif de fichiers historiques, la progression et le traitement des fichiers invalides.

## Licences

GPL-3.0 ; attributions dans `SOURCE-NOTES.md`. Respecter les droits des auteurs et personnes filmées.
