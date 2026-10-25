#!/usr/bin/env python3
import argparse
import json
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

# Default upstream request timeout (seconds).  Override via --timeout or API_RELAY_TIMEOUT.
UPSTREAM_TIMEOUT = 180

# Headers that must never be forwarded between hops (RFC 2616 §13.5.1).
# content-length is excluded because we always rewrite it from the actual payload.
HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
}


def get_target_base_url(provider: str) -> str:
    """Return the upstream base URL for *provider*, env-override respected."""
    provider = (provider or "anthropic").lower()
    configured = os.environ.get("API_RELAY_TARGET")
    if configured:
        return configured.rstrip("/")
    return TARGETS.get(provider, TARGETS["anthropic"]).rstrip("/")


def build_target_url(path: str, base_url: str) -> str:
    """Compose the full upstream URL from *path* (may include query string) and *base_url*.

    The scheme and host are taken from *base_url* so that test stubs using plain
    HTTP work identically to production HTTPS targets.
    """
    split = urlsplit(path)
    clean_path = split.path or "/"
    # Guard against an absolute URL accidentally appearing in the path segment.
    if clean_path.startswith(("http://", "https://")):
        inner = urlsplit(clean_path)
        clean_path = inner.path or "/"
        query = inner.query
    else:
        query = split.query
    base = urlsplit(base_url)
    return urlunsplit((base.scheme, base.netloc, clean_path, query, ""))


class RelayHandler(BaseHTTPRequestHandler):
    server_version = "APIRelay/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write(
            f"{self.address_string()} - - [{self.log_date_time_string()}] {fmt % args}\n"
        )

    # ── Core relay logic ───────────────────────────────────────────────────────

    def _proxy_request(self):
        provider = os.environ.get("API_RELAY_PROVIDER", "anthropic").lower()
        base_url = get_target_base_url(provider)
        target_url = build_target_url(self.path, base_url)

        api_key = self._extract_api_key()
        body = self._read_body()
        headers = self._build_upstream_headers(provider, api_key)

        req = Request(target_url, data=body or None, headers=headers, method=self.command)
        try:
            with urlopen(req, timeout=self.server.upstream_timeout) as response:
                payload = response.read()
                self._send_response(response.status, payload, response.headers)
        except HTTPError as exc:
            payload = exc.read()
            self._send_response(exc.code, payload, exc.headers)
        except URLError as exc:
            body_bytes = json.dumps(
                {"error": "upstream_unavailable", "message": str(exc.reason)}
            ).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.end_headers()
            self.wfile.write(body_bytes)

    def _extract_api_key(self) -> str:
        """Pull the API key from the incoming request headers or query string."""
        incoming = {k.lower(): v for k, v in self.headers.items()}

        if "x-api-key" in incoming:
            return incoming["x-api-key"]

        auth = incoming.get("authorization", "")
        if auth:
            return auth[7:].strip() if auth.lower().startswith("bearer ") else auth.strip()

        query_values = parse_qs(urlsplit(self.path).query)
        for key in ("api_key", "api-key"):
            if query_values.get(key):
                return query_values[key][0]

        return ""

    def _read_body(self) -> bytes:
        """Read exactly Content-Length bytes from the request body."""
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            return b""
        try:
            length = int(content_length)
        except ValueError:
            return b""
        return self.rfile.read(length) if length > 0 else b""

    def _build_upstream_headers(self, provider: str, api_key: str) -> dict:
        """Build the header dict to send to the upstream API."""
        forward = {}

        # Forward safe, non-hop-by-hop headers from the caller.
        for key, value in self.headers.items():
            lower = key.lower()
            if lower in HOP_BY_HOP_HEADERS:
                continue
            # Strip inbound auth headers; we will re-inject them correctly below.
            if lower in {"authorization", "x-api-key", "api-key"}:
                continue
            forward[key] = value

        # Inject provider-specific auth.
        if provider == "anthropic":
            forward["x-api-key"] = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            # Respect the caller's version header; fall back to env / default.
            if "anthropic-version" not in {k.lower() for k in forward}:
                forward["anthropic-version"] = os.environ.get(
                    "ANTHROPIC_VERSION", "2023-06-01"
                )
        elif provider == "openai":
            key = api_key or os.environ.get("OPENAI_API_KEY", "")
            if key:
                forward["Authorization"] = f"Bearer {key}"

        return forward

    def _send_response(self, status: int, payload: bytes, incoming_headers) -> None:
        self.send_response(status)
        for key, value in incoming_headers.items():
            if key.lower() not in HOP_BY_HOP_HEADERS:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    # ── HTTP verb dispatch ─────────────────────────────────────────────────────
    # All verbs share identical proxy logic; chained class-level assignment
    # avoids seven identical one-liner stubs.  Must appear after _proxy_request.

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = (
        _proxy_request
    )


# ── CLI & entry-point ──────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Lightweight HTTP relay – forwards requests to a fixed upstream API provider."
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("API_RELAY_HOST", "0.0.0.0"),
        help="Listen address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("API_RELAY_PORT", "8099")),
        help="Listen port (default: 8099)",
    )
    parser.add_argument(
        "--provider",
        default=os.environ.get("API_RELAY_PROVIDER", "anthropic"),
        choices=list(TARGETS),
        help="Upstream provider (default: anthropic)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("API_RELAY_TIMEOUT", str(UPSTREAM_TIMEOUT))),
        metavar="SECONDS",
        help=f"Upstream request timeout in seconds (default: {UPSTREAM_TIMEOUT})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.environ["API_RELAY_PROVIDER"] = args.provider.lower()

    target = get_target_base_url(args.provider)
    server = ThreadingHTTPServer((args.host, args.port), RelayHandler)
    server.upstream_timeout = args.timeout  # made available to RelayHandler via self.server
    print(
        f"API relay listening on http://{args.host}:{args.port}  →  {target}"
        f"  (timeout: {args.timeout}s)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping API relay.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
