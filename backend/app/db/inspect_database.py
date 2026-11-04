"""Read-only schema inventory; never prints credentials or row contents."""
import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import settings


async def inventory(connection):
    tables = (await connection.execute(text('''SELECT n.nspname AS schema, c.relname AS name,
        c.relkind AS kind, pg_total_relation_size(c.oid) AS bytes
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p')
        ORDER BY n.nspname,c.relname'''))).mappings().all()
    result = []
    quote = connection.dialect.identifier_preparer.quote_identifier
    for row in tables:
        name = f"{quote(row['schema'])}.{quote(row['name'])}"
        count = await connection.scalar(text(f'SELECT count(*) FROM {name}'))
        result.append({**dict(row), 'rows': count})
    dependencies = [dict(row) for row in (await connection.execute(text('''SELECT
        conrelid::regclass::text AS child, confrelid::regclass::text AS parent,
        conname AS constraint, pg_get_constraintdef(oid) AS definition
        FROM pg_constraint WHERE contype='f' ORDER BY 1,2'''))).mappings()]
    views = [dict(row) for row in (await connection.execute(text('''SELECT schemaname,viewname
        FROM pg_views WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2'''))).mappings()]
    return {'tables': result, 'foreign_keys': dependencies, 'views': views}


async def inspect(expected_host, output):
    url = make_url(settings.catalog_url)
    if url.host != expected_host:
        raise SystemExit('Хост не совпадает с --expected-host.')
    engine = create_async_engine(settings.catalog_url, connect_args={'timeout': 15, 'command_timeout': 30})
    try:
        async with engine.connect() as connection, connection.begin():
            await connection.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            result = await inventory(connection)
            result['target'] = {'host': url.host, 'port': url.port, 'database': url.database}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        for row in result['tables']:
            print(f"{row['schema']}.{row['name']}: {row['rows']} rows")
        print('Inventory saved; no database changes.')
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-host', required=True)
    parser.add_argument('--output', type=Path, default=Path('../.db-maintenance.local/inventory.json'))
    args = parser.parse_args()
    try:
        asyncio.run(inspect(args.expected_host, args.output))
    except Exception as exc:
        raise SystemExit(f'Inventory failed: {type(exc).__name__}; connection details omitted.') from None
