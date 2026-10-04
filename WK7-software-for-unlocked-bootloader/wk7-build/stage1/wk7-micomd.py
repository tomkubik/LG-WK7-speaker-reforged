#!/usr/bin/env python3
"""wk7-micomd: LG WK7 MICOM button/LED daemon (talks to the LG microcontroller on /dev/ttyHSL1).

Only uses the decoded, harmless commands: sync (A1), key poll (B0), assistant LEDs (C0),
version query (E0 80). Never sends reset (D9), USB switch (C2) or firmware-update commands.
Button presses are forwarded to shairport-sync over D-Bus (dbus-send).
"""
import base64
import json
import os
import re
import select
import subprocess
import sys
import termios
import threading
import time
import urllib.request

TTY = "/dev/ttyHSL1"
GUIDE = 0xE5
POLL_INTERVAL = 0.03
REPEAT_DELAY = 0.4
REPEAT_RATE = 0.15

KEY_NAMES = {
    0x00: None, 0x01: "play_pause", 0x02: "hotword", 0x03: "volume_up", 0x04: "volume_down",
    0x05: "function", 0x06: "mic_mute", 0x07: "factory_reset", 0x08: "skip", 0x09: "back",
    0x17: "wifi_short", 0x27: "wifi_click", 0x22: "hotword_short",
}
REPEATING = {"volume_up", "volume_down"}

METADATA_PIPE = "/tmp/shairport-sync-metadata"
SPS_LOG = "/tmp/shairport.log"
DACP_COMMANDS = {
    "play_pause": "playpause", "hotword": "playpause", "hotword_short": "playpause",
    "skip": "nextitem", "back": "previtem",
    "volume_up": "volumeup", "volume_down": "volumedown",
}

SPS_DEST = "org.gnome.ShairportSync"
SPS_PATH = "/org/gnome/ShairportSync"

LED_OFF = [0] * 12
LED_PLAYING = [0x10, 0x10, 0x10] * 4
LED_PRESS = [0x00, 0x30, 0x00] * 4
LED_MUTED = [0x30, 0x00, 0x00] * 4
LED_SPEAKER_MUTED = [0x30, 0x10, 0x00] * 4

# Factory reset: hold play/pause until the ring turns red (~5 s), then press play/pause again within
# RESET_CONFIRM seconds -> wk7-factory-reset runs and the speaker reboots into Wi-Fi setup. Works even with the
# buttons disabled on the dashboard. The MICOM can't report a longer hold: it sends play/pause (0x01) once on
# press and its long-press code (0x11) once at ~5 s, then nothing while the button stays down.
# MICOM key 0x07 (LG's own factory-reset gesture) triggers the reset immediately.
LONG_PRESS = "unknown_0x11"
RESET_CONFIRM = 5.0
SETUP_FLAG = "/run/wk7/setup-mode"                      # wk7-wifisetup: blue ring while in Wi-Fi setup mode
LED_SETUP = [0x00, 0x00, 0x30] * 4

SETTINGS = "/etc/wk7/settings.json"
# VOLUME POLICY (see wk7gain.c): while AirPlay or Spotify plays, the phone/Mac/Spotify app volume is the real
# control and the buttons nudge that source's trim (/run/wk7.airplay-volume or /run/wk7.spotify-volume, reset to
# 100 whenever that source starts).
# Otherwise they change the moOde stream level (/run/wk7.volume, also set by the dashboard slider), which is
# independent of moOde's own knob. wk7gain applies both.
VOLUME_FILE = "/run/wk7.volume"
AIRPLAY_VOLUME_FILE = "/run/wk7.airplay-volume"
AIRPLAY_FLAG = "/run/wk7/airplay-active"
SPOTIFY_VOLUME_FILE = "/run/wk7.spotify-volume"
SPOTIFY_FLAG = "/run/wk7/spotify-active"
VOLUME_STEP = 4


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


