# EDL 9008 + Firehose

The only route to a genuinely hard-locked WK7 that does not depend on the
destroyed ATX credential.

## Why EDL is different

Normal fastboot runs *inside* the Android bootloader (`aboot`), which is
where the lock lives. EDL (Emergency Download Mode, a.k.a. 9008) is
implemented in the SoC boot chain *below* the bootloader — it is entered
before `aboot` ever executes. The bootloader cannot prevent it because it
is not running yet.

That is the whole reason EDL is the interesting path. It also means EDL is
dangerous: it can write `sbl1` and `tz`, and a mistake there is a hard brick.

## Requirements

1. Device in EDL mode (`05c6:9008`) — see `../04_edl_enter.sh`
2. **An LG-signed SDM212 firehose programmer file**
3. `qdl` installed

## The firehose loader — the real blocker

EDL mode by itself grants nothing. Once in 9008, the host must upload a
*programmer* — an ELF blob (`prog_firehose_lge_*.mbn`) signed by LG — and
the SoC will only execute a correctly signed one. This is the same
requirement as Qualcomm's standard 9008 flow used by QFIL, and it is
firmware-signed by the vendor.

Typical naming, from LG/Qualcomm QDL packages:
- `prog_firehose_lge_ddr.mbn`
- `prog_firehose_lge_lge_ddr.mbn`

**We do not have one, and it cannot be fabricated.** The signature is the
whole point. If you cannot source an LG SDM212 loader, EDL mode is a dead
end and you should say so rather than burning time on it.

### Where loaders surface

- LG firmware/QDL packages for other SDM212 devices (if any are public)
- Qualcomm "firehose" collections that include LGE targets
- Vendor GPL release tarballs, if LG shipped one for this model
- Device-specific forum attachments

**If someone hands you a loader, treat it as untrusted.** It is arbitrary
code that runs with full hardware access before any OS loads.

### When EDL will not work at all

Some production units have EDL disabled in fuses, or the button combo is
damped. If `04_edl_enter.sh` never sees `05c6:9008` after several cold-boot
attempts, assume EDL is unavailable on that unit.

## Install qdl

```bash
pipx install qdl          # or: cargo install qdl
```

## Workflow

### 1. Enter EDL

```bash
sudo ./04_edl_enter.sh
```

Cold-boot each attempt: unplug from wall power, hold the keys, apply power.
Use a **rear motherboard USB port** — the front panel will not enumerate.

### 2. Probe the loader

```bash
sudo ./05_edl_firehose.sh --loader ./loaders/prog_firehose_lge_ddr.mbn --info
```

This uploads the programmer and reads device info. Read the log at
`state/edl_info.log`.

If it fails: wrong SoC target (SDM212 vs SDA212), secure-boot fuses blown
(only OEM loaders accepted), or wrong memory type flag.

### 3. Back up

```bash
sudo ./05_edl_firehose.sh --loader ./loaders/... --backup
```

Writes to `backups/<timestamp>/`.

### 4. Flash

```bash
sudo ./05_edl_firehose.sh --loader ./loaders/... --flash-boot boot.img --slot b
sudo ./05_edl_firehose.sh --loader ./loaders/... --wipe-vbmeta --slot b
```

Write the **active** slot. Check with `fastboot getvar current-slot` first,
or read `/proc/cmdline` for `androidboot.slot_suffix=_b`.

## Memory type

qdl needs the right target. For eMMC:

```bash
qdl --loader <loader> --memory=eMMC ...
```

If you see loader timeouts, try `--memory=ufs` — some Qualcomm parts are
mislabeled in their own programming guides.

## Recovery

EDL is below the bootloader, so it survives essentially any bad flash. If the
device will not boot at all, EDL is how you get it back. This is EDL's
practical virtue even when the bypass goal fails.

## Safety

- Back up before writing. Every time.
- Do not write `sbl1_*`, `tz_*`, `rpm_*` unless you have no other option
  and a verified backup of those exact partitions.
- `rawprogram` XML mistakes (wrong `NUM_SECTORS`, wrong offset) can write
  across partition boundaries. Compute sectors as `KB * 2` at 512 B/sector.
- `05_edl_firehose.sh` prompts for the literal word `YES`. Keep it that way.
