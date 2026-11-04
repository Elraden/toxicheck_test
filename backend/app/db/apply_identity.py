"""Transactional identity migration for the existing catalog database."""
import argparse
import asyncio
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings


async def migrate(expected_host: str, apply: bool):
    if make_url(settings.catalog_url).host != expected_host:
        raise SystemExit("Database host differs from --expected-host; no changes made.")
    engine = create_async_engine(settings.catalog_url, connect_args={"timeout": 15, "command_timeout": 30})
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            version = await connection.scalar(text("SELECT version FROM catalog.schema_version"))
            if version != 2 or not await connection.scalar(text("SELECT to_regclass('decision_tree.user_corrections')")):
                raise SystemExit("Catalog v2 and the user corrections migration are required.")
            count = await connection.scalar(text("SELECT count(*) FROM catalog.ingredients"))
            print(f"Target: {expected_host}; catalog: {count} ingredients.")
            if not apply:
                print("Dry run. Add --apply to create identity storage.")
                return
            sql = (Path(__file__).resolve().parents[2] / "db/migrations/20261029_identity.sql").read_text(encoding="utf-8")
            raw = await connection.get_raw_connection()
            await raw.driver_connection.execute(sql)
        print("Identity migration committed. Ingredient catalog unchanged.")
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-host", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(migrate(args.expected_host, args.apply))
    except Exception as error:
        raise SystemExit(f"Migration failed ({type(error).__name__}); transaction rolled back. Connection details omitted.") from None


if __name__ == "__main__":
    main()
