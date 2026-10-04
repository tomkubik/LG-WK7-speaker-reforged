#!/usr/bin/env python3
"""Off-device test: a fake moOde Sender (Opus/RTP exactly like trx-tx), a fake
MPD HTTP stream, and the same control calls moOde makes. Needs libopus + ffmpeg.

  python3 test_wk7_moode.py
"""
import ctypes, ctypes.util, json, math, os, socket, struct, subprocess, sys, tempfile, threading, time
import urllib.request, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wk7_moode as W

RATE, CH, FRAME = 48000, 2, 960
GROUP = os.environ.get("TEST_GROUP", "239.0.0.1")
RTP_PORT, HTTP_PORT, MPD_PORT, STREAM_PORT = 13500, 18089, 16600, 18000
ok_all = True


def check(name, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (" - " + str(detail) if detail else ""), flush=True)


def tone(freq, nframes, start):
    out = bytearray()
    for i in range(nframes):
        v = int(12000 * math.sin(2 * math.pi * freq * (start + i) / RATE))
        out += struct.pack("<hh", v, v)
    return bytes(out)


def analyse(path):
    """Dominant frequency (zero crossings) and RMS of the left channel."""
    data = open(path, "rb").read()
    n = len(data) // 4
    if n < RATE // 2:
        return n, 0, 0
    left = struct.unpack("<%dh" % (n * 2), data[: n * 4])[0::2]
    tail = left[n // 4:]  # skip start-up
    zc = sum(1 for a, b in zip(tail, tail[1:]) if a < 0 <= b)
    freq = zc * RATE / len(tail)
    rms = math.sqrt(sum(x * x for x in tail[::7]) / len(tail[::7]))
    return n, freq, rms


class FakeTrxTx(threading.Thread):
    """Opus 128 kbps, 20 ms frames, RTP payload type 0, 8 kHz timestamps (as trx-tx)."""

    def __init__(self, freq=1000, lose_every=0):
        super().__init__(daemon=True)
        self.freq, self.lose_every, self.stop = freq, lose_every, threading.Event()
        lib = ctypes.CDLL(ctypes.util.find_library("opus") or "libopus.so.0")
        lib.opus_encoder_create.restype = ctypes.c_void_p
        lib.opus_encoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        lib.opus_encode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int32]
        lib.opus_encoder_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        err = ctypes.c_int()
        self.enc = lib.opus_encoder_create(RATE, CH, 2049, ctypes.byref(err))  # OPUS_APPLICATION_AUDIO
        lib.opus_encoder_ctl(self.enc, 4002, 128000)  # OPUS_SET_BITRATE
        self.lib = lib

    def run(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        seq, ts, pos, t0, out = 1000, 0, 0, time.monotonic(), ctypes.create_string_buffer(1500)
        while not self.stop.is_set():
            pcm = tone(self.freq, FRAME, pos)
            n = self.lib.opus_encode(self.enc, pcm, FRAME, out, 1500)
            pkt = struct.pack("!BBHII", 0x80, 0, seq & 0xFFFF, ts & 0xFFFFFFFF, 0x1234) + out.raw[:n]
            if not (self.lose_every and seq % self.lose_every == 0):
                s.sendto(pkt, (GROUP, RTP_PORT))
            seq, ts, pos = seq + 1, ts + FRAME // 6, pos + FRAME
            delay = t0 + pos / RATE - time.monotonic()
            if delay > 0:
                time.sleep(delay)


def get(path):
    return urllib.request.urlopen("http://127.0.0.1:%d%s" % (HTTP_PORT, path), timeout=5).read().decode()


def moode_cmd(c):  # exactly how moOde's sendTrxControlCmd builds the URL
    return get("/command/?cmd=" + urllib.parse.quote("trx_control " + c))


def post(body):
    req = urllib.request.Request("http://127.0.0.1:%d/api/moode" % HTTP_PORT, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


class StreamServer(threading.Thread):
    """Real-time FLAC (or MP3) over HTTP, like MPD's httpd output."""

    def __init__(self, fmt):
        super().__init__(daemon=True)
        fmt_args = {"flac": ["-c:a", "flac", "-f", "flac"], "mp3": ["-c:a", "libmp3lame", "-b:a", "320k", "-f", "mp3"]}[fmt]
        ctype = {"flac": "audio/flac", "mp3": "audio/mpeg"}[fmt]

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.end_headers()
                p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-re", "-f", "lavfi", "-i",
                                      "sine=frequency=440:sample_rate=44100:duration=60", "-ac", "2"]
                                     + fmt_args + ["-"], stdout=subprocess.PIPE)
                try:
                    while True:
                        b = p.stdout.read(4096)
                        if not b:
                            break
                        self.wfile.write(b)
                except OSError:
                    pass
                finally:
                    p.kill()

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", STREAM_PORT), H)
        self.srv.daemon_threads = True

    def run(self):
        self.srv.serve_forever()


