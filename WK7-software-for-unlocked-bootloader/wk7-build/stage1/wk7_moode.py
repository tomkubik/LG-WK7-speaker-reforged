#!/usr/bin/env python3
"""wk7-moode: moOde playback modes for the LG WK7 speaker.

Modes (switched from the wk7.local dashboard, stored in the config file):
  off        AirPlay only.
  multiroom  Act as a moOde Multiroom Receiver. moOde finds the speaker with
             its "Discover receivers" button and controls on/off, volume and
             mute from its Receivers panel. Audio is moOde's own stream:
             Opus in RTP over UDP multicast (default 239.0.0.1:1350), decoded
             here with libopus. No trx, nothing extra on the moOde Pi.
  http       Pull moOde's built-in MPD HTTP stream (http://<moode>:8000) with
             ffmpeg, resampled to 48 kHz.

VOLUME POLICY (WK7 owner's decision): the WK7's volume is independent of moOde.
moOde's volume/mute never change what the WK7 plays. In multiroom mode the
receiver still answers moOde's -set-mpdvol / -set-mpdmute calls (so moOde is
happy and shows its number), but with "volume_hook": "true" in moode.json the
decoder plays at 0 dB and ignores mute; the actual level is applied after this
program by wk7gain from /run/wk7.volume (dashboard slider + speaker buttons,
default 40). HTTP mode never reads moOde's volume. Do not reintroduce volume
following without asking the owner (two different rooms share moOde).

Both modes write raw S16LE / 48 kHz / stereo PCM to the configured sink and
step aside while AirPlay is active (flag file written by shairport-sync's
sessioncontrol hooks, see airplay-hook.sh).

Python 3.8+, standard library only. Runtime needs: libopus (multiroom mode),
ffmpeg (http mode).
"""

import ctypes
import ctypes.util
import json
import math
import os
import select
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "0.1"
RATE = 48000
CHANNELS = 2
FRAME_BYTES = 4  # S16LE stereo

DEFAULTS = {
    "mode": "http",                      # off | multiroom | http (the WK7 starts in http at every boot)
    "hostname": "WK7",                   # name shown in moOde's Receivers panel
    "multicast": "239.0.0.1",            # must match moOde's Multiroom address
    "rtp_port": 1350,
    "jitter_ms": 80,                     # receive buffer before playback starts
    "playback_buffer_ms": 150,           # sink (aplay) buffer; the pacer leads by 80% of it
    "receiver_on": True,                 # moOde's per-receiver On/Off
    "mastervol_opt_in": True,            # follow moOde's master volume knob
    "volume": 50,                        # 0-100, set by moOde in multiroom mode
    "mute": False,
    "http_url": "http://moode.local:8000",
    "sink": ["sh", "-c", "cat > /dev/null"],  # argv; PCM goes to its stdin
    "volume_hook": "",                   # optional shell cmd, {volume} {mute}
    "airplay_flag": "/run/wk7/airplay-active",
    "http_port": 80,                     # 0 = don't serve (dashboard imports route())
    "mpd_port": 6600,                    # moOde finds receivers by this port
    "status_file": "/run/wk7/moode-status.json",
}


def log(*a):
    print(time.strftime("%H:%M:%S"), "wk7-moode:", *a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- config

class Config:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.data = dict(DEFAULTS)
        try:
            with open(path) as f:
                self.data.update(json.load(f))
        except FileNotFoundError:
            pass
        except Exception as e:
            log("config unreadable, using defaults:", e)

    def get(self, key):
        with self.lock:
            return self.data[key]

    def update(self, **kw):
        with self.lock:
            self.data.update(kw)
            tmp = self.path + ".tmp"
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(tmp, "w") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)

    def snapshot(self):
        with self.lock:
            return dict(self.data)


# ---------------------------------------------------------------- sink

