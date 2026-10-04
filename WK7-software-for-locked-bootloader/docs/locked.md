# LOCKED bootloader units — the `/data` path

> ## ⚠ EXPERIMENTAL — UNTESTED ON ANY LOCKED DEVICE
>
> **Nothing here has been tested on a speaker with a locked bootloader.
> No one has done that.**
>
> Every hardware observation behind this folder comes from **one** WK7 whose
> bootloader was **already unlocked** (serial `2132083b`, probed 2026-10-03).
> The locked-device guidance below is **inference**, not verified fact. It rests
> on one unconfirmed assumption — that `adb root` is permitted while the
> bootloader is locked. If that is wrong, nothing here works.
>
> You would be the first person to try it. Back up first.

This folder sits **parallel to** `WK7-software-for-unlocked-bootloader/`. Nothing
in the unlocked path is modified or duplicated; the two are independent.

---

## TL;DR

| | Unlocked unit | Locked unit |
|---|---|---|
| Install the software stack into `/data` | Yes, verified | **Untested**, probably yes |
| AirPlay 2, Spotify Connect, moOde, dashboard, buttons, LED | Yes, verified | Untested, probably yes |
| Auto-start at power-on | Yes | **No** |
| Manual start after each power-on | Not needed | **Required** |
| Slot B firmware work | Yes | **No** |

Why most of it should work: **the entire stack lives in `/data`**, and the
bootloader lock governs `fastboot`, not writes to `/data`. Root can write block
devices regardless of lock state.

Why auto-start does not: it requires a modified `vbmeta_b` and a changed
`system_b`, which a locked bootloader correctly refuses.

---

## What is verified vs. what is not

