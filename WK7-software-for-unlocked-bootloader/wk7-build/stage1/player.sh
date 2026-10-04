#!/bin/sh
# wk7 player: AirPlay pipe audio -> wk7gain (AirPlay trim, see VOLUME POLICY in wk7gain.c) -> 48 kHz ALSA device;
# reopens after each session. The phone/Mac AirPlay volume is the real control; the dashboard slider never applies here.
# Rate comes from /run/player.rate (44100 for AirPlay 1, 48000 for AirPlay 2); always S16_LE stereo.
[ -f /run/wk7.airplay-volume ] || echo 100 > /run/wk7.airplay-volume
while true; do
    RATE=$(cat /run/player.rate 2>/dev/null || echo 44100)
    /usr/local/bin/wk7gain /run/wk7.airplay-volume 100 < /tmp/shairport-sync-audio | aplay -q -D wk7 -f S16_LE -r "$RATE" -c 2 -t raw
    sleep 0.2
done
