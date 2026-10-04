# wk7-moode: the moOde modes

The two moOde modes for the WK7, chosen on the dashboard (http://wk7.local/, moOde section). This folder is the original, stand-alone version of the code with its off-device test (`test_wk7_moode.py`: 32 checks against a simulated moOde, covering moOde's control calls, an Opus/RTP stream like trx-tx with 2% packet loss, and MPD HTTP streams in FLAC and MP3).

**What runs on the speaker** is the copy in `WK7-software-for-unlocked-bootloader/wk7-build/stage1/wk7_moode.py`, installed by `install.sh` into `/usr/local/lib/wk7` and imported by the dashboard (`"http_port": 0` in moode.json; the dashboard passes `/command/`, `/api/moode` and `/moode` to `wk7_moode.route()`). That copy differs here in: default mode `http`, the `playback_buffer_ms` setting, the pacer lead, and the volume policy below. Integration details: the technical notes in `wk7-build/stage1/README.md` ("Dashboard + moOde").

## Modes
- **AirPlay only**: nothing extra runs.
- **moOde Multiroom Receiver**: the speaker answers on TCP 6600 and at `/command/?cmd=trx_control …`, so moOde's *Configure → Multiroom → Discover* finds it, and moOde switches it on/off. Audio is decoded in-process from moOde's Opus/RTP multicast (239.0.0.1:1350) with libopus. No trx anywhere, nothing extra on the moOde Pi. On the Pi: Sender ON, which by moOde's design silences the Pi's own jack/USB output. LG's kernel lacks IPv4 IGMP, so `wk7-igmp` joins the multicast group for it.
- **moOde HTTP stream** (default after every restart): ffmpeg pulls `http://<moode>:8000` (moOde: Audio → MPD Options → HTTP streaming ON, FLAC), resamples to 48 kHz and reconnects by itself. The Pi keeps playing locally; moOde doesn't see the speaker in this mode. moOde needs Loopback OFF here.

Both modes output S16LE / 48 kHz / stereo PCM to the `sink` command (`wk7-moode-sink` → `wk7gain` → DSP input MultiMedia2) and step aside while AirPlay or Spotify plays (`airplay_flag` in moode.json points to `/run/wk7/source-active`).

## Volume
The WK7's moOde level is its own: the dashboard "Speaker volume for moOde" slider and the speaker buttons (`/run/wk7.volume`, applied by `wk7gain`). moOde's volume and mute are acknowledged in Multiroom mode but not applied (`"volume_hook": "true"`), because the speaker and the Pi are in different rooms.

## Stand-alone use (this folder)
`python3 wk7_moode.py moode.json` with `moode.json.example` as a starting point (Python 3.8+, standard library only, plus `opus` and `ffmpeg`). `airplay-hook.sh` shows the shairport-sync `sessioncontrol` hooks that make it step aside.
