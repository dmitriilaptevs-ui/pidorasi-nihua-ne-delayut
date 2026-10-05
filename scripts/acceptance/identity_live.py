#!/usr/bin/env python3
"""Live black-box identity acceptance pack against the deployed compose stack.

Exercises the identity contract end to end over HTTP only (no imports from
``app``): register -> email verification token -> verify -> login -> me ->
logout, optional password reset (session revocation), admin bootstrap through
``app.cli`` and the RBAC witness endpoint, and the login throttle.

The mail is console-only today, so verification/reset tokens are read from the
API container log (``MAIL_TRANSPORT=console`` + ``APP_ENV=development``).
Tokens are stored one-way in the database; if the log does not contain the
token (see the uvicorn root-logger caveat in the README), the mail-dependent
steps are recorded as BLOCKED instead of silently skipped. ``--token-source
manual`` accepts pasted tokens for SMTP-based runs.

Usage::

    apps/api/.venv/bin/python scripts/acceptance/identity_live.py
    apps/api/.venv/bin/python scripts/acceptance/identity_live.py \
        --base-url http://127.0.0.1:8080 --compose-dir infra --throttle

Exit code 0 only when every required step passed. Evidence (no secrets: tokens
are recorded as length + sha256 prefix, passwords never) is written as JSON
next to the script under ``evidence/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

SESSION_COOKIE = "rb_platform_session"
TOKEN_RE_TEMPLATE = r"{marker}([A-Za-z0-9_\-]{{20,}})"
UTC = timezone.utc
REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = Path(__file__).resolve().parent / "evidence"


# --------------------------------------------------------------------------- #
# evidence collection
# --------------------------------------------------------------------------- #


class Evidence:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.notes: list[str] = []
        self.started = datetime.now(UTC)

    def record(
        self,
        area: str,
        expected: str,
        observed: str,
        *,
        ok: bool,
        blocked: bool = False,
        detail: Any = None,
    ) -> None:
        self.steps.append(
            {
                "area": area,
                "expected": expected,
                "observed": observed,
                "ok": bool(ok),
                "blocked": bool(blocked),
                "detail": detail,
            }
        )
        mark = "PASS" if ok else ("BLOCK" if blocked else "FAIL")
        print(f"[{mark:5}] {area}: {observed}")

    def note(self, text: str) -> None:
        self.notes.append(text)

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [s for s in self.steps if not s["ok"]]

    @property
    def blocked(self) -> list[dict[str, Any]]:
        return [s for s in self.steps if s["blocked"]]

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pack": "identity-live",
            "started_at": self.started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "summary": {
                "total": len(self.steps),
                "passed": sum(1 for s in self.steps if s["ok"]),
                "failed": len(self.failed),
                "blocked": len(self.blocked),
            },
            "notes": self.notes,
            "steps": self.steps,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path


# --------------------------------------------------------------------------- #
# docker helpers (read-only: logs, inspect, exec for admin bootstrap only)
# --------------------------------------------------------------------------- #


def docker_shell(inner: str) -> subprocess.CompletedProcess[str]:
    """Run a shell command with docker access.

    Uses the inherited docker group when the current process has it, otherwise
    ``sg docker -c ...``. Read-only except for the admin bootstrap exec.
    """
    if _in_docker_group():
        return subprocess.run(["bash", "-lc", inner], capture_output=True, text=True, timeout=120)
    if shutil.which("sg"):
        return subprocess.run(["sg", "docker", "-c", inner], capture_output=True, text=True, timeout=120)
    raise RuntimeError("no docker access: neither the docker group nor `sg` is available")


def _in_docker_group() -> bool:
    try:
        proc = subprocess.run(["id", "-nG"], capture_output=True, text=True, timeout=10)
        return "docker" in proc.stdout.split()
    except OSError:  # pragma: no cover - defensive
        return False


def api_image_id(container: str) -> str:
    proc = docker_shell(f"docker inspect {shlex.quote(container)} --format '{{{{.Image}}}} {{{{.State.StartedAt}}}}'")
    return (proc.stdout or proc.stderr).strip()


def fetch_logs(container: str, since: str) -> str:
    proc = docker_shell(
        f"docker logs {shlex.quote(container)} --since {shlex.quote(since)} 2>&1"
    )
    return proc.stdout or ""


def extract_mail_token(logs: str, email: str, kind: str) -> str | None:
    """Extract the newest verification/reset token sent to ``email`` from logs.

    ConsoleMailer writes one record: ``mail to=<email> subject=<s>\\n<body>``.
    """
    marker = {"verify": "/verify-email?token=", "reset": "/reset-password?token="}[kind]
    pattern = re.compile(TOKEN_RE_TEMPLATE.format(marker=re.escape(marker)))
    tokens: list[str] = []
    for block in re.split(r"(?=mail to=)", logs):
        if not block.startswith("mail to=") or email not in block:
            continue
        tokens.extend(m.group(1) for m in pattern.finditer(block))
    return tokens[-1] if tokens else None


def wait_for_token(
    container: str, since: str, email: str, kind: str, *, timeout_s: float = 10.0
) -> str | None:
    deadline = time.monotonic() + timeout_s
    while True:
        token = extract_mail_token(fetch_logs(container, since), email, kind)
        if token or time.monotonic() >= deadline:
            return token
        time.sleep(1.0)


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #


def token_fingerprint(token: str) -> str:
    return f"len={len(token)} sha256={hashlib.sha256(token.encode()).hexdigest()[:12]}"


def cookie_from_response(response: httpx.Response) -> str | None:
    for header in response.headers.get_list("set-cookie"):
        match = re.match(rf"^{SESSION_COOKIE}=([^;]+)", header)
        if match:
            return match.group(1)
    return None


def auth_headers(token: str) -> dict[str, str]:
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def body_snippet(response: httpx.Response, limit: int = 160) -> str:
    try:
        text = response.text.replace("\n", " ")
    except Exception:  # pragma: no cover - defensive
        return "<unreadable>"
    return text[:limit]


# --------------------------------------------------------------------------- #
# steps
# --------------------------------------------------------------------------- #


def run(args: argparse.Namespace) -> int:  # noqa: C901 - linear acceptance script
    evidence = Evidence()
    client = httpx.Client(base_url=args.base_url, timeout=15.0, follow_redirects=False)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"{stamp}-{secrets.token_hex(3)}"
    email = f"acceptance+{suffix}@example.com".lower()
    admin_email = f"acceptance-admin+{suffix}@example.com".lower()
    password = "Acceptance-pass-" + secrets.token_urlsafe(9)
    new_password = "Acceptance-new-pass-" + secrets.token_urlsafe(9)
    admin_password = "Admin-pass-" + secrets.token_urlsafe(9)
    current_password = password
    since = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    evidence.note(f"base_url={args.base_url}")
    evidence.note(f"api_container={args.api_container}")
    evidence.note(f"api_image={api_image_id(args.api_container)}")
    evidence.note(f"test_account={email}")

    # --- liveness -----------------------------------------------------------
    for path, expected in (("/healthz", 200), ("/readyz", 200)):
        response = client.get(path)
        evidence.record(
            f"liveness {path}",
            f"HTTP {expected}",
            f"HTTP {response.status_code} {body_snippet(response)}",
            ok=response.status_code == expected,
        )

    providers = client.get("/api/auth/providers")
    providers_ok = providers.status_code == 200 and providers.json().get("email_password") is True
    evidence.record(
        "providers",
        "HTTP 200, email_password=true (vk/yandex informational)",
        f"HTTP {providers.status_code} {body_snippet(providers)}",
        ok=providers_ok,
    )

    # --- register -----------------------------------------------------------
    register = client.post("/api/auth/register", json={"email": email, "password": password})
    register_ok = register.status_code == 201
    evidence.record(
        "register",
        "HTTP 201, verification_sent=true, email_verified=false",
        f"HTTP {register.status_code} {body_snippet(register)}",
        ok=register_ok,
        detail=None if not register_ok else {
            "user_id": register.json()["user"]["id"],
            "email_verified": register.json()["user"]["email_verified"],
        },
    )
    if not register_ok:
        evidence.note("register failed; remaining steps depend on an account and were skipped")
        return finish(evidence, args)

    # --- verify token discovery --------------------------------------------
    verify_token: str | None = None
    if args.token_source == "manual":
        verify_token = args.verify_token
    else:
        verify_token = wait_for_token(args.api_container, since, email, "verify")

    blocked_reason: str | None = None
    if not verify_token:
        blocked_reason = (
            "verification token not found in container log; ConsoleMailer logs at INFO "
            "but uvicorn's default LOGGING_CONFIG declares no root logger (root=WARNING), "
            "so mail lines never reach docker logs. Tokens are stored one-way; use "
            "--token-source manual or fix root logging in app/main.py."
        )
        evidence.note("BLOCKER: " + blocked_reason)

    if verify_token:
        first = client.post("/api/auth/verify-email", json={"token": verify_token})
        evidence.record(
            "verify-email",
            "HTTP 200, email_verified=true",
            f"HTTP {first.status_code} (token {token_fingerprint(verify_token)})",
            ok=first.status_code == 200 and first.json().get("user", {}).get("email_verified") is True,
        )
        reuse = client.post("/api/auth/verify-email", json={"token": verify_token})
        evidence.record(
            "verify-email reuse rejected",
            "HTTP 400 token_used",
            f"HTTP {reuse.status_code} {body_snippet(reuse)}",
            ok=reuse.status_code == 400 and "token_used" in reuse.text,
        )
    else:
        for area in ("verify-email", "verify-email reuse rejected"):
            evidence.record(area, "HTTP 200 / HTTP 400", "not attempted (token unavailable)", ok=False, blocked=True)

    # --- login / me / logout ------------------------------------------------
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    session = cookie_from_response(login)
    login_ok = login.status_code == 200 and session is not None
    evidence.record(
        "login",
        "HTTP 200 + session cookie",
        f"HTTP {login.status_code}, cookie={'yes' if session else 'missing'}",
        ok=login_ok,
    )
    if login_ok:
        me = client.get("/api/auth/me", headers=auth_headers(session))
        evidence.record(
            "me",
            "HTTP 200, same email",
            f"HTTP {me.status_code} {body_snippet(me)}",
            ok=me.status_code == 200 and me.json().get("user", {}).get("email") == email,
        )
        logout = client.post("/api/auth/logout", headers=auth_headers(session))
        evidence.record("logout", "HTTP 204", f"HTTP {logout.status_code}", ok=logout.status_code == 204)
        after = client.get("/api/auth/me", headers=auth_headers(session))
        evidence.record(
            "me after logout rejected",
            "HTTP 401",
            f"HTTP {after.status_code} {body_snippet(after)}",
            ok=after.status_code == 401,
        )

    # --- password reset (session revocation) --------------------------------
    if not args.skip_reset and login_ok:
        session_a = client.post("/api/auth/login", json={"email": email, "password": password})
        cookie_a = cookie_from_response(session_a)
        forgot = client.post("/api/auth/password/forgot", json={"email": email})
        evidence.record("password forgot", "HTTP 202", f"HTTP {forgot.status_code}", ok=forgot.status_code == 202)
        reset_token = args.reset_token if args.token_source == "manual" else wait_for_token(
            args.api_container, since, email, "reset"
        )
        if reset_token:
            reset = client.post(
                "/api/auth/password/reset", json={"token": reset_token, "password": new_password}
            )
            evidence.record(
                "password reset",
                "HTTP 200, sessions_revoked=true",
                f"HTTP {reset.status_code} (token {token_fingerprint(reset_token)})",
                ok=reset.status_code == 200 and reset.json().get("sessions_revoked") is True,
            )
            if cookie_a:
                revoked = client.get("/api/auth/me", headers=auth_headers(cookie_a))
                evidence.record(
                    "pre-reset session revoked",
                    "HTTP 401",
                    f"HTTP {revoked.status_code}",
                    ok=revoked.status_code == 401,
                )
            old_login = client.post("/api/auth/login", json={"email": email, "password": password})
            evidence.record(
                "old password rejected",
                "HTTP 401",
                f"HTTP {old_login.status_code}",
                ok=old_login.status_code == 401,
            )
            new_login = client.post("/api/auth/login", json={"email": email, "password": new_password})
            evidence.record(
                "new password accepted",
                "HTTP 200",
                f"HTTP {new_login.status_code}",
                ok=new_login.status_code == 200,
            )
            if new_login.status_code == 200:
                current_password = new_password
        else:
            evidence.record(
                "password reset",
                "HTTP 200",
                "not attempted (reset token unavailable; same root-logging cause)",
                ok=False,
                blocked=True,
            )

    # --- admin bootstrap + RBAC --------------------------------------------
    if not args.skip_admin:
        bootstrap = run_admin_bootstrap(args.api_container, admin_email, admin_password)
        evidence.record(
            "admin bootstrap (app.cli create-admin)",
            "exit 0, 'admin ready'",
            f"exit {bootstrap.returncode}: {body_snippet_text(bootstrap.stdout, bootstrap.stderr)}",
            ok=bootstrap.returncode == 0 and "admin ready" in (bootstrap.stdout or ""),
        )
        admin_login = client.post("/api/auth/login", json={"email": admin_email, "password": admin_password})
        admin_cookie = cookie_from_response(admin_login)
        if admin_cookie:
            whoami = client.get("/api/admin/whoami", headers=auth_headers(admin_cookie))
            evidence.record(
                "admin RBAC witness",
                "HTTP 200 {admin: email}",
                f"HTTP {whoami.status_code} {body_snippet(whoami)}",
                ok=whoami.status_code == 200 and whoami.json().get("admin") == admin_email,
            )
        else:
            evidence.record("admin RBAC witness", "HTTP 200", "admin login failed", ok=False)
        user_login = client.post("/api/auth/login", json={"email": email, "password": current_password})
        user_cookie = cookie_from_response(user_login)
        if user_cookie:
            denied = client.get("/api/admin/whoami", headers=auth_headers(user_cookie))
            evidence.record(
                "non-admin denied",
                "HTTP 403 admin_required",
                f"HTTP {denied.status_code} {body_snippet(denied)}",
                ok=denied.status_code == 403 and "admin_required" in denied.text,
            )
        else:
            evidence.record("non-admin denied", "HTTP 403", "user login failed", ok=False)

    # --- login throttle -----------------------------------------------------
    if args.throttle:
        throttle_email = f"acceptance-throttle+{suffix}@example.com"
        codes: list[int] = []
        for _ in range(args.throttle_limit + 2):
            attempt = client.post(
                "/api/auth/login", json={"email": throttle_email, "password": "wrong-password"}
            )
            codes.append(attempt.status_code)
            if attempt.status_code == 429:
                break
        evidence.record(
            "login throttle",
            f"429 within {args.throttle_limit + 1} attempts",
            f"status codes: {codes}",
            ok=429 in codes and codes.index(429) <= args.throttle_limit,
        )

    return finish(evidence, args)


def body_snippet_text(stdout: str | None, stderr: str | None, limit: int = 200) -> str:
    text = ((stdout or "") + " " + (stderr or "")).replace("\n", " ").strip()
    return text[:limit]


def run_admin_bootstrap(container: str, email: str, password: str) -> subprocess.CompletedProcess[str]:
    """Bootstrap an admin through the container's own environment.

    Equivalent to the documented ``docker compose exec ... app.cli`` but works
    regardless of which worktree the harness runs from (no compose .env needed).
    """
    inner = (
        f"docker exec -e ADMIN_PASSWORD={shlex.quote(password)} {shlex.quote(container)} "
        f"python -m app.cli create-admin --email {shlex.quote(email)} --password-env ADMIN_PASSWORD"
    )
    return docker_shell(inner)


def finish(evidence: Evidence, args: argparse.Namespace) -> int:
    out = Path(args.out) if args.out else EVIDENCE_DIR / (
        f"identity-live-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    path = evidence.write(out)
    summary = f"{path} :: {len(evidence.steps) - len(evidence.failed)}/{len(evidence.steps)} passed"
    if evidence.blocked:
        summary += f", {len(evidence.blocked)} blocked"
    print("\n" + summary)
    if evidence.failed:
        print("FAILED steps:")
        for step in evidence.failed:
            print(f"  - [{('BLOCK' if step['blocked'] else 'FAIL')}] {step['area']}: {step['observed']}")
    return 1 if evidence.failed else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--api-container", default="rubai-api-1")
    parser.add_argument("--token-source", choices=("logs", "manual"), default="logs")
    parser.add_argument("--verify-token", default=None, help="with --token-source manual")
    parser.add_argument("--reset-token", default=None, help="with --token-source manual")
    parser.add_argument("--skip-reset", action="store_true")
    parser.add_argument("--skip-admin", action="store_true")
    parser.add_argument("--throttle", action="store_true", help="run the live login-throttle probe")
    parser.add_argument("--throttle-limit", type=int, default=10)
    parser.add_argument("--out", default=None, help="evidence JSON path")
    args = parser.parse_args(argv)
    if args.token_source == "manual" and not (args.verify_token or args.reset_token):
        parser.error("--token-source manual requires --verify-token and/or --reset-token")
    return args


if __name__ == "__main__":
    sys.exit(run(parse_args()))
