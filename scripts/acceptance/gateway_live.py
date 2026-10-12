#!/usr/bin/env python3
"""Live black-box gateway acceptance pack (OpenAI SDK + read-only witnesses).

Exercises the OpenAI-compatible gateway with the real ``openai`` SDK and raw
HTTP. The owner's provider key is still absent, so the live free/paid success
path is recorded as BLOCKED; everything else is asserted now:

- ``GET /v1/models`` shape (SDK and raw), every item priced, no negative prices
- auth failures: 401 missing / invalid / revoked platform key
- routing failures: 400 invalid ``max_tokens``, 404 ``model_not_found``
- money gates before any upstream call: 402 ``insufficient_funds`` on a
  zero-balance account, 429 ``key_limit_exceeded`` on a zero-limit key
- reserve-before-send: with funds, the call reaches upstream-configured check
  (503 ``upstream_not_configured``) only after a positive reserve row exists;
  the reserve is then released, the wallet projections return to their prior
  values, and a resolved ``released/upstream_not_configured`` reconciliation
  record is written (no *open* items)
- SSE request with funds fails the same way and releases its reserve

Usage::

    .venv/bin/python scripts/acceptance/gateway_live.py \
        --base-url http://127.0.0.1:8080 \
        --admin-password-file /tmp/admin-pw.txt \
        --admin-email admin@example.com

Evidence JSON (no keys: raw keys are redacted to length+sha256 prefix) goes
under ``scripts/acceptance/evidence/``. Exit code 0 only if every required
step passed.
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import shlex
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import openai

sys.path.insert(0, str(Path(__file__).resolve().parent))
import identity_live as il  # noqa: E402

UTC = timezone.utc

RESERVE_WITNESS = """
import asyncio, asyncpg, json, os, sys
from datetime import datetime

async def main():
    cfg = json.loads(sys.stdin.read())
    since = datetime.fromisoformat(cfg["since"].replace("Z", "+00:00"))
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(
        "SELECT request_ref, amount_kopecks, status, created_at, released_at"
        " FROM reserves WHERE api_key_id = $1::uuid AND created_at >= $2"
        " ORDER BY created_at",
        cfg["api_key_id"], since,
    )
    refs = [row["request_ref"] for row in rows]
    items = await conn.fetch(
        "SELECT request_ref, kind, status, payload FROM reconciliation_items"
        " WHERE request_ref = ANY($1::text[]) ORDER BY created_at",
        refs,
    )
    wallet = None
    if cfg.get("user_id"):
        wallet = await conn.fetchrow(
            "SELECT balance_kopecks, reserved_kopecks FROM wallets WHERE user_id = $1::uuid",
            cfg["user_id"],
        )
    payload = {
        "reserves": [dict(row) for row in rows],
        "reconciliation": [dict(item) for item in items],
        "wallet": dict(wallet) if wallet else None,
    }
    print(json.dumps(payload, default=str))
    await conn.close()

