# WK7 software: technical notes

Alpine Linux 3.24 armv7 chroot at `/data/wk7linux` on the speaker, entered with `/data/wk7-enter.sh` (`enter.sh` here), running on top of LG's stock 3.10 kernel and Android 7. Slot B's boot hook starts it at every power-up (see `../slotb/`). Install steps: `INSTALL.md` at the top of the repository. Paths below without a leading `/` are relative to `WK7-software-for-unlocked-bootloader/wk7-build/stage1/`.

## Audio chain
- AirPlay: phone/Mac → shairport-sync (pipe backend) → `/tmp/shairport-sync-audio` FIFO → `player.sh` → `wk7gain` (AirPlay trim) → ALSA `pcm.wk7` (plug, speexrate_medium → 48 kHz S16) → hw:0,0 (DSP input MultiMedia1) → `PRI_MI2S_RX Audio Mixer MultiMedia1 = on` → Primary MI2S → amp.
- moOde: `wk7-moode-sink` → `wk7gain` (moOde level) → plughw:0,1 (MultiMedia2).
- Spotify Connect: spotifyd (pipe backend) → `/tmp/spotifyd-audio` → `wk7-spotify-player` → `wk7gain` (Spotify trim) → ALSA `wk7spotify` → hw:0,12 (MultiMedia5).
- The DSP mixes MultiMedia1/2/5 in hardware, so the sources never fight over one ALSA device.

Lessons:
- LG's ADSP errors on 44.1 kHz input (`q6asm ... DSP returned error`, garbled audio) → always feed 48 kHz.
- shairport-sync's ALSA backend crashes ("unknown format ... output bit depth") on the plug device → use the pipe backend.
- Android's `mdnsd` conflicts with avahi → it is stopped at boot.
- No gunzip on the device; push uncompressed tarballs.
- Heavy transfers: Wi-Fi adb only (the USB gadget path crashes the speaker).
- Never read `/tmp/shairport-sync-audio` (or /proc/<pid>/fd/0 of wk7gain) from a test script: any second reader steals audio and AirPlay stutters. Watch `/proc/asound/card0/pcm0p/sub0/status` instead.

Remove completely: reboot, then `rm -rf /data/wk7linux /data/wk7-enter.sh` (and switch back to slot A, see INSTALL.md).

## Updating the speaker's software (wireless and wired)
Two kinds of update. Almost everything is the first kind. `<speaker-ip>` is the speaker's address on your network (dashboard Status, `ping wk7.local`, or your router).

**A. The WK7 software (everything in /data/wk7linux): file copies, no flashing, usually no reboot.**
Slot B's boot hook only runs `wk7ctl start all` from /data/wk7linux, so new or changed programs there are picked up by restarting the affected service, or at the next power-up. The firmware slots are not touched.

Wireless (default; the speaker on Wi-Fi, cable out at power-on):
1. `adb connect <speaker-ip>:5555`, then `adb -s <speaker-ip>:5555 root` and connect again (adbd restarts as root).
2. Copy files from your computer: `adb -s <speaker-ip>:5555 push <file> /data/wk7linux/tmp/inst/`, then inside the chroot (`adb shell '/data/wk7-enter.sh -c "..."'`) back up the old file (e.g. to /root/bak-<date>/) and `install` the new one into place. `../install.sh --files <speaker-ip>` does this for all of our files at once.
3. Alpine packages (e.g. spotifyd): inside the chroot `apk update; apk add <pkg>`. The speaker downloads them from dl-cdn.alpinelinux.org over Wi-Fi.
4. Native builds (C, AirPlay 2): build in the chroot (gcc, see `build-ap2.sh`). The kernel has no getrandom, so fetch sources as tarballs, not git clones.
5. Restart only what changed: `wk7ctl restart airplay|spotify|buttons|dashboard` (restarting the dashboard also restarts the moOde stream for a second or two). `wk7ctl status` to check.