class Micom:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
        attrs = termios.tcgetattr(self.fd)
        attrs[0] = 0                                    # iflag: raw
        attrs[1] = 0                                    # oflag: raw
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[3] = 0                                    # lflag: raw
        attrs[4] = attrs[5] = termios.B115200
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        termios.tcflush(self.fd, termios.TCIOFLUSH)
        self.tid = 0

    def _next_tid(self):
        self.tid = self.tid + 1 if self.tid < 127 else 1
        return self.tid

    def transact(self, cmd1, cmd2, data=(), timeout=0.5):
        tid = self._next_tid()
        data = list(data)
        checksum = (GUIDE + tid + cmd1 + cmd2 + len(data) + sum(data)) & 0xFF
        os.write(self.fd, bytes([GUIDE, tid, cmd1, cmd2, checksum, len(data)] + data))
        buf = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.fd], [], [], max(0, deadline - time.monotonic()))
            if ready:
                buf += os.read(self.fd, 64)
            start = buf.find(bytes([GUIDE]))
            if start >= 0 and len(buf) - start >= 6:
                length = buf[start + 5]
                if len(buf) - start >= 6 + length:
                    pkt = buf[start:start + 6 + length]
                    if pkt[1] != (tid | 0x80) or (sum(pkt) - pkt[4]) & 0xFF != pkt[4]:
                        return None
                    return pkt[6:]                      # [status, payload...]
        return None

    def sync(self):
        return self.transact(0xA1, 0x00, [0xAA])

    def version(self):
        reply = self.transact(0xE0, 0x80, [0] * 6)
        return ".".join("%02d" % b for b in reply[1:5]) if reply else None

    def poll_key(self):
        reply = self.transact(0xB0, 0x00, timeout=0.2)
        if not reply or reply[0] != 0x80:
            return None
        return reply[1] if len(reply) > 1 else 0

    def leds(self, rgb12):
        level = max(0, min(100, int(settings().get("led_brightness", 100))))
        self.transact(0xC0, 0xFE, [v * level // 100 for v in rgb12], timeout=0.2)


class Dacp:
    """Sends AirPlay 1 remote-control (DACP) commands straight to the sending device.

    shairport-sync publishes the session's DACP-ID ('daid'), Active-Remote token ('acre')
    and source IP ('clip') on its metadata pipe; the DACP port is found via mDNS.
    """
    ITEM = re.compile(rb"<item><type>([0-9a-f]{8})</type><code>([0-9a-f]{8})</code><length>(\d+)</length>"
                      rb"(?:\s*<data encoding=\"base64\">\s*([A-Za-z0-9+/=\s]*)</data>)?\s*</item>")

    def __init__(self):
        self.dacp_id = self.active_remote = self.client_ip = None
        self.port = None
        threading.Thread(target=self._read_metadata, daemon=True).start()

    def _read_metadata(self):
        while True:
            try:
                with open(METADATA_PIPE, "rb") as pipe:
                    buf = b""
                    for chunk in iter(lambda: pipe.read1(4096), b""):
                        buf += chunk
                        while True:
                            m = self.ITEM.search(buf)
                            if not m:
                                break
                            buf = buf[m.end():]
                            code = bytes.fromhex(m.group(2).decode())
                            data = base64.b64decode(m.group(4)) if m.group(4) else b""
                            self._handle(code, data.decode(errors="replace"))
            except OSError:
                time.sleep(1)

    def _handle(self, code, value):
        if code == b"daid" and value != self.dacp_id:
            self.dacp_id, self.port = value, None
            log("dacp id %s" % value)
        elif code == b"acre":
            self.active_remote = value
        elif code == b"clip":
            self.client_ip = value

    def _find_port(self):
        out = subprocess.run(["avahi-browse", "-rpt", "_dacp._tcp"], timeout=5,
                             capture_output=True, text=True).stdout
        for line in out.splitlines():
            f = line.split(";")
            if f[0] == "=" and f[2] == "IPv4" and f[3] == "iTunes_Ctrl_" + self.dacp_id:
                return int(f[8])
        return None

    def _from_log(self):
        """Fallback when the daemon started mid-session: take the latest session details from shairport-sync's -vv log."""
        try:
            text = open(SPS_LOG, errors="replace").read()[-200000:]
        except OSError:
            return
        m = re.findall(r'SETUP DACP-ID "([0-9A-F]+)" from ([0-9.]+) ', text)
        a = re.findall(r'Active-Remote", content: "(\d+)"', text)
        if m and a:
            if m[-1][0] != self.dacp_id:
                self.port = None
            self.dacp_id, self.client_ip = m[-1]
            self.active_remote = a[-1]
            log("dacp: session details recovered from log (%s)" % self.dacp_id)

    def send(self, command):
        if not (self.dacp_id and self.active_remote and self.client_ip):
            self._from_log()
        if not (self.dacp_id and self.active_remote and self.client_ip):
            log("dacp: no active AirPlay session")
            return
        if not self.port:
            self.port = self._find_port()
            if not self.port:
                log("dacp: remote control service for %s not found" % self.dacp_id)
                return
        url = "http://%s:%d/ctrl-int/1/%s" % (self.client_ip, self.port, command)
        req = urllib.request.Request(url, headers={"Active-Remote": self.active_remote})
        try:
            urllib.request.urlopen(req, timeout=2).close()
        except OSError as e:
            log("dacp: %s failed: %s" % (command, e))
            self.port = None                            # rediscover next time



_settings_cache = [0.0, {}]


def settings():
    """Dashboard settings (/etc/wk7/settings.json), re-read when the file changes."""
    try:
        mtime = os.stat(SETTINGS).st_mtime
        if mtime != _settings_cache[0]:
            with open(SETTINGS) as f:
                _settings_cache[:] = [mtime, json.load(f)]
    except (OSError, ValueError):
        pass
    return _settings_cache[1]


def airplay_mode():
    return str(settings().get("airplay_mode", "1"))


def dacp_source():
    """True while AirPlay 1 plays: then the buttons go to the phone/Mac over DACP."""
    return airplay_mode() == "1" and os.path.exists(AIRPLAY_FLAG)


class LocalVolume:
    """Speaker-side volume/mute (via wk7gain in the audio path), used when there is no AirPlay 1 back-channel."""

    def __init__(self, path, default):
        self.path, self.default = path, default
        self.saved = None                               # volume before mute

    def get(self):
        try:
            with open(self.path) as f:
                return int(f.read().strip() or self.default)
        except (OSError, ValueError):
            return self.default

    def set(self, value):
        value = max(0, min(100, value))
        with open(self.path + ".tmp", "w") as f:
            f.write("%d\n" % value)
        os.replace(self.path + ".tmp", self.path)
        return value

    def step(self, delta):
        if self.saved is not None:                      # any volume press unmutes
            self.set(self.saved)
            self.saved = None
        return self.set(self.get() + delta)

    def toggle_mute(self):
        if self.saved is None:
            self.saved = self.get()
            self.set(0)
            return True
        self.set(self.saved)
        self.saved = None
        return False


def volume_leds(value):
    """Show a volume level on the 4-LED ring: one white LED per quarter."""
    lit = (value + 24) // 25
    return [0x20, 0x20, 0x20] * lit + [0, 0, 0] * (4 - lit)


def sps_active():
    try:
        out = subprocess.run(["dbus-send", "--system", "--print-reply", "--dest=" + SPS_DEST,
                              SPS_PATH, "org.freedesktop.DBus.Properties.Get",
                              "string:org.gnome.ShairportSync", "string:Active"],
                             timeout=2, capture_output=True, text=True).stdout
        return "boolean true" in out
    except subprocess.SubprocessError:
        return False


LED_RESET_ARMED = [0x30, 0x00, 0x00] * 4


def factory_reset(micom):
    log("factory reset requested")
    for _ in range(3):
        micom.leds([0x40, 0, 0] * 4)
        time.sleep(0.3)
        micom.leds(LED_OFF)
        time.sleep(0.3)
    subprocess.Popen(["setsid", "/usr/local/bin/wk7-factory-reset", "--yes"],
                     stdout=open("/tmp/factory-reset.log", "a"), stderr=subprocess.STDOUT)


def main():
    micom = Micom(TTY)
    if not micom.sync():
        log("MICOM did not answer sync; exiting")
        sys.exit(1)
    log("MICOM version %s" % micom.version())
    for lit in range(1, 5):                             # boot signature: blue sweep = WK7 Linux firmware up
        micom.leds([0, 0, 0x40] * lit + [0, 0, 0] * (4 - lit))
        time.sleep(0.25)
    time.sleep(0.5)
    micom.leds(LED_OFF)
    dacp = Dacp()
    moode_level = LocalVolume(VOLUME_FILE, 40)
    airplay_trim = LocalVolume(AIRPLAY_VOLUME_FILE, 100)
    spotify_trim = LocalVolume(SPOTIFY_VOLUME_FILE, 100)
    speaker_muted = False

    last_key = 0
    pressed_at = next_repeat = 0.0
    muted = False
    playing = None
    next_state_check = 0.0
    flash_until = 0.0
    armed_until, resetting = 0.0, False
    last_raw = None
    pp_local = None                                     # what the last play/pause press muted (undone by a long press)

    while True:
        now = time.monotonic()
        key = micom.poll_key()
        if key is not None:
            name = KEY_NAMES.get(key, "unknown_0x%02X" % key)
            if key != last_raw:                         # raw key trace (incl. releases) to learn the MICOM's timing
                log("raw 0x%02X" % key)
                last_raw = key
            # factory reset gesture, before the buttons-disabled check
            fresh = key != last_key
            if fresh and name == LONG_PRESS and not resetting:
                if speaker_muted and pp_local is not None:          # undo the mute from the press that began it
                    pp_local.toggle_mute()
                    speaker_muted = False
                log("reset armed: press play/pause within %d s to confirm" % RESET_CONFIRM)
                armed_until = now + RESET_CONFIRM
                micom.leds(LED_RESET_ARMED)
                flash_until = armed_until
            elif fresh and not resetting and (name == "factory_reset" or (name == "play_pause" and now < armed_until)):
                resetting = True
                factory_reset(micom)
            if resetting:
                time.sleep(POLL_INTERVAL)
                continue
            if now < armed_until:                       # while armed, other presses do nothing
                last_key = key
                time.sleep(POLL_INTERVAL)
                continue
            if not settings().get("buttons", True):
                key = 0                                 # buttons disabled on the dashboard
            # buttons act on the playing source's trim (AirPlay or Spotify), otherwise on the moOde stream level
            local = (airplay_trim if os.path.exists(AIRPLAY_FLAG) else
                     spotify_trim if os.path.exists(SPOTIFY_FLAG) else moode_level)
            if key != last_key and key != 0:
                log("key %s" % name)
                pressed_at, next_repeat = now, now + REPEAT_DELAY
                if name == "mic_mute":
                    muted = not muted
                    micom.leds(LED_MUTED if muted else LED_OFF)
                elif not dacp_source() and name in DACP_COMMANDS:
                    # no back-channel to the source (AirPlay 2, Spotify, moOde): act on the speaker itself
                    if name in REPEATING:
                        level = local.step(VOLUME_STEP if name == "volume_up" else -VOLUME_STEP)
                        speaker_muted = False
                        micom.leds(volume_leds(level))
                        flash_until = now + 1.0
                    elif DACP_COMMANDS[name] == "playpause":
                        pp_local = local
                        speaker_muted = local.toggle_mute()
                        log("speaker %s" % ("muted" if speaker_muted else "unmuted"))
                        micom.leds(LED_SPEAKER_MUTED if speaker_muted else LED_PRESS)
                        flash_until = 0.0 if speaker_muted else now + 0.25
                elif name in DACP_COMMANDS:
                    dacp.send(DACP_COMMANDS[name])
                    micom.leds(LED_PRESS)
                    flash_until = now + 0.25
            elif key == last_key and name in REPEATING and now >= next_repeat:
                if not dacp_source():
                    level = local.step(VOLUME_STEP if name == "volume_up" else -VOLUME_STEP)
                    micom.leds(volume_leds(level))
                    flash_until = now + 1.0
                else:
                    dacp.send(DACP_COMMANDS[name])
                next_repeat = now + REPEAT_RATE
            last_key = key

        if flash_until and now >= flash_until:
            flash_until = 0.0
            playing = None                              # force LED refresh below

        if now >= next_state_check and not flash_until:
            next_state_check = now + 1.0
            if os.path.exists(SETUP_FLAG):
                if playing != "setup":
                    playing = "setup"
                    micom.leds(LED_SETUP)
            else:
                active = sps_active()
                if active != playing and not muted and not speaker_muted:
                    playing = active
                    micom.leds(LED_PLAYING if playing else LED_OFF)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
