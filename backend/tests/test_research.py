import base64
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from zipfile import ZipFile

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import settings
from app.db.session import get_identity_session
from app.main import create_app
from app.schemas.research import StageInput
from app.services.research_service import transition, normalize_codes
from app.services.research_storage import decode_photo, store_photo, photo_path
from app.services.research_export import cell, write_export


class ResearchRulesTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.run = {'revision': 2, 'stage': 'manual_active', 'exclusions': [{'e_code': 'E202'}],
                    'manual_started_at': self.now - timedelta(seconds=42)}

    def test_manual_finish_requires_all_answers_and_search_response(self):
        for data in ({'answers': {}}, {'answers': {'E202': 'present'}}, {'answers': {'E211': 'absent'}, 'external_search_used': False}):
            with self.assertRaises(HTTPException):
                transition(self.run, StageInput(revision=2, action='finish_manual', **data), self.now)
        result = transition(self.run, StageInput(revision=2, action='finish_manual', answers={'E202': 'unknown'}, external_search_used=False), self.now)
        self.assertEqual(result['stage'], 'app_ready')
        self.assertEqual(result['manual_duration_ms'], 42000)

    def test_no_skipping_stages_or_stale_overwrite(self):
        for action in ('start_app', 'annotate', 'finish_app'):
            with self.assertRaises(HTTPException) as error:
                transition(self.run, StageInput(revision=2, action=action), self.now)
            self.assertEqual(error.exception.status_code, 409)
        with self.assertRaises(HTTPException):
            transition(self.run, StageInput(revision=1, action='save_manual'), self.now)

    def test_annotation_outside_timing_and_optional(self):
        run = {**self.run, 'stage': 'labeling'}
        with self.assertRaises(HTTPException):
            transition(run, StageInput(revision=2, action='annotate'), self.now)
        result = transition(run, StageInput(revision=2, action='annotate', skip_label=True), self.now)
        self.assertEqual(result['labeling_status'], 'skipped')
        self.assertNotIn('manual_duration_ms', result)

    def test_abort_never_reopens_completed_session(self):
        self.assertEqual(transition(self.run, StageInput(revision=2, action='abort'), self.now)['stage'], 'aborted')
        with self.assertRaises(HTTPException):
            transition({**self.run, 'stage': 'completed'}, StageInput(revision=2, action='abort'), self.now)

    def test_codes_and_invalid_codes(self):
        self.assertEqual(normalize_codes(['Е 202', 'E-202', 'e924a']), ['E202', 'E924A'])
        with self.assertRaises(HTTPException):
            normalize_codes(['сорбат калия'])

    def test_photo_validation_and_immutable_ids(self):
        with self.assertRaises(HTTPException):
            decode_photo(base64.b64encode(b'<svg></svg>').decode())
        output = BytesIO(); Image.new('RGB', (10, 10), 'white').save(output, 'PNG')
        data = output.getvalue()
        self.assertEqual(decode_photo(base64.b64encode(data).decode()), (data, 'image/png'))
        with tempfile.TemporaryDirectory() as folder, patch.object(settings, 'research_storage_dir', folder):
            id = uuid4(); store_photo(id, data); store_photo(id, data)
            with self.assertRaises(HTTPException):
                store_photo(id, b'other')
            with self.assertRaises(ValueError):
                photo_path('../../secret')

    def test_export_formula_safety_and_empty_workbook(self):
        self.assertEqual(cell(' =HYPERLINK("bad")'), "' =HYPERLINK(\"bad\")")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'export.zip'
            write_export(path, [], [], False, True)
            with ZipFile(path) as archive:
                self.assertIn('research.xlsx', archive.namelist())
                self.assertIn('photos_manifest.json', archive.namelist())
            record = {'id': 'test', 'user_label_text': 'строка\x0bсостав', 'photos': [], 'attempts': [], 'audit': [], 'validation': None}
            write_export(path, [record], [], False, True)
            with ZipFile(path) as archive:
                self.assertIn(b'\\u000b', archive.read('data.json'))

    def test_research_api_is_not_public(self):
        app = create_app()
        async def fake_db():
            yield AsyncMock()
        app.dependency_overrides[get_identity_session] = fake_db
        with TestClient(app) as client:
            for path in ('/api/research/studies', '/api/research/runs', '/api/research/runs/' + str(uuid4())):
                response = client.get(path)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers['cache-control'], 'no-store')
            for path in ('/api/admin/research/studies', '/api/admin/research/runs', '/api/admin/research/export'):
                self.assertEqual(client.get(path).status_code, 403)