Wired (recovery, or when Wi-Fi is down):
1. Plug your computer into the mini-USB socket, then power-cycle the speaker. `wk7-usbroute` keeps the speaker on the cable when the computer finishes the USB handshake within the cable check (25 s, extended by 60 s once the computer's first USB reset is seen); Wi-Fi is then off. If the speaker still goes to Wi-Fi, the computer never finished the handshake (see "USB / Wi-Fi routing"). Most common cause: a front-panel USB port, hub or dock. Use a port directly on the motherboard (back of a desktop); on the Linux PC used in development, the front port never finished the handshake and the back port worked at once. See FAQ.md.
2. `adb devices` shows the speaker over USB; `adb root`, then the same push / chroot / `wk7ctl` steps as wireless, without `-s <ip>`.
3. Keep wired transfers small (scripts, configs). Large USB adb transfers crashed the development unit into Qualcomm crash-dump mode three times. For anything big, get Wi-Fi back first: unplug the cable and power-cycle (sometimes 2-3 times, see "USB / Wi-Fi routing").

**B. Firmware slots (system_b, vbmeta_b, boot_b) and the active slot: deliberate, rare steps.**
- Never write slot A (LG original, the fallback). Keep your own full backup of every partition outside this repository before the first slot-B write.
- Wireless: small slot-B writes are done from the running system over Wi-Fi adb (as root, writing the slot-B partition under /dev/block/bootdevice/by-name/ and reading it back to verify with md5). This is how wk7.rc, wk7-boot.sh and the vbmeta_b flag go on (see "Slot B writes" and INSTALL.md).
- Wired: the bootloader route needs the cable. Use `fastboot` for RAM/bootloader images and to change the active slot (`fastboot --set-active=a|b`; Android's bootctl is broken on this build). Reaching fastboot is usually `adb reboot bootloader`.
- After any slot change, check: boots, joins Wi-Fi, AirPlay/Spotify appear, buttons work; if not, set slot A active over the cable.

## First-run Wi-Fi setup
- `wk7-wifisetup` (Python). Saved networks live where LG's Android keeps them (`/data/misc/wifi/wpa_supplicant.conf`), so Android's Wi-Fi service rejoins them at every boot. The WPA key is stored as the derived 64-hex PSK, not the typed password.
- At boot `wk7-usbroute` routes USB to the Wi-Fi chip; if no network is saved it exits 3 and `wk7ctl start all` runs `wk7-wifisetup run`:
  1. scan nearby networks (`iw dev wlan0 scan`),
  2. add an access point interface next to wlan0 (`iw dev wlan0 interface add softap0 type __ap`; LG's driver runs station + AP at once), start LG's own `hostapd` (open network **WK7-Setup**, 192.168.4.1) and `dnsmasq` (DHCP; every DNS name answers 192.168.4.1, so phones open the page as a sign-in page). Do not switch the driver to AP firmware (`fwpath=ap`): that reloads the USB Wi-Fi chip and failed on real boots ("HIF registration failed"). Android's Wi-Fi stays off during setup (`svc wifi disable` would unload the driver and take wlan0 away). "Scan again" on the page briefly stops the hotspot, rescans and restarts it (the page reloads after 20 s; the phone may need to rejoin WK7-Setup).
  3. serve the setup page on port 80 (network list, "Other" name field, password),
  4. on submit: save the network, stop the hotspot (hostapd, dnsmasq, remove softap0), restart Android Wi-Fi (`svc wifi disable; svc wifi enable`, as wk7-usbroute does), wait up to 60 s for an address. Success → the normal services start. Failure → the network is forgotten and WK7-Setup comes back with an error message.
- The LED ring is blue while in setup mode (`/run/wk7/setup-mode`).
- `wk7-wifisetup has-network` / `forget` are used by wk7-usbroute and the factory reset.
- Every call into Android (`chroot /proc/1/root ...`) must drop `LD_PRELOAD`: wk7ctl preloads the chroot's getrandom shim, and Android's linker then refuses to start anything ("CANNOT LINK EXECUTABLE"). This silently broke the first setup boots.
- Logs: `/var/log/wk7-wifisetup.log` and `/var/log/wk7-usbroute.log` plus `wk7-usbroute.prev.log` (the boot before), kept across reboots. Diagnostics: `touch /etc/wk7/setup-test` makes the next boot run setup for 4 minutes even with the cable in, then return to the cable.
- Verified end to end on the development unit (2026-10-04): factory reset → reboot → WK7-Setup up about 10 s after setup starts → network chosen on a phone → speaker joined it and the dashboard, AirPlay and Spotify came back. Power-on to WK7-Setup is about 2 minutes (the ~38 s cable check comes first).
- If WK7-Setup doesn't appear: plug a computer into the mini-USB socket, power-cycle (the speaker stays on the cable), `adb root`, read the two logs above.

## Factory reset
- Hold **play/pause** until the LED ring turns red (about 5 s), then press play/pause once more within 5 s. The ring flashes red three times, `wk7-factory-reset --yes` runs and the speaker reboots into Wi-Fi setup. Works even when the buttons are disabled on the dashboard. MICOM key 0x07 (LG's own factory-reset gesture) triggers it as well.
- Why a confirm press instead of a 10 s hold: the MICOM reports play/pause once on press (0x01) and its long-press code (0x11) once at ~5 s, then nothing while the button stays down, so a longer hold can't be measured. The press that starts the gesture briefly mutes the speaker; the long press undoes that.
- Clears: saved Wi-Fi networks, `/etc/wk7/settings.json` (speaker name and dashboard settings; the AirPlay name in both shairport configs goes back to "WK7" and the volume cap is removed), `/etc/wk7/moode.json` (back to `/usr/local/share/wk7/moode.json.default`), volume levels, Spotify login (`/var/cache/spotifyd`). Never touches the firmware slots; the reboot keeps the active slot.
- Why play/pause: every WK7 has it on top and finds it by touch, the hold-plus-confirm can't happen by accident, and a short press keeps its normal meaning.

## Buttons/LEDs
`wk7-micomd.py` (installed as /usr/local/bin/wk7-micomd, needs python3 + avahi-tools): polls MICOM keys on /dev/ttyHSL1 every 30 ms, LED ring feedback: blue sweep at start = WK7 software up, white = playing, green flash = press, white quarters = volume level, red = mic mute, orange = speaker muted, solid red = factory reset armed (press play/pause to confirm), blue = Wi-Fi setup. Play/pause can't be measured while held: the MICOM sends one code on press and 0x11 once at ~5 s (that is why the factory reset uses hold-then-confirm). MICOM protocol: `WK7-software-for-unlocked-bootloader/wk7-probe/MICOM_PROTOCOL.md`.
AirPlay 1: remote control goes straight to the sender via DACP (HTTP GET http://<client>:<port>/ctrl-int/1/<cmd> with Active-Remote header), because shairport-sync 5.0.4's own DACP sender never resolves the port. Session details come from the shairport metadata pipe (daid/acre/clip), with a fallback to the -vv log. AirPlay 2, Spotify and moOde have no back-channel, so the buttons act on the speaker itself.
Android's peripheralman and LG's com.lge.smartspeaker must be stopped first (they own the UART).

## AirPlay 2
Built natively in the chroot (`build-ap2.sh`): nqptp 1.2.8 → /usr/local/bin/nqptp, shairport-sync 5.5.1 with AirPlay 2 → /usr/local/bin/shairport-sync-ap2. Config `/etc/shairport-sync-ap2.conf` (pipe backend, output 48 kHz S16 stereo = what LG's ADSP wants, no resampling). Verified: clean audio from Mac and iPhone, volume from both, grouping with other AirPlay 2 speakers.
Required fixes on LG's 3.10 kernel:
- No getrandom()/getentropy() (kernel < 3.17): `libgetrandom-shim.so` (getrandom-shim.c) preloaded via LD_PRELOAD in wk7ctl. Without it shairport-sync dies at pairing ("Fatal: getentropy is not supported"). git also fails for the same reason → fetch sources as GitHub tarballs.
- nqptp needs POSIX shared memory → tmpfs on /dev/shm inside the chroot (enter.sh).
- Build deps include `libplist-util` (plistutil).
Switching: `/etc/wk7/settings.json` {"airplay_mode": "1"|"2"} then `wk7ctl restart airplay`.
AirPlay 2 ran over IPv6 (ULA) on the development network; an IPv4-only test stalled after SETPEERS (no audio), so do not block or unpublish IPv6 for AirPlay.
`wk7ctl restart airplay` kills the player's whole process group: earlier, leftover player subshells stayed blocked on the FIFO and the next session had several readers (garbled audio).

## USB / Wi-Fi routing
The speaker's single USB port is switched by the MICOM: `C2 00 00` = mini-USB socket, `C2 00 01` = internal Wi-Fi/BT hub. The MICOM remembers the last state across power loss (and sometimes picks on its own at power-up), so `wk7-usbroute auto` decides fresh at every boot (called from `wk7ctl start all`, which the slot-B hook runs):
1. route to the socket, wait until the SoC has left USB host mode (usually 2-10 s, at most 60 s)
2. a computer on the cable within 25 s → stay on USB (recovery path, needs no Wi-Fi). "Computer" = android_usb state CONNECTED/CONFIGURED (the host finished setting the speaker up). A USB bus reset in the kernel log (`CI13XXX_CONTROLLER_RESET_EVENT`) extends the wait by 60 s for slow hosts (seen with a Linux PC), but doesn't count as a computer by itself: treating it as one stopped the speaker from returning to Wi-Fi, and a reset also appears right after the switch out of host mode. Logs of the last two boots: `/var/log/wk7-usbroute.log` and `.prev.log`.
3. otherwise route to Wi-Fi and trigger the Wi-Fi driver load (`echo sta > /sys/module/wlan/parameters/fwpath`; Android gives up after failing at boot). No saved network → Wi-Fi setup (above). Otherwise `svc wifi disable/enable`, stop Android's mdnsd; if Wi-Fi doesn't connect within 60 s → back to the socket.
Not usable as a cable test: power_supply/usb/present (reads 1 right after leaving host mode without a cable).
Power-on to AirPlay without the cable: about 2 minutes.

## Dashboard + moOde
- `wk7-dashboard` (python, port 80, no PIN) → http://wk7.local/ (the .local name follows the speaker name, `wk7-hostname`; avahi publishes IPv6 too, which avoids a 5 s lookup delay on macOS): status, name, AirPlay 1/2, max volume, LED brightness, buttons on/off, Spotify on/off, moOde section, Restart AirPlay / Restart Spotify / Reboot speaker. Settings in /etc/wk7/settings.json. Page: /usr/local/share/wk7/dashboard.html.
- moOde: `wk7_moode.py` in /usr/local/lib/wk7, imported by the dashboard (`http_port: 0`, routes /command/, /api/moode, /moode). Config /etc/wk7/moode.json (default `moode.json` here; also installed as `/usr/local/share/wk7/moode.json.default` for the factory reset). The installed copy is the one in this folder; it differs from `WK7-software-for-unlocked-bootloader/wk7-moode/` (the original moOde-modes work) in: default mode `http`, `playback_buffer_ms`, Pacer lead 0.12 s, and the volume-policy note.
- moOde Multiroom needs `wk7-igmp`: LG's kernel lacks IPv4 IGMP, so the speaker never joins 239.0.0.1; wk7-igmp sends IGMPv2 reports every 30 s in multiroom mode.
- moOde HTTP stream: `.local` stream URLs are resolved via `avahi-resolve` before each connect (musl has no mDNS resolver), so http://moode.local:8000 works. moOde needs Loopback OFF in HTTP mode, or the Pi's own output goes silent.
- moOde buffering (default 80 ms network / 150 ms playback) adjustable on the dashboard (Advanced).
- Every (re)start begins in moOde HTTP-stream mode; multiroom / AirPlay-only last until the next restart.
- LG's app is disabled (`pm disable com.lge.smartspeaker`; shared userdata, so slot A too; `pm enable` restores).

## Volume policy
Separate levels, all applied by `wk7gain`:
- AirPlay: the phone/Mac AirPlay volume is the control. `player.sh` runs `wk7gain /run/wk7.airplay-volume 100`; the trim is reset to 100 at every AirPlay session and only the speaker buttons nudge it.
- Spotify: the Spotify app's volume (soft-volume, initial 50); `/run/wk7.spotify-volume` trim, same rules.
- moOde (Multiroom and HTTP stream): `/run/wk7.volume`, dashboard "Speaker volume for moOde" slider (default 40) and the buttons when nothing else plays. Independent of moOde's own knob (`volume_hook` is a no-op).
- The dashboard slider never caps or scales AirPlay or Spotify. "Maximum volume" (shairport `volume_max_db`) still caps AirPlay; 0 dB (no cap) by default.

## Spotify Connect
- `spotifyd` 0.4.2 from Alpine community (`apk add spotifyd`, librespot inside), started by `wk7ctl start spotify` (also in `start all`). Shows in the Spotify app (Premium accounts) under the speaker name. Credentials cached in /var/cache/spotifyd. Options are on the command line; `/etc/wk7/spotifyd.conf` is an empty stand-in so Alpine's /etc/spotifyd.conf is not used.
- Source arbitration, newest wins: `wk7-spotify-hook` (spotifyd `--onevent`) sets /run/wk7/spotify-active on `start` and drops any AirPlay session (shairport D-Bus DropSession); `pause`/`stop`/`sessiondisconnected` clear it. `wk7-airplay-hook start` restarts spotifyd if Spotify was playing. `wk7-source-flag` keeps /run/wk7/source-active = AirPlay or Spotify playing; moode.json `airplay_flag` points to it, so moOde steps aside for either.

## Slot B writes
- `system_b`: `/system/etc/init/wk7.rc` and `/system/bin/wk7-boot.sh` (boot hook: marks slot B successful, stops LG app/peripheralman/mdnsd, runs `wk7ctl start all`).
- `vbmeta_b`: AVB flags = 1 (hashtree disabled).
- `boot_b` header: ` androidboot.selinux=permissive` appended to the kernel command line.
- Active slot: B. Slot A untouched.
The vbmeta_b and boot_b patches are made from your own speaker's images by `../slotb/patch-slotb-images.py`; no LG images are in this repository.
