# SPRECORDER v4.3.0 — Nettoyage vérifié des TS convertis

SPRECORDER reste une **application indépendante**, avec **une seule interface sur le port 18083**. Les cartes de modèles, la prévisualisation HLS, l'enregistrement manuel/automatique, la bibliothèque et la conversion MP4 des versions précédentes sont conservés.

## Nouvelle fonction : « Supprimer les TS convertis »

Dans **Bibliothèque**, le bouton rouge **« Supprimer les TS convertis »** :

1. Examine les sources `.ts` et `.recovered.ts` (y compris `*.ts.recovered.ts`) et repère celles ayant un MP4 à l'emplacement attendu par SPRECORDER.
2. Affiche une **estimation** du nombre de TS et de l'espace libérable (l'estimation peut inclure un MP4 illisible, qui sera protégé lors de la vérification).
3. Demande une confirmation, puis une **saisie manuelle obligatoire** de `SUPPRIMER LES TS`.
4. Traite les candidats **un à un en arrière-plan**, avec une **barre de progression de suppression** en haut de l'interface, un compteur de fichiers et d'octets libérés.
5. Avant chaque suppression : valide l'existence du MP4, son en-tête, sa piste vidéo, son codec/résolution et sa durée avec FFprobe ; contrôle que le TS n'a pas été modifié pendant la vérification. Les fichiers encore récents, les symlinks et les fragments de capture sont ignorés.
6. **Supprime uniquement le TS original** correspondant à un MP4 validé. Le MP4 ne sera jamais supprimé par cette fonction. Les TS sans MP4, les fichiers corrompus, récents ou en cours de conversion sont conservés.

⚠️ La suppression est **définitive**. Il n'y a pas de corbeille. Vérifier les MP4, conserver une sauvegarde et éviter ce nettoyage pendant que les anciens enregistreurs historiques travaillent.

## Mise à jour TrueNAS (important)

1. Remplacer le contenu du dépôt `https://github.com/SaliDES/SPRECORDER` par ce ZIP, **y compris `.github/workflows/build.yml`**. Le commit sur `main` publiera les tags `4.3.0` et `latest`.
2. Vérifier la réussite de GitHub Actions et tester :
   `sudo docker pull ghcr.io/salides/sprecorder:4.3.0`
3. Dans **TrueNAS Apps**, **modifier l'application existante `sprecorder-v4`** et utiliser le **fichier `TRUENAS-APPS.yml` fourni dans le ZIP**. Il ne suffit pas de changer le tag de l'image : la suppression historique requiert deux **nouveaux montages dédiés** en écriture, et deux variables d'environnement.
4. Les volumes `/history:ro` et `/history-v3:ro` de consultation/lecture **restent en lecture seule**. Les nouveaux volumes `/history-clean:rw` et `/history-v3-clean:rw` correspondent aux mêmes dossiers hôtes et ne sont utilisés que pour le nettoyage explicitement déclenché. Vérifier que les permissions de fichiers TrueNAS autorisent la suppression.
5. Accéder à `http://IP-DU-NAS:18083` → **Bibliothèque** → **Supprimer les TS convertis**.

### Volumes (identiques à v4.2, sauf les montages de nettoyage supplémentaires)

- `/mnt/services/SPRECORDER-v4:/data` : configuration, nouveaux enregistrements et MP4 importés.
- `/mnt/services/SPRECORDER/recordings:/history:ro` : lecture des anciens fichiers.
- `/mnt/services/SPRECORDER-v3/recordings:/history-v3:ro` : lecture des anciens fichiers v3.
- `/mnt/services/SPRECORDER/recordings:/history-clean:rw` : **suppression confirmée** des anciens TS qui ont un MP4 validé.
- `/mnt/services/SPRECORDER-v3/recordings:/history-v3-clean:rw` : idem pour la v3.

**Sans les montages en écriture supplémentaires, les TS historiques ne seront pas supprimables**, par conception. Leur conversion et leur consultation restent possibles.

## Garantie et limites

La présence d'un MP4 n'est pas une preuve cryptographique que les vidéos sont identiques : la validation compare le média, son codec/résolution et sa durée. Pour une sécurité supplémentaire, vérifier visuellement les MP4 ou faire une sauvegarde avant le nettoyage groupé. Aucun effacement n'est garanti si un fichier est protégé par ACL ou si un volume TrueNAS reste en lecture seule.

Les anciens enregistrements se convertissent toujours vers `/mnt/services/SPRECORDER-v4/recordings/imports/history/` et `imports/history-v3/` ; ils sont **conservés tant que tu n'utilises pas ce nouveau bouton**.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests hors ligne : conversion réelle FFmpeg, TS classiques et récupérés, refus d'effacer sans MP4 ou si MP4 invalide/durée différente, montages historiques en écriture opt-in, confirmation API, interface et fonctions d'enregistrement antérieures. La capture sur un vrai live et le montage dans TrueNAS n'ont pas été exécutés dans cet environnement.

## Licences

GPL-3.0, attributions dans `SOURCE-NOTES.md`. Utiliser les enregistrements dans le respect des droits des personnes et des plateformes concernées.
