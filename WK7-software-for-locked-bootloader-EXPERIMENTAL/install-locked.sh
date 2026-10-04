#!/usr/bin/env bash
# install-locked.sh — install the WK7 software stack on a LOCKED-bootloader
#                     speaker, WITHOUT touching any firmware partition.
#
#   ⚠ EXPERIMENTAL — UNTESTED ON ANY LOCKED DEVICE.
#     No locked WK7 has been tested. See docs/locked.md.
#     The single point of failure is whether `adb root` is allowed on a
#     locked unit. This script checks that FIRST and stops if it fails.
#
# What this script does:
#   * installs the Alpine chroot + WK7 stack into /data/wk7linux
#   * runs entirely over Wi-Fi ADB (port 5555)
#   * writes ONLY to /data  — never to boot, system, vendor, vbmeta, aboot
#
# What this script will NOT do:
#   * make the stack start automatically at power-on (impossible while locked)
#   * unlock the bootloader
#   * touch any firmware partition, in any circumstance
#
# Usage:
#   ./install-locked.sh <speaker-ip>
#   ./install-locked.sh <speaker-ip> --files-only     # re-push files, skip Alpine/apk/builds
#   ./install-locked.sh <speaker-ip> --start          # just start the stack
#   ./install-locked.sh <speaker-ip> --check          # report capability, change nothing
#   ./install-locked.sh <speaker-ip> --probe          # READ-ONLY; works as plain shell, no root
set -uo pipefail

usage(){ sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//' | sed '/^$/d'; }

STAGE1="${STAGE1:-}"
MODE="full"
IP=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --files-only) MODE="files" ;;
    --start) MODE="start" ;;
    --check) MODE="check" ;;
    --probe) MODE="probe" ;;
    --stage1) STAGE1="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) IP="$1" ;;
  esac
  shift
done
[ -n "$IP" ] || { usage; exit 2; }
if [ -z "$STAGE1" ] && [ "$MODE" != "probe" ]; then
  echo "STAGE1 not set. Pass --stage1 <path-to-stage1-dir> or export STAGE1=..."
  echo "(--probe needs no STAGE1.)"
  exit 2
fi
[ -n "$STAGE1" ] && [ ! -d "$STAGE1" ] && { echo "STAGE1 dir not found: $STAGE1"; exit 2; }
case "$MODE" in
  check|start|probe) ;;
  *) [ -f "$STAGE1/enter.sh" ] || { echo "STAGE1/enter.sh missing -- needed to enter the chroot."; exit 2; } ;;
esac

DEV="$IP:5555"
CHROOT=/data/wk7linux
c_red=$'\033[31m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_cyn=$'\033[36m'; c_off=$'\033[0m'
log(){ printf '%s[*]%s %s\n' "$c_cyn" "$c_off" "$*"; }
ok(){ printf '%s[+]%s %s\n' "$c_grn" "$c_off" "$*"; }
warn(){ printf '%s[!]%s %s\n' "$c_ylw" "$c_off" "$*"; }
err(){ printf '%s[-]%s %s\n' "$c_red" "$c_off" "$*" >&2; }

command -v adb >/dev/null || { err "adb not found"; exit 1; }

A(){ adb -s "$DEV" shell "$@" 2>&1; }
IN(){ A "/data/wk7-enter.sh -c '$*'"; }

# Identify the target. Verified in testing: another device on the same
# network (a Google Home) answered on 5555 and would have been written to
# if we assumed any ADB target was the speaker.
device_model(){ A getprop ro.product.model | tr -d '\r' | head -1; }
device_fingerprint(){ A getprop ro.build.fingerprint | tr -d '\r' | head -1; }

echo "=============================================="
echo " WK7 locked-device installer  (EXPERIMENTAL)"
echo "  target: $DEV"
echo "  mode:   $MODE"
echo "=============================================="
[ "$MODE" = "probe" ] || warn "UNTESTED on a locked bootloader. docs/locked.md explains the risk."
[ "$MODE" = "probe" ] || true

# ---- connect --------------------------------------------------------------
adb connect "$DEV" >/dev/null 2>&1
sleep 2
# 'offline' means adbd answered but the transport died — common on these
# units when the WiFi stack is reinitialised. Retry before giving up.
_try=0
while [ $_try -lt 3 ]; do
  _st="$(adb devices | awk -v d="$DEV" '$1==d {print $2}')"
  case "$_st" in
    device) break ;;
    offline|"") adb disconnect "$DEV" >/dev/null 2>&1
              sleep 2; adb connect "$DEV" >/dev/null 2>&1; sleep 3 ;;
  esac
  _try=$((_try+1))
done

