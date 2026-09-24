import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator


class FakeVLLM(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), FakeHandler)
        self.first_chunk_sent = threading.Event()
        self.release_stream = threading.Event()

    @property
    def port(self) -> int:
        return self.server_address[1]


class FakeHandler(BaseHTTPRequestHandler):
    server: FakeVLLM

    def log_message(self, *_args: object) -> None:
        pass

    def do_GET(self) -> None:
        if self.path != "/v1/models":
            self.send_error(404)
            return
        self._json(200, {"object": "list", "data": [{"id": "demo-model", "object": "model"}]})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if payload["messages"][0]["content"] == "rate_limit":
            self._json(429, {"error": {"message": "busy", "type": "rate_limit_error"}})
            return
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b'data: {"delta":"first"}\n\n')
            self.wfile.flush()
            self.server.first_chunk_sent.set()
            self.server.release_stream.wait(timeout=5)
            try:
                self.wfile.write(b'data: {"delta":"second"}\n\ndata: [DONE]\n\n')
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        self._json(
            200,
            {
                "id": "chatcmpl-demo",
                "object": "chat.completion",
                "model": payload["model"],
                "choices": [{"message": {"role": "assistant", "content": "hello"}}],
            },
        )

    def _json(self, status: int, value: object) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def fake_vllm() -> Iterator[FakeVLLM]:
    server = FakeVLLM()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.release_stream.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
