# LG WK7 MICOM UART protocol (decompiled from LG app DAPP_WK5_171117)

Source: our own reading of LG's Micom class (com.lge.smartspeaker.services.iocontroller) with jadx and live tests on the speaker. LG's decompiled code is not included in this repository.

## Link
- Android Things UART = first entry of `getUartDeviceList()` → `UART2` = **`/dev/ttyHSL1`** (from peripheral_io.msm8x09.so)
- **115200 baud, 8 data bits, no parity, 1 stop bit**
- GPIOs exposed by the peripheral HAL: GPIO_22, GPIO_66, GPIO_67 (role not yet confirmed; LG's MICOM reset is done by UART command, not GPIO)

## Frame (host → MICOM)
| Byte | Field |
|---|---|
| 0 | Guide code `0xE5` |
| 1 | Transaction ID (1..127, increments, wraps to 1) |
| 2 | CMD1 |
| 3 | CMD2 |
| 4 | Checksum = (0xE5 + TID + CMD1 + CMD2 + LEN + sum(DATA)) & 0xFF |
| 5 | LEN (data length, max 12) |
| 6.. | DATA |

Reply (MICOM → host): same header, TID = request TID | 0x80, checksum = sum of all bytes except byte 4, DATA[0] = status (0x80 = success, 0xFF = error), then payload. Read timeout 500 ms.

## Commands
| Purpose | CMD1 | CMD2 | DATA |
|---|---|---|---|
| Sync / handshake | 0xA1 | 0x00 | `AA` |
| Poll keys (LG polls every 30 ms) | 0xB0 | 0x00 | — → reply data1 = key code |
| Assistant LED ring colours (no reply) | 0xC0 | 0xFD | colour bytes (4 LEDs) |
| Assistant LED colours (with reply) | 0xC0 | 0xFE | colour bytes |
| Function LED scenario | 0xC1 | 0xFF | scenario [, time_hi, time_lo] |
| **USB switch control** | 0xC2 | 0x00 | state byte |
| MCU reset | 0xD9 | 0x00 | — |
| MCU version | 0xE0 | 0x80 | 6× 00 |
| Touch-controller version | 0xE0 | 0x81 | 6× 00 |
| MCU LED info | 0xE0 | 0x90 | — |
| Touch firmware update | 0xF0 | 0x00/01/02 | start/data/end |

## Key codes (reply to 0xB0)
00 none · 01 play/pause · 02 hotword (Assistant) · 03 vol+ · 04 vol− · 05 function · 06 mic mute · 07 factory reset · 08 skip · 09 back-skip · 0x17 Wi-Fi short · 0x27 Wi-Fi click · 0x22 hotword short press · hidden/service: 0x83, 0x85, 0x91 · 0x11 = play/pause long press (seen on the speaker: sent once ~5 s into a hold, after 01; nothing more while the button stays down, and no release code)

## Notes
- Volume is NOT done by the MICOM: LG uses Android's software volume (AudioManager). On Linux, use ALSA/MPD software volume or the DSP volume control.
- `0xC2` USB switch explains why Wi-Fi/BT (QCA9377 on USB) and the external mini-USB port are mutually exclusive. The state values still need to be read from the caller code before use.
- MICOM firmware image: LG's `M17111305_DV_USB_Mode.hex` in the oem partition (not included; never reflash it).
- Nothing has been sent to the MICOM yet. Opening `/dev/ttyHSL1` while LG's app is running would conflict with it.
