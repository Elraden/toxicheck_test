"""Opt-in real PostgreSQL/API checks. Schema and synthetic identities are rolled back."""
import os
from pathlib import Path
import secrets
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.identity_dependencies import cookie_name
from app.core.config import settings
from app.db.session import get_annotation_session, get_identity_session
from app.main import create_app
from app.services.passwords import hasher


@unittest.skipUnless(os.environ.get("TOXICHECK_IDENTITY_DB_TEST") == "1", "Opt-in rollback-only identity check")
class IdentityDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_guest_isolation_admin_revoke_and_preference_import(self):
        self.assertEqual(make_url(settings.catalog_url).host, os.environ.get("TOXICHECK_DB_EXPECTED_HOST"))
        engine = create_async_engine(settings.catalog_url, connect_args={"timeout": 15, "command_timeout": 30})
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    raw = await connection.get_raw_connection()
                    sql = (Path(__file__).resolve().parents[1] / "db/migrations/20261029_identity.sql").read_text(encoding="utf-8")
                    await raw.driver_connection.execute(sql)
                    app = create_app()
                    async def identity_db():
                        async with AsyncSession(bind=connection, join_transaction_mode="create_savepoint") as session, session.begin():
                            yield session
                    async def annotation_db():
                        async with AsyncSession(bind=connection, join_transaction_mode="create_savepoint") as session:
                            yield session
                    app.dependency_overrides[get_identity_session] = identity_db
                    app.dependency_overrides[get_annotation_session] = annotation_db
                    code = str(await connection.scalar(text("SELECT id FROM catalog.ingredients WHERE e_code='E202' AND is_active")))
                    login = "test-" + uuid4().hex
                    password = secrets.token_urlsafe(24)
                    admin_id = await connection.scalar(text("""
                        INSERT INTO public.users(login, role, account_kind, password_hash)
                        VALUES (:login, 'admin', 'account', :hash) RETURNING id
                    """), {"login": login, "hash": hasher.hash(password)})
                    with patch.object(settings, "app_env", "production"), patch("app.api.routes.auth.throttle", new_callable=AsyncMock):
                        transport = httpx.ASGITransport(app=app)
                        async with httpx.AsyncClient(transport=transport, base_url="https://test.local") as client, \
                                   httpx.AsyncClient(transport=transport, base_url="https://test.local") as other:
                            common = {"Origin": settings.frontend_origins[0], "X-ToxiCheck-Request": "1"}
                            self.assertEqual((await client.get('/api/admin/me')).status_code, 403)
                            first = await client.post('/api/auth/guest', headers=common, json={})
                            self.assertEqual(first.status_code, 200, first.text)
                            state = first.json()
                            user_id = state['user']['id']
                            guest_cookie = client.cookies.get(cookie_name('guest'))
                            headers = {**common, 'X-CSRF-Token': state['csrf']}
                            self.assertEqual((await client.post('/api/auth/guest', headers=common, json={})).json()['user']['id'], user_id)
                            second = (await other.post('/api/auth/guest', headers=common, json={})).json()
                            self.assertNotEqual(second['user']['id'], user_id)
                            self.assertEqual((await other.get('/api/preferences/me')).json()['excludedIngredientIds'], [])
                            response = await client.post('/api/preferences/me/import', headers=headers,
                                json={'excludedIngredientIds': [code, 'unknown-legacy-id']})
                            self.assertEqual(response.status_code, 200, response.text)
                            prefs = response.json()
                            self.assertEqual(prefs['excludedIngredientIds'], [code])
                            self.assertEqual(prefs['unresolvedIds'], ['unknown-legacy-id'])
                            self.assertEqual((await other.get('/api/preferences/me')).json()['excludedIngredientIds'], [])
                            self.assertEqual((await client.put('/api/preferences/me', headers=common,
                                json={'excludedIngredientIds': [], 'revision': prefs['revision']})).status_code, 403)
                            response = await client.put('/api/preferences/me', headers=headers,
                                json={'excludedIngredientIds': [], 'revision': prefs['revision']})
                            self.assertEqual(response.status_code, 200, response.text)
                            self.assertEqual((await client.put('/api/preferences/me', headers=headers,
                                json={'excludedIngredientIds': [code], 'revision': prefs['revision']})).status_code, 409)
                            repeated = await client.post('/api/preferences/me/import', headers=headers, json={'excludedIngredientIds': [code]})
                            self.assertEqual(repeated.json()['excludedIngredientIds'], [], 'Import must not resurrect a deleted exclusion')

                            correction = {'requestId': str(uuid4()), 'ingredientsText': 'test-ocr-word', 'corrections': [
                                {'id': str(uuid4()), 'tokenIndex': 0, 'tokenRaw': 'test-ocr-word', 'correctedText': 'E202'}]}
                            response = await client.post('/api/scan/corrections', headers=headers, json=correction)
                            self.assertEqual(response.status_code, 200, response.text)
                            saved_owner = await connection.scalar(text('SELECT user_id FROM decision_tree.user_corrections WHERE id=:id'),
                                                                 {'id': correction['corrections'][0]['id']})
                            self.assertEqual(str(saved_owner), user_id)
                            response = await other.post('/api/scan/corrections', headers={**common, 'X-CSRF-Token': second['csrf']}, json=correction)
                            self.assertEqual(response.status_code, 409)

                            response = await client.post('/api/auth/admin/login', headers=headers, json={'login': login, 'password': password})
                            self.assertEqual(response.status_code, 200, response.text)
                            self.assertEqual(client.cookies.get(cookie_name('guest')), guest_cookie)
                            admin_cookie = client.cookies.get(cookie_name('admin'))
                            admin_headers = {**common, 'X-CSRF-Token': response.json()['admin_csrf']}
                            self.assertEqual((await client.get('/api/admin/me')).status_code, 200)
                            self.assertEqual((await other.get('/api/admin/me')).status_code, 403)
                            response = await client.post('/api/auth/admin/logout', headers=admin_headers, json={})
                            self.assertEqual(response.status_code, 200, response.text)
                            self.assertEqual(response.json()['user']['id'], user_id)
                            self.assertIsNone(response.json()['admin'])
                            other.cookies.set(cookie_name('admin'), admin_cookie)
                            self.assertEqual((await other.get('/api/admin/me')).status_code, 403, 'Revoked cookie must remain invalid')

                            await client.post('/api/auth/admin/login', headers=headers, json={'login': login, 'password': password})
                            await connection.execute(text("UPDATE public.users SET role='user' WHERE id=:id"), {'id': admin_id})
                            self.assertEqual((await client.get('/api/admin/me')).status_code, 403)
                            await connection.execute(text("UPDATE public.users SET role='admin' WHERE id=:id"), {'id': admin_id})
                            await connection.execute(text("UPDATE public.user_sessions SET last_seen_at=now()-interval '2 hours' WHERE user_id=:id"), {'id': admin_id})
                            self.assertEqual((await client.get('/api/admin/me')).status_code, 403)
                            await connection.execute(text("UPDATE public.user_sessions SET expires_at=now()-interval '1 second' WHERE user_id=CAST(:id AS uuid)"), {'id': user_id})
                            self.assertEqual((await client.get('/api/preferences/me')).status_code, 401)
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()
