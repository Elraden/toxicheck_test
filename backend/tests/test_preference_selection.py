import unittest
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from app.db.session import get_catalog_session
from app.main import create_app


class SelectedPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.session = AsyncMock()
        rows = MagicMock()
        rows.mappings.return_value.all.return_value = [{
            "id": "ingredient-id", "name": "Сорбат калия", "code": "E202",
            "category": "", "description": "", "legacy_ids": ["old-id"],
        }]
        self.session.execute.return_value = rows
        self.app = create_app()
        async def session():
            yield self.session
        self.app.dependency_overrides[get_catalog_session] = session

    def test_selected_ids_are_bound_and_legacy_ids_supported(self):
        with TestClient(self.app) as client:
            result = client.get("/api/preferences/catalog", params=[("ids", "old-id"), ("ids", "water")])
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()[0]["legacy_ids"], ["old-id"])
        sql, params = self.session.execute.await_args.args
        self.assertEqual(params, {"ids": ["old-id", "water"]})
        self.assertNotIn("old-id", str(sql))
        for table in ("ingredient_aliases", "ingredient_matches", "ingredient_alias_review"):
            self.assertIn(table, str(sql))

    def test_selection_batches_are_bounded(self):
        with TestClient(self.app) as client:
            result = client.get("/api/preferences/catalog", params=[("ids", str(i)) for i in range(101)])
        self.assertEqual(result.status_code, 422)
        self.session.execute.assert_not_awaited()

    def test_legacy_catalog_contract_stays_compatible(self):
        with TestClient(self.app) as client:
            result = client.get("/api/preferences/catalog")
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(self.session.execute.await_args.args[1]["ids"])
