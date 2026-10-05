#!/usr/bin/env python3
"""Live black-box API-keys + catalog acceptance pack against the compose stack.

Covers the keys-catalog contract over HTTP, with read-only database and
separate-process witnesses for the claims HTTP alone cannot show:

- raw key returned exactly once; DB stores only the SHA-256 digest + prefix;
  ``GET /api/keys`` never returns the raw key or the digest
- unverified accounts cannot create keys; ownership enforced on revoke
- revocation is seen by a *separate process* (``authenticate_api_key`` in a new
  container exec) within 5 seconds
- ``GET /api/catalog`` is public and priced for every available model; prices
  are decimal strings, not floats
- admin sync is RBAC-protected, internally consistent, idempotent on the second
  run, and append-only in the database; an independent Decimal half-up
  recomputation of every RUB row must match exactly

Usage::

    apps/api/.venv/bin/python scripts/acceptance/keys_catalog_live.py
    apps/api/.venv/bin/python scripts/acceptance/keys_catalog_live.py \
        --admin-password-file /tmp/admin-pw.txt --origin https://host

Evidence JSON (no raw keys, no passwords; raw keys are redacted to
length+sha256 prefix) is written under ``scripts/acceptance/evidence/``.
Exit code 0 only when every required step passed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import secrets
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import identity_live as il  # noqa: E402  (shared evidence/docker/log helpers)

UTC = timezone.utc
PRICE_RE = re.compile(r"^\d+\.\d{6}$")
DECIMAL_RE = re.compile(r"^\d+(\.\d+)?$")

KEY_ROW_WITNESS = """
import asyncio, asyncpg, hashlib, json, os, sys

async def main():
    cfg = json.loads(sys.stdin.read())
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    row = await conn.fetchrow(
        "SELECT key_hash, prefix, revoked_at FROM api_keys WHERE id = $1", cfg["key_id"]
    )
    raw = cfg["raw_key"]
    payload = {
        "found": row is not None,
        "digest_ok": row is not None and row["key_hash"] == hashlib.sha256(raw.encode()).hexdigest(),
        "prefix_ok": row is not None and row["prefix"] == raw[:16],
        "plaintext_absent": row is not None and raw not in json.dumps(dict(row), default=str),
        "revoked": row is not None and row["revoked_at"] is not None,
    }
    print(json.dumps(payload))
    await conn.close()

asyncio.run(main())
"""

AUTH_WITNESS = """
import asyncio, json, sys

async def main():
    cfg = json.loads(sys.stdin.read())
    from app.db import session_factory
    from app.services import keys as key_service
    async with session_factory()() as db:
        result = await key_service.authenticate_api_key(db, raw_key=cfg["raw_key"])
    print(json.dumps({"authenticated": result is not None}))

asyncio.run(main())
"""

CATALOG_WITNESS = """
import asyncio, asyncpg, json, os, sys
from decimal import Decimal, ROUND_HALF_UP

Q = Decimal("0.000001")

async def main():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(
        "SELECT model_id::text AS model_id, version, retired_at,"
        " input_usd_per_mtok, output_usd_per_mtok, cached_usd_per_mtok,"
        " fx_rate, markup, input_rub_per_mtok, output_rub_per_mtok, cached_rub_per_mtok"
        " FROM catalog_pricing"
    )

    def d(value):
        return Decimal(str(value))

    mismatches = []
    retired = 0
    active: dict[str, int] = {}
    for row in rows:
        fx, markup = d(row["fx_rate"]), d(row["markup"])
        pairs = (
            ("input_usd_per_mtok", "input_rub_per_mtok"),
            ("output_usd_per_mtok", "output_rub_per_mtok"),
            ("cached_usd_per_mtok", "cached_rub_per_mtok"),
        )
        for usd_col, rub_col in pairs:
            if row[usd_col] is None:
                if row[rub_col] is not None:
                    mismatches.append(f"{row['model_id']} v{row['version']} {rub_col} set without usd")
                continue
            expected = (d(row[usd_col]) * fx * markup).quantize(Q, rounding=ROUND_HALF_UP)
            if expected != d(row[rub_col]):
                mismatches.append(f"{row['model_id']} v{row['version']} {rub_col} exp={expected} got={row[rub_col]}")
        if row["retired_at"] is not None:
            retired += 1
        else:
            active[row["model_id"]] = active.get(row["model_id"], 0) + 1

    payload = {
        "rows": len(rows),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:3],
        "retired_rows": retired,
        "models_with_multiple_active": sum(1 for count in active.values() if count > 1),
        "models_with_active_pricing": len(active),
    }
    print(json.dumps(payload))
    await conn.close()