def main():
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "out.pcm")
    flag = os.path.join(tmp, "airplay-active")
    cfgpath = os.path.join(tmp, "moode.json")
    json.dump({"rtp_port": RTP_PORT, "http_port": HTTP_PORT, "mpd_port": MPD_PORT, "multicast": GROUP,
               "sink": ["sh", "-c", "cat >> " + out], "airplay_flag": flag,
               "status_file": os.path.join(tmp, "status.json"),
               "http_url": "http://127.0.0.1:%d/" % STREAM_PORT}, open(cfgpath, "w"))
    cfg = W.Config(cfgpath)
    mgr = W.Manager(cfg)
    mgr.apply()
    W.serve_http(cfg, mgr, HTTP_PORT)
    time.sleep(0.5)

    # ---- mode off: moOde must not find us
    check("off: rx status Disabled", moode_cmd("-rx").startswith("rx,Disabled"), moode_cmd("-rx"))
    try:
        socket.create_connection(("127.0.0.1", MPD_PORT), timeout=1).close()
        check("off: MPD port closed", False)
    except OSError:
        check("off: MPD port closed", True)

    # ---- multiroom
    st = post({"mode": "multiroom", "hostname": "Kitchen WK7"})
    check("switch to multiroom", st["mode"] == "multiroom", st.get("state"))
    time.sleep(0.5)
    c = socket.create_connection(("127.0.0.1", MPD_PORT), timeout=2)
    greet = c.recv(64)
    c.close()
    check("MPD port answers like MPD (moOde nmap scan)", greet.startswith(b"OK MPD"), greet)
    rx = moode_cmd("-rx")
    check("discovery status format", rx == "rx,On,50,0,1,Kitchen WK7,%s" % GROUP, rx)
    check("-all format", moode_cmd("-all").startswith("rx,On,") and ";tx," in moode_cmd("-all"))

    tx = FakeTrxTx(1000, lose_every=50)  # 2% packet loss
    tx.start()
    time.sleep(4)
    st = json.loads(get("/api/moode"))
    n, freq, rms = analyse(out)
    check("multiroom audio arrives", n > RATE * 3, "%.1f s" % (n / RATE))
    check("multiroom tone is 1 kHz", abs(freq - 1000) < 15, "%.0f Hz" % freq)
    check("multiroom state playing", st["state"] == "playing", st)
    check("lost packets concealed", st.get("lost", 0) > 0, st.get("lost"))

    # volume as moOde sends it
    check("set volume 30", moode_cmd("-set-mpdvol 30") == "Volume 30")
    check("master -up 5", moode_cmd("-set-mpdvol-from-master -up 5") == "Volume 35")
    check("master -dn 10", moode_cmd("-set-mpdvol-from-master -dn 10") == "Volume 25")
    check("mute toggle", moode_cmd("-set-mpdmute") == "Volume 25" and moode_cmd("-rx").split(",")[3] == "1")
    moode_cmd("-set-mpdvol -mute")
    check("unmute via -mute", moode_cmd("-rx").split(",")[3] == "0")
    open(out, "wb").close()
    time.sleep(1.5)
    _, _, rms25 = analyse(out)
    check("volume 25 is 1/4 of volume 50 (squared curve)", 0.2 < rms25 / rms < 0.3, "rms %.0f -> %.0f" % (rms, rms25))
    moode_cmd("-set-mpdvol 50")

    # AirPlay takes over, then hands back
    open(flag, "w").close()
    time.sleep(0.6)
    sz = os.path.getsize(out)
    time.sleep(1)
    check("AirPlay preempts moOde", os.path.getsize(out) == sz)
    os.remove(flag)
    time.sleep(1.2)
    check("moOde resumes after AirPlay", os.path.getsize(out) > sz)

    # moOde turns receiver off / on
    moode_cmd("-rx Off")
    time.sleep(1)
    check("receiver Off", moode_cmd("-rx").startswith("rx,Off"))
    sz = os.path.getsize(out)
    time.sleep(1)
    check("no audio when Off", os.path.getsize(out) == sz)
    moode_cmd("-rx On")
    time.sleep(1.5)
    check("receiver On again plays", os.path.getsize(out) > sz)
    tx.stop.set()

    # ---- http stream, FLAC then MP3 (moOde's two encoders)
    for fmt in ("flac", "mp3"):
        srv = StreamServer(fmt)
        srv.start()
        open(out, "wb").close()
        post({"mode": "http"})
        time.sleep(5)
        n, freq, _ = analyse(out)
        st = json.loads(get("/api/moode"))
        check("http %s audio arrives" % fmt, n > RATE * 3, "%.1f s" % (n / RATE))
        check("http %s tone 440 Hz resampled to 48k" % fmt, abs(freq - 440) < 10, "%.0f Hz" % freq)
        check("http %s state playing" % fmt, st["state"] == "playing", st.get("state"))
        check("moOde can't see us in http mode", moode_cmd("-rx").startswith("rx,Disabled"))
        srv.srv.shutdown()
        srv.srv.server_close()
        post({"mode": "off"})
        time.sleep(0.5)

    # reconnect after the stream disappears
    post({"mode": "http"})
    time.sleep(3)
    st = json.loads(get("/api/moode"))
    check("http shows problem when moOde is unreachable", "error" in st or st["state"] in ("disconnected", "connecting"), st)
    srv = StreamServer("flac")
    srv.start()
    open(out, "wb").close()
    time.sleep(12)
    check("http reconnects by itself", os.path.getsize(out) > RATE * 4 * 2)

    page = get("/")
    check("dashboard page has the mode switch", "moOde Multiroom Receiver" in page and "HTTP stream" in page)
    post({"mode": "off"})
    print("\nALL PASS" if ok_all else "\nSOME FAILED")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
