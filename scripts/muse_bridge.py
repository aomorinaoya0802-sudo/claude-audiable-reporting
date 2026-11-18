"""Muse Android companion -> local AivisSpeech synthesis. Tailnet-only HTTPS via Tailscale Serve."""
import hmac
import json
import secrets
import sys
import threading
import urllib.parse
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import aivis_env
import speak

TOKEN_PATH = Path.home() / ".claude" / "aivis-speech" / "muse-mobile-token"
LOCK = threading.Lock()
HOST = "100.114.247.101"
PORT = 18891
MAX_TEXT = 900
ENGINE = "http://127.0.0.1:10101"


def mobile_token():
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not TOKEN_PATH.exists():
        TOKEN_PATH.write_text(secrets.token_urlsafe(40), encoding="utf-8")
    return TOKEN_PATH.read_text(encoding="utf-8").strip()


def synthesize(text):
    text = text.strip()
    if not 1 <= len(text) <= MAX_TEXT:
        raise ValueError("text length out of range")
    speak.ensure_engine(ENGINE)
    cfg = aivis_env.read_settings()
    style = int(cfg.get("style", 888753761))
    reading = speak.apply_readings(text, speak.load_readings())[0]
    q = json.loads(speak.http(
        ENGINE + "/audio_query?" + urllib.parse.urlencode({"text": reading, "speaker": style}),
        data=b"", timeout=35
    ))
    for key, setting, default in (
        ("speedScale", "speed", 1.15), ("intonationScale", "intonation", 1.1),
        ("pitchScale", "pitch", 0), ("tempoDynamicsScale", "tempo", 1),
        ("pauseLengthScale", "pause", 0.6), ("volumeScale", "volume", 1)
    ):
        q[key] = float(cfg.get(setting, default))
    wav = speak.http(
        ENGINE + "/synthesis?speaker=" + str(style),
        data=json.dumps(q, ensure_ascii=False).encode("utf-8"),
        content_type="application/json", timeout=70
    )
    if not wav.startswith(b"RIFF") or len(wav) > 20_000_000:
        raise RuntimeError("invalid WAV data")
    return wav


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Do not log messages or authentication headers
        pass

    def send_json(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        header = self.headers.get("Authorization", "")
        if not hmac.compare_digest(header, "Bearer " + mobile_token()):
            self.send_json(401, {"error": "unauthorized"})
            return False
        return True

    def do_GET(self):
        if self.path != "/health":
            self.send_json(404, {"error": "not_found"})
            return
        if not self.authorized():
            return
        try:
            with urlopen(ENGINE + "/version", timeout=2):
                running = True
        except Exception:
            running = False
        self.send_json(200, {"ok": True, "engine_running": running,
                             "voice_style": aivis_env.read_settings().get("style")})

    def do_POST(self):
        if self.path != "/synthesize":
            self.send_json(404, {"error": "not_found"})
            return
        if not self.authorized():
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.send_json(415, {"error": "json_only"})
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if not 2 <= n <= 8192:
                self.send_json(413, {"error": "invalid_length"})
                return
            obj = json.loads(self.rfile.read(n).decode("utf-8"))
            if not isinstance(obj, dict) or not isinstance(obj.get("text"), str):
                raise ValueError("text is required")
            if not LOCK.acquire(blocking=False):
                self.send_json(429, {"error": "busy"})
                return
            try:
                wav = synthesize(obj["text"])
            finally:
                LOCK.release()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(wav)))
            self.end_headers()
            self.wfile.write(wav)
        except (ValueError, UnicodeError, json.JSONDecodeError) as e:
            self.send_json(400, {"error": str(e)[:80]})
        except Exception as e:
            self.send_json(502, {"error": type(e).__name__})


if __name__ == "__main__":
    mobile_token()
    print(f"Muse Aivis mobile bridge ready: http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
