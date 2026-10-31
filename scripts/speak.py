"""AivisSpeech Engine で読み上げる。--text-file の各行を1チャンクとして合成・再生する。

- 再生は独立したプロセス（ワーカー）で行う。Claude Code の mod が再読み込みされても、再生は途切れない。
- 起動した側（ランチャー）はワーカーを切り離して起動し、すぐ終了する。
- ワーカーは --progress-file に次の行を書く。mod はこれを読んでハイライトする。
    CHUNK <index> <再生時間ms> <再生開始のエポックms>   … チャンクの再生直前
    T <index> t0,t1,...,tn                              … 文字の境界ごとの時刻(ms, 再生開始から)。実際の音声の無音区間から作る
    ERROR ...                                           … 合成に失敗（そのチャンクは飛ばして続ける）
    END                                                 … 終了
- 再生中は次のチャンクを先に合成しておく（途切れにくく、出だしも速い）。
- 新しい読み上げは、再生中の古い読み上げを止めて置き換える。--stop は停止のみ。
- 音は sounddevice（voice-bridge と同じ経路）で鳴らす。
"""
import argparse
import io
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aivis_env  # noqa: E402  (保存先・エンジンの場所・ライブラリの置き場。sounddevice などの場所もここで通る)

PID_FILE = os.path.join(tempfile.gettempdir(), "claude-aivis-speech.pid")
NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
NEW_PROCESS_GROUP = 0x00000200
BREAKAWAY_FROM_JOB = 0x01000000
SYNTH_TIMEOUT = 25  # 1チャンクの合成にかけてよい秒数


def pid_alive(pid):
    out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH", "/FO", "CSV"],
                         capture_output=True, text=True, creationflags=NO_WINDOW).stdout
    return ("python" in out.lower())


