import http.client
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import api_relay


class FakeTargetHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)

        self.send_response(200, "OK")
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Authorization", "test-auth-token")
        self.send_header("X-Request-Path", "/safe/path")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class RelayServerTests(unittest.TestCase):
    def test_build_target_url(self):
        self.assertEqual(
            api_relay.build_target_url("/v1/messages?foo=bar", "https://api.anthropic.com"),
            "https://api.anthropic.com/v1/messages?foo=bar",
        )

    def test_forward_request_to_target_api(self):
        target_server = ThreadingHTTPServer(("127.0.0.1", 0), FakeTargetHandler)
        target_port = target_server.server_address[1]
        target_thread = threading.Thread(target=target_server.serve_forever, daemon=True)
        target_thread.start()

        original_target = api_relay.TARGET_API_URL
        api_relay.TARGET_API_URL = f"http://127.0.0.1:{target_port}"

        relay_server = ThreadingHTTPServer(("127.0.0.1", 0), api_relay.RelayHandler)
        relay_port = relay_server.server_address[1]
        relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True)
        relay_thread.start()

        try:
            payload = b'{"hello":"world"}'
            connection = http.client.HTTPConnection("127.0.0.1", relay_port, timeout=5)
            connection.request(
                "POST",
                "/v1/messages",
                body=payload,
                headers={
                    "Authorization": "******",
                    "Content-Type": "application/json",
                    "X-Test": "value",
                },
            )
            response = connection.getresponse()
            response_body = response.read()

            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("X-Request-Path"), "/safe/path")
            self.assertEqual(response.getheader("X-Authorization"), "test-auth-token")
            self.assertEqual(response_body, payload)
        finally:
            relay_server.shutdown()
            relay_server.server_close()
            target_server.shutdown()
            target_server.server_close()
            api_relay.TARGET_API_URL = original_target


if __name__ == "__main__":
    unittest.main()