if ! adb devices | grep -q "^$DEV[[:space:]]*device"; then
  err "cannot reach $DEV as an active ADB target."
  echo
  echo "  Current adb state:"
  adb devices | sed 's/^/    /'
  echo
  case "$(adb devices | awk -v d="$DEV" '$1==d {print $2}')" in
    offline)
      err "It shows 'offline' — adbd was reached but the transport died."
      err "Common causes: adb was disconnected, the speaker rebooted, or the"
      err "WiFi stack reinitialised. Power-cycle the speaker, then retry."
      ;;
    "")  err "No ADB entry at all. Either the IP is wrong or the speaker's"
         err "adbd is not listening. Verify both are on the same network:"
         err "  adb connect <ip>:5555"
         err "  ping <ip>"
         err "Note: an unreachable IP and a stale adb entry look identical"
         err "here, because we cannot probe a host that is not answering." ;;
    *)   err "Unexpected state." ;;
  esac
  exit 1
fi

# ---- confirm this is actually a WK7 ---------------------------------------
MODEL="$(device_model)"
FP="$(device_fingerprint)"
echo "  model               : $MODEL"
echo "  fingerprint         : $FP"

case "$MODEL" in
  *WK7*|*wk7*) ;;
  *)
    err "target $DEV is NOT a WK7 (model='$MODEL'). Refusing to continue."
    err "Another device on this network answered on port 5555. Re-check the IP:"
    err "  adb disconnect $DEV"
    err "  adb devices"
    exit 1
    ;;
esac

# ---- PROBE: read-only capability assessment, works as plain shell ---------
# Deliberately does NOT call `adb root`. Its whole purpose is to work on a
# locked unit where root is likely refused, so that we learn what we are
# dealing with before committing to anything.
probe_mode(){
  echo
  echo "--- PROBE (read-only, no root required) ---"

  local BT DBG VBS LOCKED TAG SLOT KERNEL SEL MNT
  BT="$(A getprop ro.build.type | tr -d '\r')"
  DBG="$(A getprop ro.debuggable | tr -d '\r')"
  VBS="$(A getprop ro.boot.verifiedbootstate | tr -d '\r')"
  LOCKED="$(A getprop ro.boot.flash.locked | tr -d '\r')"
  TAG="$(A getprop ro.build.tags | tr -d '\r')"
  SLOT="$(A getprop ro.boot.slot_suffix | tr -d '\r')"
  KERNEL="$(A uname -r | tr -d '\r')"
  SEL="$(A getenforce | tr -d '\r')"
  MNT="$(A "mount | grep ' /data '" | tr -d '\r')"

  printf '  %-24s %s\n' "build type"        "$BT"
  printf '  %-24s %s\n' "build tags"        "$TAG"
  printf '  %-24s %s\n' "ro.debuggable"     "$DBG"
  printf '  %-24s %s\n' "verifiedbootstate" "$VBS"
  printf '  %-24s %s\n' "ro.boot.flash.locked" "$LOCKED"
  printf '  %-24s %s\n' "active slot"       "$SLOT"
  printf '  %-24s %s\n' "kernel"            "$KERNEL"
  printf '  %-24s %s\n' "SELinux"           "$SEL"
  printf '  %-24s %s\n' "/data mount"       "$MNT"
  echo

  # --- rung 1: ADB reachable? (we are here, so yes) ---
  ok "rung 1/3 — ADB reachable (you are here)"
  echo "        ADB runs without a stored auth key on some units"
  echo "        (ro.sys.usb.default.config contains 'adb'), so it may be"
  echo "        exposed on a locked unit with no user action required."
  printf '  %-24s %s\n' "ro.sys.usb.default.config" "$(A getprop ro.sys.usb.default.config | tr -d '\r')"
  printf '  %-24s %s\n' "persist.adb.tcp.port" "$(A getprop persist.adb.tcp.port | tr -d '\r')"
  echo

  # --- rung 2: root? ---
  local CUR; CUR="$(A id -u | tr -d '\r')"
  if [ "$CUR" = "0" ]; then
    ok "rung 2/3 — ROOT AVAILABLE. The install will work as-is."
    echo "        Nothing else to determine. Next step is a plain install:"
    echo "          ./install-locked.sh <ip> --stage1 <dir>"
    return 0
  fi

  warn "rung 2/3 — NO ROOT (uid=$CUR). This is the wall."
  echo
  if [ "$DBG" = "1" ] || [ "$BT" = "userdebug" ] || [ "$TAG" = "test-keys" ]; then
    echo "  BUT: this build is userdebug/test-keys (ro.debuggable=$DBG)."
    echo "  `adb root` SHOULD be permitted. Try it manually:"
    echo "    adb -s $DEV root"
    echo "  then re-run --probe."
  else
    echo "  Build is '$BT' / '$TAG' with ro.debuggable=$DBG."
    echo "  On such builds adbd refuses \`adb root\` regardless of the bootloader."
    echo "  Root must come from elsewhere. Options, in order of practicality:"
    echo
    echo "   1. Kernel privilege escalation"
    echo "      kernel $KERNEL"
    echo "      SELinux $SEL   <- 'Permissive' materially eases exploitation"
    echo "      Run:  sudo ./exploits/00_triage.sh     (works as plain shell)"
    echo "            python3 exploits/02_cve_scout.py --kernel $KERNEL"
    echo "      See:  docs/KERNEL.md"
    echo
    echo "   2. EDL 9008 (bypasses the bootloader entirely)"
    echo "      Needs an LG-signed SDM212 firehose loader."
    echo "      Run:  sudo ./04_edl_enter.sh"
    echo "      See:  docs/EDL.md"
    echo
    echo "   3. UART console (requires opening the housing)"
    echo "      Kernel already routes console to ttyHSL0 at 115200."
    echo "      Out of scope for this toolkit."
  fi
  echo

  # --- rung 3: filesystem, if we ever do get root ---
  if echo "$MNT" | grep -q "rw"; then
    ok "rung 3/3 — /data is mounted rw. Once root is obtained, the install"
    echo "        will succeed: the bootloader lock does not gate /data."
  else
    warn "rung 3/3 — /data is NOT rw. Even with root, verify writability:"
    echo "        adb -s $DEV shell 'touch /data/.wtest && rm /data/.wtest'"
  fi
  echo
  warn "This probe is READ-ONLY. Nothing was written or modified."
}

