"""Explicitly initialize all application schemas in the same database."""
import argparse
import asyncio
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings

DB_DIR = Path(__file__).resolve().parents[2] / 'db'
SCHEMAS = ('schema_catalog.sql', 'migrations/20261029_identity.sql', 'migrations/20261030_research.sql')


async def initialize(host, apply=False):
    if make_url(settings.catalog_url).host != host:
        raise SystemExit('Хост не совпадает с --expected-host.')
    sql = [ (DB_DIR / name).read_text(encoding='utf-8') for name in SCHEMAS ]
    engine = create_async_engine(settings.catalog_url)
    try:
        async with engine.begin() as db:
            await db.execute(text("SET LOCAL lock_timeout = '5s'"))
            await db.execute(text('SELECT 1'))
            if apply:
                await db.execute(text('SELECT pg_advisory_xact_lock(873024120)'))
                raw = await db.get_raw_connection()
                for statement in sql:
                    await raw.driver_connection.execute(statement)
        print('Схемы применены.' if apply else 'Подключение проверено. Для создания схем добавьте --apply.')
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-host', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        asyncio.run(initialize(args.expected_host, args.apply))
    except Exception as exc:
        raise SystemExit(f'Инициализация отменена: {type(exc).__name__}.') from None
