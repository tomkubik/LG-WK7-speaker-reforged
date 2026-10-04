# AGENTS.md — WK7 speaker firmware

Read this first if you are an AI agent (or a new human) working in this codebase.
Paths in this file are relative to the repository root ("WK7 Speaker New Software").

## What this is
Custom firmware for an LG WK7 ThinQ speaker (obsolete; no more LG/Google updates).
The speaker does Wi-Fi, plays through its built-in speaker, is an AirPlay receiver
(AirPlay 2 default, AirPlay 1 switchable), a Spotify Connect speaker (spotifyd) and plays moOde audio (HTTP stream by
default, or moOde Multiroom Receiver). Dashboard: http://wk7.local/ (the address follows
the speaker name).

## How it is built
- Hardware: Qualcomm APQ8009 (32-bit ARMv7), 768 MB RAM, 8 GB eMMC, A/B slots.
  Applies to units with an UNLOCKED bootloader. Units with locked bootloaders
  cannot load custom slot-B firmware.
- We keep LG's Linux 3.10 kernel, drivers and Android underneath (they bring up Wi-Fi
  and the audio DSP). Our software runs in an Alpine chroot at /data/wk7linux and is
  started at boot by slot B's init service (wk7.rc -> wk7-boot.sh).
- Slot A = LG's original, untouched, the fallback. Slot B = ours, the default boot.
- Components in /data/wk7linux: dashboard (Python; Restart AirPlay / Restart Spotify / Reboot
  buttons), wk7ctl (start/stop services), wk7-usbroute (cable vs Wi-Fi at boot), wk7-wifisetup
  (first-run WK7-Setup network), wk7-factory-reset, wk7-hostname (<name>.local),
  wk7-igmp (IGMP join every 30 s; the kernel lacks it), wk7-moode (wk7_moode.py),
  wk7-micomd (buttons/LEDs over the MICOM serial protocol), wk7gain.c (volume filter),
  shairport-sync + nqptp (AirPlay, pipe -> player.sh -> aplay), spotifyd (Spotify Connect,
  pipe -> wk7-spotify-player -> DSP input MultiMedia5; newest of AirPlay/Spotify wins),
  wk7-airplay-hook / wk7-spotify-hook / wk7-source-flag (moOde steps aside while AirPlay or
  Spotify plays), plus a getentropy shim (the kernel lacks getrandom).
- The kernel is too old for git and getrandom; use source archives, not git clones.

## Rules you must not break
1. Never flash, write a partition, or change the active slot without the owner's explicit
   typed go. Slot B writes are listed in
   WK7-software-for-unlocked-bootloader/wk7-build/stage1/README.md ("Slot B writes").
2. Never print, copy or commit Wi-Fi names or passwords. The speaker keeps saved networks in
   /data/misc/wifi/wpa_supplicant.conf; when reading it, hide ssid/psk lines (they are tab-indented).
3. Ask before downloads and installs. Heavy transfers go over Wi-Fi, not USB.
4. MICOM controller: never send the D9 reset or any firmware-update command. C2 is the
   USB switch (C2 00 01 = internal Wi-Fi/BT hub, C2 00 00 = mini-USB socket).
5. VOLUME POLICY: AirPlay is controlled by the phone/Mac AirPlay volume, Spotify by the Spotify
   app's volume; the speaker buttons nudge whichever plays (trims /run/wk7.airplay-volume and
   /run/wk7.spotify-volume, reset to 100 when that source starts). The dashboard slider never caps
   or scales AirPlay or Spotify. The slider sets only the moOde stream level
   (/run/wk7.volume, default 40/100), which stays independent of moOde's own volume knob.
   Do not add "follow moOde's volume" back without asking the owner (separate rooms).
6. No trx-rx anywhere, and nothing extra running on the moOde Pi.
7. Cable always wins: a computer on the mini-USB cable at power-on means the speaker
   stays on USB (Wi-Fi may be off). "Computer" = the host finished the USB handshake
   (android_usb CONFIGURED); a USB reset only extends the wait by 60 s for slow hosts. Never
   treat the reset alone as a computer: the speaker then fails to come back on Wi-Fi. That is
   the recovery path and must never depend on Wi-Fi. If a computer doesn't see the speaker,
   first suspect the port: use one directly on the motherboard (back of a desktop), not a
   front panel, hub or dock (FAQ.md).
8. HTTP-stream mode is the default after every restart; other modes last until restart.
9. Keep it software-only: no opening the speaker.
10. Never play sound on the speaker yourself (no test tones, no AirPlay/Spotify from a script). Ask
   the owner to play and watch /proc/asound status instead.