| Claim | Status |
|---|---|
| `/data` is a writable partition on this hardware | **Verified** (real unit) |
| `adb root` works on an *unlocked* unit | **Verified** (real unit) |
| AirPlay 2 / Spotify / moOde stack runs in an Alpine chroot in `/data` | **Verified** (author's own unit) |
| Bootloader lock does not gate writes to `/data` | **Inferred** — almost certainly true, untested |
| `adb root` works on a **locked** unit | **UNKNOWN — the blocker** |
| Auto-start at power-on without a laptop | **NOT POSSIBLE** on locked (see below) |

---

## A hazard found in testing: other devices answer on 5555

While testing, a **Google Home** on the same network
(`192.168.50.206`, fingerprint `google/pineapple/pineapple:3.79/...:user/release-keys`)
answered `adb connect` on port 5555.

A script that assumed "ADB reachable ⇒ it's the speaker" would have begun
pushing an Alpine rootfs at it.

`install-locked.sh` now verifies `ro.product.model` before touching anything
and refuses unless it matches `*WK7*`. Confirmed by test: the Google Home is
rejected, the real WK7 passes.

If you hit this, the fix is to find the speaker's actual IP:

```bash
adb disconnect <wrong-ip>
adb devices
# or scan the network for 5555 and check each responder's model
```

Your WK7 was found at `192.168.50.95` when this was written. That will differ
on other networks.

---

## The blocking unknown

The upstream `install.sh` begins with a hard requirement:

```sh
[ "$(A id -u | tr -d '\r')" = 0 ] || { echo "adb root failed (needs root access)"; exit 1; }
```

**Whether `adb root` is refused on a locked WK7 is unknown and untestable from
here.** No locked unit is available.

Why it might work: the properties that enable `adb root` —
`ro.debuggable=1`, `ro.build.type=userdebug`, `ro.build.tags=test-keys` — live
in the **system image**, not the bootloader. A locked unit running the same
image would present the same properties to the ADB daemon.

Why it might not: the bootloader gates device state before the kernel runs,
and some vendors propagate that gate into `adbd` behaviour. Android's
`ro.boot.flash.locked` property exists precisely so daemons *can* see and
respect lock state.

**If `adb root` is refused, nothing below works** — not the install, not
AirPlay, not anything. That is the single point of failure for this entire
document.

### Evidence that ADB is likely exposed on locked units too

On the tested (unlocked) unit, ADB is not a user setting — it is compiled in
as the boot default:

```
ro.sys.usb.default.config = diag,adb    ← default config, not a toggle
persist.adb.tcp.port     = 5555         ← WiFi ADB listener
init.svc.adbd            = running
service.adb.root         = 1
```

Stock Android `user` builds ship with ADB **disabled** by default. This build
does not. There is also no Settings toggle for it, because there is nothing
to toggle.

That is encouraging but not conclusive: a locked unit may ship a different
`system` image even if its bootloader is the same. The `--probe` mode exists
to settle this on real hardware in about fifteen seconds.

---

## What you CAN do on a locked unit (if `adb root` works)

Everything that only writes to `/data`.

The upstream installer's own comment:

```
# It only writes to /data (the Alpine chroot, /data/wk7-enter.sh);
# it never touches a firmware slot.
```

The bootloader lock governs `fastboot` — the `fastboot flashing unlock` and
`fastboot flash` entry points. Writing to `/data` over an ADB root shell
never goes near the bootloader. Different code path.

So on a locked unit, assuming `adb root` succeeds:

- ✅ Alpine chroot installed at `/data/wk7linux`
- ✅ `shairport-sync` + `nqptp` built on-device → **AirPlay 2**
- ✅ `spotifyd` → **Spotify Connect**
- ✅ moOde receiver + multiroom
- ✅ Web dashboard, LED ring, hardware buttons, captive-portal Wi-Fi setup
- ✅ All of it, running on LG's stock kernel and audio DSP

## What you CANNOT do on a locked unit

**Auto-start at power-on.** This is the one real loss.

The upstream Phase 2 plan (`SLOT_B_PLAN.md`) does this by writing an init hook
into `system_b` and flipping the hashtree-disabled flag in `vbmeta_b`. Its own
text states the dependency:

> Required because changing `system_b` breaks LG's dm-verity hash; without this
> flag slot B would refuse to mount. **(Device is already unlocked/orange, so
> the bootloader honours it.)**

That parenthetical is the whole problem. On a locked unit `vbmeta_b` is
signature-verified by the bootloader. Change it and the signature breaks.
Change `system_b` without it and dm-verity breaks. Both roads lead to slot B
refusing to mount.

**This is not a challenge to overcome. It is the security model working
correctly.** There is no supported way to make a locked bootloader accept
modified system partitions.

### The workaround

Start the stack manually after each power-on:

```bash
adb -s <speaker-ip>:5555 shell '/data/wk7-enter.sh -c "wk7ctl start all"'
```

Put it in a shell script, a desktop shortcut, or a phone automation. Takes a
few seconds over WiFi ADB.

---

## Honest summary

| | Unlocked unit | Locked unit (assumed) |
|---|---|---|
| Install to `/data` | Verified works | Untested, likely works |
| AirPlay 2 + Spotify + moOde | Verified works | Untested, likely works |
| Dashboard, buttons, LED | Verified works | Untested, likely works |
| Auto-start at power-on | Verified (Phase 2) | **Not possible** |
| ADB reachable at all | Verified | **Likely yes** — see below |
| `adb root` | Verified works | **Unknown** |

If you are the first person to run this on a locked unit, **please report what
happened** — especially the `adb root` result. It converts this entire document
from speculation into knowledge.

---

## Trying it

### Step 0 — probe first (read-only, no root needed)

```bash
./install-locked.sh <speaker-ip> --probe
```

This is the step to run first on a locked unit, because it deliberately does
**not** attempt `adb root`. It works as a plain ADB shell and reports:

- build type / tags / `ro.debuggable` — determines whether root is even possible
- `ro.boot.flash.locked`, `verifiedbootstate` — the actual lock posture
- `ro.sys.usb.default.config` — whether ADB is baked in or user-enabled
- kernel version and SELinux mode — how hard the escalation will be
- `/data` mount state — whether the install will work once root is obtained

It then reports which of three rungs you are on and what to do next. It writes
nothing.

### Then install (only if the probe says root is available)

```bash
./install-locked.sh <speaker-ip> --stage1 <path-to-stage1>
./install-locked.sh <speaker-ip> --start --stage1 <path-to-stage1>
```

If the probe reports no root and a `user`/`release-keys` build, **stop.** The
remaining options are a kernel exploit, EDL with a signed loader, or UART —
see the probe output and `docs/KERNEL.md` / `docs/EDL.md`.

### Note on `offline`

The WK7's `adbd` over WiFi can enter `offline` — reachable once, then the
transport dies. Causes include an explicit `adb disconnect`, a reboot, or the
WiFi stack reinitialising. The script retries automatically; if it persists,
power-cycle the speaker. Note that an unreachable IP and a stale ADB entry are
indistinguishable from the host side.
