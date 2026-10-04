# AGENTS.md — locked-bootloader toolkit

Scope: this file applies to `WK7-software-for-locked-bootloader/` only. For the
main (unlocked) project see `../WK7-software-for-unlocked-bootloader/wk7-build/AGENTS.md`.

## What this is

Diagnostic and flashing tooling for WK7 units whose bootloader is **locked**.
Parallel to the main project; nothing in the unlocked path is modified or
duplicated here.

## Read this first

**Nothing here has been tested on a locked device.** All hardware observations
come from one already-unlocked unit (serial `2132083b`, 2026-10-03). The
locked-device claims are inference resting on one unconfirmed assumption:
that `adb root` is permitted while locked. See `docs/locked.md`.

## Rules you must not break

1. **Never claim a locked-device result that was not observed.** The single most
   important rule here. This folder's value is that it is honest about its own
   uncertainty; a confident-sounding claim about an untested device is worse
   than no claim.
2. **Never write to a firmware partition from this folder's tooling** except via
   `02_restore.sh` or `03_flash_adb_root.sh`, both of which require the literal
   word `YES`. `install-locked.sh` writes only to `/data` and must stay that way.
3. **Never remove the device-identity check.** Another device on the network
   will answer adb on 5555 — a real Google Home did during testing. Verify
   `ro.product.model` before writing.
4. **Do not ship a kernel exploit.** `exploits/` is a triage pipeline and a CVE
   shortlist. Do not add an unverified privilege-escalation PoC: it cannot be
   validated against this build, and a bad one can wedge consumer hardware.
   `01_generic_escalation.sh` deliberately stops when preconditions are unmet.
   Keep that behaviour.
5. **Do not touch early-boot partitions.** `sbl1_*`, `tz_*`, `rpm_*` are
   effectively unrecoverable if written wrongly on a locked device.
6. **Back up before writing.** `01_backup.sh` first, always.

## Verified facts

From the one unlocked unit:

- Front-panel USB headers do **not** enumerate the device; the rear
  motherboard port does, instantly. Zero USB events were logged on the front
  panel — not even a failed handshake.
- USB IDs: ADB `05c6:901d`, fastboot `18d1:d00d`, EDL `05c6:9008`
- ADB is compiled into the boot default (`ro.sys.usb.default.config=diag,adb`),
  not exposed through a Settings toggle. Stock `user` builds ship with ADB
  disabled by default; this build does not.
- `ro.build.type=userdebug`, `ro.build.tags=test-keys`, `ro.debuggable=1`
- `ro.boot.verifiedbootstate=orange`, `ro.boot.vbmeta.device_state=unlocked`
- `androidboot.veritymode=disabled`, `androidboot.selinux=permissive`
- eMMC at `7824900.sdhci`; by-name dir
  `/dev/block/platform/soc.0/7824900.sdhci/by-name`
- 44 partitions, A/B
- `console=ttyHSL0,115200,n8` — a UART console exists but needs the housing open

## State: what works, what does not

WORKS: device probing, partition backup/restore, `dd`-based flashing via an adb
root shell, fastboot inspection, escalation triage, the `/data` installer.

DOES NOT WORK / UNVERIFIED:
- Unlocking a locked unit. The Android Things Console that issued LG's unlock
  credentials was permanently deleted on 2022-01-05.
- EDL flashing. Requires an LG-signed SDM212 firehose loader; none included and
  one cannot be synthesised.
- Kernel exploit. Intentionally not shipped.
- Auto-start at power-on while locked. Structurally impossible.

## Conventions

- Bash for shell tooling; `source lib/common.sh` for shared state
- Device constants live only in `lib/common.sh`
- Partition sizes in the `PARTITIONS` table must match the verified table
- Destructive scripts warn, then require the literal word `YES`
- Comments explain *why*. Note the date when a value came from probing hardware
- `|| true` on command substitutions that may legitimately return non-zero;
  under `set -e` they will otherwise abort the script silently

## Testing

Safe and read-only:

```bash
./install-locked.sh <ip> --probe
./00_probe.sh
./exploits/00_triage.sh
python3 exploits/02_cve_scout.py
```

Do not exercise `02_restore.sh`, `03_flash_adb_root.sh` or
`05_edl_firehose.sh` without an existing backup.

## Tone

The documentation opens by stating what is untested. Keep it that way. Readers
here own locked consumer hardware with no vendor support; accurate expectations
are more useful than encouragement that papers over dead ends.
