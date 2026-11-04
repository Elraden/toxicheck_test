"""Preview/remove obsolete parser tables with a full database backup. No CASCADE."""
import argparse
import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.db.schema_contract import PARSER_TABLES


async def table_names(db):
    return list((await db.execute(text("""SELECT quote_ident(n.nspname)||'.'||quote_ident(c.relname)
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
        AND c.relkind IN ('r','p') ORDER BY 1"""))).scalars())


async def counts(db, names):
    return {name: await db.scalar(text(f'SELECT count(*) FROM {name}')) for name in names}


def dump_database(url, snapshot, path, executable):
    # Password never goes into process arguments, output, or the report.
    env = os.environ.copy()
    env.update(PGHOST=url.host or '', PGPORT=str(url.port or 5432),
               PGUSER=url.username or '', PGPASSWORD=url.password or '',
               PGDATABASE=url.database or '', PGCONNECT_TIMEOUT='15')
    ssl_mode = url.query.get('sslmode') or url.query.get('ssl')
    if ssl_mode:
        env['PGSSLMODE'] = str(ssl_mode)
    result = subprocess.run([str(executable), '--format=custom', '--no-password',
                             f'--snapshot={snapshot}', f'--file={path}'],
                            env=env, capture_output=True, timeout=600)
    if result.returncode or not path.exists() or not path.stat().st_size:
        raise RuntimeError('pg_dump failed; no tables removed. Check pg_dump/server versions and access.')


async def cleanup(db, url, backup_dir, executable, apply=False):
    names = await table_names(db)
    obsolete = [f'catalog.{name}' for name in PARSER_TABLES if f'catalog.{name}' in names]
    report = {'target': {'host': url.host, 'database': url.database},
              'remove': obsolete, 'keep': [n for n in names if n not in obsolete], 'applied': False}
    if not apply or not obsolete:
        report['rows'] = await counts(db, names)
        return report
    # Freeze writes while pg_dump reads the exported snapshot. Unknown tables are retained.
    await db.execute(text("SET LOCAL lock_timeout = '5s'"))
    await db.execute(text('SELECT pg_advisory_xact_lock(873024120)'))
    await db.execute(text('LOCK TABLE ' + ', '.join(names) + ' IN SHARE MODE'))
    before = await counts(db, names)
    snapshot = await db.scalar(text('SELECT pg_export_snapshot()'))
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = backup_dir / f'before-catalog-cleanup-{stamp}.dump'
    await asyncio.to_thread(dump_database, url, snapshot, backup, executable)
    report.update(rows=before, backup=str(backup.resolve()))
    # RESTRICT fails for outside FK/view dependencies; transaction rolls back every DROP.
    await db.execute(text('DROP TABLE ' + ', '.join(obsolete) + ' RESTRICT'))
    after = await counts(db, report['keep'])
    if after != {name: before[name] for name in report['keep']}:
        raise RuntimeError('Retained row counts changed; cleanup rolled back.')
    report['applied'] = True
    return report


async def run(args):
    url = make_url(settings.catalog_url)
    if url.host != args.expected_host:
        raise SystemExit('Хост не совпадает с --expected-host. Изменения не выполнены.')
    engine = create_async_engine(settings.catalog_url)
    try:
        async with engine.begin() as db:
            if not args.apply:
                await db.execute(text('SET TRANSACTION READ ONLY'))
            report = await cleanup(db, url, args.backup_dir, args.pg_dump, args.apply)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-host', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-dir', type=Path, default=Path('../.db-maintenance.local'))
    parser.add_argument('--pg-dump', type=Path, default=Path('pg_dump'))
    try:
        asyncio.run(run(parser.parse_args()))
    except Exception as exc:
        raise SystemExit(f'Очистка отменена: {type(exc).__name__}. Проверьте зависимости, pg_dump и доступ к БД.') from None
