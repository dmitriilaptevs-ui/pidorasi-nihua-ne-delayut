#!/usr/bin/env python3
"""Live ledger concurrency acceptance against the deployed gateway.

Fires 100 truly concurrent ``/v1/chat/completions`` requests on one wallet and
key, then verifies the ledger projections with read-only database witnesses.
Depending on the provider key state, every funded request either settles
(upstream 200) or releases after an upstream error; both modes are checked with
the same exact-once rules:

- phase "tiny": a fresh account holds exactly 5 kopecks; every request estimates
  a 1-kopeck reserve. Every response must be either a platform 402
  ``insufficient_funds`` (no reserve) or a funded request (exactly one reserve,
  released or settled exactly once); the final balance and reserved projection
  must be consistent.
- an in-container poller samples ``wallet.reserved_kopecks`` and the sum of
  *held* reserves every 2 ms during the burst, so the maximum concurrently held
  amount is observed. It must never exceed the wallet balance (no overspend).
- phase "ample": 100 concurrent requests on a funded wallet; same exact-once
  lifecycle with the balance consistent with any settled amounts.
- a single provider-state probe records the live upstream result (200 / absent
  key / rejected key with the exact provider body). A rejected or absent key is
  reported as blocked, not as a concurrency failure.
- global invariants: no negative reserved projection, every wallet's balance
  equals the sum of its postings, reserve references are unique, and no open
  reconciliation items come out of clean failures.

The settle/double-charge leg needs a successful upstream call and is recorded
as BLOCKED until the owner's OpenRouter key is configured (``sdk_live.py``
covers it when live).

Usage::

    apps/api/.venv/bin/python scripts/acceptance/ledger_concurrency_live.py

Evidence JSON is written under ``scripts/acceptance/evidence/``.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import secrets
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import identity_live as il  # noqa: E402

UTC = timezone.utc
CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4

POLLER = r"""
import asyncio, asyncpg, json, os, sys

async def main():
    cfg = json.loads(sys.stdin.read())
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    row = await conn.fetchrow("SELECT id FROM wallets WHERE user_id = $1::uuid", cfg["user_id"])
    if row is None:
        print(json.dumps({"samples": 0, "max_reserved": 0, "max_held_sum": 0, "error": "wallet missing"}))
        return
    wallet_id = row["id"]
    deadline = asyncio.get_event_loop().time() + cfg["duration_ms"] / 1000
    samples = 0
    max_reserved = 0
    max_held_sum = 0
    while asyncio.get_event_loop().time() < deadline:
        reserved = await conn.fetchval("SELECT reserved_kopecks FROM wallets WHERE id = $1", wallet_id)
        held = await conn.fetchval(
            "SELECT coalesce(sum(amount_kopecks), 0) FROM reserves"
            " WHERE wallet_id = $1 AND status = 'held'",
            wallet_id,
        )
        samples += 1
        max_reserved = max(max_reserved, int(reserved or 0))
        max_held_sum = max(max_held_sum, int(held or 0))
        await asyncio.sleep(cfg["interval_ms"] / 1000)
    print(json.dumps({"samples": samples, "max_reserved": max_reserved, "max_held_sum": max_held_sum}))
    await conn.close()

asyncio.run(main())
"""

WITNESS = r"""
import asyncio, asyncpg, json, os, sys
from datetime import datetime

