# LG WK7 ThinQ — read-only hardware probe (2026-10-01)

All data gathered via passive USB enumeration and unprivileged `adb shell` (uid 2000). Nothing written to the device. The raw probe outputs are not published (they contain device serials and network details).

## Summary

| Item | Value | Source |
|---|---|---|
| SoC | **Qualcomm APQ8009** (Snapdragon 212 "Home Hub" class; MSM8909 family without modem RF), platform `msm8x09` | /proc/cpuinfo, ro.board.platform |
| CPU | 4× ARM Cortex-A7 (part 0xc07 r0p5), ARMv7-A 32-bit only, NEON/VFPv4, max 1.19 GHz (thermal steps 998/533 MHz seen) | cpuinfo, dmesg |
| GPU | Adreno 304 (a300 firmware, kgsl-3d) | /system/etc/firmware |
| PMIC | PM8916 v2.0.1 | dmesg |
| RAM | **768 MB** physical (692 MB visible to Linux) | iomem, meminfo |
| Storage | eMMC **SK Hynix "H8G4a2" 8 GB** (7.28 GiB), HS200, on sdhci@7824900, 4 MB RPMB, GPT with 44 partitions | dmesg, /proc/partitions |
| Kernel | Linux **3.10.49**-gea360f739fe, SMP PREEMPT, GCC 4.9, built 2017-11-17 by Google | /proc/version |
| OS | Android Things **0.4.5-N** (Android 7.0, API 24, NIJ48, build 4457256), security patch 2017-10-05 | getprop |
| Build type | **userdebug / test-keys**, `ro.debuggable=1`, product `iot_msm8x09_lgproto` | getprop |
| Verified boot | AVB 1.0, `ro.boot.vbmeta.device_state=unlocked`, `ro.boot.verifiedbootstate=orange` (bootloader **unlocked**), dm-verity enforcing on system | getprop, dmesg |
| A/B | Yes, `ro.boot.slot_suffix=_a`, `ro.build.ab_update=true`, system-as-root | getprop |
| SELinux | Enforcing | getenforce |
| Wi-Fi | **Qualcomm QCA9377** over USB (qcacld-2.0 `wlan` driver v4.5.50.11, `hif_usb`; cfg file `qca_cld/WCNSS_qcom_cfg.ini`) | dmesg, firmware dir |
| Bluetooth | Same QCA ROME combo chip over USB (`qcom.bluetooth.soc=rome_usb`, `bt_usb`) | getprop, dmesg |
| Audio | ADSP (Q6, APR v2.8) + `msm8x09-ext-codec-snd-card`; machine driver `msm8x16-asoc-wcd`, codec modules `tasha` (WCD9335/9326) + `wcd9xxx`; **WSA881x** smart-amp driver built in; MI2S/SLIMbus routes, 24-bit/96 kHz deep-buffer playback | asound, dmesg, /sys/module |
| USB | Single ChipIdea OTG controller `msm_otg 78d9000.usb`, currently in **peripheral** mode (composition `diag,adb`, Qualcomm VID 05c6:901d) | dmesg, getprop |
| Inputs | `qpnp_pon` (power key), `gpio-keys` (buttons) | /proc/bus/input/devices |
| Other | QSEE 4.5 (qseecom), `rmt_storage`, modem PIL (q6v5-mss) loaded, camera/NFC nodes from reference DT (unused/failing) | dmesg |

## Root-level findings (adb root, read-only; adbd returned to non-root afterwards)

