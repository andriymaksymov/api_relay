"""Unit and integration tests for api_relay."""
import http.client
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import api_relay

# ── Helpers ────────────────────────────────────────────────────────────────────

class FakeTargetHandler(BaseHTTPRequestHandler):
    """Minimal upstream stub: echoes request body back with a few extra headers."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Received-Key", self.headers.get("x-api-key", ""))
        self.send_header("X-Request-Path", self.path)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):  # silence during tests
        return


def _start_server(handler_cls, host="127.0.0.1", upstream_timeout=5):
    """Start a ThreadingHTTPServer on a random port and return (server, thread)."""
    server = ThreadingHTTPServer((host, 0), handler_cls)
    server.upstream_timeout = upstream_timeout  # required by RelayHandler
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


# ── Unit tests ─────────────────────────────────────────────────────────────────

class TestBuildTargetUrl(unittest.TestCase):

    def test_path_and_query_are_preserved(self):
        self.assertEqual(
            api_relay.build_target_url("/v1/messages?foo=bar", "https://api.anthropic.com"),
            "https://api.anthropic.com/v1/messages?foo=bar",
        )

    def test_empty_path_becomes_slash(self):
        self.assertEqual(
            api_relay.build_target_url("", "https://api.anthropic.com"),
            "https://api.anthropic.com/",
        )

    def test_path_only_no_query(self):
        self.assertEqual(
            api_relay.build_target_url("/v1/chat/completions", "https://api.openai.com"),
            "https://api.openai.com/v1/chat/completions",
        )


# ── Integration tests ──────────────────────────────────────────────────────────

class TestRelayIntegration(unittest.TestCase):

    def setUp(self):
        self.target, _ = _start_server(FakeTargetHandler)
        target_port = self.target.server_address[1]
        # Point the relay at our local stub (http, not https).
        self._orig_provider = api_relay.os.environ.get("API_RELAY_PROVIDER", "anthropic")
        api_relay.os.environ["API_RELAY_PROVIDER"] = "anthropic"
        # Patch get_target_base_url to return our stub address.
        self._orig_get_target = api_relay.get_target_base_url
        api_relay.get_target_base_url = lambda _p: f"http://127.0.0.1:{target_port}"
        self.relay, _ = _start_server(api_relay.RelayHandler)

    def tearDown(self):
        self.relay.shutdown(); self.relay.server_close()
        self.target.shutdown(); self.target.server_close()
        api_relay.get_target_base_url = self._orig_get_target
        api_relay.os.environ["API_RELAY_PROVIDER"] = self._orig_provider

    def _conn(self):
        return http.client.HTTPConnection("127.0.0.1", self.relay.server_address[1], timeout=5)

    def test_body_and_status_forwarded(self):
        payload = b'{"model":"claude-3","messages":[]}'
        conn = self._conn()
        conn.request("POST", "/v1/messages",
                     body=payload,
                     headers={"Content-Type": "application/json",
                              "x-api-key": "sk-test",
                              "Content-Length": str(len(payload))})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.read(), payload)

    def test_api_key_injected_in_upstream_header(self):
        payload = b"{}"
        conn = self._conn()
        conn.request("POST", "/v1/messages",
                     body=payload,
                     headers={"Content-Type": "application/json",
                              "x-api-key": "my-secret-key",
                              "Content-Length": str(len(payload))})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("X-Received-Key"), "my-secret-key")

    def test_bearer_token_extracted_as_api_key(self):
        payload = b"{}"
        conn = self._conn()
        conn.request("POST", "/v1/messages",
                     body=payload,
                     headers={"Content-Type": "application/json",
                              "Authorization": "Bearer bearer-key-123",
                              "Content-Length": str(len(payload))})
        resp = conn.getresponse()
        resp.read()
        self.assertEqual(resp.status, 200)

    def test_request_path_forwarded(self):
        payload = b"{}"
        conn = self._conn()
        conn.request("POST", "/v1/messages?stream=false",
                     body=payload,
                     headers={"Content-Type": "application/json",
                              "x-api-key": "k",
                              "Content-Length": str(len(payload))})
        resp = conn.getresponse()
        resp.read()
        self.assertEqual(resp.getheader("X-Request-Path"), "/v1/messages?stream=false")

    def test_502_on_upstream_unavailable(self):
        # Override to point at a port nothing is listening on.
        api_relay.get_target_base_url = lambda _p: "http://127.0.0.1:1"
        payload = b"{}"
        conn = self._conn()
        conn.request("POST", "/v1/messages",
                     body=payload,
                     headers={"Content-Type": "application/json",
                              "x-api-key": "k",
                              "Content-Length": str(len(payload))})
        resp = conn.getresponse()
        body = json.loads(resp.read())
        self.assertEqual(resp.status, 502)
        self.assertEqual(body["error"], "upstream_unavailable")


if __name__ == "__main__":
    unittest.main()
