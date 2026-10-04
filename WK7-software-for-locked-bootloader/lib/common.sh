#!/usr/bin/env bash
# Shared helpers, device constants, partition table.
# Sourced by every script in this toolkit.

set -u
set -o pipefail

# --- Device identity (verified on a real unit, 2026-10-03) -------------------
DEV_MODEL="LG_WK7_Smart_Speaker"
DEV_PRODUCT="iot_msm8x09_lgproto"
DEV_SOC="msm8x09"          # APQ8009 / SDM212
DEV_SERIAL_PREFIX="2132083b"

# USB vendor/product IDs seen in the field:
USB_ADB="05c6:901d"        # Qualcomm, normal/adb mode
USB_FASTBOOT="18d1:d00d"   # Google fastboot
USB_EDL="05c6:9008"        # Qualcomm EDL / 9008 (QDL)
USB_EDL_ALT="05c6:901d"    # some units report this ID in EDL

# In-kernel eMMC device + by-name symlink dir
EMMC="mmcblk0"
BY_NAME="/dev/block/platform/soc.0/7824900.sdhci/by-name"

# Project root = parent of lib/. Derived from this file so the toolkit works
# regardless of where it is cloned or which directory you invoke scripts from.
PROJ_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Backups and probe state live INSIDE the project, and are gitignored.
# backup/ holds verbatim LG firmware images (abl, boot, system_b, ...) which
# are LG-copyrighted and must never be committed or uploaded.
WORKDIR="${WORKDIR:-$PROJ_ROOT}"
BACKUP_DIR="$WORKDIR/backups"
STATE_DIR="$WORKDIR/state"

mkdir -p "$BACKUP_DIR" "$STATE_DIR"

# name:partition:size_kb  -- real table read from /proc/partitions on a live unit
PARTITIONS="
modem_a:mmcblk0p1:65536
modem_b:mmcblk0p2:65536
sbl1_a:mmcblk0p3:512
sbl1_b:mmcblk0p4:512
aboot_a:mmcblk0p5:1024
aboot_b:mmcblk0p6:1024
rpm_a:mmcblk0p7:512
rpm_b:mmcblk0p8:512
tz_a:mmcblk0p9:768
tz_b:mmcblk0p10:768
pad:mmcblk0p11:1024
modemst1:mmcblk0p12:1536
modemst2:mmcblk0p13:1536
misc:mmcblk0p14:1024
fsc:mmcblk0p15:1
ssd:mmcblk0p16:8
DDR:mmcblk0p17:32
fsg:mmcblk0p18:1536
sec:mmcblk0p19:16
boot_a:mmcblk0p20:32768
boot_b:mmcblk0p21:32768
system_a:mmcblk0p22:524288
system_b:mmcblk0p23:524288
vbmeta_a:mmcblk0p24:64
vbmeta_b:mmcblk0p25:64
vendor_a:mmcblk0p26:65536
vendor_b:mmcblk0p27:65536
oem_a:mmcblk0p28:262144
oem_b:mmcblk0p29:262144
oem_bootloader_a:mmcblk0p30:4096
oem_bootloader_b:mmcblk0p31:4096
factory:mmcblk0p32:32768
factory_bootloader:mmcblk0p33:16384
userdata:mmcblk0p34:2184980
cmnlib_a:mmcblk0p35:1024
cmnlib_b:mmcblk0p36:1024
keymaster_a:mmcblk0p37:1024
keymaster_b:mmcblk0p38:1024
devinfo:mmcblk0p39:1024
keystore:mmcblk0p40:512
config:mmcblk0p41:512
CDT:mmcblk0p42:1
persist:mmcblk0p43:32768
gapps:mmcblk0p44:3440647
"

# Partitions worth backing up before any modification.
# A/B pairs: back up the ACTIVE slot; the inactive one is a fallback.
CRITICAL="aboot_a aboot_b boot_a boot_b sbl1_a sbl1_b devinfo vbmeta_a vbmeta_b config CDT persist factory factory_bootloader oem_bootloader_a oem_bootloader_b keymaster_a keymaster_b modem_a modem_b fsc fsg sec"
# Never back up: userdata (huge, PII), gapps (huge), system/vendor/oem (recoverable from OTA)

c_red=$'\033[31m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'
c_cyn=$'\033[36m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
log()  { printf '%s[*]%s %s\n' "$c_cyn" "$c_off" "$*"; }
ok()   { printf '%s[+]%s %s\n' "$c_grn" "$c_off" "$*"; }
warn() { printf '%s[!]%s %s\n' "$c_ylw" "$c_off" "$*"; }
err()  { printf '%s[-]%s %s\n' "$c_red" "$c_off" "$*" >&2; }
die()  { err "$*"; exit 1; }

need_root() { [[ $EUID -eq 0 ]] || die "must run as root (sudo)"; }

# device_present <vid:pid>  -- true if that USB id is on the bus
device_present() {
  lsusb 2>/dev/null | grep -qi "$1"
}

# which_mode -> prints: adb | fastboot | edl | none
which_mode() {
  if device_present "$USB_EDL" || device_present "$USB_EDL_ALT"; then
    # 05c6:901d is ambiguous; disambiguate by adb presence
    if adb devices 2>/dev/null | grep -q "device$"; then echo adb; else echo edl; fi
  elif device_present "$USB_FASTBOOT"; then
    echo fastboot
  elif adb devices 2>/dev/null | grep -q "device$"; then
    echo adb
  else
    echo none
  fi
}

# adb_sh <cmd...> -- run a shell command, bail on device loss
adb_sh() {
  timeout 30 adb shell "$@" 2>&1
}

# adb_root -- attempt adb root and report whether we actually got uid=0
adb_root() {
  timeout 20 adb root >/dev/null 2>&1
  sleep 2
  timeout 30 adb wait-for-device >/dev/null 2>&1
  local id
  id="$(timeout 20 adb shell id 2>/dev/null)"
  if [[ "$id" == uid=0* ]]; then ok "root shell: $id"; return 0; fi
  warn "no root (shell only): $id"; return 1
}

require_device() {
  local m; m="$(which_mode)"
  [[ "$m" == none ]] && die "no device on USB. Check: rear motherboard port (NOT front panel), known-good data cable, device powered."
  log "device mode: $m"
}