async def main():
    cfg = json.loads(sys.stdin.read())
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    since = datetime.fromisoformat(cfg["since"].replace("Z", "+00:00"))
    rows = await conn.fetch(
        "SELECT request_ref, amount_kopecks, status, settled_kopecks, released_at"
        " FROM reserves WHERE api_key_id = $1::uuid AND created_at >= $2 ORDER BY created_at",
        cfg["api_key_id"], since,
    )
    refs = [row["request_ref"] for row in rows]
    open_items = 0
    if refs:
        open_items = await conn.fetchval(
            "SELECT count(*) FROM reconciliation_items WHERE request_ref = ANY($1::text[])"
            " AND status = 'open'",
            refs,
        )
    duplicates = await conn.fetchval(
        "SELECT count(*) FROM (SELECT request_ref FROM reserves WHERE api_key_id = $1::uuid"
        " GROUP BY request_ref HAVING count(*) > 1) AS dup",
        cfg["api_key_id"],
    )
    wallet = None
    postings = None
    if cfg.get("user_id"):
        wallet = await conn.fetchrow(
            "SELECT id, balance_kopecks, reserved_kopecks FROM wallets WHERE user_id = $1::uuid",
            cfg["user_id"],
        )
        if wallet is not None:
            postings = await conn.fetchval(
                "SELECT coalesce(sum(amount_kopecks), 0) FROM ledger_postings"
                " WHERE account_code = 'wallet:' || $1::text",
                str(wallet["id"]),
            )
    global_checks = {}
    if cfg.get("global"):
        global_checks = {
            "negative_reserved": await conn.fetchval(
                "SELECT count(*) FROM wallets WHERE reserved_kopecks < 0"
            ),
            "balance_projection_mismatches": await conn.fetchval(
                "SELECT count(*) FROM ("
                "  SELECT w.id FROM wallets w"
                "  LEFT JOIN ledger_postings p ON p.account_code = 'wallet:' || w.id::text"
                "  GROUP BY w.id, w.balance_kopecks"
                "  HAVING w.balance_kopecks <> coalesce(sum(p.amount_kopecks), 0)"
                ") AS mismatched"
            ),
        }
    payload = {
        "reserves": [dict(row) for row in rows],
        "open_items": open_items,
        "duplicate_references": duplicates,
        "wallet": dict(wallet) if wallet else None,
        "postings_sum": postings,
        "global": global_checks,
    }
    print(json.dumps(payload, default=str))
    await conn.close()

asyncio.run(main())
"""


def docker_argv(inner: str) -> list[str]:
    groups = subprocess.run(["id", "-nG"], capture_output=True, text=True, timeout=10).stdout.split()
    if "docker" in groups:
        return ["bash", "-lc", inner]
    return ["sg", "docker", "-c", inner]


def witness(container: str, script: str, payload: dict[str, Any]) -> dict[str, Any]:
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    code = "import base64,sys;exec(base64.b64decode('%s'))" % encoded
    inner = (
        f"printf %s {shlex.quote(json.dumps(payload))} | "
        f"docker exec -i {shlex.quote(container)} python -c {shlex.quote(code)}"
    )
    proc = subprocess.run(docker_argv(inner), capture_output=True, text=True, timeout=120)
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"_error": ((proc.stderr or proc.stdout) or "no output").strip()[:300]}


def start_poller(container: str, payload: dict[str, Any]) -> subprocess.Popen[str]:
    encoded = base64.b64encode(POLLER.encode("utf-8")).decode("ascii")
    code = "import base64,sys;exec(base64.b64decode('%s'))" % encoded
    inner = f"docker exec -i {shlex.quote(container)} python -c {shlex.quote(code)}"
    proc = subprocess.Popen(
        docker_argv(inner), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(payload))
    proc.stdin.close()
    return proc


def read_poller(proc: subprocess.Popen[str]) -> dict[str, Any]:
    try:
        proc.wait(timeout=120)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        proc.kill()
        return {"_error": "poller timeout"}
    stdout = proc.stdout.read() if proc.stdout else ""
    stderr = proc.stderr.read() if proc.stderr else ""
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"_error": (stderr or stdout or "no output").strip()[:300]}


def expected_reserve_kopecks(pricing: dict[str, str], *, input_chars: int, max_tokens: int) -> int:
    input_tokens = input_chars // CHARS_PER_TOKEN + MESSAGE_OVERHEAD_TOKENS
    rub = (input_tokens * float(pricing["input_rub_per_mtok"]) + max_tokens * float(pricing["output_rub_per_mtok"])) / 1_000_000
    if rub <= 0:
        return 0
    return max(1, math.ceil(rub * 100))


async def burst(base_url: str, raw_key: str, payload: dict[str, Any], count: int) -> list[tuple[int, str]]:
    limits = httpx.Limits(max_connections=count + 20, max_keepalive_connections=count + 20)
    headers = {"Authorization": f"Bearer {raw_key}"}
    async with httpx.AsyncClient(base_url=base_url, timeout=90.0, limits=limits) as client:
        tasks = [
            client.post("/v1/chat/completions", json=payload, headers=headers) for _ in range(count)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    summary: list[tuple[int, str]] = []
    for result in results:
        if isinstance(result, BaseException):
            summary.append((-1, type(result).__name__))
            continue
        try:
            code = result.json().get("error", {}).get("code", "")
        except ValueError:
            code = ""
        summary.append((result.status_code, code))
    return summary


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


def origin_of(container: str) -> str:
    for line in (il.docker_shell(f"docker exec {shlex.quote(container)} env").stdout or "").splitlines():
        if line.startswith("PUBLIC_ORIGIN="):
            return line.split("=", 1)[1].strip()
    return ""


def origin_headers(origin: str) -> dict[str, str]:
    return {"Origin": origin} if origin else {}


def as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def status_histogram(summary: list[tuple[int, str]]) -> dict[str, int]:
    histogram: dict[str, int] = {}
    for status, code in summary:
        key = f"{status}:{code}" if code else str(status)
        histogram[key] = histogram.get(key, 0) + 1
    return histogram


def evaluate_phase(
    summary: list[tuple[int, str]],
    reserves: list[dict[str, Any]],
    wallet: dict[str, Any],
    *,
    start_balance: int,
    observed: dict[str, Any],
    held_limit: int | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Mode-agnostic phase check.

    Every request either hits the platform funds gate (402 insufficient_funds,
    no reserve), or reserves and reaches upstream (200 → settled, other status →
    released). Works whether the provider key is absent, rejected or working.
    """
    exceptions = sum(1 for status, _ in summary if status == -1)
    insufficient = sum(1 for _, code in summary if code == "insufficient_funds")
    successes = sum(1 for status, _ in summary if status == 200)
    funded = len(summary) - exceptions - insufficient
    released = [row for row in reserves if row.get("status") == "released"]
    settled = [row for row in reserves if row.get("status") == "settled"]
    settled_sum = sum(int(row.get("settled_kopecks") or 0) for row in settled)
    checks = {
        "no_exceptions": exceptions == 0,
        "reserves_match_funded": len(reserves) == funded,
        "lifecycle_complete": len(released) + len(settled) == len(reserves),
        "settled_matches_successes": len(settled) == successes,
        "reserved_zero": wallet.get("reserved_kopecks") == 0,
        "balance_consistent": wallet.get("balance_kopecks") == start_balance - settled_sum,
        "held_within_balance": held_limit is None
        or observed.get("max_held_sum", 10**9) <= held_limit
        and observed.get("max_reserved", 10**9) <= held_limit,
        "poller_sampled": observed.get("samples", 0) > 0,
    }
    metrics = {
        "histogram": status_histogram(summary),
        "exceptions": exceptions,
        "insufficient": insufficient,
        "successes": successes,
        "funded": funded,
        "reserves": len(reserves),
        "released": len(released),
        "settled": len(settled),
        "settled_kopecks": settled_sum,
        "start_balance": start_balance,
        "end_balance": wallet.get("balance_kopecks"),
        "end_reserved": wallet.get("reserved_kopecks"),
        "max_held_seen": observed.get("max_held_sum"),
        "max_reserved_seen": observed.get("max_reserved"),
        "poller_samples": observed.get("samples"),
        "checks": checks,
    }
    return all(checks.values()), metrics


