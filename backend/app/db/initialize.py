"""Explicitly initialize all application schemas in the same database."""
import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.db.bootstrap_catalog import CatalogBootstrapError, bootstrap_catalog, check_existing_schema, load_seed
from app.db.build_catalog import DEFAULT_OUTPUT

DB_DIR = Path(__file__).resolve().parents[2] / 'db'
SCHEMAS = ('schema_catalog.sql', 'migrations/20261029_identity.sql', 'migrations/20261030_research.sql', 'migrations/20261108_orchestration_experiments.sql')


async def initialize(host, apply=False, *, bootstrap=False, catalog_file=DEFAULT_OUTPUT):
    if bootstrap and not apply:
        raise CatalogBootstrapError("Для загрузки справочника нужны --apply и --bootstrap-catalog.")
    if make_url(settings.catalog_url).host != host:
        raise SystemExit('Хост не совпадает с --expected-host.')
    # Validate before connecting or executing any DDL.
    seed = load_seed(catalog_file) if bootstrap else None
    sql = [ (DB_DIR / name).read_text(encoding='utf-8') for name in SCHEMAS ]
    engine = create_async_engine(settings.catalog_url)
    result = None
    try:
        async with engine.begin() as db:
            await db.execute(text("SET LOCAL lock_timeout = '5s'"))
            await db.execute(text('SELECT 1'))
            if apply:
                await db.execute(text('SELECT pg_advisory_xact_lock(873024120)'))
                raw = await db.get_raw_connection()
                await check_existing_schema(raw.driver_connection)
                for statement in sql:
                    await raw.driver_connection.execute(statement)
                if seed is not None:
                    result = await bootstrap_catalog(raw.driver_connection, seed)
        if result:
            message = 'Справочник загружен.' if result['status'] == 'loaded' else 'Существующий справочник сохранён без изменений.'
            print('Схемы применены. ' + message + ' ' + json.dumps(result, ensure_ascii=False))
        else:
            print('Схемы применены без загрузки справочника. Для первичной загрузки добавьте --bootstrap-catalog.'
                  if apply else 'Подключение проверено. Для создания схем добавьте --apply.')
        return result
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-host', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--bootstrap-catalog', action='store_true', help='Load the bundled catalog only into empty catalog tables')
    parser.add_argument('--catalog-file', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        asyncio.run(initialize(args.expected_host, args.apply, bootstrap=args.bootstrap_catalog, catalog_file=args.catalog_file))
    except CatalogBootstrapError as exc:
        raise SystemExit(f'Инициализация отменена: {exc}') from None
    except Exception as exc:
        raise SystemExit(f'Инициализация отменена: {type(exc).__name__}.') from None
