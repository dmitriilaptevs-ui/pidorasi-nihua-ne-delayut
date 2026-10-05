"""Operator CLI: bootstrap the first administrator.

    docker compose exec api python -m app.cli create-admin --email you@example.com

The password is read interactively (getpass) and never echoed or logged.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os

from .db import session_factory
from .errors import ApiError
from .security import hash_password
from .services import identity as service


async def _create_admin(email: str, password: str) -> None:
    async with session_factory()() as db:
        try:
            user, _ = await service.register(db, email=email, password=password)
        except ApiError as exc:
            if exc.code != "email_taken":
                raise
            user = await service.get_user_by_email(db, email)
            if user is None:  # pragma: no cover - race only
                raise
            user.password_hash = hash_password(password)
        user.role = "admin"
        user.email_verified_at = service.utcnow()
        await db.commit()
        print(f"admin ready: {user.email}")


async def _promote_admin(email: str) -> None:
    async with session_factory()() as db:
        user = await service.promote_admin(db, email=email)
        await db.commit()
        print(f"promoted: {user.email}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-admin", help="create or update an administrator account")
    create.add_argument("--email", required=True)
    create.add_argument(
        "--password-env",
        default=None,
        help="read the password from this environment variable instead of the terminal",
    )

    promote = sub.add_parser("promote-admin", help="grant the admin role to an existing account")
    promote.add_argument("--email", required=True)

    args = parser.parse_args()
    if args.command == "create-admin":
        password = os.environ.get(args.password_env) if args.password_env else None
        if not password:
            password = getpass.getpass("password: ")
        if len(password) < 10:
            raise SystemExit("password must be at least 10 characters")
        asyncio.run(_create_admin(args.email, password))
    elif args.command == "promote-admin":
        asyncio.run(_promote_admin(args.email))


if __name__ == "__main__":
    main()
