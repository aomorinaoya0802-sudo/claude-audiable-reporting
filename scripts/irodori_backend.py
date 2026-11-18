"""Opt-in Irodori-TTS speech backend for existing AivisSpeech Claude Code mod."""
import json, os, subprocess, time, urllib.request, wave, io
from pathlib import Path

URL = "http://127.0.0.1:8088"
MODEL = "Aratako/Irodori-TTS-v4.1-Small-Quantized/int8-weight-only"

def _settings():
    try:
        p = Path.home()/".claude"/"aivis-speech"/"settings.json"
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}

def backend():
    return os.environ.get("CLAUDE_TTS_BACKEND", _settings().get("tts_backend", "aivis")).lower()

def _ready():
    try:
        with urllib.request.urlopen(URL+"/health", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False

def ensure_server():
    if _ready(): return
    base = Path(os.environ.get("IRODORI_TTS_HOME",str(Path.home()/"AI秘書"/"3_resources"/"tools"/"Irodori-TTS")))
    server = Path(os.environ.get("IRODORI_TTS_SERVER_HOME",str(base.parent/"Irodori-TTS-Server")))
    python = base/".venv"/"Scripts"/"python.exe"
    if not python.is_file() or not server.is_dir():
        raise FileNotFoundError("Local Irodori-TTS / Irodori-TTS-Server installation not found")
    env = os.environ.copy()
    env.update(IRODORI_HF_CHECKPOINT=MODEL, IRODORI_MODEL_PRECISION="fp32", IRODORI_MODEL_DEVICE="cpu", IRODORI_CODEC_DEVICE="cpu", IRODORI_HOST="127.0.0.1", IRODORI_PRELOAD="false")
    flags = getattr(subprocess,"CREATE_NO_WINDOW",0)|getattr(subprocess,"DETACHED_PROCESS",0)|getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0)
    p = subprocess.Popen([str(python),"-m","irodori_openai_tts","--host","127.0.0.1","--port","8088"],cwd=str(server),env=env,creationflags=flags,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    for _ in range(60):
        if _ready(): return
        if p.poll() is not None: raise RuntimeError(f"Irodori server exited: {p.returncode}")
        time.sleep(1)
    raise TimeoutError("Irodori server did not start on loopback 8088")

def synthesize_wav(text, speed=1.0):
    caption=_settings().get("irodori_caption","落ち着いた自然な日本語の女性の声。明瞭な発音。")
    data=json.dumps({"model":"irodori-tts","input":text,"voice":"none","response_format":"wav","speed":speed,"irodori":{"caption":caption,"num_steps":4}},ensure_ascii=False).encode("utf-8")
    req=urllib.request.Request(URL+"/v1/audio/speech",data,headers={"Content-Type":"application/json"},method="POST")
    with urllib.request.urlopen(req,timeout=240) as r: return r.read()
