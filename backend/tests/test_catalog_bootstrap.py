"""Bootstrap regression tests; PostgreSQL tests create only disposable databases."""

import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import asyncpg
import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.bootstrap_catalog import CatalogBootstrapError, load_seed, sql_value
from app.db.build_catalog import COLUMNS, DEFAULT_OUTPUT
from app.db.initialize import initialize
from app.main import create_app


class CatalogSeedTests(unittest.TestCase):
    def test_release_snapshot_is_complete_and_has_stable_identifiers(self):
        seed = load_seed()
        self.assertGreater(seed.counts['ingredients'], 1000)
        self.assertGreater(seed.counts['ingredient_aliases'], seed.counts['ingredients'])
        self.assertGreater(seed.counts['ingredient_rules'], 0)
        self.assertEqual(len(seed.sha256), 64)
        self.assertEqual(seed.sha256, load_seed().sha256)

    def test_invalid_missing_empty_and_incompatible_seed_fail_before_connecting(self):
        empty = {table: [] for table in COLUMNS}
        empty['metadata'] = {'dataset': 'toxicheck_catalog', 'schema_version': 2}
        wrong_version = copy.deepcopy(empty)
        wrong_version['metadata']['schema_version'] = 3
        duplicate = copy.deepcopy(load_seed().bundle)
        duplicate['ingredients'].append(duplicate['ingredients'][0].copy())
        orphan = copy.deepcopy(load_seed().bundle)
        orphan['ingredient_aliases'][0]['ingredient_id'] = str(uuid4())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'seed.json'
            for content in (None, '{invalid', 'null', json.dumps(empty), json.dumps(wrong_version),
                            json.dumps(duplicate), json.dumps(orphan)):
                with self.subTest(content='missing' if content is None else 'invalid'):
                    if content is not None:
                        path.write_text(content, encoding='utf-8')
                    with patch('app.db.initialize.settings', SimpleNamespace(catalog_url=
                               'postgresql+asyncpg://bootstrap_test@127.0.0.1:55440/postgres')), \
                         patch('app.db.initialize.create_async_engine') as connect:
                        with self.assertRaises(CatalogBootstrapError):
                            asyncio.run(initialize('127.0.0.1', True, bootstrap=True, catalog_file=path))
                        connect.assert_not_called()

    def test_explicit_apply_and_expected_host_are_required(self):
        with patch('app.db.initialize.create_async_engine') as connect:
            with self.assertRaises(CatalogBootstrapError):
                asyncio.run(initialize('postgres', bootstrap=True))
            with patch('app.db.initialize.settings', SimpleNamespace(
                    catalog_url='postgresql+asyncpg://bootstrap_test@127.0.0.1:55440/postgres')):
                with self.assertRaises(SystemExit):
                    asyncio.run(initialize('wrong-host', True, bootstrap=True))
            connect.assert_not_called()


@unittest.skipUnless(os.environ.get('TOXICHECK_BOOTSTRAP_TEST') == '1',
                     'Requires isolated bootstrap_test PostgreSQL on loopback port 55440')
class CatalogBootstrapDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Do not consume DATABASE_URL or production credentials, even when present.
        self.admin = await asyncpg.connect(host='127.0.0.1', port=55440,
                                          user='bootstrap_test', database='postgres')
        self.name = 'bootstrap_test_' + uuid4().hex
        await self.admin.execute(f'CREATE DATABASE {self.name}')
        self.addAsyncCleanup(self.drop_database)
        self.url = f'postgresql+asyncpg://bootstrap_test@127.0.0.1:55440/{self.name}'
        self.connection = await asyncpg.connect(self.url.replace('+asyncpg', ''))
        self.addAsyncCleanup(self.connection.close)
        self.engine = create_async_engine(self.url)
        self.addAsyncCleanup(self.engine.dispose)

    async def drop_database(self):
        # Generated names only; never DROP schemas/tables in a user-provided DB.
        assert self.name.startswith('bootstrap_test_') and len(self.name) == len('bootstrap_test_') + 32
        await self.admin.execute(f'DROP DATABASE {self.name} WITH (FORCE)')
        await self.admin.close()

    async def initialize(self, bootstrap=True):
        with patch('app.db.initialize.settings', SimpleNamespace(catalog_url=self.url)):
            return await initialize('127.0.0.1', True, bootstrap=bootstrap)

    async def health(self):
        sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        with patch('app.db.session.CatalogSessionLocal', sessions):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                         base_url='http://test') as client:
                return await client.get('/api/health/db')

    async def snapshot(self, tables):
        return {table: await self.connection.fetchval(
            f"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text), '[]'::jsonb)::text FROM {table} t"
        ) for table in tables}

    async def add_user_data(self):
        user_id = await self.connection.fetchval(
            "INSERT INTO public.users (display_name) VALUES ('Keep this user') RETURNING id"
        )
        await self.connection.execute(
            "INSERT INTO public.user_preferences (user_id, options) VALUES ($1, '{\"keep\":true}')", user_id
        )
        await self.connection.execute(
            "INSERT INTO research.studies (id, title, instruction, scenario) VALUES ($1, 'Keep study', 'Keep instruction', 'comparison')",
            uuid4(),
        )
        await self.connection.execute(
            "INSERT INTO decision_tree.training_data (token_raw, canonical_name, label, source) VALUES ('keep', 'keep', 1, 'test')"
        )
        return user_id

    async def test_schema_only_503_then_seed_200_repeat_preserves_users_exclusions_and_edits(self):
        await self.initialize(bootstrap=False)
        self.assertEqual(await self.connection.fetchval('SELECT version FROM catalog.schema_version'), 2)
        before = await self.health()
        self.assertEqual(before.status_code, 503)
        self.assertIn('Справочник версии 2', before.json()['detail'])
        user_id = await self.add_user_data()
        tables = ['public.users', 'public.user_preferences', 'research.studies', 'decision_tree.training_data']
        user_data = await self.snapshot(tables)

        result = await self.initialize()
        self.assertEqual(result['status'], 'loaded')
        self.assertEqual(result['counts'], load_seed().counts)
        self.assertEqual(user_data, await self.snapshot(tables))
        after = await self.health()
        self.assertEqual(after.status_code, 200, after.text)
        self.assertEqual(after.json()['counts']['ingredients'], result['counts']['ingredients'])
        ingredient = await self.connection.fetchval("SELECT id FROM catalog.ingredients WHERE e_code='E202'")
        await self.connection.execute(
            "INSERT INTO public.user_ingredient_exclusions (user_id, ingredient_id) VALUES ($1, $2)", user_id, ingredient
        )
        await self.connection.execute(
            "UPDATE catalog.ingredients SET full_description='Manual edit must survive' WHERE id=$1", ingredient
        )
        tables += ['public.user_ingredient_exclusions', 'catalog.ingredients', 'catalog.ingredient_rules',
                   'catalog.ingredient_aliases', 'catalog.import_runs']
        populated = await self.snapshot(tables)
        self.assertEqual((await self.initialize())['status'], 'preserved')
        self.assertEqual((await self.initialize())['status'], 'preserved')
        self.assertEqual(populated, await self.snapshot(tables))
        self.assertEqual(await self.connection.fetchval('SELECT count(*) FROM catalog.import_runs'), 1)
        self.assertEqual((await self.health()).status_code, 200)

    async def test_concurrent_first_install_creates_one_import(self):
        with patch('app.db.initialize.settings', SimpleNamespace(catalog_url=self.url)):
            results = await asyncio.gather(
                initialize('127.0.0.1', True, bootstrap=True),
                initialize('127.0.0.1', True, bootstrap=True),
            )
        self.assertEqual(sorted(r['status'] for r in results), ['loaded', 'preserved'])
        self.assertEqual(await self.connection.fetchval('SELECT count(*) FROM catalog.import_runs'), 1)
        self.assertEqual((await self.health()).status_code, 200)

    async def test_insert_failure_rolls_back_partial_seed_and_can_retry(self):
        await self.initialize(bootstrap=False)
        await self.add_user_data()
        tables = ['public.users', 'public.user_preferences', 'research.studies', 'decision_tree.training_data']
        before = await self.snapshot(tables)

        def fail_alias(key, value):
            return None if key == 'alias' else sql_value(key, value)

        with patch('app.db.bootstrap_catalog.sql_value', fail_alias):
            with self.assertRaises(asyncpg.NotNullViolationError):
                await self.initialize()
        for table in ('ingredients', 'regulatory_sources', 'ingredient_aliases', 'import_runs'):
            self.assertEqual(await self.connection.fetchval(f'SELECT count(*) FROM catalog.{table}'), 0)
        self.assertEqual(before, await self.snapshot(tables))
        self.assertEqual((await self.health()).status_code, 503)
        self.assertEqual((await self.initialize())['status'], 'loaded')

    async def test_fresh_install_failure_rolls_back_schema_as_well_as_rows(self):
        with patch('app.db.bootstrap_catalog.sql_value', side_effect=RuntimeError('interrupted seed')):
            with self.assertRaises(RuntimeError):
                await self.initialize()
        self.assertIsNone(await self.connection.fetchval("SELECT to_regclass('catalog.ingredients')"))
        self.assertIsNone(await self.connection.fetchval("SELECT to_regclass('public.users')"))
        self.assertEqual((await self.initialize())['status'], 'loaded')

    async def test_unknown_schema_version_is_rejected_without_changing_data(self):
        await self.initialize(bootstrap=False)
        await self.add_user_data()
        await self.connection.execute('ALTER TABLE catalog.schema_version DROP CONSTRAINT schema_version_version_check')
        await self.connection.execute('UPDATE catalog.schema_version SET version=3')
        before = await self.snapshot(['public.users', 'catalog.schema_version'])
        with self.assertRaisesRegex(CatalogBootstrapError, 'Несовместимая версия'):
            await self.initialize()
        self.assertEqual(before, await self.snapshot(['public.users', 'catalog.schema_version']))
        self.assertEqual((await self.health()).status_code, 503)

    async def test_incomplete_inactive_catalog_is_not_destructively_repaired(self):
        await self.initialize(bootstrap=False)
        await self.add_user_data()
        await self.connection.execute(
            "INSERT INTO catalog.regulatory_sources (id, code, title) VALUES ($1, 'MANUAL', 'Keep source')", uuid4()
        )
        before = await self.snapshot(['public.users', 'catalog.regulatory_sources'])
        with self.assertRaisesRegex(CatalogBootstrapError, 'частично'):
            await self.initialize()
        self.assertEqual(before, await self.snapshot(['public.users', 'catalog.regulatory_sources']))
        self.assertEqual((await self.health()).status_code, 503)

    async def test_unversioned_populated_catalog_is_not_relabelled(self):
        await self.initialize()
        await self.connection.execute('DELETE FROM catalog.schema_version')
        before = await self.snapshot(['catalog.ingredients', 'catalog.import_runs'])
        with self.assertRaisesRegex(CatalogBootstrapError, 'нет версии схемы'):
            await self.initialize()
        self.assertEqual(before, await self.snapshot(['catalog.ingredients', 'catalog.import_runs']))
        self.assertEqual((await self.health()).status_code, 503)

    async def test_schema_only_v1_is_migrated_then_seeded(self):
        await self.initialize(bootstrap=False)
        await self.connection.execute('ALTER TABLE catalog.schema_version DROP CONSTRAINT schema_version_version_check')
        await self.connection.execute('UPDATE catalog.schema_version SET version=1')
        self.assertEqual((await self.initialize())['status'], 'loaded')
        self.assertEqual(await self.connection.fetchval('SELECT version FROM catalog.schema_version'), 2)
        self.assertEqual((await self.health()).status_code, 200)