asyncio.run(main())
"""


def witness(container: str, script: str, payload: dict[str, Any]) -> dict[str, Any]:
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    code = "import base64,sys;exec(base64.b64decode('%s'))" % encoded
    inner = (
        f"printf %s {shlex.quote(json.dumps(payload))} | "
        f"docker exec -i {shlex.quote(container)} python -c {shlex.quote(code)}"
    )
    proc = il.docker_shell(inner)
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"_error": ((proc.stderr or proc.stdout) or "no output").strip()[:300]}


def env_value(container: str, name: str) -> str:
    proc = il.docker_shell(f"docker exec {shlex.quote(container)} env")
    for line in (proc.stdout or "").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return ""


def admin_login(client: httpx.Client, args: argparse.Namespace, origin: str) -> tuple[bool, str | None, str]:
    path = Path(args.admin_password_file)
    if not path.exists():
        return False, None, f"admin password file not found: {path}"
    password = path.read_text(encoding="utf-8").strip()
    response = client.post("/api/auth/login", json={"email": args.admin_email, "password": password})
    cookie = il.cookie_from_response(response)
    if response.status_code != 200 or not cookie:
        return False, None, f"admin login HTTP {response.status_code}: {il.body_snippet(response)}"
    return True, cookie, ""


def origin_headers(origin: str) -> dict[str, str]:
    return {"Origin": origin} if origin else {}


def snippet_text(stdout: str | None, stderr: str | None, limit: int = 200) -> str:
    return (((stdout or "") + " " + (stderr or "")).replace("\n", " ").strip())[:limit]


def raw_error_shape(response: httpx.Response) -> tuple[int, str]:
    try:
        code = response.json().get("error", {}).get("code", "")
    except ValueError:
        code = ""
    return response.status_code, code


def sdk_client(base_url: str, api_key: str, timeout: float = 60.0) -> openai.OpenAI:
    return openai.OpenAI(base_url=f"{base_url.rstrip('/')}/v1", api_key=api_key, max_retries=0, timeout=timeout)


def sdk_call_error(client: openai.OpenAI, **kwargs: Any) -> tuple[int | None, str, str]:
    """Return (status, error_code_or_type, exception_name) for a chat call."""
    try:
        client.chat.completions.create(**kwargs)
        return None, "", "no-error"
    except openai.APIStatusError as exc:
        code = ""
        if isinstance(exc.body, dict):
            code = str(exc.body.get("error", {}).get("code", ""))
        return exc.status_code, code, type(exc).__name__
    except openai.OpenAIError as exc:  # pragma: no cover - transport-level
        return None, "", type(exc).__name__


def run(args: argparse.Namespace) -> int:  # noqa: C901 - linear acceptance script
    evidence = il.Evidence()
    client = httpx.Client(base_url=args.base_url, timeout=60.0, follow_redirects=False)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"{stamp}-{secrets.token_hex(3)}"
    admin_password_file = Path(args.admin_password_file)
    since = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    origin = il.docker_shell(f"docker exec {shlex.quote(args.api_container)} env").stdout or ""
    origin = next((line.split("=", 1)[1] for line in origin.splitlines() if line.startswith("PUBLIC_ORIGIN=")), "")
    provider_key_present = bool(env_value(args.api_container, "OPENROUTER_API_KEY"))

    evidence.note(f"base_url={args.base_url}")
    evidence.note(f"api_container={args.api_container}")
    evidence.note(f"api_image={il.api_image_id(args.api_container)}")
    evidence.note(f"provider_key_configured={provider_key_present}")

    for path, expected in (("/healthz", 200), ("/readyz", 200)):
        response = client.get(path)
        evidence.record(
            f"liveness {path}",
            f"HTTP {expected}",
            f"HTTP {response.status_code}",
            ok=response.status_code == expected,
        )

    admin_ok, admin_cookie, admin_why = admin_login(client, args, origin)
    evidence.record("admin session", "login as admin", admin_why or "established", ok=admin_ok)
    if not admin_ok or not admin_cookie:
        return finish(evidence, args)
    admin_auth = il.auth_headers(admin_cookie)

    me = client.get("/api/auth/me", headers=admin_auth)
    admin_user_id = me.json().get("user", {}).get("id")
    if not admin_user_id:
        evidence.record("admin identity", "user id from /me", f"HTTP {me.status_code} {il.body_snippet(me)}", ok=False)
        return finish(evidence, args)

    # --- models --------------------------------------------------------------
    raw_models = client.get("/v1/models")
    raw_items = raw_models.json().get("data", []) if raw_models.status_code == 200 else []
    raw_ok = (
        raw_models.status_code == 200
        and raw_models.json().get("object") == "list"
        and len(raw_items) >= 400
        and all(item.get("object") == "model" for item in raw_items)
        and all(
            isinstance((item.get("x-rubai-pricing") or {}).get("input_rub_per_mtok"), str)
            for item in raw_items
        )
        and not [
            item["id"]
            for item in raw_items
            if str((item.get("x-rubai-pricing") or {}).get("input_rub_per_mtok", "")).startswith("-")
            or str((item.get("x-rubai-pricing") or {}).get("output_rub_per_mtok", "")).startswith("-")
        ]
    )
    evidence.record(
        "v1/models raw shape and pricing",
        "HTTP 200, object=list, >=400 models, every item priced and non-negative",
        f"HTTP {raw_models.status_code}, count={len(raw_items)}, negative="
        f"{sum(1 for i in raw_items if str((i.get('x-rubai-pricing') or {}).get('input_rub_per_mtok', '')).startswith('-'))}",
        ok=raw_ok,
    )

    try:
        page = sdk_client(args.base_url, "sk-rubai-unused-for-models").models.list()
        sdk_ids = [model.id for model in page.data]
        sdk_ok = len(sdk_ids) >= 400 and set(sdk_ids) == {item["id"] for item in raw_items}
        evidence.record(
            "OpenAI SDK models.list",
            "same model ids as raw HTTP",
            f"SDK count={len(sdk_ids)}",
            ok=sdk_ok,
        )
    except openai.OpenAIError as exc:
        evidence.record("OpenAI SDK models.list", "list page", f"{type(exc).__name__}: {exc}", ok=False)

    if args.public_base_url:
        try:
            public_page = sdk_client(args.public_base_url, "sk-rubai-unused-for-models").models.list()
            evidence.record(
                "OpenAI SDK models.list via deployed origin",
                "same ids through the public HTTPS base URL",
                f"{args.public_base_url}: count={len(public_page.data)}",
                ok=len(public_page.data) == len(raw_items),
            )
        except openai.OpenAIError as exc:
            evidence.record(
                "OpenAI SDK models.list via deployed origin",
                "same ids through the public HTTPS base URL",
                f"{type(exc).__name__}: {exc}",
                ok=False,
            )

    # --- pick a cheap paid model and a key -----------------------------------
    paid = None
    for item in raw_items:
        pricing = item.get("x-rubai-pricing") or {}
        try:
            out_price = float(pricing.get("output_rub_per_mtok", "0"))
        except ValueError:
            continue
        if out_price > 0:
            paid = (item["id"], pricing)
            break
    evidence.record("cheap paid model available", "an available model with a positive price", f"{paid[0] if paid else None}", ok=paid is not None)
    if paid is None:
        return finish(evidence, args)
    paid_model = paid[0]
    chat = {"model": paid_model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 16}

    created = client.post(
        "/api/keys", json={"name": f"gateway-acceptance-{suffix}"}, headers={**origin_headers(origin), **admin_auth}
    )
    main_key = created.json().get("key") if created.status_code == 201 else None
    created_item = created.json().get("item") if created.status_code == 201 else None
    evidence.record(
        "platform key for gateway",
        "HTTP 201 with raw key",
        f"HTTP {created.status_code} raw={il.token_fingerprint(main_key) if main_key else 'missing'}",
        ok=created.status_code == 201 and isinstance(main_key, str),
    )
    if not main_key or not created_item:
        return finish(evidence, args)

    revoked_key = None
    revoked = client.post(
        "/api/keys", json={"name": f"gateway-revoked-{suffix}"}, headers={**origin_headers(origin), **admin_auth}
    )
    if revoked.status_code == 201:
        revoked_key = revoked.json()["key"]
        revoked_item = revoked.json()["item"]
        client.post(
            f"/api/keys/{revoked_item['id']}/revoke", headers={**origin_headers(origin), **admin_auth}
        )

    # --- failure surfaces ----------------------------------------------------
    missing = client.post("/v1/chat/completions", json=chat)
    status, code = raw_error_shape(missing)
    evidence.record(
        "401 missing key",
        "HTTP 401 missing_api_key",
        f"HTTP {status} {code}",
        ok=status == 401 and code == "missing_api_key",
    )

    invalid = client.post(
        "/v1/chat/completions", json=chat, headers={"Authorization": "Bearer sk-rubai-not-a-real-key"}
    )
    status, code = raw_error_shape(invalid)
    sdk_status, sdk_code, sdk_name = sdk_call_error(
        sdk_client(args.base_url, "sk-rubai-not-a-real-key"), **chat
    )
    evidence.record(
        "401 invalid key",
        "HTTP 401 invalid_api_key; SDK AuthenticationError",
        f"HTTP {status} {code}; SDK {sdk_name} {sdk_status} {sdk_code}",
        ok=status == 401 and code == "invalid_api_key" and sdk_name == "AuthenticationError",
    )

    if revoked_key:
        revoked_call = client.post(
            "/v1/chat/completions", json=chat, headers={"Authorization": f"Bearer {revoked_key}"}
        )
        status, code = raw_error_shape(revoked_call)
        evidence.record(
            "401 revoked key",
            "HTTP 401 invalid_api_key",
            f"HTTP {status} {code}",
            ok=status == 401 and code == "invalid_api_key",
        )
    else:
        evidence.record("401 revoked key", "HTTP 401", "key creation failed", ok=False)

    unknown = client.post(
        "/v1/chat/completions",
        json={**chat, "model": "acceptance/does-not-exist"},
        headers={"Authorization": f"Bearer {main_key}"},
    )
    status, code = raw_error_shape(unknown)
    sdk_status, sdk_code, sdk_name = sdk_call_error(
        sdk_client(args.base_url, main_key), model="acceptance/does-not-exist", messages=chat["messages"], max_tokens=16
    )
    evidence.record(
        "404 model_not_found",
        "HTTP 404 model_not_found; SDK NotFoundError",
        f"HTTP {status} {code}; SDK {sdk_name} {sdk_status} {sdk_code}",
        ok=status == 404 and code == "model_not_found" and sdk_name == "NotFoundError",
    )

    bad_tokens = client.post(
        "/v1/chat/completions",
        json={**chat, "max_tokens": 0},
        headers={"Authorization": f"Bearer {main_key}"},
    )
    status, code = raw_error_shape(bad_tokens)
    evidence.record(
        "400 invalid max_tokens",
        "HTTP 400 invalid_max_tokens",
        f"HTTP {status} {code}",
        ok=status == 400 and code == "invalid_max_tokens",
    )

    # --- zero-balance account: 402 before any upstream -----------------------
    zero_email = f"gateway-zero+{suffix}@example.com".lower()
    zero_password = "Zero-pass-" + secrets.token_urlsafe(9)
    bootstrap = il.docker_shell(
        f"docker exec -e ADMIN_PASSWORD={shlex.quote(zero_password)} {shlex.quote(args.api_container)} "
        f"python -m app.cli create-admin --email {shlex.quote(zero_email)} --password-env ADMIN_PASSWORD"
    )
    zero_ok = bootstrap.returncode == 0 and "admin ready" in (bootstrap.stdout or "")
    zero_cookie = None
    zero_user_id = None
    if zero_ok:
        zero_login = client.post("/api/auth/login", json={"email": zero_email, "password": zero_password})
        zero_cookie = il.cookie_from_response(zero_login)
        if zero_cookie:
            zero_me = client.get("/api/auth/me", headers=il.auth_headers(zero_cookie))
            zero_user_id = zero_me.json().get("user", {}).get("id") if zero_me.status_code == 200 else None
    zero_key = None
    zero_key_id = None
    if zero_cookie:
        zero_created = client.post(
            "/api/keys",
            json={"name": f"gateway-zero-{suffix}"},
            headers={**origin_headers(origin), **il.auth_headers(zero_cookie)},
        )
        if zero_created.status_code == 201:
            zero_key = zero_created.json()["key"]
            zero_key_id = zero_created.json()["item"]["id"]
    if zero_key:
        zero_call = client.post(
            "/v1/chat/completions", json=chat, headers={"Authorization": f"Bearer {zero_key}"}
        )
        status, code = raw_error_shape(zero_call)
        sdk_status, sdk_code, sdk_name = sdk_call_error(sdk_client(args.base_url, zero_key), **chat)
        # No upstream call happened: the provider key is absent, so reaching
        # upstream would give 503. A 402 proves the funds gate ran first.
        evidence.record(
            "402 insufficient funds before upstream",
            "HTTP 402 insufficient_funds; no reserve row created; SDK APIStatusError 402",
            f"HTTP {status} {code}; SDK {sdk_name} {sdk_status} {sdk_code}",
            ok=status == 402 and code == "insufficient_funds" and sdk_status == 402,
        )
        zero_wallet = witness(
            args.api_container,
            RESERVE_WITNESS,
            {"api_key_id": zero_key_id, "user_id": zero_user_id, "since": since},
        )
        evidence.record(
            "402 left no reserve",
            "zero reserves for the failed request",
            json.dumps(zero_wallet.get("reserves", zero_wallet)),
            ok=zero_wallet.get("reserves") == [],
        )
    else:
        evidence.record("402 insufficient funds before upstream", "HTTP 402", f"zero-balance setup failed: {snippet_text(bootstrap.stdout, bootstrap.stderr)}", ok=False)

    # --- 429 key limit before upstream --------------------------------------
    limited = client.post(
        "/api/keys",
        json={"name": f"gateway-limited-{suffix}", "monthly_limit_kopecks": 0},
        headers={**origin_headers(origin), **admin_auth},
    )
    limited_key = limited.json().get("key") if limited.status_code == 201 else None
    limited_id = limited.json().get("item", {}).get("id") if limited.status_code == 201 else None
    if limited_key:
        limited_call = client.post(
            "/v1/chat/completions", json=chat, headers={"Authorization": f"Bearer {limited_key}"}
        )
        status, code = raw_error_shape(limited_call)
        sdk_status, sdk_code, sdk_name = sdk_call_error(sdk_client(args.base_url, limited_key), **chat)
        evidence.record(
            "429 monthly key limit before upstream",
            "HTTP 429 key_limit_exceeded; SDK RateLimitError",
            f"HTTP {status} {code}; SDK {sdk_name} {sdk_status} {sdk_code}",
            ok=status == 429 and code == "key_limit_exceeded" and sdk_name == "RateLimitError",
        )
    else:
        evidence.record("429 monthly key limit before upstream", "HTTP 429", "limited key creation failed", ok=False)

    # --- funded path: reserve committed, upstream absent ----------------------
    wallet_before = client.get("/api/wallet", headers=admin_auth)
    state_before = wallet_before.json().get("wallet", {}) if wallet_before.status_code == 200 else {}
    credit_ref = f"gateway-acceptance-{suffix}"
    credit = client.post(
        "/api/admin/wallet/credit",
        json={"email": args.admin_email, "amount_kopecks": args.credit_kopecks, "reference": credit_ref},
        headers={**origin_headers(origin), **admin_auth},
    )
    credited = credit.json().get("wallet", {}) if credit.status_code == 200 else {}
    evidence.record(
        "sandbox credit via admin endpoint",
        "HTTP 200, balance increases",
        f"HTTP {credit.status_code} balance {state_before.get('balance_kopecks')} -> {credited.get('balance_kopecks')}",
        ok=credit.status_code == 200 and credited.get("balance_kopecks", 0) >= args.credit_kopecks,
    )

    reconciliation_before = client.get("/api/admin/reconciliation", headers=admin_auth)
    open_before = len(reconciliation_before.json().get("items", [])) if reconciliation_before.status_code == 200 else None

    funded = client.post("/v1/chat/completions", json=chat, headers={"Authorization": f"Bearer {main_key}"})
    status, code = raw_error_shape(funded)
    sdk_status, sdk_code, sdk_name = sdk_call_error(sdk_client(args.base_url, main_key), **chat)
    wallet_after = client.get("/api/wallet", headers=admin_auth)
    state_after = wallet_after.json().get("wallet", {}) if wallet_after.status_code == 200 else {}
    witness_main = witness(
        args.api_container,
        RESERVE_WITNESS,
        {"api_key_id": created_item.get("id"), "user_id": admin_user_id, "since": since},
    )
    reserves = witness_main.get("reserves", [])
    released = [r for r in reserves if r.get("status") == "released" and int(r.get("amount_kopecks", 0)) > 0]
    def item_reason(item: dict[str, Any]) -> str:
        payload = item.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        return str((payload or {}).get("reason", ""))

    released_items = [
        i for i in witness_main.get("reconciliation", []) if i.get("kind") == "released"
        and item_reason(i) == "upstream_not_configured"
    ]
    released_values_ok = (
        state_after.get("reserved_kopecks") == state_before.get("reserved_kopecks")
        and (state_after.get("balance_kopecks") or 0) == (credited.get("balance_kopecks") or 0)
    )
    evidence.record(
        "503 upstream_not_configured after reserve commit",
        "reserve row with amount>0 exists and is released; wallet projections restored; resolved reconciliation, no open items",
        f"HTTP {status} {code}; SDK {sdk_name} {sdk_status} {sdk_code}; "
        f"reserves={len(reserves)} released={len(released)} "
        f"released_items={len(released_items)} reserved {state_before.get('reserved_kopecks')}->{state_after.get('reserved_kopecks')}",
        ok=status == 503
        and code == "upstream_not_configured"
        and sdk_name == "InternalServerError"
        and len(released) >= 1
        and len(released_items) >= 1
        and released_values_ok,
        detail=witness_main,
    )

    sse = client.post(
        "/v1/chat/completions",
        json={**chat, "stream": True},
        headers={"Authorization": f"Bearer {main_key}"},
    )
    status, code = raw_error_shape(sse)
    sdk_stream_status, sdk_stream_code, sdk_stream_name = sdk_call_error(
        sdk_client(args.base_url, main_key), **{**chat, "stream": True}
    )
    witness_stream = witness(
        args.api_container,
        RESERVE_WITNESS,
        {"api_key_id": created_item.get("id"), "user_id": admin_user_id, "since": since},
    )
    released_stream = [
        r for r in witness_stream.get("reserves", []) if r.get("status") == "released" and int(r.get("amount_kopecks", 0)) > 0
    ]
    stream_reserved = (witness_stream.get("wallet") or {}).get("reserved_kopecks")
    evidence.record(
        "503 for SSE request releases its reserve",
        "same upstream_not_configured error on stream=true; reserve released; no residue",
        f"HTTP {status} {code} content-type={sse.headers.get('content-type')}; "
        f"SDK {sdk_stream_name} {sdk_stream_status} {sdk_stream_code}; released={len(released_stream)} "
        f"reserved={stream_reserved}",
        ok=status == 503
        and code == "upstream_not_configured"
        and len(released_stream) >= 2
        and stream_reserved == 0,
    )

    reconciliation_after = client.get("/api/admin/reconciliation", headers=admin_auth)
    open_after = len(reconciliation_after.json().get("items", [])) if reconciliation_after.status_code == 200 else None
    evidence.record(
        "no open reconciliation items from clean failures",
        "open item count unchanged",
        f"open {open_before} -> {open_after}",
        ok=open_before is not None and open_before == open_after,
    )

    if not provider_key_present:
        evidence.record(
            "free and paid live call with real usage",
            "200 + usage settled in the ledger",
            "BLOCKED: OPENROUTER_API_KEY is not configured in the api container; owner key pending",
            ok=False,
            blocked=True,
        )

    return finish(evidence, args)


def finish(evidence: il.Evidence, args: argparse.Namespace) -> int:
    out = Path(args.out) if args.out else il.EVIDENCE_DIR / (
        f"gateway-live-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    path = evidence.write(out)
    summary = f"{path} :: {len(evidence.steps) - len(evidence.failed)}/{len(evidence.steps)} passed"
    if evidence.blocked:
        summary += f", {len(evidence.blocked)} blocked"
    print("\n" + summary)
    for step in evidence.failed:
        print(f"  - [{'BLOCK' if step['blocked'] else 'FAIL'}] {step['area']}: {step['observed']}")
    return 1 if evidence.failed else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--public-base-url", default="", help="public HTTPS origin for an SDK models.list check")
    parser.add_argument("--api-container", default="rubai-api-1")
    parser.add_argument("--admin-email", default="admin@example.com")
    parser.add_argument("--admin-password-file", default="/tmp/admin-pw.txt")
    parser.add_argument("--credit-kopecks", type=int, default=1000)
    parser.add_argument("--out", default=None)
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run(parse_args()))