## Boot and operations
- Cable out: Wi-Fi and AirPlay in about 2 minutes; about 30 s of that is the cable check.
- ADB is open over Wi-Fi (port 5555, no authorization, root on request) on purpose: the owner
  wants to control the speaker from any computer on the home network. Don't disable it.
- After a crash, or a power-cycle with the cable in, the controller may route internal USB
  to the cable socket, so Wi-Fi won't start. Power-cycle with the cable OUT (sometimes 2-3x).
- RAM/bootloader images load over the cable; everything else over Wi-Fi.
- LG's app is disabled (pm disable com.lge.smartspeaker); shared userdata, so slot A too.
  Restore with pm enable com.lge.smartspeaker.
- moOde needs Loopback OFF in HTTP mode, or the Pi's jack goes silent.
- Android's bootctl is broken on this build; use fastboot --set-active=a|b over the cable.
- Slot B boots with SELinux permissive (boot_b header cmdline).
- No saved Wi-Fi (new install or factory reset): the speaker opens the open network "WK7-Setup"
  with a setup page at http://192.168.4.1/ (wk7-wifisetup); blue LED ring meanwhile. Saved
  networks live in Android's /data/misc/wifi/wpa_supplicant.conf. Verified end to end 2026-10-04.
  How it works and what not to change: wk7-build/stage1/README.md, "First-run Wi-Fi setup". In short:
  - The hotspot is a second interface (iw ... interface add softap0 type __ap) on the station-mode
    driver. Never write fwpath=ap (reloads the USB Wi-Fi chip, fails at boot) and don't call
    "svc wifi disable" during setup (unloads the driver).
  - Every call into Android (chroot /proc/1/root) must drop LD_PRELOAD ("env -u LD_PRELOAD" in shell,
    env without it in Python). wk7ctl preloads the chroot's getrandom shim, and Android's linker then
    refuses to run anything. This silently broke setup at boot until fixed.
  - Logs survive reboots: /var/log/wk7-wifisetup.log, /var/log/wk7-usbroute.log and .prev.log
    (the boot before), inside the chroot.
    To test setup with the cable in: touch /etc/wk7/setup-test, reboot; setup runs 4 minutes, then
    the speaker returns to the cable so adb over USB works again.
- Factory reset: hold play/pause until the ring turns red (~5 s), then press it again within 5 s
  (the MICOM can't report holds longer than its ~5 s long-press code). Clears Wi-Fi, name, settings, moOde
  config and the Spotify login, then reboots into setup. Never touches the firmware slots.

## Updating the speaker (full steps: wk7-build/stage1/README.md, "Updating the speaker's software")
- Our software lives in /data/wk7linux, so a normal update is a file copy plus `wk7ctl restart <service>`.
  No flashing and usually no reboot. Back up each replaced file on the speaker first.
- Wireless (default): `adb connect <speaker-ip>:5555`, `adb root`, `adb push` to
  /data/wk7linux/tmp, install inside the chroot via /data/wk7-enter.sh. Alpine packages go in with
  `apk add` (the speaker downloads them over Wi-Fi; ask the owner first).
- Wired (recovery): a computer on the mini-USB cable at power-on keeps the speaker on USB (Wi-Fi off);
  then the same adb steps over USB. Keep wired transfers small: big USB adb transfers crash it.
- Firmware slots (system_b, vbmeta_b, boot_b, active slot): only with the owner's typed go. Small
  slot-B writes from the running system over Wi-Fi adb, read back and verify; fastboot (cable)
  for RAM/bootloader images and `fastboot --set-active`. Never touch slot A.

## Where things are
- README.md, INSTALL.md — public overview and install steps (unlocked bootloader units only)
- WK7-software-for-unlocked-bootloader/wk7-build/stage1/ — all speaker-side software; README.md there has the
  full technical notes (read before changing anything)
- WK7-software-for-unlocked-bootloader/wk7-build/slotb/ — slot-B boot hook and the vbmeta/boot patch tool
- WK7-software-for-unlocked-bootloader/wk7-probe/ — REPORT.md (hardware), MICOM_PROTOCOL.md (buttons/LEDs)
- WK7-software-for-unlocked-bootloader/wk7-moode/ — moOde modes code and INSTALL.md
- Never commit LG firmware images, partition backups or decompiled LG code (.gitignore is an allowlist).

## Working style
Make changes small and testable; verify on the device before calling anything done. The
owner is often away from the speaker, so batch listening checks and don't ask for them
mid-build. Update wk7-build/stage1/README.md when behaviour changes.
