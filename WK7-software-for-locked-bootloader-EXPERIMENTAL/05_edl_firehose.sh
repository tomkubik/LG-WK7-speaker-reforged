#!/usr/bin/env bash
# 05_edl_firehose.sh -- Flash partitions over EDL using an LG firehose loader.
#
# REQUIRES: (a) device in EDL 9008 mode, (b) an LG SDM212 firehose loader
#            program file, (c) qdl installed.
#
# This is the only route that works on a hard-locked unit with no ATX credential,
# PROVIDED a signed LG firehose loader is obtainable. See docs/EDL.md for where
# to look and what happens if it is not.
#
# Usage:
#   sudo ./05_edl_firehose.sh --loader ./loaders/prog_firehose_lge_ddr.mbn --info
#   sudo ./05_edl_firehose.sh --loader ./loaders/prog_firehose_lge_ddr.mbn --backup
#   sudo ./05_edl_firehose.sh --loader ./loaders/... --flash-boot boot.img
#   sudo ./05_edl_firehose.sh --loader ./loaders/... --wipe-vbmeta
source "$(dirname "$0")/lib/common.sh"
need_root

LOADER=""; MODE="info"; IMG=""; SLOT=""; WIPE_VBMETA=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --loader) LOADER="$2"; shift ;;
    --info) MODE="info" ;;
    --backup) MODE="backup" ;;
    --flash-boot) MODE="flash"; IMG="$2"; shift ;;
    --wipe-vbmeta) MODE="wipevbmeta" ;;
    --slot) SLOT="$2"; shift ;;
    *) die "unknown arg $1" ;;
  esac
  shift
done
[[ -n "$LOADER" ]] || die "--loader <firehose.mbn> required"
[[ -f "$LOADER" ]] || die "loader not found: $LOADER
  See docs/EDL.md. You need an LG SDM212 signed programmer file.
  Without it, EDL mode alone cannot write anything."
command -v qdl >/dev/null || die "qdl not installed -- see docs/EDL.md"

device_present "$USB_EDL" || die "device not in EDL mode (expect 05c6:9008). Run 04_edl_enter.sh"

[[ -n "$SLOT" ]] || SLOT="a"

echo "=============================================="
echo " WK7 EDL firehose flash"
echo "  loader : $LOADER"
echo "  mode   : $MODE"
echo "=============================================="

# Build a rawprogram XML for the partitions we care about.
# Layout and sizes come from the verified partition table in lib/common.sh.
gen_rawprogram() {
  local out="$1"
  {
    echo '<?xml version="1.0" ?>'
    echo '<data>'
    while IFS=: read -r name part size; do
      [[ -z "$name" ]] && continue
      case "$name" in userdata|gapps|system_*|vendor_*|oem_*) continue ;; esac
      local kb="${size}"
      # convert KiB -> sectors (512 bytes)
      local sectors=$(( kb * 2 ))
      echo "  <program SECTOR_SIZE_IN_BYTES=\"512\" NUM_SECTORS=\"$sectors\""
      echo "           file_sector_offset=\"0\" filename=\"$name.img\""
      echo "           label=\"$name\" start_byte=\"0\" physical_partition_number=\"0\" size_in_KB=\"$kb\" />"
    done <<<"$PARTITIONS"
    echo '</data>'
  } > "$out"
  echo "$out"
}

XP="$(mktemp /tmp/wk7_rawprogram.XXXXXX.xml)"

case "$MODE" in
  info)
    log "probing firehose (this uploads the loader and reads device info)"
    qdl --loader "$LOADER" --print-log --include-log --sectors-per-block 64 \
        --out "$XP" 2>&1 | tee "$WORKDIR/state/edl_info.log" || true
    log "log saved to $WORKDIR/state/edl_info.log"
    echo
    echo "If this failed, typical causes:"
    echo "  - loader signed for a different SoC (SDM212 vs SDA212)"
    echo "  - secure boot fuses blown; only OEM-signed loaders accepted"
    echo "  - wrong firehose target memory (--memory=ufs|eMMC)"
    ;;

  backup)
    STAMP="$(date +%Y%m%d-%H%M%S)"; OUT="$BACKUP_DIR/$STAMP"; mkdir -p "$OUT"
    log "dumping all critical partitions to $OUT"
    gen_rawprogram "$XP"
    # qdl reads images named in rawprogram; for a READ we pre-create placeholders
    while IFS=: read -r name part size; do
      case "$name" in userdata|gapps|system_*|vendor_*|oem_*) continue ;; esac
      : > "$OUT/$name.img"
    done <<<"$PARTITIONS"
    ( cd "$OUT" && qdl --loader "$LOADER" --include-log --rawprogram "$XP" \
        --sectors-per-block 64 2>&1 | tee "$OUT/edl_backup.log" )
    echo "$OUT" > "$BACKUP_DIR/LATEST"
    ok "EDL backup written to $OUT"
    ;;

  flash)
    [[ -f "$IMG" ]] || die "boot image not found: $IMG"
    warn "Writing boot_$SLOT over EDL. Verify you have a backup (--backup)."
    read -rp "Type YES to proceed: " ans; [[ "$ans" == YES ]] || die "aborted"
    XP2="$(mktemp /tmp/wk7_flash.XXXXXX.xml)"
    cat > "$XP2" <<EOF
<?xml version="1.0" ?>
<data>
  <program SECTOR_SIZE_IN_BYTES="512" NUM_SECTORS="65536"
           file_sector_offset="0" filename="$IMG"
           label="boot_$SLOT" start_byte="0" physical_partition_number="0"
           size_in_KB="32768" />
</data>
EOF
    adb push "$IMG" "$IMG" >/dev/null 2>&1 || true
    qdl --loader "$LOADER" --rawprogram "$XP2" --sectors-per-block 64 2>&1
    ok "boot_$SLOT written over EDL"
    ;;

  wipevbmeta)
    warn "Zeroing vbmeta_$SLOT over EDL -- disables AVB verification."
    read -rp "Type YES to proceed: " ans; [[ "$ans" == YES ]] || die "aborted"
    XP3="$(mktemp /tmp/wk7_vbmeta.XXXXXX.xml)"
    dd if=/dev/zero of="$WORKDIR/zero.img" bs=4096 count=1 status=none
    cat > "$XP3" <<EOF
<?xml version="1.0" ?>
<data>
  <program SECTOR_SIZE_IN_BYTES="512" NUM_SECTORS="128"
           file_sector_offset="0" filename="$WORKDIR/zero.img"
           label="vbmeta_$SLOT" start_byte="0" physical_partition_number="0"
           size_in_KB="64" />
</data>
EOF
    qdl --loader "$LOADER" --rawprogram "$XP3" --sectors-per-block 64 2>&1
    ok "vbmeta_$SLOT zeroed"
    ;;
esac

rm -f "$XP"
echo
echo "Finished mode=$MODE"