class Sink:
    """Feeds PCM to the sink command's stdin; steps aside while AirPlay plays."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.proc = None
        self.preempted = False
        self._checked = 0.0

    def airplay_active(self):
        now = time.monotonic()
        if now - self._checked > 0.25:
            self._checked = now
            self.preempted = os.path.exists(self.cfg.get("airplay_flag"))
        return self.preempted

    def write(self, pcm):
        if self.airplay_active():
            self.close()
            return False
        if self.proc is None or self.proc.poll() is not None:
            try:
                self.proc = subprocess.Popen(self.cfg.get("sink"), stdin=subprocess.PIPE)
            except OSError as e:
                log("sink start failed:", e)
                self.proc = None
                time.sleep(1)
                return False
        try:
            self.proc.stdin.write(pcm)
            return True
        except (BrokenPipeError, OSError):
            self.close()
            return False

    def close(self):
        if self.proc:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None


def is_silent(pcm):
    return pcm.count(0) == len(pcm)


class Pacer:
    """Keeps writes close to real time so a deep sink buffer can't drain ours."""

    def __init__(self, lead_s=0.12):
        self.lead = lead_s
        self.reset()

    def reset(self):
        self.t0 = None
        self.frames = 0

    def wait(self, nframes):
        now = time.monotonic()
        if self.t0 is None:
            self.t0 = now
        ahead = self.t0 + self.frames / RATE - now
        if ahead > self.lead:
            time.sleep(ahead - self.lead)
        elif ahead < -0.5:  # we stalled; don't try to catch up
            self.t0 = now - self.frames / RATE
        self.frames += nframes


# ---------------------------------------------------------------- opus

class Opus:
    OPUS_SET_GAIN = 4034

    def __init__(self):
        name = ctypes.util.find_library("opus") or "libopus.so.0"
        self.lib = ctypes.CDLL(name)
        self.lib.opus_decoder_create.restype = ctypes.c_void_p
        self.lib.opus_decoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        self.lib.opus_decode.restype = ctypes.c_int
        self.lib.opus_decode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int32,
                                         ctypes.POINTER(ctypes.c_int16), ctypes.c_int, ctypes.c_int]
        self.lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
        self.lib.opus_decoder_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        err = ctypes.c_int()
        self.dec = self.lib.opus_decoder_create(RATE, CHANNELS, ctypes.byref(err))
        if err.value != 0 or not self.dec:
            raise RuntimeError("opus_decoder_create failed: %d" % err.value)
        self.max_frames = 5760  # 120 ms
        self.buf = (ctypes.c_int16 * (self.max_frames * CHANNELS))()

    def decode(self, packet, plc_frames=960):
        """Decode a packet, or conceal a lost one when packet is None."""
        if packet is None:
            n = self.lib.opus_decode(self.dec, None, 0, self.buf, plc_frames, 0)
        else:
            n = self.lib.opus_decode(self.dec, packet, len(packet), self.buf, self.max_frames, 0)
        if n < 0:
            return b""
        return ctypes.string_at(self.buf, n * FRAME_BYTES)

    def set_gain_db(self, db):
        self.lib.opus_decoder_ctl(self.dec, self.OPUS_SET_GAIN, int(max(-120.0, min(0.0, db)) * 256))

    def close(self):
        if self.dec:
            self.lib.opus_decoder_destroy(self.dec)
            self.dec = None


# ---------------------------------------------------------------- multiroom

