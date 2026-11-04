"""Cleanup integration is restricted to a newly created loopback test database."""
import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import asyncpg
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.exc import DBAPIError

from app.db.cleanup_catalog import cleanup, table_names
from app.db.initialize import DB_DIR, SCHEMAS
from app.db.schema_contract import CATALOG_DATA_TABLES, PARSER_TABLES, REQUIRED_TABLES
from app.db.import_catalog import import_catalog, STORED_COLUMNS
from app.db.apply_catalog_supplement import snapshot, update


class SchemaContractTests(unittest.TestCase):
    def test_only_runtime_tables_are_created(self):
        import re
        sql = '\n'.join((DB_DIR / name).read_text(encoding='utf-8') for name in SCHEMAS)
        tables = set(re.findall(r'CREATE TABLE IF NOT EXISTS ([\w.]+)', sql))
        self.assertEqual(tables, REQUIRED_TABLES)
        self.assertEqual(set(STORED_COLUMNS), set(CATALOG_DATA_TABLES))


@unittest.skipUnless(os.environ.get('TOXICHECK_CLEANUP_LOCAL_TEST') == '1', 'Opt-in isolated localhost PostgreSQL')
class CleanupDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_backup_cleanup_restore_and_compact_import(self):
        root = await asyncpg.connect(host='127.0.0.1', port=55439, user='research_test', database='postgres')
        name = 'cleanup_test_' + uuid4().hex
        restored = name + '_restore'
        await root.execute(f'CREATE DATABASE {name}')
        await root.execute(f'CREATE DATABASE {restored}')
        url = make_url(f'postgresql+asyncpg://research_test@127.0.0.1:55439/{name}')
        engine = create_async_engine(url)
        executable = Path(os.environ.get('TOXICHECK_PG_BIN', r'C:\Program Files\PostgreSQL\16\bin'))
        pg_dump = executable / ('pg_dump.exe' if os.name == 'nt' else 'pg_dump')
        try:
            async with engine.begin() as db:
                raw = await db.get_raw_connection()
                for path in SCHEMAS:
                    await raw.driver_connection.execute((DB_DIR / path).read_text(encoding='utf-8'))
                await db.execute(text("INSERT INTO public.users(id) VALUES(:id)"), {'id': uuid4()})
                await db.execute(text("INSERT INTO research.studies(id,title,instruction,scenario) VALUES(:id,'Keep','Keep','ocr_quality')"), {'id': uuid4()})
                await db.execute(text("INSERT INTO decision_tree.training_data(token_raw,canonical_name,label,source) VALUES('test','test',1,'test')"))
                for table in PARSER_TABLES:
                    await db.execute(text(f'CREATE TABLE catalog.{table}(id integer PRIMARY KEY, note text)'))
                    await db.execute(text(f"INSERT INTO catalog.{table} VALUES(1,'preserve in backup')"))
                await db.execute(text('ALTER TABLE catalog.document_links ADD FOREIGN KEY(id) REFERENCES catalog.documents(id)'))
                await db.execute(text('CREATE TABLE public.keep_me(id integer REFERENCES catalog.documents(id))'))
            with tempfile.TemporaryDirectory() as temp:
                folder = Path(temp)
                # Preview never calls backup or removes tables.
                with patch('app.db.cleanup_catalog.dump_database', side_effect=AssertionError('preview writes')):
                    async with engine.begin() as db:
                        report = await cleanup(db, url, folder, pg_dump)
                        self.assertEqual(len(report['remove']), 9)
                        self.assertFalse(report['applied'])
                # Real backup + restrictive DROP must roll back if an external FK exists.
                with self.assertRaises(DBAPIError) as blocked:
                    async with engine.begin() as db:
                        await cleanup(db, url, folder, pg_dump, apply=True)
                self.assertEqual(blocked.exception.orig.sqlstate, '2BP01')
                async with engine.begin() as db:
                    self.assertEqual(await db.scalar(text('SELECT count(*) FROM catalog.documents')), 1)
                    await db.execute(text('ALTER TABLE public.keep_me DROP CONSTRAINT keep_me_id_fkey'))
                # Failed backup cannot delete anything.
                with patch('app.db.cleanup_catalog.dump_database', side_effect=RuntimeError('backup failed')):
                    with self.assertRaises(RuntimeError):
                        async with engine.begin() as db:
                            await cleanup(db, url, folder, pg_dump, apply=True)
                async with engine.begin() as db:
                    report = await cleanup(db, url, folder, pg_dump, apply=True)
                    self.assertTrue(report['applied'])
                    self.assertEqual(set(await table_names(db)), REQUIRED_TABLES | {'public.keep_me'})
                    for table in ('public.users', 'research.studies', 'decision_tree.training_data'):
                        self.assertEqual(await db.scalar(text(f'SELECT count(*) FROM {table}')), 1)
                async with engine.begin() as db:
                    self.assertFalse((await cleanup(db, url, folder, pg_dump, apply=True))['applied'])
                restore = executable / ('pg_restore.exe' if os.name == 'nt' else 'pg_restore')
                result = await asyncio.to_thread(subprocess.run, [str(restore), '--exit-on-error', '--no-owner',
                    '-h', '127.0.0.1', '-p', '55439', '-U', 'research_test', '-d', restored, report['backup']],
                    capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
                restored_db = await asyncpg.connect(host='127.0.0.1', port=55439, user='research_test', database=restored)
                try:
                    self.assertEqual(await restored_db.fetchval('SELECT count(*) FROM catalog.documents'), 1)
                    self.assertEqual(await restored_db.fetchval('SELECT count(*) FROM decision_tree.training_data'), 1)
                finally:
                    await restored_db.close()
                # Full source bundle imports only runtime tables, and supplement still reads it.
                connection = await asyncpg.connect(host='127.0.0.1', port=55439, user='research_test', database=name)
                try:
                    bundle = json.loads((DB_DIR.parents[1] / 'toxicheck_catalog.json').read_text(encoding='utf-8'))
                    await import_catalog(connection, bundle, filename='test.json', file_sha256='0' * 64)
                    captured = await snapshot(connection)
                    self.assertEqual(len(captured['ingredients']), len(bundle['ingredients']))
                    await update(connection, False, folder / 'supplement.json')
                    await connection.execute("UPDATE catalog.ingredients SET full_description='outdated test text' WHERE e_code='E121'")
                    changed = await update(connection, True, folder / 'supplement.json')
                    self.assertGreater(changed['changed_rows']['ingredients'], 0)
                    self.assertNotEqual(await connection.fetchval("SELECT full_description FROM catalog.ingredients WHERE e_code='E121'"), 'outdated test text')
                    for table in PARSER_TABLES:
                        self.assertIsNone(await connection.fetchval('SELECT to_regclass($1)::text', f'catalog.{table}'))
                finally:
                    await connection.close()
        finally:
            await engine.dispose()
            await root.execute(f'DROP DATABASE {name} WITH (FORCE)')
            await root.execute(f'DROP DATABASE {restored} WITH (FORCE)')
            await root.close()
