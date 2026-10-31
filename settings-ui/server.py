"""aivis-speech mod の設定画面サーバー（標準ライブラリのみ）。
画面が閉じられて一定時間 ping が途絶えたら自動終了する。"""
import json, os, subprocess, sys, tempfile, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 18890
HERE = Path(__file__).resolve().parent
MOD = HERE.parent
sys.path.insert(0, str(MOD / "scripts"))
import aivis_env  # noqa: E402

SETTINGS = Path(aivis_env.SETTINGS_FILE)
READINGS = Path(aivis_env.READINGS_FILE)
AIVIS = "http://127.0.0.1:10101"
NOFLAGS = 0x08000000  # CREATE_NO_WINDOW

# voice-bridge の Claude 用既定値と範囲に合わせる
DEFAULTS = {"python_path": "", "engine_path": "", "enabled": True, "max_chars": 1000, "volume": 1.0, "style": 888753761, "speed": 1.15,
            "pitch": 0.0, "intonation": 1.1, "tempo": 1.0, "pause": 0.6, "fast_first": 1,
            "progress_tts": True, "ai_rewrite": False, "highlight": True, "band": True, "inline_highlight": True, "sync_offset_ms": 100, "highlight_color": "#ffd400"}
LIMITS = {"max_chars": (40, 5000), "sync_offset_ms": (-500, 800), "volume": (0.1, 2.0), "speed": (0.6, 1.8), "pitch": (-0.15, 0.15),
          "intonation": (0.3, 2.0), "tempo": (0.0, 2.0), "pause": (0.2, 1.5)}


def load():
    out = dict(DEFAULTS)
    try:
        raw = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    return clean(raw, out)


def clean(raw, base):
    out = dict(base)
    for k, (lo, hi) in LIMITS.items():
        if k in raw:
            try:
                v = max(lo, min(hi, float(raw[k])))
                out[k] = int(v) if k == "max_chars" else round(v, 3)
            except (TypeError, ValueError):
                pass
    for k in ("python_path", "engine_path"):
        if isinstance(raw.get(k), str):
            out[k] = raw[k]
    for k in ("enabled", "progress_tts", "ai_rewrite", "highlight", "band", "inline_highlight"):
        if k in raw:
            out[k] = raw[k] not in (0, False, "0", "false", "", None)
    if "fast_first" in raw:
        out["fast_first"] = 1 if raw["fast_first"] in (1, True, "1", "true") else 0
    import re as _re
    if isinstance(raw.get("highlight_color"), str) and _re.fullmatch(r"#[0-9a-fA-F]{6}", raw["highlight_color"]):
        out["highlight_color"] = raw["highlight_color"]
    if "style" in raw:
        try:
            out["style"] = int(raw["style"])
        except (TypeError, ValueError):
            pass
    return out


def save(s):
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def speak_argv(extra):
    py = Path(sys.executable).with_name("python.exe")
    return [str(py), str(MOD / "scripts" / "speak.py")] + extra


def speakers():
    try:
        with urllib.request.urlopen(AIVIS + "/speakers", timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    return [{"id": st["id"], "name": f'{sp["name"]}（{st["name"]}）'} for sp in data for st in sp.get("styles", [])]


last_ping = time.monotonic()
started = time.monotonic()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, code=200, ctype="application/json; charset=utf-8"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def do_GET(self):
        global last_ping
        last_ping = time.monotonic()
        if self.path in ("/", "/index.html"):
            self.send((HERE / "index.html").read_bytes(), ctype="text/html; charset=utf-8")
        elif self.path == "/api/settings":
            self.send({**load(), "defaults": DEFAULTS, "limits": LIMITS, "speakers": speakers()})
        elif self.path == "/api/readings":
            try:
                data = json.loads(READINGS.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            self.send({"readings": data})
        elif self.path == "/api/ping":
            self.send({"ok": True})
        else:
            self.send({"error": "not found"}, 404)

    def do_POST(self):
        global last_ping
        last_ping = time.monotonic()
        msg = self.body()
        if self.path == "/api/settings":
            s = DEFAULTS.copy() if msg.get("reset") else clean(msg, load())
            save(s)
            self.send(s)
        elif self.path == "/api/test":
            s = load()
            tf = Path(tempfile.gettempdir()) / "claude-aivis-settings-test.txt"
            tf.write_text(str(msg.get("text") or "AivisSpeech で読み上げています。この声で聞こえていますか？"), encoding="utf-8")
            args = ["--text-file", str(tf), "--style", str(s["style"]), "--speed", str(s["speed"]), "--intonation", str(s["intonation"]),
                    "--pitch", str(s["pitch"]), "--tempo", str(s["tempo"]), "--pause", str(s["pause"]),
                    "--volume", str(s["volume"]), "--fast-first", str(s["fast_first"])]
            subprocess.Popen(speak_argv(args), creationflags=NOFLAGS, close_fds=True)
            self.send({"ok": True})
        elif self.path == "/api/readings":
            data = msg.get("readings") if isinstance(msg.get("readings"), dict) else {}
            clean_map = {str(k).strip(): str(v).strip() for k, v in data.items() if str(k).strip() and str(v).strip()}
            READINGS.parent.mkdir(parents=True, exist_ok=True)
            READINGS.write_text(json.dumps(clean_map, ensure_ascii=False, indent=2), encoding="utf-8")
            self.send({"readings": clean_map})
        elif self.path == "/api/kana":
            # AivisSpeech Engine が、この文をどう読むか(カナ)を返す
            try:
                text = str(msg.get("text") or "")
                s = load()
                q = json.loads(urllib.request.urlopen(urllib.request.Request(
                    AIVIS + "/audio_query?" + urllib.parse.urlencode({"text": text, "speaker": s["style"]}),
                    data=b"", method="POST"), timeout=10).read().decode("utf-8"))
                kana = []
                for ap in q.get("accent_phrases", []):
                    kana.append("".join(m.get("text", "") for m in ap.get("moras", [])))
                    if ap.get("pause_mora"):
                        kana.append("、")
                self.send({"kana": " ".join(kana)})
            except Exception as e:
                self.send({"kana": "", "error": repr(e)[:120]})
        elif self.path == "/api/stop":
            subprocess.Popen(speak_argv(["--stop"]), creationflags=NOFLAGS, close_fds=True)
            self.send({"ok": True})
        elif self.path == "/api/engine/start":
            exe = aivis_env.find_engine()
            if exe:
                subprocess.Popen([exe, "--host", "127.0.0.1", "--port", "10101"], cwd=os.path.dirname(exe),
                                 creationflags=NOFLAGS, close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.send({"ok": bool(exe)})
        else:
            self.send({"error": "not found"}, 404)


def watchdog():
    while True:
        time.sleep(5)
        if time.monotonic() - last_ping > (90 if time.monotonic() - started < 120 else 30):
            os._exit(0)


if __name__ == "__main__":
    threading.Thread(target=watchdog, daemon=True).start()
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
    except OSError:
        sys.exit(0)  # すでに起動中