- **SoC confirmed:** `soc_id 265` = APQ8009, revision 2.0, foundry 1, raw_id 2408. `hw_platform: Dragon`, subtype `charm` (Qualcomm reference board ID — the kernel uses a near-stock 8909 reference DT; the I²C sensors listed below are DT leftovers with no driver bound).
- **Boot chain:** `BOOT.BF.3.1.2.C2-00043`, image `8909A-DAASANAZA-40000000`. PMIC model 65547 (PM8916), die rev 2.0.
- **RAM:** /proc/iomem System RAM 0x80000000–0x879FFFFF and 0x8A500000–0xAF7FFFFF (top of DRAM ≈ 0xB0000000) → **768 MB physical** (gap 0x87A00000–0x8A4FFFFF = modem/ADSP/TZ carve-outs).
- **eMMC:** SK Hynix (manfid 0x90, OEM 0x014A), name H8G4a2, PRV 0xA5. The board serial is the eMMC serial (from the CID).
- **Wi-Fi/BT chip: Qualcomm QCA9377** ("ROME 3.0"/Naples). Firmware in modem_a: `qwlan30.bin`/`athwlan.bin`, `bdwlan30.bin` (board data), `otp30.bin`, `utf30.bin`, BT `btfwnpla.tlv`/`btnvnpla.bin` (npl = Naples = QCA9377), attached over USB.
- **Kernel cmdline:** `console=ttyHSL0,115200,n8 earlyprintk androidboot.hardware=msm8x09 ... skip_initramfs rootwait ro init=/init root=/dev/dm-0 androidboot.verifiedbootstate=orange androidboot.vbmeta.device_state=unlocked androidboot.slot_suffix=_a` → **UART console on ttyHSL0 at 115200 8N1** (BLSP UART @ 0x78AF000) — find its test pads on the PCB for a serial console.
- **I²C:** bus 1: ak8963, mmc3416x, apds9900, mpu6050 (no driver bound — reference DT); bus 3: camera/actuator/led-flash/eeprom (reference DT, eeprom probe fails). Buses 2, 4, 5: nothing enumerated. No SLIMbus/SoundWire devices enumerated → audio output is most likely I²S (MI2S) to an external amplifier rather than a WCD codec; amp chip not identifiable from software — needs PCB inspection.
- **LEDs:** only `torch-light0` in /sys/class/leds; the ring LED animation is driven from userspace (`LedAnimation` app), probably via SPI (`spi_qsd`/spidev) or GPIO.
- **USB gadget:** `diag,ffs`, iProduct "Android".

## Partition table (from /dev/block/bootdevice/by-name)

| Part | Name | Size |
|---|---|---|
| p1 / p2 | modem_a / modem_b | 64 MB (vfat; DSP + WLAN/BT firmware) |
| p3 / p4 | sbl1_a / sbl1_b | 512 KB |
| p5 / p6 | aboot_a / aboot_b | 1 MB (LK bootloader / fastboot) |
| p7 / p8 | rpm_a / rpm_b | 512 KB |
| p9 / p10 | tz_a / tz_b | 768 KB |
| p11 | pad | 1 MB |
| p12 / p13 | modemst1 / modemst2 | 1.5 MB |
| p14 | misc | 1 MB |
| p15 | fsc | 1 KB |
| p16 | ssd | 8 KB |
| p17 | DDR | 32 KB |
| p18 | fsg | 1.5 MB |
| p19 | sec | 16 KB |
| p20 / p21 | boot_a / boot_b | 32 MB |
| p22 / p23 | system_a / system_b | 512 MB |
| p24 / p25 | vbmeta_a / vbmeta_b | 64 KB |
| p26 / p27 | vendor_a / vendor_b | 64 MB |
| p28 / p29 | oem_a / oem_b | 256 MB |
| p30 / p31 | oem_bootloader_a / _b | 4 MB |
| p32 | factory | 32 MB |
| p33 | factory_bootloader | 16 MB |
| p34 | userdata | 2.1 GB |
| p35 / p36 | cmnlib_a / cmnlib_b | 1 MB |
| p37 / p38 | keymaster_a / keymaster_b | 1 MB |
| p39 | devinfo | 1 MB (holds unlock state) |
| p40 | keystore | 512 KB |
| p41 | config | 512 KB |
| p42 | CDT | 1 KB |
| p43 | persist | 32 MB |
| p44 | gapps | 3.4 GB (unmounted) |
| rpmb | — | 4 MB |
## Notable observations

