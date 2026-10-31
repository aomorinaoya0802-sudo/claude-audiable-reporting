"""aivis-speech の共有設定: 保存先・エンジンの場所・Python ライブラリの置き場。

speak.py(再生)、setup.py(初期設定)、settings-ui/server.py(設定画面)が、同じ場所を見るためのモジュール。
このPC専用の固定パスは持たない。エンジンは次の順で探す。
  1. 環境変数 AIVIS_ENGINE_EXE
  2. settings.json の engine_path
  3. このmodが setup でダウンロードした場所(~/.claude/aivis-speech/engine)
  4. AivisSpeech アプリが入れた場所(%LOCALAPPDATA%/Programs/AivisSpeech)
"""
import glob
import json
import os

HOME = os.environ.get("USERPROFILE") or os.path.expanduser("~")
DATA = os.path.join(HOME, ".claude", "aivis-speech")
SETTINGS_FILE = os.path.join(DATA, "settings.json")
READINGS_FILE = os.path.join(DATA, "readings.json")
LIB_DIR = os.path.join(DATA, "pylib")  # sounddevice / numpy など、setup が入れる場所
ENGINE_DIR = os.path.join(DATA, "engine")  # setup engine が展開する場所
ENGINE_VERSION = "1.2.0"  # 動作を確認したバージョン

if os.path.isdir(LIB_DIR):
    import sys

    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)


def read_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def find_engine():
    """AivisSpeech Engine の run.exe を探す。無ければ None。"""
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [os.environ.get("AIVIS_ENGINE_EXE", ""), str(read_settings().get("engine_path") or "")]
    patterns = [
        os.path.join(ENGINE_DIR, "run.exe"),
        os.path.join(ENGINE_DIR, "*", "run.exe"),
        os.path.join(local, "Programs", "AivisSpeech", "run.exe"),
        os.path.join(local, "Programs", "AivisSpeech", "*", "run.exe"),
        os.path.join(local, "Programs", "AivisSpeech", "*", "*", "run.exe"),
    ]
    for pattern in patterns:
        candidates.extend(sorted(glob.glob(pattern)))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None
