#!/bin/sh
# install.sh: put the WK7 software into /data/wk7linux on a speaker, from a computer with adb (macOS or Linux).
#
#   ./install.sh <speaker-ip>        e.g. ./install.sh 192.168.1.50   (adb over Wi-Fi, port 5555)
#   ./install.sh --files <speaker-ip>   only (re)install our files, skip Alpine, packages and builds
#
# Runs on LG's firmware or on ours. It only writes to /data (the Alpine chroot, /data/wk7-enter.sh);
# it never touches a firmware slot. Making slot B start it at boot is a separate step: INSTALL.md, part 4.
# Needs: adb, curl, gunzip on the computer; internet on the speaker (apk downloads over its Wi-Fi).
set -e
ALPINE_VER=3.24.2
ALPINE_URL=https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/armv7/alpine-minirootfs-$ALPINE_VER-armv7.tar.gz
NQPTP_URL=https://github.com/mikebrady/nqptp/archive/refs/tags/1.2.8.tar.gz
SPS_URL=https://github.com/mikebrady/shairport-sync/archive/refs/tags/5.5.1.tar.gz
PACKAGES="python3 alsa-utils alsa-plugins avahi avahi-tools dbus ffmpeg opus curl spotifyd shairport-sync
  build-base autoconf automake libtool pkgconf xxd alsa-lib-dev avahi-dev ffmpeg-dev glib-dev libconfig-dev
  libgcrypt-dev libplist-dev libplist-util libsodium-dev openssl-dev popt-dev soxr-dev util-linux-dev"

FILES_ONLY=
[ "$1" = "--files" ] && { FILES_ONLY=1; shift; }
[ -n "$1" ] || { sed -n '2,9p' "$0"; exit 2; }
DEV=$1:5555
HERE=$(cd "$(dirname "$0")" && pwd)
S=$HERE/stage1
C=/data/wk7linux
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

adb connect "$DEV" >/dev/null
adb -s "$DEV" root >/dev/null 2>&1 || true
sleep 3; adb connect "$DEV" >/dev/null
A() { adb -s "$DEV" shell "$@"; }
IN() { A "/data/wk7-enter.sh -c '$*'"; }                 # run inside the chroot
[ "$(A id -u | tr -d '\r')" = 0 ] || { echo "adb root failed (needs root access)"; exit 1; }

if [ -z "$FILES_ONLY" ]; then
    echo "== Alpine $ALPINE_VER root file system"
    curl -fsSL "$ALPINE_URL" -o "$TMP/rootfs.tar.gz"
    gunzip "$TMP/rootfs.tar.gz"                            # the speaker has no gunzip
    A "mkdir -p $C"
    adb -s "$DEV" push "$TMP/rootfs.tar" /data/local/tmp/rootfs.tar >/dev/null
    A "cd $C && tar -xf /data/local/tmp/rootfs.tar && rm /data/local/tmp/rootfs.tar"
    adb -s "$DEV" push "$S/enter.sh" /data/wk7-enter.sh >/dev/null
    A "chmod 755 /data/wk7-enter.sh"
    # public resolvers for apk; .local names go through avahi
    A "printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\n' > $C/etc/resolv.conf"
    echo "== packages"
    IN "apk update && apk add $(echo $PACKAGES)"
fi

echo "== WK7 files"
A "mkdir -p $C/tmp/inst"
for f in "$S"/*; do
    case "$(basename "$f")" in README.md|*.bak|*.wav|start.sh|DSP|__pycache__) continue ;; esac
    adb -s "$DEV" push "$f" $C/tmp/inst/ >/dev/null
done
IN "set -e; cd /tmp/inst
mkdir -p /etc/wk7 /usr/local/bin /usr/local/lib/wk7 /usr/local/share/wk7 /var/cache/spotifyd /root
for f in wk7-airplay-hook wk7-hostname wk7-igmp wk7-lgstop wk7-moode-sink wk7-source-flag wk7-spotify-hook \
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

if [ -z "$FILES_ONLY" ]; then
    echo "== AirPlay 2 (nqptp + shairport-sync, built on the speaker; takes a while)"
    curl -fsSL "$NQPTP_URL" -o "$TMP/nqptp.tar.gz"; curl -fsSL "$SPS_URL" -o "$TMP/sps.tar.gz"
    gunzip "$TMP/nqptp.tar.gz" "$TMP/sps.tar.gz"
    adb -s "$DEV" push "$TMP/nqptp.tar" $C/tmp/ >/dev/null; adb -s "$DEV" push "$TMP/sps.tar" $C/tmp/ >/dev/null
    IN "set -e; mkdir -p /root/src && cd /root/src && rm -rf nqptp shairport-sync
tar -xf /tmp/nqptp.tar && mv nqptp-* nqptp && tar -xf /tmp/sps.tar && mv shairport-sync-* shairport-sync
rm /tmp/nqptp.tar /tmp/sps.tar; LD_PRELOAD=/usr/local/lib/libgetrandom-shim.so sh /root/build-ap2.sh"
fi

echo "== done. Start by hand with: adb -s $DEV shell '/data/wk7-enter.sh -c \"wk7ctl start all\"'"
echo "   (on LG's firmware, stop LG's app first: INSTALL.md part 3). Slot B boot hook: INSTALL.md part 4."
