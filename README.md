# API Relay

A lightweight HTTP relay that accepts requests on port 8099 and forwards them to a single fixed upstream API provider (Anthropic by default; OpenAI is also supported by changing the provider constant or `--provider` flag).

Features:
- listens on all interfaces (`0.0.0.0:80`)
- forwards any HTTP method and request body to the configured upstream API endpoint
- forwards the response body and status back to the caller
- reads the API key from the caller request and injects the correct upstream auth header for the selected provider
- no streaming support

Default configuration:
- provider: `anthropic`
- upstream base URL: `https://api.anthropic.com`

Run:

```bash
python3 api_relay.py --host 0.0.0.0 --port 80 --provider anthropic
```

Example request to the relay:

```bash
curl -X POST http://localhost:80/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: YOUR_ANTHROPIC_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-3-5-sonnet-20240620",
    "max_tokens": 64,
    "messages": [{"role": "user", "content": "Hello from the relay"}]
  }'
```

For OpenAI instead, run:

```bash
python3 api_relay.py --provider openai
```

And call the relay with an OpenAI-compatible request, e.g.:

```bash
curl -X POST http://localhost:80/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_OPENAI_KEY" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello from the relay"}]
  }'
```

Note: the code uses a single fixed provider at a time, which matches the requirement for a simple console relay.
