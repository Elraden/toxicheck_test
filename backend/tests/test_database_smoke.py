"""Opt-in read-only checks against a populated database, including Railway."""

import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


@unittest.skipUnless(os.environ.get("TOXICHECK_DB_SMOKE") == "1", "Requires a populated catalog v2 database (read-only)")
class DatabaseSmokeTests(unittest.TestCase):
    def test_catalog_analysis_and_personal_exclusions(self):
        from app.main import app

        with TestClient(app) as client:
            health = client.get("/api/health/db").json()
            self.assertEqual(health["database"], "available")
            self.assertEqual(health["schema"], "catalog")
            self.assertEqual(health["schema_version"], 2)
            self.assertGreater(health["counts"]["ingredients"], 1000)
            response = client.get("/api/preferences/catalog")
            self.assertEqual(response.status_code, 200, response.text[:500])
            catalog = response.json()
            self.assertEqual(len(catalog), health["counts"]["ingredients"])
            self.assertTrue(any("soy-lecithin" in item["legacy_ids"] for item in catalog))

            response = client.get("/api/ingredients?limit=2")
            self.assertEqual(response.status_code, 200, response.text[:500])
            self.assertEqual(response.json()["total"], len(catalog))
            self.assertEqual(len(response.json()["items"]), 2)
            second_page = client.get("/api/ingredients?limit=2&offset=2").json()
            self.assertFalse({i["id"] for i in response.json()["items"]} & {i["id"] for i in second_page["items"]})
            for query in ("E202", "Е-202", "potassium sorbate"):
                response = client.get("/api/ingredients", params={"q": query})
                self.assertEqual(response.status_code, 200, response.text[:500])
                sorbate = next(i for i in response.json()["items"] if i["code"] == "E202")
                response = client.get("/api/ingredients/" + sorbate["id"])
                self.assertEqual(response.status_code, 200, response.text[:500])
                detail = response.json()
                self.assertTrue(detail["description"])
                self.assertTrue(detail["functions"])
                self.assertTrue(detail["origins"])
                self.assertTrue(detail["rules"])
                self.assertEqual(detail["severity"], sorbate["severity"])
            response = client.get("/api/ingredients", params={"q": "вода", "kind": "foods"}).json()
            water_item = next(i for i in response["items"] if i["name"] == "Вода")
            self.assertEqual(water_item["severity"], "unknown")
            self.assertEqual(client.get("/api/ingredients/" + water_item["id"]).json()["rules"], [])

            response = client.post("/api/analysis", json={"ingredientsText": "E102, E123, E330, вода, фруктоза, неизвестный_ингредиент_xyz"})
            self.assertEqual(response.status_code, 200, response.text[:500])
            result = response.json()
            self.assertEqual(result["verdict"]["level"], "attention")
            self.assertIn("неизвестный_ингредиент_xyz", result["unmatched"])
            self.assertEqual({item["code"] for item in result["matched"]}, {"E102", "E123", "E330", None})
            self.assertTrue(all(rule["citation"] and rule["source_url"]
                for item in result["matched"] for rule in item["rules"]))
            self.assertTrue(all(rule["conditions"]["evidence"]
                for item in result["matched"] for rule in item["rules"]))
            for item in result["matched"]:
                if item["code"] is None:
                    self.assertEqual(item["severity"], "unknown")
                    self.assertEqual(item["rules"], [])

            resolved = client.post("/api/ingredients/resolve", json={
                "ingredientsText": "амарант, крахмал, водда, E450(viii), E450(xv)"
            })
            self.assertEqual(resolved.status_code, 200, resolved.text[:500])
            self.assertEqual({i["code"] for i in resolved.json()["matched"]}, {"E450(viii)"})
            self.assertEqual(resolved.json()["unmatched"], ["амарант", "крахмал", "водда", "E450(xv)"])

            result = client.post("/api/analysis", json={
                "ingredientsText": "E322", "preferences": {"excludedIngredientIds": ["soy-lecithin"]}
            }).json()
            self.assertEqual(result["verdict"]["level"], "risky_for_user")
            legacy_item = next(item for item in catalog if item["code"] == "E330")
            old_uuid = next(identifier for identifier in legacy_item["legacy_ids"] if len(identifier) == 36)
            result = client.post("/api/analysis", json={
                "ingredientsText": "E330", "preferences": {"excludedIngredientIds": [old_uuid]}
            }).json()
            self.assertEqual(result["verdict"]["level"], "risky_for_user")
            water = next(item for item in catalog if item["name"] == "Вода")
            result = client.post("/api/analysis", json={
                "ingredientsText": "вода", "preferences": {"excludedIngredientIds": [water["id"]]}
            }).json()
            self.assertEqual(result["verdict"]["level"], "risky_for_user")
            result = client.post("/api/analysis", json={"ingredientsText": "неизвестный_ингредиент_xyz"}).json()
            self.assertEqual(result["verdict"]["level"], "unknown")

            from app.schemas.ocr import OcrResult
            with patch("app.services.scan_service.OpenFoodFactsClient.get_product", new_callable=AsyncMock) as product:
                product.return_value = {"product": {"product_name": "Smoke test", "ingredients_text": "вода, фруктоза"}}
                response = client.post("/api/scan/barcode", json={"barcode": "4600000000000"})
                self.assertEqual(response.status_code, 200, response.text[:500])
                self.assertEqual(len(response.json()["analysis"]["matched"]), 2)
            with patch("app.api.routes.scan.OcrService.recognize_image", new_callable=AsyncMock) as ocr:
                ocr.return_value = OcrResult(status="success", composition_text="вода, фруктоза")
                response = client.post("/api/scan/composition", json={"imageBase64": "test", "captureSource": "native"})
                self.assertEqual(response.status_code, 200, response.text[:500])
                analysis = client.post("/api/analysis", json={"ingredientsText": response.json()["ingredientsText"]})
                self.assertEqual(len(analysis.json()["matched"]), 2)
                self.assertEqual(analysis.json()["verdict"]["level"], "unknown")
