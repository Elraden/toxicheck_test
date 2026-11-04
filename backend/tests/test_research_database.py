"""Opt-in integration test on a NEW disposable localhost database. Never uses .env URLs."""
import base64
import json
import os
import secrets
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4
from zipfile import ZipFile

import asyncpg
import httpx
from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core.config import settings
from app.db.session import get_identity_session, get_annotation_session
from app.main import create_app
from app.schemas.ocr import OcrResult
from app.services.passwords import hasher


@unittest.skipUnless(os.environ.get('TOXICHECK_RESEARCH_LOCAL_TEST') == '1', 'Opt-in isolated localhost PostgreSQL test')
class ResearchDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_flow_access_control_and_export(self):
        # Fixed loopback and dedicated test port prevent accidental use of remote/project DBs.
        name = 'research_test_' + uuid4().hex
        root = await asyncpg.connect(host='127.0.0.1', port=55439, user='research_test', database='postgres')
        await root.execute(f'CREATE DATABASE {name}')
        engine = create_async_engine(f'postgresql+asyncpg://research_test@127.0.0.1:55439/{name}')
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as db:
                raw = await db.get_raw_connection()
                base = Path(__file__).resolve().parents[1] / 'db'
                for path in (base / 'schema_catalog.sql', base / 'migrations/20261029_identity.sql', base / 'migrations/20261030_research.sql'):
                    await raw.driver_connection.execute(path.read_text(encoding='utf-8'))
                # Reapply verifies idempotence.
                await raw.driver_connection.execute((base / 'migrations/20261030_research.sql').read_text(encoding='utf-8'))
                ingredient = uuid4()
                await db.execute(text("INSERT INTO catalog.ingredients(id,kind,e_code,canonical_name_ru) VALUES(:id,'additive','E202','Сорбат калия')"), {'id': ingredient})
                password = secrets.token_urlsafe(24)
                await db.execute(text("INSERT INTO public.users(login,role,account_kind,password_hash) VALUES('testadmin','admin','account',:hash)"), {'hash': hasher.hash(password)})
            app = create_app()
            async def identity_db():
                async with sessions() as db, db.begin():
                    yield db
            async def research_db():
                async with sessions() as db:
                    yield db
            app.dependency_overrides[get_identity_session] = identity_db
            app.dependency_overrides[get_annotation_session] = research_db
            with tempfile.TemporaryDirectory() as folder, patch.object(settings, 'research_storage_dir', folder), \
                 patch.object(settings, 'app_env', 'local'), \
                 patch('app.api.routes.auth.throttle', new_callable=AsyncMock), \
                 patch('app.api.routes.research.throttle', new_callable=AsyncMock):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url='http://test.local') as client, \
                           httpx.AsyncClient(transport=transport, base_url='http://test.local') as other:
                    common = {'Origin': settings.frontend_origins[0], 'X-ToxiCheck-Request': '1'}
                    state = (await client.post('/api/auth/guest', headers=common, json={})).json()
                    headers = {**common, 'X-CSRF-Token': state['csrf']}
                    state2 = (await other.post('/api/auth/guest', headers=common, json={})).json()
                    other_headers = {**common, 'X-CSRF-Token': state2['csrf']}
                    login = await client.post('/api/auth/admin/login', headers=headers, json={'login': 'testadmin', 'password': password})
                    self.assertEqual(login.status_code, 200, login.text)
                    admin_headers = {**common, 'X-CSRF-Token': login.json()['admin_csrf']}
                    study = await client.post('/api/admin/research/studies', headers=admin_headers,
                        json={'title': 'Пилот', 'instruction': 'Проверьте состав', 'scenario': 'comparison'})
                    self.assertEqual(study.status_code, 200, study.text)
                    study_id = study.json()['id']
                    payload = {'id': str(uuid4()), 'study_id': study_id, 'title': '=Тестовая этикетка', 'consent': True}
                    self.assertEqual((await client.post('/api/research/runs', headers=headers, json=payload)).status_code, 409)
                    await client.put(f'/api/admin/research/studies/{study_id}/state', headers=admin_headers, json={'is_open': True})
                    self.assertEqual((await client.post('/api/research/runs', headers=headers, json=payload)).status_code, 422)
                    await client.put('/api/preferences/me', headers=headers, json={'excludedIngredientIds': [str(ingredient)], 'revision': 0})
                    created = await client.post('/api/research/runs', headers=headers, json=payload)
                    self.assertEqual(created.status_code, 200, created.text)
                    run = created.json(); id = run['id']; prefix = f'/api/research/runs/{id}'
                    self.assertEqual((await client.post('/api/research/runs', headers=headers, json=payload)).json()['id'], id)
                    self.assertEqual((await other.get(prefix)).status_code, 404)
                    self.assertEqual((await other.get(f'/api/admin/research/runs/{id}')).status_code, 403)
                    self.assertEqual((await client.post(prefix + '/stage', json={'revision': 0, 'action': 'start_manual'})).status_code, 403)
                    # Snapshot does not follow subsequent profile changes.
                    await client.put('/api/preferences/me', headers=headers, json={'excludedIngredientIds': [], 'revision': 1})
                    self.assertEqual((await client.get(prefix)).json()['exclusions'][0]['e_code'], 'E202')
                    image = BytesIO(); Image.new('RGB', (100, 60), 'white').save(image, 'PNG')
                    photo_payload = {'id': str(uuid4()), 'image_base64': base64.b64encode(image.getvalue()).decode()}
                    self.assertEqual((await client.post(prefix + '/photos', headers=headers, json=photo_payload)).status_code, 409)
                    async def step(action, **extra):
                        nonlocal run
                        response = await client.post(prefix + '/stage', headers=headers, json={'revision': run['revision'], 'action': action, **extra})
                        self.assertEqual(response.status_code, 200, response.text)
                        run = response.json()
                    await step('start_manual')
                    self.assertEqual((await client.post(prefix + '/stage', headers=headers, json={'revision': run['revision'], 'action': 'start_app'})).status_code, 409)
                    await step('save_manual', answers={'E202': 'present'})
                    await step('finish_manual', answers={'E202': 'present'}, external_search_used=False)
                    self.assertIsNotNone(run['manual_duration_ms'])
                    await step('start_app')
                    photo_response = await client.post(prefix + '/photos', headers=headers, json=photo_payload)
                    self.assertEqual(photo_response.status_code, 200, photo_response.text)
                    self.assertEqual((await client.post(prefix + '/photos', headers=headers, json=photo_payload)).status_code, 200)
                    photo_url = prefix + '/photos/' + photo_payload['id']
                    self.assertEqual((await other.get(photo_url)).status_code, 404)
                    self.assertEqual((await client.get(photo_url)).content, image.getvalue())
                    attempt_payload = {'id': str(uuid4()), 'photo_id': photo_payload['id'], 'capture_source': 'file'}
                    with patch('app.api.routes.research.OcrService.recognize_image', new_callable=AsyncMock,
                               return_value=OcrResult(status='success', rawText='Состав: E202, вода', compositionText='E202, вода', processingTimeMs=10)) as ocr:
                        response = await client.post(prefix + '/attempts', headers=headers, json=attempt_payload)
                        self.assertEqual(response.status_code, 200, response.text)
                        attempt = response.json()
                        self.assertEqual(attempt['status'], 'success', attempt)
                        self.assertEqual(attempt['result']['matched_restrictions'], ['E202'])
                        self.assertTrue(attempt['catalog_version'].startswith('sha256:'))
                        again = await client.post(prefix + '/attempts', headers=headers, json=attempt_payload)
                        self.assertEqual(again.json()['result'], attempt['result']); ocr.assert_awaited_once()
                    self.assertEqual((await other.post(prefix + '/attempts', headers=other_headers, json=attempt_payload)).status_code, 404)
                    missing_display = await client.post(prefix + '/stage', headers=headers, json={'revision': run['revision'], 'action': 'finish_app', 'answers': {'E202': 'present'}, 'selected_attempt_id': attempt['id']})
                    self.assertEqual(missing_display.status_code, 422)
                    e2e = attempt['server_processing_ms'] + 100
                    display_url = prefix + '/attempts/' + attempt['id'] + '/displayed'
                    self.assertEqual((await client.post(display_url, headers=headers, json={'app_e2e_ms': e2e})).status_code, 200)
                    self.assertEqual((await client.post(display_url, headers=headers, json={'app_e2e_ms': e2e+1})).status_code, 409)
                    await step('finish_app', answers={'E202': 'present'}, selected_attempt_id=attempt['id'])
                    await step('annotate', user_label_text='E202, вода')
                    self.assertEqual(run['stage'], 'completed')
                    admin_url = f'/api/admin/research/runs/{id}'
                    hidden = (await client.get(admin_url)).json()
                    self.assertNotIn('result', hidden['attempts'][0])
                    self.assertNotIn('manual_answers', hidden)
                    validation = {'revision': 0, 'status': 'confirmed', 'verified_label_text': 'E202, вода', 'ground_truth_e_codes': ['Е 202'], 'comment': 'Проверено по фото'}
                    self.assertEqual((await client.put(admin_url + '/validation', headers=headers, json=validation)).status_code, 403)
                    saved = await client.put(admin_url + '/validation', headers=admin_headers, json=validation)
                    self.assertEqual(saved.status_code, 200, saved.text)
                    self.assertEqual(saved.json()['validation']['ground_truth_e_codes'], ['E202'])
                    self.assertEqual((await client.put(admin_url + '/validation', headers=admin_headers, json=validation)).status_code, 409)
                    export = await client.get('/api/admin/research/export?include_photos=true')
                    self.assertEqual(export.status_code, 200, export.text if export.status_code != 200 else '')
                    with ZipFile(BytesIO(export.content)) as archive:
                        self.assertIn('research.xlsx', archive.namelist())
                        data = json.loads(archive.read('data.json'))
                        self.assertEqual(data[0]['validation']['status'], 'confirmed')
                        self.assertIn("'=Тестовая", archive.read('runs.csv').decode('utf-8-sig'))
                        manifest = json.loads(archive.read('photos_manifest.json'))
                        self.assertTrue(manifest[0]['included'])
                        self.assertEqual(archive.read(manifest[0]['file']), image.getvalue())
                    # Separate OCR-only study: errors and interrupted attempts remain available.
                    second_study = (await client.post('/api/admin/research/studies', headers=admin_headers,
                        json={'title': 'OCR', 'instruction': 'Проверьте фото', 'scenario': 'ocr_quality'})).json()['id']
                    await client.put(f'/api/admin/research/studies/{second_study}/state', headers=admin_headers, json={'is_open': True})
                    response = await client.post('/api/research/runs', headers=headers, json={**payload, 'id': str(uuid4()), 'study_id': second_study})
                    self.assertEqual(response.status_code, 200, response.text)
                    run = response.json(); id = run['id']; prefix = f'/api/research/runs/{id}'
                    self.assertEqual(run['stage'], 'app_ready')
                    self.assertEqual(run['exclusions'], [])
                    await step('start_app')
                    photo_payload['id'] = str(uuid4())
                    await client.post(prefix + '/photos', headers=headers, json=photo_payload)
                    failure_id = str(uuid4())
                    with patch('app.api.routes.research.OcrService.recognize_image', new_callable=AsyncMock, side_effect=TimeoutError()):
                        failure = await client.post(prefix + '/attempts', headers=headers, json={'id': failure_id, 'photo_id': photo_payload['id']})
                    self.assertEqual(failure.json()['status'], 'error')
                    recovered = await client.post(prefix + '/attempts/' + failure_id + '/recover', headers=headers, json={})
                    self.assertEqual(recovered.status_code, 200, recovered.text)
                    self.assertIsNone(recovered.json()['attempts'][0]['app_e2e_ms'])
                    await step('finish_app', answers={}, selected_attempt_id=failure_id)
                    await step('annotate', skip_label=True)
                    self.assertEqual(run['labeling_status'], 'skipped')
                    filtered = await client.get('/api/admin/research/runs?errors=true&limit=1')
                    self.assertEqual(filtered.json()['total'], 1)
                    self.assertEqual(filtered.json()['items'][0]['id'], id)
        finally:
            await engine.dispose()
            await root.execute(f'DROP DATABASE {name} WITH (FORCE)')
            await root.close()
