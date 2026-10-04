#!/usr/bin/env bash
# 03_flash_adb_root.sh -- Write boot/system images with NO bootloader unlock.
#
# This is the path that works on a unit whose bootloader is LOCKED, as long
# as we can obtain an adb root shell. The bootloader lock gates *fastboot*;
# it does nothing to stop uid=0 writing to /dev/block.
#
# Requires: adb root working. That depends on the build flavour (userdebug
# ships it) -- it is NOT guaranteed on a locked unit. Verify with 00_probe.sh.
#
# Usage:
#   sudo ./03_flash_adb_root.sh --boot boot.img
#   sudo ./03_flash_adb_root.sh --slot b --boot boot.img --vendor vendor.img
#   sudo ./03_flash_adb_root.sh --wipe-vbmeta      # disable AVB verification
source "$(dirname "$0")/lib/common.sh"

BOOT=""; SLOT=""; VENDOR=""; SYSTEM=""; WIPE_VBMETA=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --boot) BOOT="$2"; shift ;;
    --vendor) VENDOR="$2"; shift ;;
    --system) SYSTEM="$2"; shift ;;
    --slot) SLOT="$2"; shift ;;
    --wipe-vbmeta) WIPE_VBMETA=1 ;;
    *) die "unknown arg $1" ;;
  esac
  shift
done
need_root
[[ -n "$BOOT" ]] || die "--boot <boot.img> required (this is the one that matters)"

require_device
[[ "$(which_mode)" == adb ]] || die "needs Android/ADB mode"
adb_root || die "no root -- this script cannot proceed. Use EDL instead."

# Resolve active slot if not given
if [[ -z "$SLOT" ]]; then
  SLOT="$(adb_sh 'getprop ro.boot.slot_suffix' | tr -d '\r' | sed 's/_//')"
  [[ -n "$SLOT" ]] || SLOT="a"
  log "active slot: $SLOT"
fi

[[ -f "$BOOT" ]] || die "boot image not found: $BOOT"

echo
echo "=============================================="
echo " Flashing WITHOUT bootloader unlock"
echo "  boot   -> boot_$SLOT"
[[ -n "$VENDOR" ]] && echo "  vendor -> vendor_$SLOT"
[[ -n "$SYSTEM" ]] && echo "  system -> system_$SLOT"
[[ "$WIPE_VBMETA" == 1 ]] && echo "  vbmeta -> WIPED (verification disabled)"
echo "=============================================="
echo
if [[ "$WIPE_VBMETA" == 1 ]]; then
  warn "wiping vbmeta disables AVB. Combined with an unlocked bootloader this"
  warn "is what allows unsigned boot images to boot."
fi
warn "This writes to block devices directly. Back up first (01_backup.sh)."
read -rp "Type YES to proceed: " ans
[[ "$ans" == "YES" ]] || die "aborted"

push_img() {
  local local_img="$1" part="$2"
  log "pushing $(basename "$local_img") -> /data/local/tmp/"
  adb push "$local_img" /data/local/tmp/ >/dev/null || die "adb push failed"

  # Capture dd output and verify it actually wrote. On a locked device this
  # can fail with EROFS; without this check we would falsely report success.
  local out
  out="$(adb shell "dd if=/data/local/tmp/$(basename "$local_img") of=$BY_NAME/$part bs=4096" 2>&1)"
  adb shell "rm -f /data/local/tmp/$(basename "$local_img")" >/dev/null 2>&1

  if echo "$out" | grep -qiE "Read-only file system|denied|No space left|Input/output error"; then
    err "dd failed writing $part:"
    echo "$out" | tail -3 | sed 's/^/      /'
    err "Nothing was written. The device is likely still enforcing verity"
    err "or the partition is protected. Use 02_restore.sh if anything changed."
    exit 1
  fi
  if ! echo "$out" | grep -qE "copied|written|records"; then
    err "dd produced no completion record for $part -- treating as FAILED"
    echo "$out" | tail -3 | sed 's/^/      /'
    exit 1
  fi
  ok "$part written"
}

push_img "$BOOT" "boot_$SLOT"
[[ -n "$VENDOR" ]] && push_img "$VENDOR" "vendor_$SLOT"
[[ -n "$SYSTEM" ]] && push_img "$SYSTEM" "system_$SLOT"

if [[ "$WIPE_VBMETA" == 1 ]]; then
  log "zeroing vbmeta_$SLOT ..."
  local vout
  vout="$(adb shell "dd if=/dev/zero of=$BY_NAME/vbmeta_$SLOT bs=4096 count=1" 2>&1)"
  if echo "$vout" | grep -qiE "Read-only file system|denied|Input/output error"; then
    err "could not zero vbmeta_$SLOT -- verification stays ENABLED"
    echo "$vout" | tail -3 | sed 's/^/      /'
    exit 1
  fi
  ok "vbmeta_$SLOT zeroed"
fi

echo
log "rebooting ..."
timeout 30 adb reboot
echo "Done. Watch for it to come back on adb."
