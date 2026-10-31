"""ローカル翻訳(英語→日本語)。トークンを使わず、ネットワークも要らない(最初のモデル取得を除く)。

voice-bridge が使っていた翻訳モデル staka/fugumt-en-ja(CC BY-SA 4.0)を CTranslate2 に変換して使う。
変換後は約60MBで、CPU だけで 1 文 0.1〜0.3 秒ほどで訳せる。

    python mt.py install        モデルを取得して変換する(約125MBをダウンロード。変換後 約60MB)
    python mt.py serve          翻訳サーバーを起動する(127.0.0.1:18891。10分使われなければ自動で終了)
    python mt.py translate 文    1 文を訳して表示する(動作確認用)
    python mt.py status         入っているか確認する
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import aivis_env as env  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

MODEL_ID = "staka/fugumt-en-ja"
MT_DIR = os.path.join(env.DATA, "mt")
CT2_DIR = os.path.join(MT_DIR, "fugumt-en-ja-ct2")
PORT = 18891
IDLE_SECONDS = 600
NO_WINDOW = 0x08000000


def installed():
    return all(os.path.isfile(os.path.join(CT2_DIR, f)) for f in ("model.bin", "source.spm", "target.spm"))


def install():
    if installed():
        print("翻訳モデルはすでに入っています: " + CT2_DIR, flush=True)
        return True
    free = shutil.disk_usage(env.DATA if os.path.isdir(env.DATA) else env.HOME).free
    if free < 600 * 1024 ** 2:
        print("空き容量が足りません(必要 約600MB、現在 %dMB)。" % (free // 1024 ** 2), flush=True)
        return False
    work = tempfile.mkdtemp(prefix="aivis-mt-")
    try:
        libs = os.path.join(work, "libs")
        print("変換に使う部品(transformers など)を一時フォルダに入れます…", flush=True)
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--target", libs, "transformers", "sentencepiece"],
                           capture_output=True, text=True, creationflags=NO_WINDOW)
        if r.returncode != 0:
            print("部品を入れられませんでした: " + r.stderr.strip()[-300:], flush=True)
            return False
        # 実行時に必要な sentencepiece だけは、永続の置き場にも入れる
        os.makedirs(env.LIB_DIR, exist_ok=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--target", env.LIB_DIR, "sentencepiece", "ctranslate2"],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
        out_tmp = os.path.join(work, "ct2")
        script = (
            "import sys, os\n"
            "sys.path.insert(0, %r)\n"
            "os.environ['HF_HOME'] = %r\n"
            "import ctranslate2\n"
            "from ctranslate2.converters import TransformersConverter\n"
            "TransformersConverter(%r).convert(%r, quantization='int8', force=True)\n"
            "from huggingface_hub import hf_hub_download\n"
            "import shutil\n"
            "for f in ('source.spm', 'target.spm'):\n"
            "    shutil.copyfile(hf_hub_download(%r, f), os.path.join(%r, f))\n"
            "print('converted')\n"
        ) % (libs, os.path.join(work, "hf"), MODEL_ID, out_tmp, MODEL_ID, out_tmp)
        print("モデルを取得して変換します(約125MBをダウンロード)…", flush=True)
        r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, creationflags=NO_WINDOW)
        if r.returncode != 0 or not os.path.isfile(os.path.join(out_tmp, "model.bin")):
            print("変換に失敗しました: " + (r.stderr.strip()[-400:] or r.stdout.strip()[-400:]), flush=True)
            return False
        os.makedirs(MT_DIR, exist_ok=True)
        if os.path.isdir(CT2_DIR):
            shutil.rmtree(CT2_DIR)
        shutil.move(out_tmp, CT2_DIR)
        print("完了: " + CT2_DIR, flush=True)
        return True
    finally:
        shutil.rmtree(work, ignore_errors=True)  # 一時フォルダ(ダウンロードした元のモデルを含む)を消す


class Translator:
    def __init__(self):
        import ctranslate2
        import sentencepiece as spm

        self.src = spm.SentencePieceProcessor(model_file=os.path.join(CT2_DIR, "source.spm"))
        self.tgt = spm.SentencePieceProcessor(model_file=os.path.join(CT2_DIR, "target.spm"))
        self.tr = ctranslate2.Translator(CT2_DIR, device="cpu", compute_type="int8", inter_threads=1, intra_threads=4)

    def translate(self, texts):
        # Marian 系は、文の終わりに </s> が要る(無いと同じ言葉を繰り返し続ける)
        tokens = [self.src.encode(t, out_type=str) + ["</s>"] for t in texts]
        results = self.tr.translate_batch(tokens, beam_size=2, max_decoding_length=160)
        return [self.tgt.decode(r.hypotheses[0]).strip() for r in results]


def serve():
    if not installed():
        print("翻訳モデルが入っていません。`python mt.py install` を実行してください。", flush=True)
        return
    translator = Translator()
    lock = threading.Lock()
    state = {"last": time.monotonic()}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            state["last"] = time.monotonic()
            self.reply({"ok": True})

        def do_POST(self):
            state["last"] = time.monotonic()
            try:
                n = int(self.headers.get("Content-Length") or 0)
                texts = [str(t) for t in json.loads(self.rfile.read(n).decode("utf-8")).get("texts", [])][:20]
                with lock:
                    self.reply({"texts": translator.translate(texts) if texts else []})
            except Exception as e:  # noqa: BLE001
                self.reply({"error": repr(e)[:200]}, 500)

    def watchdog():
        while True:
            time.sleep(15)
            if time.monotonic() - state["last"] > IDLE_SECONDS:
                os._exit(0)

    threading.Thread(target=watchdog, daemon=True).start()
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
    except OSError:
        pass  # すでに起動している


def serve_detached():
    flags = 0x00000008 | 0x00000200 | 0x01000000  # DETACHED_PROCESS | NEW_PROCESS_GROUP | BREAKAWAY_FROM_JOB
    for f in (flags, flags & ~0x01000000):
        try:
            subprocess.Popen([sys.executable, os.path.abspath(__file__), "serve"], creationflags=f, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            continue


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "install":
        sys.exit(0 if install() else 1)
    elif cmd == "serve":
        if "--detach" in sys.argv:
            serve_detached()
        else:
            serve()
    elif cmd == "translate":
        t0 = time.time()
        translator = Translator()
        t1 = time.time()
        out = translator.translate([" ".join(sys.argv[2:])])
        print(out[0])
        print("(読み込み %.1f 秒 / 翻訳 %.2f 秒)" % (t1 - t0, time.time() - t1))
    else:
        print("翻訳モデル: " + ("入っています " + CT2_DIR if installed() else "入っていません(`python mt.py install`)"))


if __name__ == "__main__":
    main()
