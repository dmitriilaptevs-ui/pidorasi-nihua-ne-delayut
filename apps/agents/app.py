"""Authenticated local broker for isolated Hermes and Pi processes."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_PROMPT_CHARS = 12_000
MAX_BODY_BYTES = MAX_PROMPT_CHARS * 6 + 4096
MAX_TEXT_CHARS = 64_000
MAX_MODEL_CHARS = 200
MAX_KEY_CHARS = 120
RUN_TIMEOUT_SECONDS = 130
MAX_CONCURRENT_RUNS = 4
RUNS = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
AGENT_ROOT = Path(__file__).resolve().parent

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: str = Field(min_length=1, max_length=16)
    api_key: str = Field(min_length=20, max_length=MAX_KEY_CHARS)
    model: str = Field(min_length=1, max_length=MAX_MODEL_CHARS)
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)


def _auth_ok(request: Request) -> bool:
    expected = os.getenv("AGENT_RUNTIME_TOKEN", "")
    authorization = request.headers.get("authorization", "")
    supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    return bool(expected and supplied and hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8")))


def _safe_platform_api_base() -> str | None:
    value = os.getenv("PLATFORM_API_BASE", "").strip().rstrip("/")
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
            return value
        if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
            return None
        try:
            address = ipaddress.ip_address(parsed.hostname)
            return value if address.is_loopback else None
        except ValueError:
            return value if parsed.hostname.lower() == "localhost" else None
    except ValueError:
        return None


def _availability() -> dict[str, bool]:
    hermes_source = os.getenv("HERMES_AGENT_DIR", "")
    hermes_python = os.getenv("HERMES_PYTHON", "")
    return {
        "hermes": bool(hermes_source and Path(hermes_source, "run_agent.py").is_file() and hermes_python and Path(hermes_python).is_file()),
        "pi": bool(shutil.which("node") and (AGENT_ROOT / "node_modules" / "@earendil-works" / "pi-coding-agent").is_dir()),
    }


async def _read_json(request: Request) -> object:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise ValueError("body too large")
    return json.loads(body)


def _child_env(home: Path, platform_api_base: str) -> dict[str, str]:
    env: dict[str, str] = {"PATH": os.environ.get("PATH", "")}
    for name in ("SystemRoot", "WINDIR", "COMSPEC"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    env.update({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "TMP": str(home),
        "TEMP": str(home),
        "HERMES_HOME": str(home / ".hermes"),
        "PLATFORM_API_BASE": platform_api_base,
    })
    return env


async def _run_process(command: list[str], *, cwd: Path, env: dict[str, str], payload: dict) -> str:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        process.stdin.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()

        async def read_bounded_output() -> bytes:
            output = bytearray()
            while chunk := await process.stdout.read(8192):
                output.extend(chunk)
                if len(output) > MAX_TEXT_CHARS + 100:
                    process.kill()
                    raise RuntimeError("runner output exceeded limit")
            return bytes(output)

        stdout, _ = await asyncio.wait_for(
            asyncio.gather(read_bounded_output(), process.wait()),
            timeout=RUN_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise
    except Exception:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode != 0 or len(stdout) > MAX_TEXT_CHARS + 100:
        raise RuntimeError("runner failed")
    response = json.loads(stdout)
    text = response.get("text") if isinstance(response, dict) else None
    if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
        raise RuntimeError("runner returned invalid output")
    return text


@app.get("/health")
async def health(request: Request):
    if not _auth_ok(request):
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    return {"agents": _availability()}


@app.post("/run")
async def run(request: Request):
    if not _auth_ok(request):
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    try:
        payload = RunRequest.model_validate(await _read_json(request))
    except (ValueError, json.JSONDecodeError, ValidationError):
        return JSONResponse(status_code=400, content={"error": "invalid_request"})
    if payload.engine not in {"hermes", "pi"}:
        return JSONResponse(status_code=400, content={"error": "invalid_request"})
    platform_api_base = _safe_platform_api_base()
    available = _availability()
    if platform_api_base is None or not available[payload.engine]:
        return JSONResponse(status_code=503, content={"error": "runtime_unavailable"})

    async with RUNS:
        with tempfile.TemporaryDirectory(prefix="rubai-agent-") as temp_dir:
            work_dir = Path(temp_dir)
            home = work_dir / "home"
            home.mkdir()
            env = _child_env(home, platform_api_base)
            child_payload = {
                "api_key": payload.api_key,
                "model": payload.model,
                "prompt": payload.prompt,
                "platform_api_base": platform_api_base,
                "proxy_token": os.getenv("API_PROXY_TOKEN", ""),
            }
            if payload.engine == "pi":
                command = ["node", str(AGENT_ROOT / "pi-runner.mjs")]
                cwd = work_dir
            else:
                python = Path(os.environ["HERMES_PYTHON"])
                source = Path(os.environ["HERMES_AGENT_DIR"]).resolve()
                env["HERMES_AGENT_DIR"] = str(source)
                command = [str(python), str(AGENT_ROOT / "hermes-runner.py")]
                cwd = work_dir
            try:
                text = await _run_process(command, cwd=cwd, env=env, payload=child_payload)
            except asyncio.TimeoutError:
                return JSONResponse(status_code=504, content={"error": "agent_timeout"})
            except Exception:
                return JSONResponse(status_code=502, content={"error": "agent_failed"})
    return {"text": text}