asyncio.run(main())
"""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def witness(container: str, script: str, payload: dict[str, Any]) -> dict[str, Any]:
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    code = "import base64,sys;exec(base64.b64decode('%s'))" % encoded
    inner = (
        f"printf %s {shlex.quote(json.dumps(payload))} | "
        f"docker exec -i {shlex.quote(container)} python -c {shlex.quote(code)}"
    )
    proc: subprocess.CompletedProcess[str] = il.docker_shell(inner)
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"_error": ((proc.stderr or proc.stdout) or "no output").strip()[:300]}


def resolve_origin(container: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    proc = il.docker_shell(f"docker exec {shlex.quote(container)} env")
    for line in (proc.stdout or "").splitlines():
        if line.startswith("PUBLIC_ORIGIN="):
            return line.split("=", 1)[1].strip()
    return ""


def origin_headers(origin: str) -> dict[str, str]:
    return {"Origin": origin} if origin else {}


def register_and_login(
    client: httpx.Client,
    container: str,
    since: str,
    email: str,
    password: str,
    *,
    verify: bool,
    origin: str,
) -> tuple[bool, str | None, str]:
    """Register, optionally verify via the console-mail token, then log in."""
    register = client.post("/api/auth/register", json={"email": email, "password": password})
    if register.status_code != 201:
        return False, None, f"register HTTP {register.status_code}: {il.body_snippet(register)}"
    if verify:
        token = il.wait_for_token(container, since, email, "verify")
        if not token:
            return False, None, "verification token not found in container log"
        verified = client.post("/api/auth/verify-email", json={"token": token})
        if verified.status_code != 200:
            return False, None, f"verify HTTP {verified.status_code}: {il.body_snippet(verified)}"
    login = client.post(
        "/api/auth/login", json={"email": email, "password": password}, headers=origin_headers(origin)
    )
    cookie = il.cookie_from_response(login)
    if login.status_code != 200 or not cookie:
        return False, None, f"login HTTP {login.status_code}, cookie={'yes' if cookie else 'no'}"
    return True, cookie, ""


# --------------------------------------------------------------------------- #
# main flow
# --------------------------------------------------------------------------- #


def run(args: argparse.Namespace) -> int:  # noqa: C901 - linear acceptance script
    evidence = il.Evidence()
    client = httpx.Client(base_url=args.base_url, timeout=90.0, follow_redirects=False)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"{stamp}-{secrets.token_hex(3)}"
    user_email = f"keys-acceptance+{suffix}@example.com".lower()
    unverified_email = f"keys-unverified+{suffix}@example.com".lower()
    password = "Acceptance-pass-" + secrets.token_urlsafe(9)
    since = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    origin = resolve_origin(args.api_container, args.origin)

    evidence.note(f"base_url={args.base_url}")
    evidence.note(f"origin={origin or '<none>'}")
    evidence.note(f"api_container={args.api_container}")
    evidence.note(f"api_image={il.api_image_id(args.api_container)}")
    evidence.note(f"test_account={user_email}")

    for path, expected in (("/healthz", 200), ("/readyz", 200)):
        response = httpx.get(f"{args.health_base_url}{path}", timeout=15.0)
        evidence.record(
            f"liveness {path}",
            f"HTTP {expected}",
            f"HTTP {response.status_code} {il.body_snippet(response)}",
            ok=response.status_code == expected,
        )

    # --- verified account ----------------------------------------------------
    using_admin = False
    ok, cookie, why = register_and_login(
        client, args.api_container, since, user_email, password, verify=True, origin=origin
    )
    if not ok and "HTTP 429" in why:
        # Register throttle is per client IP and shared across runs; fall back
        # to the bootstrap admin account, which is also a verified account.
        admin_fallback, admin_fallback_cookie, admin_fallback_why = admin_login(client, args, origin)
        if admin_fallback and admin_fallback_cookie:
            ok, cookie, using_admin = True, admin_fallback_cookie, True
            why = "register throttled (429); using the verified admin account"
        else:
            why = f"{why}; admin fallback failed: {admin_fallback_why}"
    evidence.record(
        "verified account ready", "register + verify + login", why or "session established", ok=ok
    )
    if not ok:
        evidence.note("cannot continue without a verified session; aborting")
        return finish(evidence, args)

    # --- unverified account cannot create keys -------------------------------
    if using_admin:
        u_ok, u_cookie, u_why = False, None, "register throttle exhausted"
    else:
        u_ok, u_cookie, u_why = register_and_login(
            client, args.api_container, since, unverified_email, password, verify=False, origin=origin
        )
    if u_ok and u_cookie:
        denied = client.post(
            "/api/keys",
            json={"name": "unverified-attempt"},
            headers={**origin_headers(origin), **il.auth_headers(u_cookie)},
        )
        evidence.record(
            "unverified account key creation blocked",
            "HTTP 403 email_not_verified",
            f"HTTP {denied.status_code} {il.body_snippet(denied)}",
            ok=denied.status_code == 403 and "email_not_verified" in denied.text,
        )
    else:
        evidence.record(
            "unverified account key creation blocked",
            "HTTP 403 email_not_verified",
            f"not attempted ({u_why}); covered by earlier runs recorded in the PR",
            ok=False,
            blocked=True,
        )

    # --- key creation: raw once ---------------------------------------------
    auth = il.auth_headers(cookie)
    created = client.post(
        "/api/keys",
        json={"name": "acceptance-key", "monthly_limit_kopecks": 500000},
        headers={**origin_headers(origin), **auth},
    )
    raw_key = created.json().get("key") if created.status_code == 201 else None
    item = created.json().get("item") if created.status_code == 201 else None
    key_id = item.get("id") if isinstance(item, dict) else None
    shape_ok = (
        created.status_code == 201
        and isinstance(raw_key, str)
        and raw_key.startswith("sk-rubai-")
        and isinstance(item, dict)
        and item.get("prefix") == raw_key[:16]
        and "key_hash" not in item
        and item.get("monthly_limit_kopecks") == 500000
        and item.get("revoked_at") is None
    )
    evidence.record(
        "key creation returns raw key once",
        "HTTP 201, key prefix sk-rubai-, item.prefix=raw[:16], no digest",
        f"HTTP {created.status_code}, raw={il.token_fingerprint(raw_key) if raw_key else 'missing'}, "
        f"prefix={item.get('prefix') if isinstance(item, dict) else None}",
        ok=shape_ok,
    )
    if not (shape_ok and key_id and raw_key):
        return finish(evidence, args)

    # --- key list never leaks the raw key or the digest ----------------------
    listing = client.get("/api/keys", headers=auth)
    listed = next(
        (row for row in listing.json().get("items", []) if row.get("id") == key_id), None
    ) if listing.status_code == 200 else None
    list_ok = (
        listing.status_code == 200
        and listed is not None
        and listed.get("prefix") == raw_key[:16]
        and raw_key not in listing.text
        and "key_hash" not in listing.text
        and "key" not in listed
    )
    evidence.record(
        "key list is prefix-only",
        "HTTP 200, no raw key and no digest in payload",
        f"HTTP {listing.status_code}, listed={listed is not None}, "
        f"raw_in_body={raw_key in listing.text}, hash_in_body={'key_hash' in listing.text}",
        ok=list_ok,
    )

    # --- persisted as digest only, then revoke -------------------------------
    db_before = witness(args.api_container, KEY_ROW_WITNESS, {"key_id": key_id, "raw_key": raw_key})
    evidence.record(
        "database stores digest + prefix only",
        "digest_ok, prefix_ok, plaintext_absent, not revoked",
        json.dumps(db_before, ensure_ascii=False),
        ok=all(db_before.get(k) for k in ("found", "digest_ok", "prefix_ok", "plaintext_absent"))
        and db_before.get("revoked") is False,
    )

    auth_started = time.monotonic()
    auth_before = witness(args.api_container, AUTH_WITNESS, {"raw_key": raw_key})
    witness_baseline = time.monotonic() - auth_started
    evidence.record(
        "key authenticates before revoke (separate process)",
        "authenticated=true",
        f"{json.dumps(auth_before)} (process round-trip baseline {witness_baseline:.2f}s)",
        ok=auth_before.get("authenticated") is True,
    )

    foreign = client.post(f"/api/keys/{key_id}/revoke", headers={**origin_headers(origin), **il.auth_headers(u_cookie)}) if u_cookie else None
    evidence.record(
        "foreign revoke denied",
        "HTTP 404 key_not_found",
        f"HTTP {foreign.status_code} {il.body_snippet(foreign)}" if foreign else "not attempted",
        ok=foreign is not None and foreign.status_code == 404 and "key_not_found" in foreign.text,
        blocked=foreign is None,
    )

    started = time.monotonic()
    revoked = client.post(f"/api/keys/{key_id}/revoke", headers={**origin_headers(origin), **auth})
    auth_after = witness(args.api_container, AUTH_WITNESS, {"raw_key": raw_key})
    raw_elapsed = time.monotonic() - started
    # The raw number includes starting a fresh python process inside the
    # container; subtract the pre-revoke round-trip baseline so the assertion
    # measures revocation propagation, not harness startup.
    adjusted = max(0.0, raw_elapsed - witness_baseline)
    db_after = witness(args.api_container, KEY_ROW_WITNESS, {"key_id": key_id, "raw_key": raw_key})
    evidence.record(
        "revoked key rejected within 5s across processes",
        "revoke 200; separate-process auth false immediately; adjusted elapsed < 5s; revoked_at set",
        f"revoke HTTP {revoked.status_code}, auth={auth_after.get('authenticated')}, "
        f"raw={raw_elapsed:.2f}s, baseline={witness_baseline:.2f}s, adjusted={adjusted:.2f}s, "
        f"revoked={db_after.get('revoked')}",
        ok=revoked.status_code == 200
        and auth_after.get("authenticated") is False
        and adjusted < 5.0
        and db_after.get("revoked") is True,
    )

    # --- public catalog ------------------------------------------------------
    catalog = client.get("/api/catalog")
    items = catalog.json().get("items", []) if catalog.status_code == 200 else []
    format_problems: list[str] = []
    negative_prices: list[str] = []
    available = 0
    for entry in items:
        pricing = entry.get("pricing")
        if entry.get("available"):
            available += 1
            if pricing is None:
                format_problems.append(f"{entry.get('id')}: available without pricing")
                continue
            price_fields = ["input_rub_per_mtok", "output_rub_per_mtok"]
            if pricing.get("cached_rub_per_mtok") is not None:
                price_fields.append("cached_rub_per_mtok")
            for field in price_fields:
                value = str(pricing.get(field, ""))
                if value.startswith("-"):
                    negative_prices.append(f"{entry.get('id')}:{field}={value}")
                elif not PRICE_RE.match(value):
                    format_problems.append(f"{entry.get('id')}: {field}={pricing.get(field)!r}")
            for field in ("fx_rate", "markup"):
                if not DECIMAL_RE.match(str(pricing.get(field, ""))):
                    format_problems.append(f"{entry.get('id')}: {field}={pricing.get(field)!r}")
            if not isinstance(pricing.get("version"), int) or pricing["version"] < 1:
                format_problems.append(f"{entry.get('id')}: version={pricing.get('version')!r}")
            try:
                datetime.fromisoformat(str(pricing.get("valid_from")))
            except ValueError:
                format_problems.append(f"{entry.get('id')}: valid_from={pricing.get('valid_from')!r}")
    evidence.record(
        "public catalog with decimal RUB prices",
        "HTTP 200 anonymously; every available model priced, non-negative, decimal strings",
        f"HTTP {catalog.status_code}, items={len(items)}, available={available}, "
        f"format_problems={len(format_problems)}, negative_prices={len(negative_prices)}",
        ok=catalog.status_code == 200 and available > 0 and not format_problems and not negative_prices,
        detail=(format_problems + negative_prices)[:3],
    )

    # --- sync RBAC + idempotency + append-only history -----------------------
    anonymous = client.post("/api/admin/catalog/sync", headers=origin_headers(origin))
    evidence.record(
        "catalog sync requires a session",
        "HTTP 401",
        f"HTTP {anonymous.status_code} {il.body_snippet(anonymous)}",
        ok=anonymous.status_code == 401,
    )
    if u_cookie:
        forbidden = client.post(
            "/api/admin/catalog/sync", headers={**origin_headers(origin), **il.auth_headers(u_cookie)}
        )
        evidence.record(
            "catalog sync requires admin",
            "HTTP 403 admin_required",
            f"HTTP {forbidden.status_code} {il.body_snippet(forbidden)}",
            ok=forbidden.status_code == 403 and "admin_required" in forbidden.text,
        )
    else:
        evidence.record("catalog sync requires admin", "HTTP 403", "unverified session unavailable", ok=False, blocked=True)

    admin_ok, admin_cookie, admin_why = admin_login(client, args, origin)
    evidence.record(
        "admin session", "login as admin", admin_why or "admin session established", ok=admin_ok
    )
    if not admin_ok or not admin_cookie:
        return finish(evidence, args)

    def snapshot() -> dict[str, tuple]:
        page = client.get("/api/catalog")
        out: dict[str, tuple] = {}
        for entry in page.json().get("items", []):
            pricing = entry.get("pricing") or {}
            out[entry.get("id")] = (
                pricing.get("version"),
                pricing.get("valid_from"),
                pricing.get("input_rub_per_mtok"),
                pricing.get("output_rub_per_mtok"),
                pricing.get("cached_rub_per_mtok"),
            )
        return out

    admin_auth = {**origin_headers(origin), **il.auth_headers(admin_cookie)}
    first = client.post("/api/admin/catalog/sync", headers=admin_auth)
    report_first = first.json() if first.status_code == 200 else {}
    consistent = (
        first.status_code == 200
        and isinstance(report_first, dict)
        and report_first.get("created", 0)
        + report_first.get("repriced", 0)
        + report_first.get("unchanged", 0)
        == report_first.get("seen", -1)
    )
    evidence.record(
        "sync report internally consistent",
        "HTTP 200, created+repriced+unchanged == seen",
        f"HTTP {first.status_code} {json.dumps(report_first, ensure_ascii=False)}",
        ok=consistent,
    )

    before_second = snapshot()
    second = client.post("/api/admin/catalog/sync", headers=admin_auth)
    report_second = second.json() if second.status_code == 200 else {}
    after_second = snapshot()
    idempotent = (
        second.status_code == 200
        and report_second.get("created") == 0
        and report_second.get("repriced") == 0
        and report_second.get("unavailable") == 0
        and report_second.get("unchanged") == report_second.get("seen")
    )
    evidence.record(
        "second sync is idempotent",
        "created=0, repriced=0, unavailable=0, unchanged=seen",
        f"HTTP {second.status_code} {json.dumps(report_second, ensure_ascii=False)}",
        ok=idempotent,
    )
    drifted = [key for key in before_second if before_second[key] != after_second.get(key)]
    evidence.record(
        "sync does not rewrite pricing history",
        "no existing model's active version/valid_from/prices change on an idempotent sync",
        f"drifted={len(drifted)}" + (f" e.g. {drifted[:2]}" if drifted else ""),
        ok=idempotent and not drifted,
    )

    if report_second.get("seen"):
        evidence.record(
            "catalog availability matches feed",
            "available models == sync seen",
            f"available={available}, seen={report_second.get('seen')}",
            ok=available == report_second.get("seen"),
        )

    db_catalog = witness(args.api_container, CATALOG_WITNESS, {})
    evidence.record(
        "decimal math and append-only history in the database",
        "exact half-up recomputation matches every row; no duplicate active versions",
        json.dumps(db_catalog, ensure_ascii=False),
        ok=db_catalog.get("mismatch_count") == 0
        and db_catalog.get("models_with_multiple_active") == 0
        and db_catalog.get("rows", 0) > 0,
        detail=db_catalog.get("mismatches"),
    )

    return finish(evidence, args)


def admin_login(client: httpx.Client, args: argparse.Namespace, origin: str) -> tuple[bool, str | None, str]:
    password_path = Path(args.admin_password_file)
    if not password_path.exists():
        return False, None, f"admin password file not found: {password_path}"
    password = password_path.read_text(encoding="utf-8").strip()
    response = client.post(
        "/api/auth/login",
        json={"email": args.admin_email, "password": password},
        headers=origin_headers(origin),
    )
    cookie = il.cookie_from_response(response)
    if response.status_code != 200 or not cookie:
        return False, None, f"admin login HTTP {response.status_code}: {il.body_snippet(response)}"
    who = client.get("/api/admin/whoami", headers=il.auth_headers(cookie))
    if who.status_code != 200:
        return False, None, f"admin whoami HTTP {who.status_code}"
    return True, cookie, ""


def finish(evidence: il.Evidence, args: argparse.Namespace) -> int:
    out = Path(args.out) if args.out else il.EVIDENCE_DIR / (
        f"keys-catalog-live-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    return _finish(evidence, out)


def _finish(evidence: il.Evidence, out: Path) -> int:
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
    parser.add_argument(
        "--health-base-url",
        default="http://127.0.0.1:8080",
        help="API origin for /healthz and /readyz (not proxied by the web app)",
    )
    parser.add_argument("--api-container", default="rubai-api-1")
    parser.add_argument("--origin", default=None, help="Origin header value; default: PUBLIC_ORIGIN from the container")
    parser.add_argument("--admin-email", default="admin@example.com")
    parser.add_argument("--admin-password-file", default="/tmp/admin-pw.txt")
    parser.add_argument("--out", default=None)
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run(parse_args()))
