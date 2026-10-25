#!/usr/bin/env python3
import argparse
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit, urlunsplit
from urllib.request import Request, urlopen


TARGETS = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
}

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def get_target_base_url(provider: str) -> str:
    provider = (provider or "anthropic").lower()
    configured = os.environ.get("API_RELAY_TARGET")
    if configured:
        return configured.rstrip("/")
    return TARGETS.get(provider, TARGETS["anthropic"]).rstrip("/")


def get_api_key(headers) -> str:
    value = headers.get("x-api-key") or headers.get("X-API-Key")
    if value:
        return value

    auth = headers.get("Authorization") or headers.get("authorization")
    if auth:
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return auth.strip()

    for query_name in ("api_key", "api-key"):
        for _, values in parse_qs(urlsplit(headers.get("_path", "")).query).items():
            if values:
                return values[0]
    return ""


class RelayHandler(BaseHTTPRequestHandler):
    server_version = "APIRelay/1.0"

    def do_GET(self):
        self._proxy_request()

    def do_POST(self):
        self._proxy_request()

    def do_PUT(self):
        self._proxy_request()

    def do_PATCH(self):
        self._proxy_request()

    def do_DELETE(self):
        self._proxy_request()

    def do_HEAD(self):
        self._proxy_request()

    def do_OPTIONS(self):
        self._proxy_request()

    def log_message(self, format, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format % args))

    def _proxy_request(self):
        target_url = self._build_target_url()
        api_key = self._extract_api_key()
        body = self._read_body()
        headers = self._build_upstream_headers(api_key)

        request = Request(target_url, data=body, headers=headers, method=self.command)
        try:
            with urlopen(request, timeout=60) as response:
                payload = response.read()
                self._send_response(response.status, payload, response.headers)
        except HTTPError as exc:
            payload = exc.read()
            self._send_response(exc.code, payload, exc.headers)
        except URLError as exc:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                ({"error": "upstream_unavailable", "message": str(exc.reason)}).encode("utf-8")
            )

    def _build_target_url(self):
        provider = os.environ.get("API_RELAY_PROVIDER", "anthropic").lower()
        base_url = get_target_base_url(provider)
        split_url = urlsplit(self.path)
        path = split_url.path or "/"
        if path.startswith("http://") or path.startswith("https://"):
            target = urlsplit(path)
            path = target.path or "/"
            query = target.query
        else:
            query = split_url.query

        return urlunsplit(("https", urlsplit(base_url).netloc, path, query, ""))

    def _extract_api_key(self):
        incoming = {k.lower(): v for k, v in self.headers.items()}
        if "x-api-key" in incoming:
            return incoming["x-api-key"]

        auth = incoming.get("authorization")
        if auth:
            if auth.lower().startswith("bearer "):
                return auth[7:].strip()
            return auth.strip()

        parsed = urlsplit(self.path)
        query_values = parse_qs(parsed.query)
        for key in ("api_key", "api-key"):
            if key in query_values and query_values[key]:
                return query_values[key][0]
        return ""

    def _read_body(self):
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            return b""
        try:
            length = int(content_length)
        except ValueError:
            return b""
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _build_upstream_headers(self, api_key: str):
        provider = os.environ.get("API_RELAY_PROVIDER", "anthropic").lower()
        forward_headers = {}

        for key, value in self.headers.items():
            lower_key = key.lower()
            if lower_key in HOP_BY_HOP_HEADERS:
                continue
            if lower_key in {"authorization", "x-api-key", "api-key"}:
                continue
            forward_headers[key] = value

        if provider == "anthropic":
            forward_headers["x-api-key"] = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            forward_headers["anthropic-version"] = os.environ.get("ANTHROPIC_VERSION", "2023-06-01")
        elif provider == "openai":
            key = api_key or os.environ.get("OPENAI_API_KEY", "")
            if key:
                forward_headers["Authorization"] = f"Bearer {key}"

        return forward_headers

    def _send_response(self, status, payload, incoming_headers):
        self.send_response(status)
        for key, value in incoming_headers.items():
            lower_key = key.lower()
            if lower_key in HOP_BY_HOP_HEADERS:
                continue
            if lower_key in {"transfer-encoding", "content-length"}:
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)


def parse_args():
    parser = argparse.ArgumentParser(description="Simple API relay forwarding requests to a static upstream API provider.")
    parser.add_argument("--host", default=os.environ.get("API_RELAY_HOST", "0.0.0.0"), help="Listen address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("API_RELAY_PORT", "8099")), help="Listen port (default: 8099)")
    parser.add_argument("--provider", default=os.environ.get("API_RELAY_PROVIDER", "anthropic"), help="Upstream provider: anthropic or openai")
    return parser.parse_args()


def main():
    args = parse_args()
    os.environ["API_RELAY_PROVIDER"] = args.provider.lower()

    server = ThreadingHTTPServer((args.host, args.port), RelayHandler)
    print(f"API relay listening on http://{args.host}:{args.port} -> {get_target_base_url(args.provider)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping API relay.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