class RtpReceiver(threading.Thread):
    """Receives moOde Multiroom audio: one Opus packet per RTP packet."""

    def __init__(self, cfg, status):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.status = status
        self.stop_ev = threading.Event()
        self.sock = None

    def open_socket(self):
        group, port = self.cfg.get("multicast"), int(self.cfg.get("rtp_port"))
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 18)
        s.bind(("", port))
        mreq = struct.pack("4s4s", socket.inet_aton(group), socket.inet_aton("0.0.0.0"))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        s.setblocking(False)
        return s

    @staticmethod
    def parse(pkt):
        if len(pkt) < 12 or pkt[0] >> 6 != 2:
            return None
        cc, ext, pad = pkt[0] & 0x0F, pkt[0] & 0x10, pkt[0] & 0x20
        seq = struct.unpack_from("!H", pkt, 2)[0]
        off = 12 + 4 * cc
        if ext:
            if len(pkt) < off + 4:
                return None
            off += 4 + 4 * struct.unpack_from("!H", pkt, off + 2)[0]
        end = len(pkt) - (pkt[-1] if pad else 0)
        if off >= end:
            return None
        return seq, pkt[off:end]

    def apply_volume(self, opus):
        if self.cfg.get("volume_hook"):
            # WK7 volume policy: moOde's volume is ignored; wk7gain applies the speaker's own volume downstream
            opus.set_gain_db(0)
            return
        vol = int(self.cfg.get("volume"))
        if vol <= 0:
            opus.set_gain_db(-120)
        else:
            opus.set_gain_db(40 * math.log10(vol / 100.0))

    def run(self):
        try:
            self.sock = self.open_socket()
            opus = Opus()
        except Exception as e:
            log("multiroom receiver failed to start:", e)
            self.status["error"] = str(e)
            return
        sink, pacer = Sink(self.cfg), Pacer(lead_s=int(self.cfg.get("playback_buffer_ms")) * 0.8 / 1000)
        buf = {}            # extended seq -> payload
        expected = None     # next extended seq to play
        last_ext = None
        playing = False
        frame_size = 960
        last_rx = 0.0
        vol_seen = None
        log("multiroom receiver listening on %s:%s" % (self.cfg.get("multicast"), self.cfg.get("rtp_port")))
        while not self.stop_ev.is_set():
            # wait briefly for packets, then drain whatever has arrived
            select.select([self.sock], [], [], 0.01)
            while True:
                try:
                    pkt = self.sock.recv(4096)
                except (BlockingIOError, InterruptedError):
                    break
                except OSError:
                    break
                p = self.parse(pkt)
                if not p:
                    continue
                seq, payload = p
                # extend the 16-bit sequence number
                if last_ext is None:
                    ext = seq
                else:
                    delta = (seq - (last_ext & 0xFFFF) + 0x8000) % 0x10000 - 0x8000
                    ext = last_ext + delta
                if last_ext is None or ext > last_ext:
                    last_ext = ext
                if expected is not None and ext < expected:
                    continue  # too late
                if expected is not None and ext - expected > 500:
                    buf.clear()  # sender restarted
                    expected, playing = None, False
                buf[ext] = payload
                last_rx = time.monotonic()
                self.status["packets"] = self.status.get("packets", 0) + 1
                if len(buf) > 2000:
                    break

            vol_now = (self.cfg.get("volume"), self.cfg.get("mute"))
            if vol_now != vol_seen:
                vol_seen = vol_now
                self.apply_volume(opus)

            now = time.monotonic()
            if not buf:
                if playing and now - last_rx > 2.0:
                    log("multiroom stream stopped")
                    playing, expected = False, None
                    sink.close()
                    pacer.reset()
                    self.status["state"] = "waiting"
                if not playing:
                    continue
            buffered_ms = len(buf) * frame_size * 1000 // RATE
            if not playing:
                if buffered_ms < int(self.cfg.get("jitter_ms")):
                    continue
                expected = min(buf)
                playing = True
                pacer.reset()
                log("multiroom stream started")
            # drift guard: if the buffer grows far past target, skip ahead
            if buffered_ms > 4 * int(self.cfg.get("jitter_ms")) + 200:
                newest = max(buf)
                target = newest - int(self.cfg.get("jitter_ms")) * RATE // (1000 * frame_size)
                for k in [k for k in buf if k < target]:
                    del buf[k]
                expected = max(expected, target)
                self.status["skips"] = self.status.get("skips", 0) + 1
            # play everything that's due, pacing to real time
            produced = 0
            while expected is not None and produced < 3:
                if expected in buf:
                    pcm = opus.decode(buf.pop(expected))
                elif buf and min(buf) > expected:
                    pcm = opus.decode(None, frame_size)  # lost packet
                    self.status["lost"] = self.status.get("lost", 0) + 1
                else:
                    break  # underrun; wait for more packets
                expected += 1
                produced += 1
                if not pcm:
                    continue
                frame_size = len(pcm) // FRAME_BYTES
                if self.cfg.get("mute") and not self.cfg.get("volume_hook"):
                    pcm = bytes(len(pcm))
                self.status["state"] = "idle" if is_silent(pcm) else "playing"
                pacer.wait(len(pcm) // FRAME_BYTES)
                if not sink.write(pcm):
                    self.status["state"] = "airplay" if sink.preempted else self.status["state"]
        sink.close()
        opus.close()
        self.sock.close()

    def stop(self):
        self.stop_ev.set()


class MpdStub(threading.Thread):
    """Answers on the MPD port so moOde's network scan finds the speaker."""

    def __init__(self, cfg, status):
        super().__init__(daemon=True)
        cfg_ref, status_ref = cfg, status

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    self.wfile.write(b"OK MPD 0.23.5\n")
                    for raw in self.rfile:
                        cmd = raw.decode("utf-8", "replace").strip()
                        if cmd == "close":
                            return
                        if cmd == "status":
                            state = "play" if status_ref.get("state") == "playing" else "stop"
                            self.wfile.write(("volume: %d\nrepeat: 0\nrandom: 0\nsingle: 0\nconsume: 0\n"
                                              "playlistlength: 0\nstate: %s\nOK\n"
                                              % (cfg_ref.get("volume"), state)).encode())
                        else:
                            self.wfile.write(b"OK\n")
                except OSError:
                    pass

        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.server = socketserver.ThreadingTCPServer(("", int(cfg.get("mpd_port"))), Handler)
        self.server.daemon_threads = True

    def run(self):
        self.server.serve_forever(poll_interval=0.5)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


# ---------------------------------------------------------------- http stream

class HttpStream(threading.Thread):
    """Plays moOde's MPD HTTP stream (FLAC or MP3) via ffmpeg."""

    CHUNK = 960 * FRAME_BYTES  # 20 ms

    def __init__(self, cfg, status):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.status = status
        self.stop_ev = threading.Event()
        self.proc = None

    @staticmethod
    def resolve_local(url):
        """musl can't resolve mDNS .local names; ask avahi and substitute the IPv4 address."""
        u = urllib.parse.urlsplit(url)
        host = u.hostname or ""
        if not host.endswith(".local"):
            return url
        try:
            out = subprocess.run(["avahi-resolve", "-4", "-n", host], capture_output=True, text=True, timeout=5).stdout
            ip = out.split()[1]
        except (OSError, subprocess.SubprocessError, IndexError):
            return url
        netloc = ip + (":%d" % u.port if u.port else "")
        return urllib.parse.urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment))

    def ffmpeg_cmd(self, url):
        return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                "-fflags", "nobuffer", "-probesize", "32768", "-analyzeduration", "0",
                "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "4",
                "-i", url, "-vn", "-ac", str(CHANNELS), "-ar", str(RATE),
                "-f", "s16le", "-"]

    def run(self):
        sink = Sink(self.cfg)
        backoff = 1
        while not self.stop_ev.is_set():
            url = self.resolve_local(self.cfg.get("http_url"))
            self.status["state"] = "connecting"
            try:
                self.proc = subprocess.Popen(self.ffmpeg_cmd(url), stdout=subprocess.PIPE)
            except OSError as e:
                self.status["error"] = "ffmpeg: %s" % e
                log("cannot start ffmpeg:", e)
                self.stop_ev.wait(10)
                continue
            log("http stream connecting to", url)
            got_audio = False
            while not self.stop_ev.is_set():
                pcm = self.proc.stdout.read(self.CHUNK)
                if not pcm:
                    break
                got_audio = True
                backoff = 1
                self.status.pop("error", None)
                self.status["state"] = "idle" if is_silent(pcm) else "playing"
                if not sink.write(pcm) and sink.preempted:
                    self.status["state"] = "airplay"
            self.kill_proc()
            sink.close()
            if self.stop_ev.is_set():
                break
            self.status["state"] = "disconnected"
            if not got_audio:
                self.status["error"] = "cannot reach %s" % url
            self.stop_ev.wait(backoff)
            backoff = min(backoff * 2, 15)
        sink.close()

    def kill_proc(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def stop(self):
        self.stop_ev.set()
        self.kill_proc()


# ---------------------------------------------------------------- manager

class Manager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.parts = []
        self.status = {}
        self.mode = None

    def apply(self):
        """(Re)start components to match the configured mode."""
        with self.lock:
            for p in self.parts:
                p.stop()
            for p in self.parts:
                if p.is_alive():
                    p.join(timeout=3)
            self.parts = []
            self.status = {"state": "off"}
            self.mode = self.cfg.get("mode")
            try:
                if self.mode == "multiroom":
                    self.status["state"] = "receiver off"
                    self.parts.append(MpdStub(self.cfg, self.status))
                    if self.cfg.get("receiver_on"):
                        self.status["state"] = "waiting"
                        self.parts.append(RtpReceiver(self.cfg, self.status))
                elif self.mode == "http":
                    self.parts.append(HttpStream(self.cfg, self.status))
            except OSError as e:
                self.status["error"] = str(e)
                log("mode start failed:", e)
            for p in self.parts:
                p.start()
            log("mode:", self.mode)

    def run_volume_hook(self):
        hook = self.cfg.get("volume_hook")
        if hook:
            cmd = hook.format(volume=int(self.cfg.get("volume")), mute=1 if self.cfg.get("mute") else 0)
            subprocess.Popen(cmd, shell=True)

    def public_status(self):
        s = self.cfg.snapshot()
        out = {k: s[k] for k in ("mode", "hostname", "multicast", "rtp_port", "http_url",
                                 "receiver_on", "volume", "mute", "mastervol_opt_in")}
        out.update(self.status)
        out["version"] = VERSION
        return out


# ---------------------------------------------------------------- moOde control API

def moode_rx_status(cfg, mgr):
    if mgr.mode != "multiroom":
        state = "Disabled"
    else:
        state = "On" if cfg.get("receiver_on") else "Off"
    return ",".join(["rx", state, str(int(cfg.get("volume"))), "1" if cfg.get("mute") else "0",
                     "1" if cfg.get("mastervol_opt_in") else "0", cfg.get("hostname"),
                     cfg.get("multicast")])


def apply_vol_cmd(cfg, mgr, args):
    """moOde vol.sh syntax: N | -up N | -dn N | -mute | -restore"""
    vol, mute = int(cfg.get("volume")), bool(cfg.get("mute"))
    if not args or args[0] == "-restore":
        pass
    elif args[0] == "-mute":
        mute = not mute
    elif args[0] in ("-up", "-dn") and len(args) > 1:
        step = int(float(args[1]))
        vol = vol + step if args[0] == "-up" else vol - step
        mute = False
    else:
        vol, mute = int(float(args[0])), False
    vol = max(0, min(100, vol))
    cfg.update(volume=vol, mute=mute)
    mgr.run_volume_hook()
    return "Volume %d" % vol


def trx_control(cfg, mgr, argv):
    """Mirrors moOde's /var/www/util/trx-control.php."""
    if not argv:
        return "Missing option"
    opt, args = argv[0], argv[1:]
    if opt == "-rx":
        if args:
            on = args[0].lower() == "on"
            if mgr.mode == "multiroom" and on != bool(cfg.get("receiver_on")):
                cfg.update(receiver_on=on)
                threading.Thread(target=mgr.apply, daemon=True).start()
            return ""
        return moode_rx_status(cfg, mgr)
    if opt == "-tx":
        return "tx,Disabled,0,0,,%s" % cfg.get("hostname")
    if opt == "-all":
        return moode_rx_status(cfg, mgr) + ";tx,Disabled,0,0,,%s" % cfg.get("hostname")
    if opt == "-set-mpdvol":
        return apply_vol_cmd(cfg, mgr, args)
    if opt == "-set-mpdvol-from-master":
        if not cfg.get("mastervol_opt_in"):
            return "Master volume opt-in is No"
        return apply_vol_cmd(cfg, mgr, args)
    if opt == "-set-mpdmute":
        return apply_vol_cmd(cfg, mgr, ["-mute"])
    if opt == "-set-alsavol":
        return ""
    return "Missing option"


MODE_LABELS = [("off", "AirPlay only"), ("multiroom", "moOde Multiroom Receiver"), ("http", "moOde HTTP stream")]

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>WK7 moOde mode</title>
<style>
:root{--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--card:#f5f5f7;--acc:#0a66d6}
@media (prefers-color-scheme:dark){:root{--bg:#000;--fg:#f5f5f7;--mut:#a1a1a6;--card:#1c1c1e;--acc:#4c9aff}}
body{font:16px/1.4 -apple-system,system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:16px;max-width:560px}
h1{font-size:20px;margin:0 0 12px}.card{background:var(--card);border-radius:12px;padding:14px;margin-bottom:12px}
label.opt{display:flex;gap:10px;align-items:center;padding:8px 0;cursor:pointer}
input[type=text]{width:100%%;box-sizing:border-box;padding:8px;border-radius:8px;border:1px solid var(--mut);background:var(--bg);color:var(--fg)}
button{background:var(--acc);color:#fff;border:0;border-radius:8px;padding:10px 16px;font-size:16px;margin-top:10px}
.mut{color:var(--mut);font-size:14px}#st{white-space:pre-wrap}
</style></head><body>
<h1>moOde mode</h1>
<div class="card"><form id="f">%(opts)s
<div id="urlrow"><div class="mut">moOde stream address</div><input type="text" name="http_url" value="%(url)s"></div>
<div class="mut" style="margin-top:8px">Name in moOde</div><input type="text" name="hostname" value="%(host)s">
<button type="submit">Save</button></form></div>
<div class="card"><div class="mut">Status</div><div id="st">loading…</div></div>
<script>
const f=document.getElementById('f');
function sync(){document.getElementById('urlrow').style.display=f.mode.value==='http'?'':'none'}
f.addEventListener('change',sync);sync();
f.addEventListener('submit',async e=>{e.preventDefault();
 const body={mode:f.mode.value,http_url:f.http_url.value.trim(),hostname:f.hostname.value.trim()};
 await fetch('/api/moode',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});poll();});
async function poll(){try{const s=await (await fetch('/api/moode')).json();
 const lines=['Mode: '+s.mode,'State: '+s.state];
 if(s.mode==='multiroom'){lines.push('moOde receiver switch: '+(s.receiver_on?'On':'Off'),'Volume from moOde: '+s.volume+(s.mute?' (muted)':''))}
 if(s.error)lines.push('Problem: '+s.error);
 document.getElementById('st').textContent=lines.join('\\n');}catch(e){}}
poll();setInterval(poll,2000);
</script></body></html>"""


def render_page(cfg):
    import html
    mode = cfg.get("mode")
    opts = "".join('<label class="opt"><input type="radio" name="mode" value="%s"%s> %s</label>'
                   % (v, " checked" if v == mode else "", lbl) for v, lbl in MODE_LABELS)
    return PAGE % {"opts": opts, "url": html.escape(cfg.get("http_url"), quote=True),
                   "host": html.escape(cfg.get("hostname"), quote=True)}


def route(cfg, mgr, method, path, body=b""):
    """Dashboard integration point. Returns (status, content_type, bytes) or None."""
    u = urllib.parse.urlsplit(path)
    if u.path in ("/command/", "/command") and method == "GET":
        q = urllib.parse.parse_qs(u.query)
        cmd = (q.get("cmd") or [""])[0].split()
        if cmd and cmd[0] == "trx_control":
            out = trx_control(cfg, mgr, cmd[1:])
        else:
            out = "Unknown command"
        return 200, "text/plain; charset=utf-8", out.encode()
    if u.path == "/api/moode":
        if method == "POST":
            try:
                req = json.loads(body or b"{}")
            except ValueError:
                return 400, "text/plain", b"bad json"
            changes = {}
            if req.get("mode") in ("off", "multiroom", "http"):
                changes["mode"] = req["mode"]
            if isinstance(req.get("http_url"), str) and req["http_url"].startswith(("http://", "https://")):
                changes["http_url"] = req["http_url"]
            if isinstance(req.get("hostname"), str) and req["hostname"] and "," not in req["hostname"]:
                changes["hostname"] = req["hostname"][:40]
            if changes:
                restart = any(cfg.get(k) != v for k, v in changes.items() if k in ("mode", "http_url"))
                cfg.update(**changes)
                if restart:
                    mgr.apply()
        st = mgr.public_status()
        try:
            os.makedirs(os.path.dirname(cfg.get("status_file")), exist_ok=True)
            with open(cfg.get("status_file"), "w") as f:
                json.dump(st, f)
        except OSError:
            pass
        return 200, "application/json", json.dumps(st).encode()
    if u.path in ("/moode", "/moode/") and method == "GET":
        return 200, "text/html; charset=utf-8", render_page(cfg).encode()
    return None


def serve_http(cfg, mgr, port):
    class H(BaseHTTPRequestHandler):
        def _do(self, method):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            path = "/moode" if self.path == "/" else self.path
            r = route(cfg, mgr, method, path, body)
            if r is None:
                r = (404, "text/plain", b"not found")
            code, ctype, data = r
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._do("GET")

        def do_POST(self):
            self._do("POST")

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("control page on port", port)
    return srv


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/etc/wk7/moode.json"
    cfg = Config(path)
    mgr = Manager(cfg)
    mgr.apply()
    port = int(cfg.get("http_port"))
    if port:
        serve_http(cfg, mgr, port)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
