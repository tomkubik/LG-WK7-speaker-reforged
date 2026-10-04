#!/usr/bin/env bash
# 04_edl_enter.sh -- Put the WK7 into Qualcomm EDL (9008) mode and confirm it.
#
# EDL is a ROM-level debug mode implemented in the SoC boot chain, BEFORE the
# Android bootloader. Entering it does not require the bootloader to be unlocked.
# This is the only route to flash a hard-locked unit where no ATX credential exists.
#
# There is NO reliable software command to force EDL on this device from
# Android -- it is a hardware boot-strapping path. You must cold-boot it with
# the right pin/key state. This script therefore:
#   1. installs the 9008 udev rule
#   2. watches the USB bus
#   3. tells you exactly what to hold, then reports what it actually saw
#
# Usage: sudo ./04_edl_enter.sh [--watch 120]
source "$(dirname "$0")/lib/common.sh"
need_root

WATCH=180
[[ "$1" == "--watch" ]] && WATCH="$2"

echo "=============================================="
echo " WK7 -> EDL (9008)"
echo "=============================================="

# --- udev rule so non-root tools can see the device -----------------------
RULE=/etc/udev/rules.d/51-android.rules
if ! grep -q '05c6' "$RULE" 2>/dev/null; then
  log "installing udev rule for Qualcomm 05c6"
  printf 'SUBSYSTEM=="usb", ATTR{idVendor}=="05c6", MODE="0666", GROUP="plugdev"\n' > "$RULE"
  udevadm control --reload >/dev/null 2>&1
  udevadm trigger >/dev/null 2>&1
  ok "udev rule installed: $RULE"
else
  ok "udev rule already present"
fi

echo
echo "--- BEFORE ---"
lsusb | grep -i 05c6 || warn "not in EDL yet (expected)"

cat <<'EOF'

------------------------------------------------------------------
 HOW TO ENTER EDL ON THE WK7
------------------------------------------------------------------
 Use a REAR motherboard USB port on the PC. The front panel header
 will NOT enumerate this device (verified: it enumerates on rear
 only).

 Try these in order, cable connected to the PC throughout:

  A) EDL key (most likely on this unit)
     1. Unplug the speaker from wall power.
     2. Hold VOLUME-UP + VOLUME-DOWN (both, and keep holding).
     3. While still holding BOTH, plug in wall power.
     4. Keep holding ~10 seconds. Release.
     -> expect lsusb to show  05c6:9008

  B) Volume-down + power
     Same as above but hold VOLUME-DOWN + POWER.

  C) Factory reset pin combo
     Hold both volume keys AND the power button while applying power.

 If a key combo does nothing at all, the unit may have the EDL testpoint
 disabled in production fuses. In that case EDL is unavailable and the
 only remaining route is a kernel exploit -> root -> dd (see exploits/).

------------------------------------------------------------------
EOF

log "watching USB for $WATCH seconds -- go ahead and try the combos now"
log "(the speaker must be COLD -- unplugged from wall power -- between attempts)"

END=$(( $(date +%s) + WATCH ))
while [[ $(date +%s) -lt $END ]]; do
  if device_present "$USB_EDL"; then
    echo
    ok "*** EDL DETECTED: 05c6:9008 ***"
    lsusb | grep -i 05c6
    echo
    echo "Next: see docs/EDL.md for the firehose loader + qdl flashing steps."
    exit 0
  fi
  # surface any other Qualcomm/android appearance (e.g. it fell into adb)
  new="$(lsusb | grep -iE '05c6|18d1' || true)"
  [[ -n "$new" ]] && warn "saw: $new"
  sleep 2
done

echo
err "EDL not detected within $WATCH s."
echo "Interpretation:"
echo "  - Device never appeared at all  => port/cable/power issue, or EDL disabled in fuses."
echo "  - Device appeared as 05c6:901d  => it fell into ADB, not EDL. Try combo A again."
