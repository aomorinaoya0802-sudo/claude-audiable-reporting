import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "muse_bridge.py"
spec = importlib.util.spec_from_file_location("muse_mobile_bridge", SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class MuseMobileBridgeTests(unittest.TestCase):
    def test_token_stable_and_private(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(m, "TOKEN_PATH", Path(temp) / "token"):
            first = m.mobile_token()
            self.assertEqual(first, m.mobile_token())
            self.assertGreaterEqual(len(first), 40)

    def test_bad_text_rejected(self):
        for text in ("", "a" * 901):
            with self.assertRaises(ValueError):
                m.synthesize(text)

    def test_synth_uses_existing_voice(self):
        with patch.object(m.speak, "ensure_engine"), \
             patch.object(m.speak, "apply_readings", return_value=("テスト", [0])), \
             patch.object(m.speak, "load_readings", return_value={}), \
             patch.object(m.aivis_env, "read_settings",
                          return_value={"style": 888753761, "speed": 1.15, "intonation": 1.1}), \
             patch.object(m.speak, "http", side_effect=[
                 json.dumps({"speedScale": 1.0}).encode(),
                 b"RIFF" + b"\0" * 40
             ]) as http:
            wav = m.synthesize("テスト")
            self.assertTrue(wav.startswith(b"RIFF"))
            self.assertIn("speaker=888753761", http.call_args.args[0])
            payload = json.loads(http.call_args.kwargs["data"])
            self.assertEqual(payload["speedScale"], 1.15)
            self.assertEqual(payload["intonationScale"], 1.1)

    def test_unauthenticated_request_denied(self):
        with patch.object(m, "mobile_token", return_value="test-only-secret"):
            server = ThreadingHTTPServer(("127.0.0.1", 0), m.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/health"
                with self.assertRaises(HTTPError) as cm:
                    urlopen(url, timeout=3)
                self.assertEqual(cm.exception.code, 401)
                req = Request(url, headers={"Authorization": "Bearer wrong"})
                with self.assertRaises(HTTPError) as cm:
                    urlopen(req, timeout=3)
                self.assertEqual(cm.exception.code, 401)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
