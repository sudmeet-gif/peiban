import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import unittest
from unittest.mock import patch

from companion.models import LocalModels, ModelError


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.server.request_data = json.loads(self.rfile.read(length))
        self.send_response(200)
        self.end_headers()
        for part in ("你", "好"):
            item = {"choices": [{"delta": {"content": part}}]}
            self.wfile.write(("data: " + json.dumps(item) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *args):
        pass


class LocalModelTests(unittest.TestCase):
    def test_stream_reads_local_server(self):
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        models = LocalModels()
        models._port = server.server_port
        try:
            with patch.object(models, "_start"):
                result = "".join(models.stream_chat([{"role": "user", "content": "test"}]))
            self.assertEqual(result, "你好")
            self.assertTrue(server.request_data["stream"])
            self.assertFalse(server.request_data["chat_template_kwargs"]["enable_thinking"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_missing_model_is_actionable(self):
        with self.assertRaises(ModelError):
            list(LocalModels({"chat_model": "missing-model.gguf"}).stream_chat([{"role": "user", "content": "你好"}]))

    def test_voice_clone_requires_consent(self):
        with self.assertRaisesRegex(ModelError, "确认"):
            LocalModels().synthesize("你好", "out.wav", reference_audio="sample.wav")

    def test_precancelled_chat_does_not_start(self):
        stopped = threading.Event()
        stopped.set()
        self.assertEqual(list(LocalModels().stream_chat([], stopped)), [])


if __name__ == "__main__":
    unittest.main()
