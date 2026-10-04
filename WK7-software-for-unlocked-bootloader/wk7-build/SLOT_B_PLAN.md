# Slot-B firmware plan (for review — nothing flashed yet)

## Goal
Speaker boots by default into "our" firmware on slot B: AirPlay 2 (AirPlay 1 switchable), MICOM buttons/LEDs, no laptop needed. LG's original firmware stays untouched on slot A as the fallback.

## Recommended approach: Phase 2a — "Android-hosted" slot B
Slot B keeps LG's kernel *and* LG's Android underneath (exactly the environment we tested all evening), plus a small boot hook that starts our Linux stack automatically.

Why this first:
- It is the environment that already works: Wi-Fi, the audio DSP, firmware loading, modem/ADSP storage daemons (rmt_storage etc.) are all brought up by LG's Android, as today.
- Very small change: a few files added to `system_b`, one flag changed in `vbmeta_b`. No kernel or bootloader changes.
- Our Linux stack stays in `/data/wk7linux` (shared by both slots), so updating our software never needs reflashing.

A later Phase 2b ("pure Linux", no Android at all) is possible but needs several Android hardware daemons replaced or carried along — much more work and risk. Not proposed now.

## What gets written (all to the inactive slot B; slot A untouched)
1. `system_b` (512 MB): mount read-write from the running slot-A system and add
   - `/system/etc/init/wk7.rc` — an init service that runs after boot completes
   - `/system/bin/wk7-boot.sh` — the boot hook. It:
     - marks slot B as successfully booted (`bootctl mark-boot-successful`), so the bootloader keeps using it
     - stops the LG pieces that conflict: LG speaker app, Android's mdnsd, peripheral manager (Cast/Assistant are off in our firmware)
     - enters `/data/wk7linux` and runs `wk7ctl start all` (AirPlay per settings, buttons, later the dashboard)
     - turns on a distinct LED pattern as our "boot signature"
2. `vbmeta_b`: set the "hashtree disabled" flag. Required because changing `system_b` breaks LG's dm-verity hash; without this flag slot B would refuse to mount. (Device is already unlocked/orange, so the bootloader honours it.)
3. Active slot → B (`bootctl set-active-boot-slot 1`).

All writes are done over Wi-Fi from the running slot-A system (no big USB transfers — that is where the crashes happened). Size of the actual change: a few KB.

## Safety nets
- Slot A = untouched LG firmware. If slot B fails to boot, the bootloader's retry counter falls back to A automatically (B is only marked "successful" by our hook once it is up).
- Manual switch back any time: `bootctl set-active-boot-slot 0` (from adb) or `fastboot --set-active=a`.
- Verified, bit-exact backups of `system_b`/`vbmeta_b` (and everything else) on the Mac to restore B exactly.

## How you'll know which firmware booted
- Ours: AirPlay "LG WK7" + distinct LED boot pattern; no Google Cast.
- LG: Google Cast present, no AirPlay.
- (Dashboard/status page comes after, per your earlier decision.)

## Risks / unknowns
- SELinux (enforcing): the boot service needs permission to run our chroot. Mitigation: run it in the userdebug `su` domain; if init refuses, slot B still boots LG's Android normally (no harm) and I adjust.
- Bootloader behaviour with the hashtree-disabled flag is standard AVB but untested on this unit; failure mode = slot B fails to mount → automatic fallback to A.
- MICOM USB routing after reboots (seen today): power-cycle if Wi-Fi doesn't come up.

## Steps, each confirmed with you
1. Prepare files on the Mac, show them to you.
2. Write them into `system_b` + flip the `vbmeta_b` flag (over Wi-Fi).
3. Verify by reading back (md5 / file listing).
4. Set active slot B and reboot — **needs your explicit go**.
5. Check: boots, joins Wi-Fi, AirPlay appears, buttons work. If anything is off → back to A.