def kill_previous():
    try:
        old = int(open(PID_FILE).read().strip())
    except Exception:
        return
    # PID の使い回しで無関係なプロセスを止めないよう、python のプロセスだけを対象にする
    if old and old != os.getpid() and pid_alive(old):
        subprocess.run(["taskkill", "/F", "/PID", str(old)], capture_output=True, creationflags=NO_WINDOW)
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def http(url, data=None, content_type=None, timeout=60):
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": content_type} if content_type else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def ensure_engine(url):
    exe = aivis_env.find_engine()
    try:
        http(url + "/version", timeout=2)
        return
    except Exception:
        pass
    if not exe:
        raise RuntimeError("AivisSpeech Engine が見つかりません。`/aivis doctor` で確認し、`/aivis setup engine` で入れてください")
    subprocess.Popen([exe, "--host", "127.0.0.1", "--port", "10101"], cwd=os.path.dirname(exe),
                     creationflags=NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        time.sleep(1)
        try:
            http(url + "/version", timeout=2)
            return
        except Exception:
            pass
    raise RuntimeError("AivisSpeech Engine did not start")


def launch_worker(argv):
    """自分自身を切り離して起動する（親や mod が終了しても再生が続くように）。"""
    cmd = [sys.executable, os.path.abspath(__file__), "--worker"] + argv
    for flags in (DETACHED_PROCESS | NEW_PROCESS_GROUP | BREAKAWAY_FROM_JOB, DETACHED_PROCESS | NEW_PROCESS_GROUP):
        try:
            subprocess.Popen(cmd, creationflags=flags, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            continue
    raise RuntimeError("could not start the playback worker")



READINGS_FILE = aivis_env.READINGS_FILE
# 既定の読み替え(voice-bridge の READING_MAP と、Claude Code まわりでよく出る語)。
# 自分の辞書は readings.json ({"語": "よみ"}) か、設定画面の「読みの辞書」で足せる。
DEFAULT_READINGS = {
    "Claude Code": "クロード コード", "Claude": "クロード", "ChatGPT": "チャットジーピーティー", "GPT": "ジーピーティー",
    "VTuber": "ブイチューバー", "YouTube": "ユーチューブ", "Wikipedia": "ウィキペディア", "GitHub": "ギットハブ",
    "VRChat": "ブイアールチャット", "AivisSpeech": "エーヴィスピーチ", "voice-bridge": "ボイスブリッジ",
    "mod": "モッド", "MCP": "エムシーピー", "API": "エーピーアイ", "TTS": "ティーティーエス", "STT": "エスティーティー",
    "UI": "ユーアイ", "JSON": "ジェイソン", "URL": "ユーアールエル", "PC": "ピーシー", "AI": "エーアイ",
    "tsx": "ティーエスエックス", "TypeScript": "タイプスクリプト", "Python": "パイソン", "PowerShell": "パワーシェル",
    "Exa": "エクサ", "Notion": "ノーション", "Gmail": "ジーメール", "Figma": "フィグマ", "Canva": "キャンバ",
    "Markdown": "マークダウン", "Chrome": "クローム", "Windows": "ウィンドウズ", "Haiku": "ハイク",
}


def load_readings():
    mapping = dict(DEFAULT_READINGS)
    try:
        with open(READINGS_FILE, encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            mapping.update({str(k): str(v) for k, v in user.items() if str(k)})
    except Exception:  # noqa: BLE001
        pass
    return mapping


def apply_readings(text, mapping):
    """読み替えを適用する。戻り値は (読み上げ用の文, 元の文の各境界が読み上げ用の文のどこに当たるか)。

    表示用の文字(元の文)と音声用の文字の数が変わっても、ハイライトの時刻表を元の文に合わせ直せるようにする。
    英数字だけの語は、語の途中(model の中の mod など)には当てない。
    """
    if not mapping:
        return text, list(range(len(text) + 1))
    lowered = {k.lower(): v for k, v in mapping.items()}
    parts = []
    for k in sorted(mapping, key=len, reverse=True):
        body = re.escape(k)
        if re.fullmatch(r"[A-Za-z0-9 ._-]+", k):
            body = r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])"
        parts.append(body)
    pat = re.compile("|".join(parts), re.IGNORECASE)
    out, bound, pos, out_len = [], [0], 0, 0
    for m in pat.finditer(text):
        for ch in text[pos:m.start()]:
            out.append(ch)
            out_len += 1
            bound.append(out_len)
        rep = lowered.get(m.group(0).lower(), m.group(0))
        length = m.end() - m.start()
        for k in range(length):
            bound.append(out_len + int(round(len(rep) * (k + 1) / length)))
        out.append(rep)
        out_len += len(rep)
        pos = m.end()
    for ch in text[pos:]:
        out.append(ch)
        out_len += 1
        bound.append(out_len)
    return "".join(out), bound


def make_chime(np, volume, kind=1):
    """効果音。kind=1: 質問(ピンポン) / kind=2: 作業完了(ドミソ、上がって終わる)。音声ファイルは使わず、その場で作る。"""
    rate = 44100

    def tone(freq, dur):
        t = np.arange(int(rate * dur)) / rate
        env = np.exp(-5.0 * t) * np.minimum(1.0, t * 200.0)
        return np.sin(2 * np.pi * freq * t) * env + 0.4 * np.sin(2 * np.pi * freq * 2 * t) * env

    if kind == 2:
        wave_ = np.concatenate([tone(523.25, 0.12), tone(659.25, 0.12), tone(783.99, 0.12), tone(1046.5, 0.42)])
    else:
        wave_ = np.concatenate([tone(880.0, 0.16), tone(1318.5, 0.36)])
    pcm = (wave_ / np.abs(wave_).max() * 0.55 * 32767 * min(1.0, volume)).astype(np.int16)
    return rate, pcm


PUNCT = "、，,。！？!?"
KANA = re.compile(r"[ぁ-んァ-ヴー]")
KANJI = re.compile(r"[一-龥々]")


def char_weight(ch):
    """1文字あたりの読みの長さの目安（モーラ数）。漢字は2モーラ前後、英数字は短め。"""
    if ch in PUNCT:
        return 0.0
    if KANA.match(ch):
        return 1.0
    if KANJI.match(ch):
        return 1.8
    if ch.isdigit():
        return 1.5
    if ch.isalpha():
        return 0.8
    return 0.0


def build_times(np, text, pcm, rate):
    """実際の音声の無音区間から、文字の境界ごとの時刻(ms)を作る。作れなければ None。

    読点・句点のところには必ず無音（間）があるので、音声の「声のある区間」を、句読点で区切った文の断片に
    対応づける。断片の中は、文字の重み（かな=1, 漢字=1.8 など）に比例して時間を割り振る。
    """
    x = pcm if pcm.ndim == 1 else pcm[:, 0]
    hop = max(1, int(rate * 0.01))
    n = len(x) // hop
    if n < 3 or not text:
        return None
    frames = x[: n * hop].astype(np.float32).reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    peak = float(rms.max())
    if peak <= 0:
        return None
    active = rms > max(peak * 0.04, 30.0)
    runs, i = [], 0
    while i < n:
        if active[i]:
            j = i
            while j < n and active[j]:
                j += 1
            runs.append([i, j])
            i = j
        else:
            i += 1
    if not runs:
        return None
    merged = []
    for r in runs:  # 120ms 未満の隙間は声の途中とみなして繋ぐ
        if merged and r[0] - merged[-1][1] < 12:
            merged[-1][1] = r[1]
        else:
            merged.append(r)

    segs, cur = [], ""
    for ch in text:
        cur += ch
        if ch in PUNCT:
            segs.append(cur)
            cur = ""
    if cur:
        segs.append(cur)

    # 声の区間が断片より多いときは、隙間の小さいところから繋いで数を合わせる
    while len(merged) > len(segs) and len(merged) > 1:
        gaps = [merged[k + 1][0] - merged[k][1] for k in range(len(merged) - 1)]
        k = gaps.index(min(gaps))
        merged[k][1] = merged[k + 1][1]
        del merged[k + 1]

    def ms(frame):
        return frame * 10

    total = len(text)
    times = [None] * (total + 1)
    if len(merged) == len(segs):
        offset = 0
        for seg, (a, b) in zip(segs, merged):
            body = seg[:-1] if seg[-1] in PUNCT else seg
            weights = [max(char_weight(c), 0.01) for c in body]
            wsum = sum(weights) or 1.0
            acc = 0.0
            times[offset] = ms(a)
            for k, w in enumerate(weights):
                acc += w
                times[offset + k + 1] = ms(a) + (ms(b) - ms(a)) * acc / wsum
            if seg[-1] in PUNCT:
                times[offset + len(seg)] = ms(b)  # 句読点は間の終わりまで（次の断片の頭で上書きされる）
            offset += len(seg)
    else:  # 区切れなかった: 声のある範囲全体に、句読点を重めに数えて割り振る
        a, b = merged[0][0], merged[-1][1]
        weights = [char_weight(c) if c not in PUNCT else (6.0 if c in "。！？!?" else 4.0) for c in text]
        weights = [max(w, 0.01) for w in weights]
        wsum = sum(weights)
        acc = 0.0
        times[0] = ms(a)
        for k, w in enumerate(weights):
            acc += w
            times[k + 1] = ms(a) + (ms(b) - ms(a)) * acc / wsum
    # 欠けた境界を前の値で埋め、単調にそろえる
    last = times[0] if times[0] is not None else 0
    for k in range(total + 1):
        if times[k] is None or times[k] < last:
            times[k] = last
        last = times[k]
    return [int(t) for t in times]


def run_worker(a):
    progress = open(a.progress_file, "w", encoding="utf-8", buffering=1) if a.progress_file else None

    def report(line):
        if progress:
            progress.write(line + "\n")
            progress.flush()

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    try:
        import numpy as np
        import sounddevice as sd

        with open(a.text_file, encoding="utf-8") as f:
            chunks = [ln.strip() for ln in f if ln.strip()]
        if not chunks:
            return
        ensure_engine(a.url)
        readings = load_readings()

        def synth(text):
            q = json.loads(http(a.url + "/audio_query?" + urllib.parse.urlencode({"text": text, "speaker": a.style}),
                                data=b"", timeout=SYNTH_TIMEOUT))
            q["speedScale"] = a.speed
            q["intonationScale"] = a.intonation
            q["tempoDynamicsScale"] = a.tempo
            q["pitchScale"] = a.pitch
            q["pauseLengthScale"] = a.pause
            q["volumeScale"] = a.volume
            q["prePhonemeLength"] = min(float(q.get("prePhonemeLength", 0.1)), 0.04)
            if a.fast_first:
                q["outputSamplingRate"] = 24000
            wav = http(a.url + "/synthesis?speaker=%d" % a.style, data=json.dumps(q).encode("utf-8"),
                       content_type="application/json", timeout=SYNTH_TIMEOUT)
            with wave.open(io.BytesIO(wav)) as wf:
                rate, ch = wf.getframerate(), wf.getnchannels()
                pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            if ch > 1:
                pcm = pcm.reshape(-1, ch)
            return rate, pcm

        def synth_safe(text):
            """失敗したら、記号を外した文でもう一度。"""
            try:
                return synth(text)
            except Exception:  # noqa: BLE001
                plain = re.sub(r"[【】「」『』（）()\[\]<>＜＞・･~〜]", " ", text).strip()
                if not plain or plain == text:
                    raise
                return synth(plain)

        ready = queue.Queue(maxsize=2)  # 先読み。2つ先まで合成する

        def synth_worker():
            failures = 0
            for i, text in enumerate(chunks):
                try:
                    rate, pcm = synth_safe(apply_readings(text, readings)[0])
                    failures = 0
                    ready.put((i, rate, pcm))
                except Exception as e:  # noqa: BLE001
                    failures += 1
                    report("ERROR synth %d: %r" % (i, e))
                    ready.put((i, None, None))  # このチャンクは飛ばして続ける
                    if failures >= 2:  # 続けて失敗するならエンジンの問題。あきらめる
                        break
            ready.put(None)

        threading.Thread(target=synth_worker, daemon=True).start()
        if a.chime:  # 合成を進めている間に、効果音を鳴らす
            chime_rate, chime_pcm = make_chime(np, a.volume, a.chime)
            sd.play(chime_pcm, chime_rate)
            sd.wait()
        while True:
            item = ready.get()
            if item is None:
                break
            i, rate, pcm = item
            if pcm is None:
                continue
            try:
                spoken, bound = apply_readings(chunks[i], readings)
                spoken_times = build_times(np, spoken, pcm, rate)
                times = [spoken_times[min(b, len(spoken_times) - 1)] for b in bound] if spoken_times else None
            except Exception:  # noqa: BLE001
                times = None
            started = time.time()
            report("CHUNK %d %d %d" % (i, int(len(pcm) * 1000 / rate), int(started * 1000)))
            if times:
                report("T %d %s" % (i, ",".join(str(t) for t in times)))
            sd.play(pcm, rate)
            sd.wait()
    except Exception as e:  # noqa: BLE001
        report("ERROR %r" % (e,))
    finally:
        report("END")
        try:
            if open(PID_FILE).read().strip() == str(os.getpid()):
                os.remove(PID_FILE)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--worker", action="store_true")
    ap.add_argument("--text-file")
    ap.add_argument("--progress-file")
    ap.add_argument("--style", type=int, default=1878365376)
    ap.add_argument("--speed", type=float, default=1.15)
    ap.add_argument("--intonation", type=float, default=1.1)
    ap.add_argument("--pitch", type=float, default=0.0)
    ap.add_argument("--tempo", type=float, default=1.0)
    ap.add_argument("--pause", type=float, default=0.6)
    ap.add_argument("--volume", type=float, default=1.0)
    ap.add_argument("--fast-first", type=int, default=1)
    ap.add_argument("--chime", type=int, default=0)
    ap.add_argument("--url", default="http://127.0.0.1:10101")
    a = ap.parse_args()

    if a.worker:
        run_worker(a)
        return
    kill_previous()
    if a.stop:
        return
    # ランチャー: 引数をそのままワーカーに渡して切り離し、すぐ終了する
    launch_worker(sys.argv[1:])


if __name__ == "__main__":
    main()