if [ "$MODE" = "probe" ]; then
  probe_mode
  exit 0
fi

adb -s "$DEV" root >/dev/null 2>&1
sleep 3
adb connect "$DEV" >/dev/null 2>&1
UID_="$(A id -u | tr -d '\r')"
LOG="$(A getprop ro.boot.flash.locked | tr -d '\r')"
VBS="$(A getprop ro.boot.verifiedbootstate | tr -d '\r')"
DBG="$(A getprop ro.build.type | tr -d '\r')"

echo
echo "--- device state ---"
echo "  uid                 : $UID_"
echo "  ro.boot.flash.locked: $LOG"
echo "  verifiedbootstate   : $VBS"
echo "  build type          : $DBG"

if [ "$UID_" != "0" ]; then
  err "adb root was REFUSED on this device."
  cat <<'EOF'

  This is the documented single point of failure (docs/locked.md).

  If `adb root` is refused while the bootloader is locked, the entire
  stack cannot be installed — the bootloader lock does not gate writes to
  /data, but you need root to reach /data in the first place.

  There is no workaround from here. The remaining options are:
    - a kernel exploit to gain root without adb (see docs/KERNEL.md)
    - EDL 9008 mode with an LG-signed firehose loader (see docs/EDL.md)

  This result is valuable either way — please record it.
EOF
  exit 1
fi
ok "root shell confirmed — install can proceed"

if [ "$MODE" = "check" ]; then
  echo
  ok "--check complete. Nothing was written."
  echo "  If flash.locked=1 above and root worked, this device is a SUCCESS case"
  echo "  for the locked-device path. Please document it."
  exit 0
fi

if [ "$MODE" = "start" ]; then
  log "starting stack ..."
  A "/data/wk7-enter.sh -c 'wk7ctl start all'"
  echo
  A "/data/wk7-enter.sh -c 'wk7ctl status'"
  exit 0
fi

# ---- filesystem writability ----------------------------------------------
MOUNT_="$(A "mount | grep ' /data '")"
echo "  /data mount         : $MOUNT_"
echo "$MOUNT_" | grep -q "rw" || warn "/data is not mounted rw; writes may fail"

# ---- install --------------------------------------------------------------
if [ "$MODE" = "full" ]; then
  ALPINE_VER=3.24.2
  ALPINE_URL="https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/armv7/alpine-minirootfs-$ALPINE_VER-armv7.tar.gz"
  NQPTP_URL="https://github.com/mikebrady/nqptp/archive/refs/tags/1.2.8.tar.gz"
  SPS_URL="https://github.com/mikebrady/shairport-sync/archive/refs/tags/5.5.1.tar.gz"
  PACKAGES="python3 alsa-utils alsa-plugins avahi avahi-tools dbus ffmpeg opus curl spotifyd shairport-sync
    build-base autoconf automake libtool pkgconf xxd alsa-lib-dev avahi-dev ffmpeg-dev glib-dev libconfig-dev
    libgcrypt-dev libplist-dev libplist-util libsodium-dev openssl-dev popt-dev soxr-dev util-linux-dev"
  TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

  log "downloading Alpine $ALPINE_VER rootfs (armv7)..."
  curl -fsSL "$ALPINE_URL" -o "$TMP/rootfs.tar.gz" || { err "download failed"; exit 1; }
  gunzip "$TMP/rootfs.tar.gz"

  log "uploading to /data/wk7linux ..."
  A "mkdir -p $CHROOT"
  adb -s "$DEV" push "$TMP/rootfs.tar" /data/local/tmp/rootfs.tar >/dev/null || { err "push failed"; exit 1; }
  A "cd $CHROOT && tar -xf /data/local/tmp/rootfs.tar && rm -f /data/local/tmp/rootfs.tar"
  adb -s "$DEV" push "$STAGE1/enter.sh" /data/wk7-enter.sh >/dev/null || { err "push enter.sh failed"; exit 1; }
  A "chmod 755 /data/wk7-enter.sh"
  A "printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\n' > $CHROOT/etc/resolv.conf"

  log "installing packages inside the chroot (this takes a while)..."
  IN "apk update && apk add $PACKAGES"
