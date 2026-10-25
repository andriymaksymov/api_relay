#!/usr/bin/env python3
"""Simple API relay that forwards incoming HTTP requests to a fixed target API."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Iterable, Tuple
from urllib import error, request

TARGET_API_URL = "https://api.anthropic.com"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8099


def build_target_url(path: str, target_base: str | None = None) -> str:
    """Build the target URL for the incoming request path."""
    base = TARGET_API_URL if target_base is None else target_base
    normalized_base = base.rstrip("/")
    if not path or path == "/":
        return normalized_base
    return f"{normalized_base}{path}"


def _sanitize_header_value(value: str) -> str:
    return value.replace("\r", "").replace("\n", "")


def _filtered_headers(headers: Iterable[Tuple[str, str]]) -> Dict[str, str]:
    excluded = {
        "host",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
    filtered: Dict[str, str] = {}
    for name, value in headers:
        if name.lower() in excluded:
            continue
        filtered[name] = _sanitize_header_value(value)
    return filtered


def relay_request(
    path: str,
    method: str,
    headers: Dict[str, str],
    body: bytes,
    target_base: str | None = None,
    timeout: int = 60,
):
    """Forward one request to the configured upstream API and return the result."""
    target_url = build_target_url(path, target_base)
    outbound_headers = _filtered_headers(headers.items())
    request_obj = request.Request(target_url, data=body, headers=outbound_headers, method=method)

    try:
        with request.urlopen(request_obj, timeout=timeout) as response:
            payload = response.read()
            return response.status, response.reason, dict(response.headers.items()), payload
    except error.HTTPError as exc:
        payload = exc.read()
        return exc.code, exc.reason, dict(exc.headers.items()), payload


class RelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "api_relay/1.0"

    def do_GET(self):
        self._handle_request()

    def do_POST(self):
        self._handle_request()

    def do_PUT(self):
        self._handle_request()

    def do_DELETE(self):
        self._handle_request()

    def do_PATCH(self):
        self._handle_request()

    def do_HEAD(self):
        self._handle_request()

    def do_OPTIONS(self):
        self._handle_request()

    def _handle_request(self):
        body_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(body_length) if body_length > 0 else b""

        try:
            status, reason, response_headers, response_body = relay_request(
                path=self.path,
                method=self.command,
                headers=dict(self.headers.items()),
                body=body,
                target_base=TARGET_API_URL,
            )
        except Exception as exc:  # pragma: no cover - defensive last-resort error handling
            payload = json.dumps({"error": str(exc)}).encode("utf-8")
            self.send_response(502, "Bad Gateway")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        self.send_response(status, reason)
        for name, value in response_headers.items():
            lower_name = name.lower()
            if lower_name in {"transfer-encoding", "connection"}:
                continue
            self.send_header(name, _sanitize_header_value(value))
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()

        if self.command != "HEAD" and response_body:
            self.wfile.write(response_body)

    def log_message(self, format, *args):
        return


def main() -> None:
    global TARGET_API_URL

    parser = argparse.ArgumentParser(description="Simple HTTP relay for API traffic.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Interface to bind to (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"TCP port to listen on (default: {DEFAULT_PORT})")
    parser.add_argument(
        "--target-url",
        default=TARGET_API_URL,
        help=f"Upstream API endpoint to forward to (default: {TARGET_API_URL})",
    )
    args = parser.parse_args()

    TARGET_API_URL = args.target_url

    server = ThreadingHTTPServer((args.host, args.port), RelayHandler)
    print(f"API relay listening on {args.host}:{args.port} -> {TARGET_API_URL}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping relay.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
