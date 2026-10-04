#!/usr/bin/env python3
"""wk7-dashboard: settings/status web page for the WK7 Linux firmware (port 80, home network, no PIN).

Also hosts the moOde integration from wk7_moode.py (Multiroom receiver / HTTP stream): moOde's
/command/ calls, /api/moode and /moode are passed to wk7_moode.route().
"""
import json
import os
import re
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "/usr/local/lib/wk7")
import wk7_moode  # noqa: E402

SETTINGS = "/etc/wk7/settings.json"
MOODE_CONF = "/etc/wk7/moode.json"
PAGE_FILE = "/usr/local/share/wk7/dashboard.html"
SPS_CONFS = ("/etc/shairport-sync.conf", "/etc/shairport-sync-ap2.conf")
# VOLUME POLICY: this file is the WK7's moOde stream level (Multiroom and HTTP stream, applied by wk7gain),
# independent of moOde's own volume knob: only the dashboard slider and the speaker buttons (when AirPlay is not
# playing) write it; moOde's volume commands are acknowledged but never applied. AirPlay never uses it: the
# phone/Mac AirPlay volume controls AirPlay and the Spotify app's volume controls Spotify Connect; the buttons
# nudge a separate trim for whichever of them plays (/run/wk7.airplay-volume, /run/wk7.spotify-volume).
VOLUME_FILE = "/run/wk7.volume"
DEFAULTS = {"speaker_volume": 40, "name": "WK7", "airplay_mode": "2", "max_volume_db": 0, "led_brightness": 100, "buttons": True, "spotify": True}


def load():
    try:
        with open(SETTINGS) as f:
            return {**DEFAULTS, **json.load(f)}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def current_volume(default):
    try:
        return int(open(VOLUME_FILE).read().strip())
    except (OSError, ValueError):
        return default


def set_volume(v):
    with open(VOLUME_FILE + ".tmp", "w") as f:
        f.write("%d\n" % v)
    os.replace(VOLUME_FILE + ".tmp", VOLUME_FILE)


def save(s):
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, SETTINGS)


