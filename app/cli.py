"""Operational CLI entrypoint for Local Voice Agent administrative tasks."""

import argparse
import asyncio
import sys
from typing import Optional

from app.services.admin_auth import AdminAuthService
from app.storage.database import init_db


async def _seed_admin() -> None:
    await init_db()
    generated_pass = await AdminAuthService.seed_admin_user()
    if generated_pass:
        print(f"ADMIN_SEED_GENERATED:{generated_pass}")
    else:
        print("ADMIN_ALREADY_INITIALIZED")


async def _reset_admin(password: Optional[str] = None) -> None:
    await init_db()
    res = await AdminAuthService.reset_admin_password(new_password=password)
    print(f"ADMIN_RESET_SUCCESS: New initial password is '{res}' (must change on next login).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Voice AI Agent CLI Tools")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    seed_parser = subparsers.add_parser("seed-admin", help="Seed super admin if database is empty")

    reset_parser = subparsers.add_parser("reset-admin", help="Break-glass super admin password reset")
    reset_parser.add_argument("--password", help="Explicit password to set (generates random if omitted)")

    args = parser.parse_args()

    if args.subcommand == "seed-admin":
        asyncio.run(_seed_admin())
    elif args.subcommand == "reset-admin":
        asyncio.run(_reset_admin(args.password))


if __name__ == "__main__":
    main()

