"""aivis-speech の初期設定と診断。

    python setup.py doctor            今の環境を診断する(何も変更しない)
    python setup.py install           必要なものを用意する(Python ライブラリ・初期設定・設定画面のショートカット)
    python setup.py engine [--yes]    AivisSpeech Engine を公式の配布からダウンロードして展開する(約 220MB、展開後 約 0.8GB)

エンジン(AivisSpeech)がすでに入っていれば、engine は不要。`doctor` が場所を教える。
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import aivis_env as env  # noqa: E402

MOD = os.path.dirname(HERE)
NO_WINDOW = 0x08000000
LOG_FILE = os.path.join(env.DATA, "setup.log")
RELEASE_API = "https://api.github.com/repos/Aivis-Project/AivisSpeech-Engine/releases/tags/%s"
ASSET = "AivisSpeech-Engine-Windows-x64-%s.7z.001"
SHORTCUT_NAME = "声の設定(Claude Code読み上げ).lnk"
NEEDED_LIBS = ["sounddevice", "numpy"]


for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


def say(msg):
    print(msg, flush=True)


def lib_ok(name):
    try:
        __import__(name)
        return True
    except Exception:  # noqa: BLE001
        return False


def http_json(url, data=None, timeout=15):
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET",
                                 headers={"User-Agent": "aivis-speech-mod"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def engine_running():
    try:
        urllib.request.urlopen("http://127.0.0.1:10101/version", timeout=2).read()
        return True
    except Exception:  # noqa: BLE001
        return False


def speakers():
    try:
        return http_json("http://127.0.0.1:10101/speakers", timeout=5)
    except Exception:  # noqa: BLE001
        return None


def desktop_dir():
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                         capture_output=True, text=True, creationflags=NO_WINDOW).stdout.strip()
    return out or os.path.join(env.HOME, "Desktop")


def doctor():
    ok = True

    def line(good, label, detail=""):
        nonlocal ok
        ok = ok and good
        say("%s %s%s" % ("OK " if good else "NG ", label, (" — " + detail) if detail else ""))

    line(sys.version_info >= (3, 10), "Python 3.10 以上", sys.version.split()[0] + " (" + sys.executable + ")")
    for lib in NEEDED_LIBS:
        line(lib_ok(lib), "Python ライブラリ " + lib, "" if lib_ok(lib) else "`/aivis setup` で入れます")
    engine = env.find_engine()
    line(bool(engine), "AivisSpeech Engine", engine or "見つかりません。AivisSpeech を入れるか、`/aivis setup engine` でダウンロードします")
    line(engine_running(), "エンジンの応答 (127.0.0.1:10101)", "" if engine_running() else "止まっています(読み上げ時に自動で起動します)")
    sp = speakers()
    if sp:
        names = ["%s(%s)" % (s["name"], ", ".join(st["name"] for st in s["styles"])) for s in sp]
        say("   声: " + " / ".join(names))
        style = env.read_settings().get("style")
        has = any(st["id"] == style for s in sp for st in s["styles"])
        line(has, "設定の声(style %s)" % style, "" if has else "このエンジンにその声が無いので、設定画面で選び直してください")
    line(os.path.isfile(env.SETTINGS_FILE), "設定ファイル", env.SETTINGS_FILE)
    line(os.path.isfile(os.path.join(desktop_dir(), SHORTCUT_NAME)), "設定画面のショートカット", "デスクトップ")
    try:
        import sounddevice as sd

        say("   音の出力先: " + str(sd.query_devices(sd.default.device[1])["name"]))
    except Exception as e:  # noqa: BLE001
        line(False, "音の出力先", repr(e)[:80])
    say("結果: " + ("問題ありません" if ok else "直すところがあります(上の NG)"))
    return ok


def install_libs():
    missing = [lib for lib in NEEDED_LIBS if not lib_ok(lib)]
    if not missing:
        say("Python ライブラリ: すでに入っています")
        return True
    os.makedirs(env.LIB_DIR, exist_ok=True)
    say("Python ライブラリを入れます: " + ", ".join(missing) + " → " + env.LIB_DIR)
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--target", env.LIB_DIR] + missing,
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    if r.returncode != 0:
        say("失敗: " + r.stderr.strip()[-300:])
        return False
    if env.LIB_DIR not in sys.path:
        sys.path.insert(0, env.LIB_DIR)
    return True


def install_defaults():
    os.makedirs(env.DATA, exist_ok=True)
    src = os.path.join(MOD, "defaults", "settings.json")
    if not os.path.isfile(env.SETTINGS_FILE) and os.path.isfile(src):
        shutil.copyfile(src, env.SETTINGS_FILE)
        say("初期設定を作りました: " + env.SETTINGS_FILE)
    else:
        say("設定ファイル: すでにあります(変更しません)")
    # このPCの Python の場所を覚えておく(mod が読み上げを起動するときに使う)
    settings = env.read_settings()
    if not settings.get("python_path"):
        settings["python_path"] = sys.executable
        with open(env.SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        say("Python の場所を記録しました: " + sys.executable)


def install_shortcut():
    desktop = desktop_dir()
    target = os.path.join(MOD, "settings-ui", "open.ps1")
    ps = (
        "$ws = New-Object -ComObject WScript.Shell\r\n"
        "$lnk = $ws.CreateShortcut('%s')\r\n"
        "$lnk.TargetPath = 'powershell.exe'\r\n"
        "$lnk.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"%s\"'\r\n"
        "$lnk.WorkingDirectory = '%s'\r\n"
        "$lnk.Description = 'aivis-speech mod 設定画面'\r\n"
        "$lnk.Save()\r\n"
    ) % (os.path.join(desktop, SHORTCUT_NAME).replace("'", "''"), target, os.path.dirname(target))
    path = os.path.join(tempfile.gettempdir(), "aivis-speech-shortcut.ps1")
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(ps)
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    created = r.returncode == 0 and os.path.isfile(os.path.join(desktop, SHORTCUT_NAME))
    say("設定画面のショートカット: " + ("デスクトップに作りました" if created else "作れませんでした " + r.stderr.strip()[:120]))


def install():
    say("== aivis-speech セットアップ ==")
    okay = install_libs()
    install_defaults()
    install_shortcut()
    if not env.find_engine():
        say("AivisSpeech Engine が見つかりません。AivisSpeech を入れるか、`/aivis setup engine` でダウンロードしてください。")
    say("== 診断 ==")
    return doctor() and okay


def install_engine(assume_yes):
    existing = env.find_engine()
    if existing:
        say("AivisSpeech Engine はすでにあります: " + existing)
        return True
    free = shutil.disk_usage(env.DATA if os.path.isdir(env.DATA) else env.HOME).free
    if free < 1.6 * 1024 ** 3:
        say("空き容量が足りません(必要 約1.6GB、現在 %.1fGB)。" % (free / 1024 ** 3))
        return False
    if not assume_yes:
        say("公式の配布(github.com/Aivis-Project/AivisSpeech-Engine %s)から約220MBをダウンロードし、約0.8GBに展開します。" % env.ENGINE_VERSION)
        say("実行するには `--yes` を付けてください。")
        return False
    name = ASSET % env.ENGINE_VERSION
    release = http_json(RELEASE_API % env.ENGINE_VERSION)
    asset = next((a for a in release.get("assets", []) if a["name"] == name), None)
    if not asset:
        say("配布ファイルが見つかりません: " + name)
        return False
    os.makedirs(env.DATA, exist_ok=True)
    archive = os.path.join(env.DATA, name)
    say("ダウンロード中: %s (%.0fMB)" % (name, asset["size"] / 1e6))
    req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "aivis-speech-mod"})
    sha = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(archive, "wb") as f:
        while True:
            block = r.read(1024 * 1024)
            if not block:
                break
            f.write(block)
            sha.update(block)
            done += len(block)
            if done % (20 * 1024 * 1024) < 1024 * 1024:
                say("  %d / %d MB" % (done // 1024 // 1024, asset["size"] // 1024 // 1024))
    digest = str(asset.get("digest") or "")
    if digest.startswith("sha256:") and digest[7:] != sha.hexdigest():
        say("ダウンロードの検証に失敗しました(sha256 が一致しません)。やり直してください。")
        os.remove(archive)
        return False
    say("展開中…")
    if not lib_ok("py7zr"):
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--target", env.LIB_DIR, "py7zr"],
                           capture_output=True, text=True, creationflags=NO_WINDOW)
        if r.returncode != 0:
            say("py7zr を入れられませんでした: " + r.stderr.strip()[-200:])
            return False
        if env.LIB_DIR not in sys.path:
            sys.path.insert(0, env.LIB_DIR)
    import py7zr

    os.makedirs(env.ENGINE_DIR, exist_ok=True)
    with py7zr.SevenZipFile(archive, "r") as z:
        z.extractall(env.ENGINE_DIR)
    os.remove(archive)
    found = env.find_engine()
    say("完了: " + (found or "展開しましたが run.exe が見つかりません"))
    return bool(found)


def detach(argv):
    """長い処理を切り離して実行し、ログを setup.log に書く。"""
    os.makedirs(env.DATA, exist_ok=True)
    log = open(LOG_FILE, "w", encoding="utf-8")
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([sys.executable, os.path.abspath(__file__)] + argv, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    say("バックグラウンドで始めました。経過は %s で見られます。終わったら `/aivis doctor` で確認してください。" % LOG_FILE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["doctor", "install", "engine"])
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--detach", action="store_true")
    a = ap.parse_args()
    if a.command == "doctor":
        doctor()
    elif a.command == "install":
        install()
    else:
        if a.detach:
            detach(["engine", "--yes"] if a.yes else ["engine"])
        else:
            install_engine(a.yes)


if __name__ == "__main__":
    main()
