# api_relay

A lightweight Python API relay that accepts HTTP requests from a local app and forwards them to a fixed upstream API endpoint, such as Anthropic or OpenAI.

Usage:

```bash
python api_relay.py --host 0.0.0.0 --port 8099 --target-url https://api.anthropic.com
```

The relay listens on all interfaces by default on TCP port 8099 and forwards the request path, headers, and body to the configured upstream API while passing the upstream response back to the caller without modifying the payload.
