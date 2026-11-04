"""Add annotation storage to the existing catalog, without changing ingredient data."""

import argparse
import asyncio
from pathlib import Path

import asyncpg
from sqlalchemy.engine import make_url

from app.core.config import settings


async def migrate(expected_host: str, apply: bool) -> None:
    url = make_url(settings.catalog_url)
    if url.host != expected_host:
        raise SystemExit("Database host differs from --expected-host; no changes made.")
    connection = await asyncpg.connect(host=url.host, port=url.port or 5432,
        user=url.username, password=url.password, database=url.database,
        ssl=url.query.get("ssl", "prefer"), timeout=15, command_timeout=30)
    try:
        async with connection.transaction():
            await connection.execute("SET LOCAL lock_timeout = '5s'")
            version = await connection.fetchval("SELECT version FROM catalog.schema_version")
            if version != 2 or not await connection.fetchval("SELECT to_regclass('decision_tree.review_queue')"):
                raise SystemExit("Catalog v2 and decision_tree.review_queue must already exist.")
            count = await connection.fetchval("SELECT count(*) FROM catalog.ingredients")
            print(f"Existing catalog: {count} ingredients. Target host: {url.host}.")
            if not apply:
                print("Dry run. Pass --apply to add the annotation table.")
                return
            migration = Path(__file__).resolve().parents[2] / "db/migrations/20261018_user_corrections.sql"
            await connection.execute(migration.read_text(encoding="utf-8"))
            if count != await connection.fetchval("SELECT count(*) FROM catalog.ingredients"):
                raise RuntimeError("Ingredient count changed; rolling back.")
        print("Annotation migration committed. Catalog data unchanged.")
    finally:
        await connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-host", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(migrate(args.expected_host, args.apply))
    except (asyncpg.PostgresError, OSError, TimeoutError) as error:
        raise SystemExit(f"Migration failed: {type(error).__name__}. Connection details omitted.") from None


if __name__ == "__main__":
    main()