fi

# ---- stage1 files ---------------------------------------------------------
log "installing WK7 files into the chroot ..."
A "mkdir -p $CHROOT/tmp/inst"
for f in "$STAGE1"/*; do
  b=$(basename "$f")
  case "$b" in README.md|*.bak|*.wav|start.sh|DSP|__pycache__) continue ;; esac
  adb -s "$DEV" push "$f" "$CHROOT/tmp/inst/" >/dev/null || warn "push failed: $b"
done
IN "set -e; cd /tmp/inst
mkdir -p /etc/wk7 /usr/local/bin /usr/local/lib/wk7 /usr/local/share/wk7 /var/cache/spotifyd /root
for f in wk7-airplay-hook wk7-hostname wk7-igmp wk7-lgstop wk7-moode-sink wk7-source-flag wk7-spotify-hook \\
         wk7-spotify-player wk7-usbroute wk7ctl wk7-wifisetup wk7-factory-reset; do install -m 755 \$f /usr/local/bin/\$f; done
install -m 755 wk7-dashboard.py /usr/local/bin/wk7-dashboard
install -m 755 wk7-micomd.py /usr/local/bin/wk7-micomd
install -m 755 player.sh /root/player.sh
install -m 755 build-ap2.sh /root/build-ap2.sh
install -m 644 wk7_moode.py /usr/local/lib/wk7/wk7_moode.py
install -m 644 dashboard.html /usr/local/share/wk7/dashboard.html
install -m 644 moode.json /usr/local/share/wk7/moode.json.default
[ -f /etc/wk7/moode.json ] || install -m 644 moode.json /etc/wk7/moode.json
install -m 644 spotifyd.conf /etc/wk7/spotifyd.conf
install -m 644 asound.conf /etc/asound.conf
install -m 644 wk7-dbus.conf /etc/dbus-1/system.d/wk7.conf
for f in shairport-sync.conf shairport-sync-ap2.conf; do [ -f /etc/\$f.wk7 ] || cp /etc/\$f /etc/\$f.wk7 2>/dev/null || true; install -m 644 \$f /etc/\$f; done
gcc -O2 -shared -fPIC -o /usr/local/lib/libgetrandom-shim.so getrandom-shim.c
gcc -O2 -Wall -o /usr/local/bin/wk7gain wk7gain.c -lm
cd /; rm -rf /tmp/inst"

if [ "$MODE" = "full" ]; then
  log "building AirPlay 2 (nqptp + shairport-sync) on the speaker; this takes a while..."
  curl -fsSL "$NQPTP_URL" -o "$TMP/nqptp.tar.gz"
  curl -fsSL "$SPS_URL" -o "$TMP/sps.tar.gz"
  gunzip "$TMP/nqptp.tar.gz" "$TMP/sps.tar.gz"
  adb -s "$DEV" push "$TMP/nqptp.tar" "$CHROOT/tmp/" >/dev/null
  adb -s "$DEV" push "$TMP/sps.tar" "$CHROOT/tmp/" >/dev/null
  IN "set -e; mkdir -p /root/src && cd /root/src && rm -rf nqptp shairport-sync
tar -xf /tmp/nqptp.tar && mv nqptp-* nqptp && tar -xf /tmp/sps.tar && mv shairport-sync-* shairport-sync
rm /tmp/nqptp.tar /tmp/sps.tar; LD_PRELOAD=/usr/local/lib/libgetrandom-shim.so sh /root/build-ap2.sh"
fi

echo
ok "install complete"
echo
cat <<EOF
 START IT (required every power-on while the bootloader is locked):
   adb -s $DEV shell '/data/wk7-enter.sh -c "wk7ctl start all"'

 Dashboard (once running): http://wk7.local/

 Auto-start at power-on is NOT possible on a locked bootloader — it requires
 modifying signed firmware partitions. See docs/locked.md.
EOF
warn "This has never been run on a locked device. Please report what happened."