def run(args: argparse.Namespace) -> int:  # noqa: C901 - linear acceptance script
    evidence = il.Evidence()
    client = httpx.Client(base_url=args.base_url, timeout=60.0, follow_redirects=False)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"{stamp}-{secrets.token_hex(3)}"
    since = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    origin = origin_of(args.api_container)

    evidence.note(f"base_url={args.base_url}")
    evidence.note(f"api_container={args.api_container}")
    evidence.note(f"api_image={il.api_image_id(args.api_container)}")
    evidence.note(f"concurrency={args.count} tiny_balance_kopecks={args.tiny_kopecks}")

    admin_ok, admin_cookie, admin_why = admin_login(client, args, origin)
    evidence.record("admin session", "login as admin", admin_why or "established", ok=admin_ok)
    if not admin_ok or not admin_cookie:
        return finish(evidence, args)
    admin_auth = il.auth_headers(admin_cookie)

    models = client.get("/v1/models")
    items = models.json().get("data", []) if models.status_code == 200 else []
    probe = {"messages": [{"role": "user", "content": "ping"}], "max_tokens": args.max_tokens}
    chosen = None
    for item in items:
        pricing = item.get("x-rubai-pricing") or {}
        try:
            reserve = expected_reserve_kopecks(
                pricing,
                input_chars=len(probe["messages"][0]["content"]),
                max_tokens=args.max_tokens,
            )
        except (KeyError, ValueError):
            continue
        if reserve == 1:
            chosen = (item["id"], pricing, reserve)
            break
    evidence.record(
        "1-kopeck model chosen",
        "an available model whose request estimate is exactly 1 kopeck",
        f"{chosen[0] if chosen else None} reserve={chosen[2] if chosen else None}",
        ok=chosen is not None,
    )
    if chosen is None:
        return finish(evidence, args)
    chat = {"model": chosen[0], **probe}

    # --- phase tiny: 5 kopecks, 100 concurrent 1-kopeck requests -------------
    tiny_email = f"ledger-conc+{suffix}@example.com".lower()
    tiny_password = "Ledger-pass-" + secrets.token_urlsafe(9)
    bootstrap = il.docker_shell(
        f"docker exec -e ADMIN_PASSWORD={shlex.quote(tiny_password)} {shlex.quote(args.api_container)} "
        f"python -m app.cli create-admin --email {shlex.quote(tiny_email)} --password-env ADMIN_PASSWORD"
    )
    tiny_ok = bootstrap.returncode == 0 and "admin ready" in (bootstrap.stdout or "")
    tiny_cookie = None
    tiny_user_id = None
    if tiny_ok:
        tiny_login = client.post("/api/auth/login", json={"email": tiny_email, "password": tiny_password})
        tiny_cookie = il.cookie_from_response(tiny_login)
        if tiny_cookie:
            me = client.get("/api/auth/me", headers=il.auth_headers(tiny_cookie))
            tiny_user_id = me.json().get("user", {}).get("id") if me.status_code == 200 else None
    tiny_key = None
    tiny_key_id = None
    if tiny_cookie and tiny_user_id:
        credit = client.post(
            "/api/admin/wallet/credit",
            json={"email": tiny_email, "amount_kopecks": args.tiny_kopecks, "reference": f"ledger-conc-{suffix}"},
            headers={**origin_headers(origin), **admin_auth},
        )
        created = client.post(
            "/api/keys",
            json={"name": f"ledger-conc-{suffix}"},
            headers={**origin_headers(origin), **il.auth_headers(tiny_cookie)},
        )
        if credit.status_code == 200 and created.status_code == 201:
            tiny_key = created.json()["key"]
            tiny_key_id = created.json()["item"]["id"]
    evidence.record(
        "tiny balance account ready",
        f"{args.tiny_kopecks} kopecks + platform key",
        "ready" if tiny_key else f"bootstrap failed: {il.body_snippet(bootstrap)}",
        ok=bool(tiny_key),
    )
    if not tiny_key:
        return finish(evidence, args)

    # --- provider upstream state (credential health, not a concurrency check) --
    probe_status = None
    probe_body = ""
    probe_key_response = client.post(
        "/api/keys", json={"name": f"ledger-probe-{suffix}"}, headers={**origin_headers(origin), **admin_auth}
    )
    if probe_key_response.status_code == 201:
        probe_key = probe_key_response.json()["key"]
        probe = client.post(
            "/v1/chat/completions", json=chat, headers={"Authorization": f"Bearer {probe_key}"}
        )
        probe_status = probe.status_code
        probe_body = il.body_snippet(probe, 300)
    upstream_ok = probe_status == 200
    evidence.record(
        "provider upstream state",
        "200 from a funded live call so the settle leg is exercised",
        f"HTTP {probe_status} {probe_body}" if probe_status is not None else "no probe key available",
        ok=upstream_ok,
        blocked=not upstream_ok,
        detail={"upstream_body": probe_body},
    )

    poller = start_poller(
        args.api_container,
        {"user_id": tiny_user_id, "duration_ms": 15000, "interval_ms": 2},
    )
    summary = asyncio.run(burst(args.base_url, tiny_key, chat, args.count))
    observed = read_poller(poller)
    tiny_witness = witness(
        args.api_container,
        WITNESS,
        {"api_key_id": tiny_key_id, "user_id": tiny_user_id, "since": since, "global": False},
    )
    reserves = tiny_witness.get("reserves", [])
    wallet = tiny_witness.get("wallet") or {}
    tiny_ok, tiny_metrics = evaluate_phase(
        summary,
        reserves,
        wallet,
        start_balance=args.tiny_kopecks,
        observed=observed,
        held_limit=args.tiny_kopecks,
    )
    tiny_ok = (
        tiny_ok
        and tiny_witness.get("open_items") == 0
        and as_int(tiny_witness.get("postings_sum")) == wallet.get("balance_kopecks")
    )
    evidence.record(
        "100 concurrent requests on a 5-kopeck wallet",
        "every request is a platform 402 (no reserve) or funded (one reserve, released/settled exactly once); balance consistent; max held <= 5",
        json.dumps(tiny_metrics, ensure_ascii=False),
        ok=tiny_ok,
        detail={"metrics": tiny_metrics, "witness": tiny_witness, "poller": observed},
    )

    # --- phase ample: 100 concurrent requests on a funded wallet -------------
    admin_me = client.get("/api/auth/me", headers=admin_auth)
    admin_user_id = admin_me.json().get("user", {}).get("id")
    admin_key = client.post(
        "/api/keys", json={"name": f"ledger-ample-{suffix}"}, headers={**origin_headers(origin), **admin_auth}
    )
    if admin_key.status_code != 201:
        evidence.record("ample balance key", "HTTP 201", f"HTTP {admin_key.status_code}", ok=False)
        return finish(evidence, args)
    ample_key = admin_key.json()["key"]
    ample_key_id = admin_key.json()["item"]["id"]
    wallet_before = client.get("/api/wallet", headers=admin_auth).json().get("wallet", {})

    poller = start_poller(
        args.api_container,
        {"user_id": admin_user_id, "duration_ms": 15000, "interval_ms": 2},
    )
    summary = asyncio.run(burst(args.base_url, ample_key, chat, args.count))
    observed = read_poller(poller)
    ample_witness = witness(
        args.api_container,
        WITNESS,
        {"api_key_id": ample_key_id, "user_id": admin_user_id, "since": since, "global": False},
    )
    reserves = ample_witness.get("reserves", [])
    wallet_db = ample_witness.get("wallet") or {}
    ample_ok, ample_metrics = evaluate_phase(
        summary,
        reserves,
        wallet_db,
        start_balance=wallet_before.get("balance_kopecks", 0),
        observed=observed,
        held_limit=wallet_before.get("balance_kopecks", 0),
    )
    ample_ok = (
        ample_ok
        and ample_witness.get("open_items") == 0
        and as_int(ample_witness.get("postings_sum")) == wallet_db.get("balance_kopecks")
    )
    evidence.record(
        "100 concurrent requests on a funded wallet",
        "every request funded with exactly one reserve, released/settled exactly once; balance and reserved consistent with the provider result",
        json.dumps(ample_metrics, ensure_ascii=False),
        ok=ample_ok,
        detail={"metrics": ample_metrics, "witness": ample_witness, "poller": observed},
    )

    # --- global invariants ---------------------------------------------------
    glob = witness(args.api_container, WITNESS, {"api_key_id": ample_key_id, "user_id": None, "since": since, "global": True})
    checks = glob.get("global", {})
    evidence.record(
        "global ledger invariants",
        "no negative reserved wallet; every wallet balance equals its posting sum",
        f"negative_reserved={checks.get('negative_reserved')} "
        f"balance_projection_mismatches={checks.get('balance_projection_mismatches')}",
        ok=checks.get("negative_reserved") == 0 and checks.get("balance_projection_mismatches") == 0,
    )
    evidence.record(
        "reserve references unique",
        "no duplicate request references for the test keys",
        f"tiny_dups={tiny_witness.get('duplicate_references')} ample_dups={ample_witness.get('duplicate_references')}",
        ok=tiny_witness.get("duplicate_references") == 0 and ample_witness.get("duplicate_references") == 0,
    )

    if upstream_ok:
        evidence.record(
            "settle/double-charge concurrency with real usage",
            "settled exactly once under concurrency",
            "covered by the phase metrics above (settled == 200 responses, balance consistent)",
            ok=True,
        )
    else:
        evidence.record(
            "settle/double-charge concurrency with real usage",
            "successful upstream call settles exactly once under concurrency",
            f"BLOCKED: provider upstream returned HTTP {probe_status}: {probe_body[:200]}",
            ok=False,
            blocked=True,
        )
    return finish(evidence, args)


def finish(evidence: il.Evidence, args: argparse.Namespace) -> int:
    out = Path(args.out) if args.out else il.EVIDENCE_DIR / (
        f"ledger-concurrency-live-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
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
    parser.add_argument("--api-container", default="rubai-api-1")
    parser.add_argument("--admin-email", default="admin@example.com")
    parser.add_argument("--admin-password-file", default="/tmp/admin-pw.txt")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--tiny-kopecks", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=16)
    parser.add_argument("--out", default=None)
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run(parse_args()))
