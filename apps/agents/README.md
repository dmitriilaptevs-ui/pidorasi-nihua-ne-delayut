# Local agent runtime

The platform API runs Hermes and Pi through this loopback-only broker. It starts a fresh child process and temporary home for each request. Both are real text runtimes; filesystem, shell, browser, and all other agent tools are disabled. Each model request uses the user's platform API key and selected catalog model through `PLATFORM_API_BASE`.

## Requirements

- Python 3.11 or later with the packages in `requirements.txt`.
- Node.js 22.19 or later, with `npm ci` run in this directory for the Pi SDK.
- A Hermes Agent source checkout and its own Python environment. Set `HERMES_AGENT_DIR` to that checkout and `HERMES_PYTHON` to its Python executable. The broker does not install or alter Hermes.
- A random `AGENT_RUNTIME_TOKEN` shared with the platform API.
- `PLATFORM_API_BASE`, normally `http://127.0.0.1:8080/v1`.
- Optional `API_PROXY_TOKEN` when the platform API is behind a Sites proxy that requires `X-Rubai-Proxy-Token`.

The API accepts an HTTP runtime URL only when it resolves to a loopback host. Keep the broker on loopback. In the private rollout, the Sites Worker proxies web API requests to the API on the owner's computer; it does not expose the agent broker directly. Leave runtime settings empty until both engines are installed and tested.

## Start

```powershell
cd apps/agents
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
npm install
$env:AGENT_RUNTIME_TOKEN = '<same random token configured in the API>'
$env:PLATFORM_API_BASE = 'http://127.0.0.1:8080/v1'
$env:HERMES_AGENT_DIR = 'C:\path\to\hermes-agent'
$env:HERMES_PYTHON = 'C:\path\to\hermes-agent\venv\Scripts\python.exe'
python -m uvicorn app:app --host 127.0.0.1 --port 8090
```

For a Linux host, use the Hermes environment's `bin/python` and bind to `127.0.0.1` in the same way. The platform API uses `AGENT_RUNTIME_URL=http://127.0.0.1:8090` and the matching `AGENT_RUNTIME_TOKEN`.

## Upstream references

- [Pi SDK](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md) documents `createAgentSession`, in-memory sessions, model runtimes, and `noTools`.
- [Pi custom models](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md) documents OpenAI-compatible API endpoints.
- [Hermes toolsets](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/toolsets-reference.md) documents per-session toolset selection; this runner passes an empty enabled set and disables all toolsets.
