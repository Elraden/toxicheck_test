"""Opt-in read-only checks against a populated database, including Railway."""

import os
import unittest

from fastapi.testclient import TestClient


@unittest.skipUnless(os.environ.get("TOXICHECK_DB_SMOKE") == "1", "Requires a populated DATABASE_URL")
class DatabaseSmokeTests(unittest.TestCase):
    def test_catalog_analysis_and_personal_exclusions(self):
        from app.main import app

        with TestClient(app) as client:
            health = client.get("/api/health/db").json()
            self.assertEqual(health["database"], "available")
            self.assertGreater(health["counts"]["ingredients"], 3000)
            response = client.get("/api/preferences/catalog")
            self.assertEqual(response.status_code, 200, response.text[:500])
            catalog = response.json()
            self.assertEqual(len(catalog), health["counts"]["ingredients"])
            self.assertTrue(any("soy-lecithin" in item["legacy_ids"] for item in catalog))

            response = client.post("/api/analysis", json={"ingredientsText": "E102, E121, лимонная кислота, неизвестный_ингредиент_xyz"})
            self.assertEqual(response.status_code, 200, response.text[:500])
            result = response.json()
            self.assertEqual(result["verdict"]["level"], "attention")
            self.assertIn("неизвестный_ингредиент_xyz", result["unmatched"])
            self.assertEqual({item["code"] for item in result["matched"]}, {"E102", "E121", "E330"})
            self.assertTrue(all(rule["citation"] and rule["source_url"]
                for item in result["matched"] for rule in item["rules"]))

            result = client.post("/api/analysis", json={
                "ingredientsText": "E322", "preferences": {"excludedIngredientIds": ["soy-lecithin"]}
            }).json()
            self.assertEqual(result["verdict"]["level"], "risky_for_user")
            result = client.post("/api/analysis", json={"ingredientsText": "неизвестный_ингредиент_xyz"}).json()
            self.assertEqual(result["verdict"]["level"], "unknown")