1. **Unlocked, debuggable build.** The bootloader reports `unlocked`/`orange`, the OS is a userdebug test-keys Android Things build. That's very favourable for replacement firmware — custom boot images should be accepted without exploits. (`adb root` would very likely work on a userdebug build.)
2. **Wi-Fi/BT fail to initialise while a computer is connected.** dmesg shows `hdd_hif_register_driver: HIF registration failed` and `bt_usb: no device probed yet`, repeated. The Wi-Fi/BT chip is a USB device, and the SoC has only one USB controller, which is currently in peripheral mode toward the computer. Hypothesis: the board muxes the single USB port between the internal QCA chip (host mode) and the mini-USB connector (device mode). Any new firmware must handle that switch.
3. Device tree isn't exposed at /proc/device-tree (old 3.10 kernel/permissions); `/proc/cmdline`, `/sys/devices/soc0/*`, eMMC CID and `/dev/block/by-name` all need root.


## Audio / peripheral architecture (from LG's Android userspace, pulled read-only)

**Audio path (what LG's HAL actually does for music):**
- ALSA card 0 `msm8x09-ext-codec-snd-card` doesn't match any card name the HAL knows (`msm8x09-tasha-snd-card` etc.), so the HAL falls back to `/system/etc/mixer_paths.xml`.
- Music ("deep-buffer-playback") route is a single control: `PRI_MI2S_RX Audio Mixer MultiMedia1 = 1` → PCM device 0 (MultiMedia1) through the ADSP to **Primary MI2S (I²S)** → external amplifier.
- Format: 24-bit (S24_3LE) at 96 kHz enabled by `persist.deepbuf_24b96kHz.enable=true`; `MI2S_RX Channels = Two`.
- Service `tinyhostless -D 0 -P 5` (init) permanently opens PCM 5 "Primary MI2S_RX Hostless" — this keeps the I²S clocks running so the amp never loses lock. Must be replicated.
- Speaker protection off (`persist.speaker.prot.enable=false`).
- EQ/tuning: LG app `DAPP_WK5` ships `libpostprocessing.so` (post-processing effect, likely the Meridian tuning). A new OS loses this unless reimplemented (e.g. with CamillaDSP / ALSA EQ).

**LG "MICOM" microcontroller over UART** (the square TQFP on the board):
- LG app `com.lge.smartspeaker.services.iocontroller.Micom` talks to it via Android Things `UartManager` on **`UART2` = `/dev/ttyHSL1`**, plus GPIOs **GPIO_22, GPIO_66, GPIO_67** (likely reset/boot/IRQ).
- It handles: buttons (`CMD_MUTE_BUTTON_PRESSED`, play/pause/forward/back), LEDs (`CMD_SET_FUNCTION_LED`, `..._WITH_TIME`), mic mute, beeps, volume (`ExternalVolumeControllerImpl` — volume appears to be applied by the MICOM, probably to the amp), BT/Wi-Fi state, sysinfo, and its own firmware update (`CMD_WRITE/VERIFY/BOOTSWAP`).
- MICOM firmware is in the app: `M17111305_DV_USB_Mode.hex` (LG's file, not included here). "USB_Mode" hints the MICOM also controls the USB mux — consistent with Wi-Fi/BT dropping out when the mini-USB is used.
- **Implication:** to get buttons, LEDs and probably volume/amp-enable on a new OS we need to reimplement this UART protocol. Decompiling the Micom class (e.g. with jadx) will give the exact frame format and baud rate.

**Wi-Fi:** stock `wpa_supplicant`/`wpa_cli`/`hostapd`/`iw` present; config template `/system/etc/wifi/wpa_supplicant.conf`; Android Things `WifiSetup`/`IoTWifiService` apps do provisioning.
