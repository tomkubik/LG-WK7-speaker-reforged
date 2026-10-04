#!/usr/bin/env bash
# 01_backup.sh -- Back up critical partitions so a bricked device can be recovered.
#
# Two backends:
#   adb  -- preferred. Pull via root shell. Works with NO bootloader unlock.
#   edl  -- read via firehose (see 04/05). Works on hard-locked units.
#
# Usage:
#   sudo ./01_backup.sh                 # adb backend, critical set
#   sudo ./01_backup.sh --all           # every partition except userdata/gapps
#   sudo ./01_backup.sh --backend edl
source "$(dirname "$0")/lib/common.sh"

BACKEND="adb"
SCOPE="critical"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --all) SCOPE="all" ;;
    --backend) BACKEND="$2"; shift ;;
    --critical) SCOPE="critical" ;;
    *) die "unknown arg $1" ;;
  esac
  shift
done
need_root

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/$STAMP"
mkdir -p "$OUT"

echo "=============================================="
echo " WK7 partition backup -> $OUT"
echo "=============================================="

if [[ "$BACKEND" == adb ]]; then
  require_device
  [[ "$(which_mode)" == adb ]] || die "adb backend needs the device in Android/ADB mode"
  adb_root || warn "continuing without root; only root-readable partitions will work"

  targets="$PARTITIONS"
  [[ "$SCOPE" == critical ]] && targets="$(for n in $CRITICAL; do
        grep "^$n:" <<<"$PARTITIONS"; done)"

  echo
  echo "reading partitions (this takes a few minutes)..."
  while IFS=: read -r name part size; do
    [[ -z "$name" ]] && continue
    case "$name" in userdata|gapps) continue ;; esac
    dst="$OUT/$name.img"
    if timeout 300 adb pull "/dev/block/$EMMC/$part" "$dst" >/dev/null 2>&1; then
      printf '  %-22s %8s KB  OK\n' "$name" "$size"
    else
      printf '  %-22s %8s KB  FAILED\n' "$name" "$size"
    fi
  done <<<"$targets"
  echo "$OUT" > "$BACKUP_DIR/LATEST"

elif [[ "$BACKEND" == edl ]]; then
  command -v qdl >/dev/null || die "qdl not installed (see docs/EDL.md)"
  die "EDL backend not implemented in this build. Use qdl directly per docs/EDL.md -- 'qdl backup' with your firehose loader."
fi

echo
ok "backup written to $OUT"
cat > "$OUT/MANIFEST.txt" <<EOF
WK7 partition backup
created : $(date -Is)
model   : $DEV_MODEL ($DEV_PRODUCT / $DEV_SOC)
serial  : $(adb shell getprop ro.boot.serialno 2>/dev/null | tr -d '\r')
scope   : $SCOPE

Restore with: ./02_restore.sh --from $OUT
Never relock a bootloader that is not currently in the same state.
EOF
cat "$OUT/MANIFEST.txt"
