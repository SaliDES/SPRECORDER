#!/usr/bin/env bash
# Nettoyage CIRCONSCRIT aux essais SPRECORDER standalone 2.2.
# Aucune opération sur les conteneurs historiques / enregistrements partagés.
set -euo pipefail

TARGETS=(
  /mnt/services/SPRECORDER-standalone-2.2
  /mnt/services/SPRECORDER/config-standalone-2.2
  /mnt/services/SPRECORDER/logs-standalone-2.2
)

echo 'Éléments concernés :'
echo '  Docker : sprecorder-standalone-220'
echo '  Image  : sprecorder:2.2.0-standalone (si non utilisée ailleurs)'
for dir in "${TARGETS[@]}"; do
  if [ -e "$dir" ]; then
    echo "  Dossier : $dir"
    sudo du -sh "$dir" 2>/dev/null || true
  else
    echo "  Absent : $dir"
  fi
done
cat <<'EOF'
Conservés :
  - sprecorder-210
  - sprecorder-chantrail-048
  - /mnt/services/SPRECORDER/recordings
  - /mnt/services/SPRECORDER/config-chantrail-0.4
  - /mnt/services/SPRECORDER-v3
  - sprecorder-v3-recorder / sprecorder-v3-library
EOF
if [[ "${1:-}" != "--apply" ]]; then
  echo 'Simulation uniquement. Pour exécuter : bash scripts/nettoyer-essais-2.2.sh --apply'
  exit 0
fi

read -r -p 'Tapez SUPPRIMER-2.2 pour confirmer : ' answer
if [[ "$answer" != "SUPPRIMER-2.2" ]]; then
  echo 'Annulé.'
  exit 1
fi
sudo docker rm -f sprecorder-standalone-220 2>/dev/null || true
sudo docker image rm sprecorder:2.2.0-standalone 2>/dev/null || true
for dir in "${TARGETS[@]}"; do
  case "$dir" in
    /mnt/services/SPRECORDER-standalone-2.2|/mnt/services/SPRECORDER/config-standalone-2.2|/mnt/services/SPRECORDER/logs-standalone-2.2)
      # Refuser les symlinks avant suppression : pas de traversée involontaire.
      if [[ -L "$dir" ]]; then
        echo "Lien symbolique refusé : $dir" >&2
        exit 2
      fi
      if [[ -d "$dir" ]]; then sudo rm -rf -- "$dir"; fi
      ;;
    *) echo "Chemin inattendu, abandon : $dir" >&2; exit 2 ;;
  esac
done
echo 'Nettoyage des essais 2.2 terminé. Historiques et v3 conservés.'
