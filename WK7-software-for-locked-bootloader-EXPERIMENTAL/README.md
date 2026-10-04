# WK7 software for LOCKED bootloaders — experimental

> ## ⚠ EXPERIMENTAL — UNTESTED ON ANY LOCKED DEVICE
>
> **No WK7 with a locked bootloader has ever been tested with these tools.**
> Every hardware observation here comes from a unit whose bootloader was already
> unlocked. See [docs/locked.md](docs/locked.md) for exactly what is known,
> what is inferred, and what is unknown.

A parallel toolkit for WK7 owners whose bootloader is **locked** and who cannot
unlock it. The main project targets unlocked units; this folder exists because
a locked unit can still run the software stack — just not the firmware.

## What this can and cannot do

| | Unlocked unit | Locked unit |
|---|---|---|
| Install the stack into `/data` | Yes, verified | Untested, probably yes |
| AirPlay 2 / Spotify / moOde / dashboard | Yes, verified | Untested, probably yes |
| Auto-start at power-on | Yes | **No** |
| Slot B firmware work | Yes | **No** |

The stack lives in `/data`. The bootloader lock governs `fastboot`, not
block-device writes, so root access is the only real requirement.

Auto-start is impossible while locked: it needs a modified `vbmeta_b`, which a
locked bootloader refuses to accept. Start the stack manually instead.

## Scripts

| Script | Purpose | Needs device root | Destructive |
|---|---|---|---|
| `install-locked.sh` | Install the stack; writes **only** to `/data` | yes | no |
| `00_probe.sh` | Identify device, mode, unlock state, build posture | no | no |
| `01_backup.sh` | Dump critical partitions | yes | no |
| `02_restore.sh` | Write partitions back (brick recovery) | yes | **YES** |
| `03_flash_adb_root.sh` | Flash boot/system without fastboot | yes | **YES** |
| `04_edl_enter.sh` | Enter EDL 9008 mode | n/a | no |
| `05_edl_firehose.sh` | Flash over EDL (needs LG-signed loader) | n/a | **YES** |
| `exploits/00_triage.sh` | Enumerate escalation options | no | no |
| `exploits/01_generic_escalation.sh` | Writable-path escalation chain | partial | **YES** |
| `exploits/02_cve_scout.py` | Map kernel version to CVE candidates | no | no |

`install-locked.sh` and `00_probe.sh` are read-only or `/data`-only and safe to
run. Destructive scripts require typing the literal word `YES`.

## Quick start

```bash
cd WK7-software-for-locked-bootloader-EXPERIMENTAL

# Always start here. Read-only, works as a plain ADB shell, no root needed.
./install-locked.sh <speaker-ip> --probe

# If the probe reports root available:
./install-locked.sh <speaker-ip> --stage1 /path/to/stage1

# Start the stack (required after every power-on while locked):
./install-locked.sh <speaker-ip> --start --stage1 /path/to/stage1
```

`--probe` tells you which of three rungs you are on: ADB reachable, root
available or not, and what to do next. On a locked unit it is almost always the
only step you can run.

## Known hazards

**Other devices answer on port 5555.** A Google Home on the same network will
respond to `adb connect` just as readily as the speaker. Both `install-locked.sh`
and the main project's `install.sh` verify `ro.product.model` before writing
anything. Do not remove that check.

**Unlocked units should use the other folder.** If
`getprop ro.boot.vbmeta.device_state` returns `unlocked`, use
`WK7-software-for-unlocked-bootloader/` — it is better tested, supports
auto-start, and has the full feature set including slot B firmware work.

**No kernel exploit is included.** `exploits/` contains a triage pipeline and a
CVE shortlist, not a working privilege-escalation PoC. See
[docs/KERNEL.md](docs/KERNEL.md) for why, and for the prerequisites any real
attempt would need.

## Documentation

- [docs/locked.md](docs/locked.md) — the locked-device path, what is verified, the blocking unknown
- [docs/EDL.md](docs/EDL.md) — EDL 9008 and the firehose loader requirement
- [docs/KERNEL.md](docs/KERNEL.md) — escalation triage and CVE shortlist
- [lib/common.sh](lib/common.sh) — device constants and the verified partition table

## Backups

Backups are written to `backups/` inside this folder and are gitignored. That
directory holds verbatim LG firmware images (`aboot`, `boot`, `system_b`,
`vbmeta`, …), which are LG-copyrighted and must never be committed or uploaded.
