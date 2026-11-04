"""Create/reset/disable administrators without public registration or plaintext credentials."""
import argparse
import asyncio
import getpass
import re

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.services.passwords import hasher


async def manage(command: str, login: str, password_hash: str | None):
    engine = create_async_engine(settings.catalog_url, connect_args={"timeout": 15, "command_timeout": 30})
    try:
        async with engine.begin() as db:
            existing = (await db.execute(text("SELECT id, role FROM public.users WHERE lower(login)=:login FOR UPDATE"),
                                         {"login": login})).mappings().first()
            if command == "create":
                if existing:
                    raise SystemExit("Login already exists; no changes made.")
                await db.execute(text("""
                    INSERT INTO public.users(login, display_name, role, account_kind, password_hash)
                    VALUES (:login, :login, 'admin', 'account', :hash)
                """), {"login": login, "hash": password_hash})
            else:
                if not existing or existing["role"] != "admin":
                    raise SystemExit("Administrator not found; no changes made.")
                if command == "reset-password":
                    await db.execute(text("UPDATE public.users SET password_hash=:hash, updated_at=now() WHERE id=:id"),
                                     {"id": existing["id"], "hash": password_hash})
                elif command == "disable":
                    await db.execute(text("UPDATE public.users SET is_active=false, updated_at=now() WHERE id=:id"), {"id": existing["id"]})
                await db.execute(text("UPDATE public.user_sessions SET revoked_at=now() WHERE user_id=:id AND revoked_at IS NULL"),
                                 {"id": existing["id"]})
        print(f"Administrator operation completed: {command}. No credentials printed.")
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["create", "reset-password", "disable", "revoke-sessions"])
    parser.add_argument("--login", required=True)
    parser.add_argument("--expected-host", required=True)
    args = parser.parse_args()
    if make_url(settings.catalog_url).host != args.expected_host:
        raise SystemExit("Database host differs from --expected-host; no changes made.")
    login = args.login.lower()
    if not re.fullmatch(r"[a-z0-9_.@-]{1,100}", login):
        raise SystemExit("Login must contain 1-100 ASCII letters, digits, '.', '_', '@' or '-'.")
    encoded = None
    if args.command in {"create", "reset-password"}:
        password = getpass.getpass("New password (15-256 characters): ")
        if not 15 <= len(password) <= 256 or password != getpass.getpass("Repeat password: "):
            raise SystemExit("Passwords must match and contain 15-256 characters.")
        encoded = hasher.hash(password)
        del password
    try:
        asyncio.run(manage(args.command, login, encoded))
    except Exception as error:
        raise SystemExit(f"Operation failed ({type(error).__name__}). No credentials printed.") from None


if __name__ == "__main__":
    main()
