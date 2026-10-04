# Kernel Exploit Path

Linux 3.10, October 2017 patches, `userdebug` build. This document explains
what is realistic and, deliberately, does not hand you a working exploit.

## Why there is no PoC here

We could write a plausible-looking privilege-escalation exploit for this
kernel. We are not going to, for three concrete reasons:

1. **It cannot be verified.** We have a live unit and can observe its
   configuration, but we cannot safely iterate a kernel exploit against the
   user's hardware — a bad write or a crash-and-reboot loop can leave an
   8 GB eMMC device unrecoverable without EDL.
2. **It would be a guess dressed as a deliverable.** Unverified exploit code
   that looks authoritative is worse than no code: it wastes the reader's
   time and can damage their device.
3. **The cheap paths may not even be necessary.** On this class of build the
   generic escalation frequently works. Check that before reading this.

What we provide instead is a triage pipeline, a genuine generic escalation
chain, and a CVE shortlist with preconditions — so you can determine whether
this path is even worth taking.

## Run the triage first

```bash
sudo ./exploits/00_triage.sh
python3 exploits/02_cve_scout.py
```

`00_triage.sh` is read-only. It reports build flavour, SUID binaries,
SELinux state, writable mounts, `kptr_restrict`, `perf_event_paranoid`,
`modules_disabled`, and the loaded module list.

### Reading the results

| Signal | Meaning |
|---|---|
| `id` already `uid=0` | No exploit needed. Use `../03_flash_adb_root.sh`. |
| `/system` or `/vendor` mounted `rw` | Generic chain viable — go to section 3 |
| `getenforce` → `Permissive` | Much easier; SELinux is not fighting you |
| `kptr_restrict=0` | Kernel pointers leakable; many primitives become possible |
| `perf_event_paranoid` low | perf-based attack surface is open |
| `modules_disabled=0` | Module loading possible — but only *after* root, so no help alone |

The verified unit has `selinux=permissive` in the kernel cmdline. That is a
meaningfully weaker posture than enforcing. Other units may differ; trust
`00_probe.sh` output over this document.

## 1. Attack surface on this kernel

Linux 3.10 with 2017 patches means the CVE list is long and mostly
third-party-module or subsystem-specific. What matters is which of these are
reachable from an unprivileged shell:

- **Kernel modules loaded on the device** — from `/proc/modules` in the
  triage output. A vulnerable *loaded* module is a far better target than a
  vulnerable but unloaded one.
- **Filesystems mounted** — `mount` output. Stack overflows in filesystem
  code are the classic unprivileged path.
- **Netfilter** — if `CONFIG_NF_TABLES` is on and a local socket can reach
  it.
- **perf subsystem** — if `perf_event_paranoid` is permissive.

## 2. CVE shortlist

`02_cve_scout.py` prints this dynamically against the live kernel string.
The relevant candidates for 3.10/2017:

| CVE | Component | Precondition to verify |
|---|---|---|
| CVE-2016-5195 | `mm` / dirty cow | Classic, well-documented PoCs exist; needs the right mapping primitive |
| CVE-2017-7616 | `inet_set_abstract` / AF_UNIX | Reachable unprivileged; candidate |
| CVE-2016-8655 | Netfilter race | `CONFIG_NETFILTER` + local socket |
| CVE-2017-17840 | `nf_tables` | Needs `CAP_NET_ADMIN` — usually a dead end from `shell` |
| CVE-2017-1000253 | Various / Qualcomm | Applicability varies wildly by SoC |
| CVE-2015-7547 | glibc `getaddrinfo` | **Userland, not kernel** — but reachable via a network service |

Two entries are deliberately listed as **do not attempt** on 3.10
(`CVE-2017-16942`, `CVE-2018-17143`) so you do not waste time on
64-bit-only bugs. The scout marks them `skip`.

For any candidate, before attempting anything:
1. Confirm the affected subsystem is present and reachable
2. Confirm the config option is enabled (`/proc/config.gz` if available, or
   `/boot/config`)
3. Confirm the fix is actually absent for *this* build — patch level matters

## 3. Generic escalation (no CVE required)

This is the path most likely to succeed, and
`exploits/01_generic_escalation.sh` implements it.

**Precondition:** the device mounts `/system` or `/vendor` read-write, or
SELinux is permissive *and* a root-run service execs from a writable path.

**The chain:**
1. `adbd` runs as `shell` (uid 2000). Root-run init services are a
   different principal.
2. On a writable `/system` or `/vendor`, replace a binary or library that a
   root-privileged process loads or execs.
3. Trigger that process. It runs your code as uid 0.
4. From root: `chmod u+s` a shell, or drop a root adbd, or just write
   partitions directly.

**The script refuses to guess the target.** On a consumer speaker, firing an
untested payload at an unknown root service risks an unrecoverable state,
and the specific target binary for this LG build is not something we have
verified. It reports which preconditions hold and stops.

If the triage shows a writable mount, identify the exact target yourself:

```bash
adb shell 'grep -r "exec\|service" /system/etc/init/*.rc' 2>/dev/null
adb shell 'ls -la /system/xbin/ /vendor/bin/'
```

Look for a root service whose binary lives on a writable path.

## 4. Manual write test

If triage says `/system` is read-only but you suspect otherwise, test it
directly — harmless, because you write to an unused offset on a partition you
have backed up:

```bash
adb root
adb shell 'dd if=/dev/zero of=/dev/block/mmcblk0p22 bs=1 count=1 seek=0 conv=notrunc'
```

**Only do this after `01_backup.sh`.** If `/system` is genuinely read-only
via dm-verity, this fails with `Read-only file system`, which is your answer.

## 5. After you get root

You do not need an exploit to *use* root. Once you have `uid=0`:

```bash
sudo ./03_flash_adb_root.sh --boot boot.img --wipe-vbmeta
```

The bootloader lock governs fastboot only. Root writing block devices is a
different path entirely.

## Realistic assessment

Linux 3.10 (2017) on a `userdebug` build with permissive SELinux is a soft
target. If the generic chain is blocked, a kernel exploit is plausible but
will require real work — and the effort is better spent confirming you
actually need it. Run the triage, read the output, and decide from evidence
rather than assumption.
