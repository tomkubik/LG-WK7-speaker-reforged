#!/bin/sh
# Build nqptp + shairport-sync (AirPlay 2) natively inside the chroot; installs as /usr/local/bin/{nqptp,shairport-sync-ap2}
set -e
cd /root/src/nqptp
echo "== nqptp $(date +%T)"; autoreconf -fi >/dev/null 2>&1; ./configure >/dev/null; make -j4 >/dev/null
install -m 755 nqptp /usr/local/bin/nqptp
cd /root/src/shairport-sync
echo "== shairport-sync configure $(date +%T)"; autoreconf -fi >/dev/null 2>&1
./configure --sysconfdir=/etc --with-alsa --with-pipe --with-soxr --with-avahi --with-ssl=openssl \
    --with-airplay-2 --with-metadata --with-dbus-interface --with-mpris-interface >/tmp/sps-configure.log 2>&1
echo "== shairport-sync make $(date +%T)"; make -j4 >/tmp/sps-make.log 2>&1
install -m 755 shairport-sync /usr/local/bin/shairport-sync-ap2
echo "== done $(date +%T)"; /usr/local/bin/shairport-sync-ap2 -V
