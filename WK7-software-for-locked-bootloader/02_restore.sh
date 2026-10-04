#!/usr/bin/env bash
# 02_restore.sh -- Write backed-up partitions back to the device.
#
# This is the script you run when a flash went wrong. It is deliberately
# conservative: it verifies the source files exist and are non-empty, shows
# exactly what it will do, and requires an explicit --yes.
#
# Usage:
#   sudo ./02_restore.sh --from backups/20261003-210000
#   sudo ./02_restore.sh --from <dir> --list
#   sudo ./02_restore.sh --from <dir> --only boot_a aboot_a --yes
source "$(dirname "$0")/lib/common.sh"

SRC=""; ONLY=""; ASSUME_YES=0; LIST=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --from) SRC="$2"; shift ;;
    --only) ONLY="$2"; shift ;;
    --yes) ASSUME_YES=1 ;;
    --list) LIST=1 ;;
    *) die "unknown arg $1" ;;
  esac
  shift
done
[[ -z "$SRC" ]] && die "--from <backup-dir> required"
[[ -d "$SRC" ]] || die "no such directory: $SRC"
need_root

echo "=============================================="
echo " WK7 partition restore from $SRC"
echo "=============================================="

# Build the worklist
declare -a WORK=()
while IFS=: read -r name part size; do
  [[ -z "$name" ]] && continue
  [[ -n "$ONLY" && " $ONLY " != *" $name "* ]] && continue
  f="$SRC/$name.img"
  if [[ -f "$f" && -s "$f" ]]; then
    WORK+=("$name:$part:$size:$f")
  elif [[ -n "$ONLY" ]]; then
    die "--only $name requested but $f missing or empty"
  fi
done <<<"$PARTITIONS"

if [[ "$LIST" == 1 ]]; then
  echo "available in backup:"
  for w in "${WORK[@]}"; do
    IFS=: read -r n p s f <<<"$w"
    printf '  %-22s %8s KB  %s\n' "$n" "$s" "$(du -h "$f" | cut -f1)"
  done
  exit 0
fi

[[ ${#WORK[@]} -eq 0 ]] && die "nothing to restore"

echo
echo "will write:"
for w in "${WORK[@]}"; do
  IFS=: read -r n p s f <<<"$w"
  printf '  %-22s -> %-14s %8s KB  (from %s)\n' "$n" "$p" "$s" "$(basename "$f")"
done
echo

if [[ "$ASSUME_YES" != 1 ]]; then
  warn "THIS OVERWRITES PARTITIONS. A bad write here can brick the device."
  read -rp "Type the word YES to proceed: " ans
  [[ "$ans" == "YES" ]] || die "aborted"
fi

require_device
[[ "$(which_mode)" == adb ]] || die "adb backend needs the device in Android/ADB mode"
adb_root || die "restore requires root"

for w in "${WORK[@]}"; do
  IFS=: read -r n p s f <<<"$w"
  log "writing $n ($p, ${s} KB) ..."
  # dd via root shell; verify by reading back the size
  if timeout 300 adb shell "dd if=$f of=/dev/block/$EMMC/$p bs=4096 2>&1 | tail -1" | grep -qE "written|copied"; then
    printf '  %-22s OK\n' "$n"
  else
    err "  $n FAILED to write"
    err "  do NOT continue. Restore another partition or stop and assess."
    exit 1
  fi
done

echo
ok "restore complete. Rebooting."
timeout 30 adb reboot
