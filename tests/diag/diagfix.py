"""Shared test helpers for the diag tests: a fake token and a loopback stand-in for the in-game bridge."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "test-token-0123456789abcdef"


class FakeMcp:
    """A loopback stand-in for the in-game bridge plugin: checks the bearer token, answers initialize and tools/call."""

    def __init__(self):
        self.calls = []
        self.reply_text = "@@SE2DIAG@@" + json.dumps({"probe": "x", "ok": True, "elapsed_ms": 1.0, "missing": [],
                                                       "notes": [], "data": {"hello": 1}})
        self.status = 200
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.calls.append({"headers": dict(self.headers), "body": body})
                if self.headers.get("Authorization") != f"Bearer {TOKEN}" or fake.status != 200:
                    self.send_response(fake.status if fake.status != 200 else 401)
                    self.end_headers()
                    return
                if body["method"] == "initialize":
                    res = {"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-06-18"}}
                    hdr = {"Mcp-Session-Id": "sess-1"}
                else:
                    res = {"jsonrpc": "2.0", "id": body["id"],
                           "result": {"content": [{"type": "text", "text": fake.reply_text}], "isError": False}}
                    hdr = {}
                data = b"   " + json.dumps(res).encode()     # the plugin writes keep-alive spaces before the JSON
                self.send_response(200)
                for k, v in hdr.items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://localhost:{self.port}/"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