def sh(*cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def write_airplay_conf(s):
    """Put the name and volume ceiling into both shairport-sync configs."""
    name = re.sub(r'["\\]', "", s["name"])[:40] or DEFAULTS["name"]
    for path in SPS_CONFS:
        text = open(path).read()
        text = re.sub(r'name = "[^"]*";', 'name = "%s";' % name, text, count=1)
        text = re.sub(r"\n  volume_max_db = [-0-9.]+;", "", text)
        text = text.replace("  volume_range_db = 50;\n", "  volume_range_db = 50;\n  volume_max_db = %.1f;\n"
                            % float(s["max_volume_db"]), 1)
        open(path, "w").write(text)


def update(req):
    s, old = load(), load()
    if isinstance(req.get("name"), str) and req["name"].strip():
        s["name"] = req["name"].strip()[:40]
    if str(req.get("airplay_mode")) in ("1", "2"):
        s["airplay_mode"] = str(req["airplay_mode"])
    for key, lo, hi in (("max_volume_db", -30, 0), ("led_brightness", 0, 100), ("speaker_volume", 0, 100)):
        if key in req:
            s[key] = max(lo, min(hi, int(float(req[key]))))
    for key in ("buttons", "spotify"):
        if key in req:
            s[key] = bool(req[key])
    save(s)
    if "speaker_volume" in req:
        set_volume(s["speaker_volume"])
    if s["name"] != old["name"]:
        subprocess.Popen(["wk7-hostname", "--apply"])           # address follows the name: <name>.local
    if any(s[k] != old[k] for k in ("name", "airplay_mode", "max_volume_db")):
        write_airplay_conf(s)
        subprocess.Popen(["wk7ctl", "restart", "airplay"])
    if s["name"] != old["name"] or s["spotify"] != old["spotify"]:      # Spotify Connect shows the speaker name
        subprocess.Popen(["wk7ctl", "restart" if s["spotify"] else "stop", "spotify"])
    return s


def action(name):
    """Dashboard buttons. Reboot goes through Android (outside the chroot); the active slot is unchanged."""
    if name == "restart_airplay":
        subprocess.Popen(["wk7ctl", "restart", "airplay"])
    elif name == "restart_spotify":
        subprocess.Popen(["wk7ctl", "restart", "spotify"])
    elif name == "reboot":
        subprocess.Popen(["sh", "-c", "sleep 1; sync; env -u LD_PRELOAD chroot /proc/1/root /system/bin/reboot"])
    else:
        return False
    return True


def status():
    svc = dict(l.split("=", 1) for l in sh("wk7ctl", "status").split() if "=" in l)
    ip = re.search(r"inet ([0-9.]+)", sh("ip", "-4", "addr", "show", "wlan0"))
    up = float(open("/proc/uptime").read().split()[0])
    route = sh("tail", "-n", "1", "/tmp/usbroute.log").strip()
    m = re.search(r"androidboot\.slot_suffix=_(\w)", open("/proc/cmdline").read())
    slot = m.group(1) if m else "?"
    return {
        "firmware": "WK7 Linux (stage 1) on slot %s" % slot.upper(),
        "uptime": "%dh %02dm" % (up // 3600, up % 3600 // 60),
        "wifi": ip.group(1) if ip else "not connected",
        "address": "http://%s.local/" % sh("wk7-hostname").strip(),
        "usb_route": route.split("usbroute: ", 1)[-1] if route else "unknown",
        "airplay": "running" if svc.get("shairport", "stopped") != "stopped" else "stopped",
        "airplay_version": "2" if os.path.exists("/proc/%s" % svc.get("shairport", "x")) and
                           "ap2" in sh("cat", "/proc/%s/cmdline" % svc.get("shairport", "x")) else "1",
        "spotify": "running" if svc.get("spotify", "stopped") != "stopped" else (
            "stopped" if load()["spotify"] else "off"),
        "buttons": "running" if svc.get("micomd", "stopped") != "stopped" else "stopped",
        "playing": "AirPlay" if os.path.exists("/run/wk7/airplay-active") else (
            "Spotify" if os.path.exists("/run/wk7/spotify-active") else "no"),
    }


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, ctype, data):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_req(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        r = wk7_moode.route(MCFG, MGR, method, self.path, body)
        if r:
            return self.reply(*r)
        if self.path == "/" and method == "GET":
            return self.reply(200, "text/html; charset=utf-8", open(PAGE_FILE, "rb").read())
        if self.path == "/api/settings":
            if method == "POST":
                try:
                    update(json.loads(body or b"{}"))
                except (ValueError, TypeError):
                    return self.reply(400, "text/plain", b"bad request")
            st = load()
            st["speaker_volume"] = current_volume(st["speaker_volume"])   # the buttons may have changed it
            return self.reply(200, "application/json", json.dumps(st).encode())
        if self.path == "/api/moode-buffer":
            if method == "POST":
                try:
                    req = json.loads(body or b"{}")
                    MCFG.update(jitter_ms=max(20, min(1000, int(req["jitter_ms"]))),
                                playback_buffer_ms=max(60, min(1000, int(req["playback_buffer_ms"]))))
                except (ValueError, TypeError, KeyError):
                    return self.reply(400, "text/plain", b"bad request")
                MGR.apply()                                     # restart the receiver with the new buffers
            return self.reply(200, "application/json", json.dumps(
                {k: MCFG.get(k) for k in ("jitter_ms", "playback_buffer_ms")}).encode())
        if self.path == "/api/action" and method == "POST":
            try:
                ok = action(json.loads(body or b"{}").get("action"))
            except (ValueError, TypeError, AttributeError):
                ok = False
            return self.reply(200 if ok else 400, "application/json", json.dumps({"ok": ok}).encode())
        if self.path == "/api/status":
            return self.reply(200, "application/json", json.dumps(status()).encode())
        self.reply(404, "text/plain", b"not found")

    def do_GET(self):
        self.handle_req("GET")

    def do_POST(self):
        self.handle_req("POST")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    MCFG = wk7_moode.Config(MOODE_CONF)
    # Owner's choice: every (re)start begins in moOde HTTP-stream mode, the everyday source. Multiroom /
    # AirPlay-only are edge cases chosen on the dashboard and last until the next restart.
    MCFG.update(mode="http")
    MGR = wk7_moode.Manager(MCFG)
    MGR.apply()
    srv = ThreadingHTTPServer(("", 80), Handler)
    srv.daemon_threads = True
    print(time.strftime("%H:%M:%S"), "wk7-dashboard on port 80", flush=True)
    srv.serve_forever()
