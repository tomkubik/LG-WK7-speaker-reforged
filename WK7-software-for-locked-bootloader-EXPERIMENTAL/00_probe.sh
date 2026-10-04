#!/usr/bin/env bash
# 00_probe.sh -- Identify device, mode, unlock state, and build suitability.
# READ-ONLY. Safe to run on any unit, locked or unlocked.
#
# Usage: sudo ./00_probe.sh
source "$(dirname "$0")/lib/common.sh"

need_root
echo "=============================================="
echo " WK7 probe  --  $(date -Is)"
echo "=============================================="

MODE="$(which_mode)"
echo
echo "[mode] $MODE"
lsusb | grep -iE "05c6|18d1" || warn "no Qualcomm/Google Android device on USB bus"

# ---- fastboot / ADB reachable? -------------------------------------------
if [[ "$MODE" == fastboot ]]; then
  log "fastboot device: $(timeout 10 fastboot devices | head -1)"
  echo
  echo "--- fastboot getvar ---"
  for v in unlocked secure product current-slot version-bootloader; do
    printf '  %-18s %s\n' "$v" "$(timeout 10 fastboot getvar "$v" 2>&1 | sed 's/.*: //')"
  done
  echo
  echo "--- ATX authenticated-unlock probe ---"
  if timeout 15 fastboot oem at-get-vboot-unlock-challenge 2>&1 | tee "$STATE_DIR/atx_challenge.txt" | grep -qiE "version|product|challenge|0x"; then
    ok "ATX challenge endpoint ALIVE -- authenticated unlock is implemented in this bootloader"
    warn "you still need LG's PUK to mint unlock_credential.bin; the issuing console was deleted 2022-01-05"
  else
    warn "no ATX challenge response -- bootloader has no authenticated-unlock path"
  fi
  timeout 20 fastboot reboot >/dev/null 2>&1
  echo "  (rebooting to Android)"
  sleep 15
fi

# ---- Android side ---------------------------------------------------------
if timeout 20 adb devices 2>/dev/null | grep -q "device$"; then
  echo
  echo "--- device identity ---"
  adb_sh 'getprop ro.product.model; getprop ro.product.device; getprop ro.build.fingerprint'
  echo
  echo "--- build / security posture ---"
  adb_sh 'getprop ro.build.type; getprop ro.build.tags; getprop ro.debuggable; getprop ro.secure'
  echo
  echo "--- bootloader / AVB state ---"
  adb_sh 'getprop ro.boot.verifiedbootstate; getprop ro.boot.flash.locked; getprop ro.boot.vbmeta.device_state; getprop ro.oem_unlock_supported'
  echo
  echo "--- kernel ---"
  adb_sh 'uname -a'
  echo
  echo "--- root attempt ---"
  if adb_root; then ROOT_OK=yes; else ROOT_OK=no; fi

  echo
  echo "--- writable system? (decides generic escalation) ---"
  SYSRO="$(adb_sh 'mount | grep -E " /system | /vendor " ' | head -2)"
  echo "$SYSRO"
  if echo "$SYSRO" | grep -q "rw"; then
    ok "/system mounted rw -- generic module-load escalation is VIABLE"
  else
    warn "/system is read-only -- generic escalation blocked; kernel exploit required"
  fi

  # Save state for later scripts
  {
    echo "mode=$MODE"; echo "root=$ROOT_OK"
    adb_sh 'getprop ro.boot.verifiedbootstate' | sed 's/^/verifiedbootstate=/'
    adb_sh 'getprop ro.build.type' | sed 's/^/build_type=/'
  } > "$STATE_DIR/probe.txt"

  echo
  echo "--- build suitability for the 3 toolkit goals ---"
  if [[ "$ROOT_OK" == yes ]]; then
    ok "GOAL 'flash own system' : VIABLE via adb root + dd to by-name (no fastboot needed)"
  else
    warn "GOAL 'flash own system' : blocked at root. Need EDL or kernel exploit."
  fi
  echo "  GOAL 'EDL firehose'      : run 04_edl_enter.sh (needs rear port + LG firehose loader)"
  echo "  GOAL 'kernel exploit'    : run exploits/00_triage.sh"
fi

echo
echo "probe complete. state saved to $STATE_DIR/probe.txt"
