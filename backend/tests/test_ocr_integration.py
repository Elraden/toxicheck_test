import base64
import unittest
import importlib.util
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.db.session import get_catalog_session
from app.main import create_app
from app.schemas.ocr import OcrResult
from app.services.ingredient_resolver import IngredientResolver
from app.services.normalization import normalize_e_code, split_ingredients_text
from app.services.ocr_service import OcrService, _embedded_lock


class OcrAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_adaptive_pipeline_preserves_single_unknown_word(self):
        engine = MagicMock()
        engine.CONFIDENCE_RETAKE_THRESHOLD = 0.45
        engine.adaptive_enhance.return_value = (object(), {"upscale_factor": 2})
        engine.run_ocr.return_value = ("Состав: фруктоза", 0.9)
        engine.extract_composition_block.return_value = "фруктоза"
        engine.extract_allergens_block.return_value = ""
        with patch("app.services.ocr_service._load_embedded_ocr_module", return_value=engine):
            result = await OcrService()._recognize_embedded("image", "native_camera")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.composition_text, "фруктоза")
        engine.adaptive_enhance.assert_called_once()
        engine.resize_if_needed.assert_not_called()

    async def test_low_confidence_returns_text_but_no_composition(self):
        engine = MagicMock()
        engine.CONFIDENCE_RETAKE_THRESHOLD = 0.45
        engine.adaptive_enhance.return_value = (object(), {})
        engine.run_ocr.return_value = ("E216?", 0.2)
        with patch("app.services.ocr_service._load_embedded_ocr_module", return_value=engine):
            result = await OcrService()._recognize_embedded("image", "browser_camera")
        self.assertEqual(result.status, "needs_retake")
        self.assertEqual(result.raw_text, "E216?")
        self.assertIsNone(result.composition_text)
        engine.extract_composition_block.assert_not_called()

    async def test_busy_worker_does_not_start_another_ocr(self):
        _embedded_lock.acquire()
        try:
            result = await OcrService()._recognize_embedded("image", "native_camera")
            self.assertEqual(result.status, "ocr_unavailable")
        finally:
            _embedded_lock.release()

    async def test_failure_releases_worker_without_exposing_exception(self):
        with patch.object(OcrService, "_recognize_embedded_sync", side_effect=RuntimeError("secret")):
            result = await OcrService()._recognize_embedded("image", "native_camera")
        self.assertNotIn("secret", result.message)
        self.assertFalse(_embedded_lock.locked())

    async def test_invalid_base64_never_starts_ocr(self):
        with patch.object(OcrService, "_recognize_embedded", new_callable=AsyncMock) as run:
            result = await OcrService().recognize_image("%%%")
        self.assertEqual(result.status, "error")
        run.assert_not_awaited()


class OcrCatalogRouteTests(unittest.TestCase):
    def test_both_cameras_use_existing_catalog_resolver(self):
        app = create_app()
        session = AsyncMock()

        async def catalog():
            yield session

        app.dependency_overrides[get_catalog_session] = catalog
        row = dict(ingredient_id="ingredient-uuid", name="Фруктоза", code=None,
                   matched_by="alias", match_score=1.0)
        with TestClient(app) as client, \
             patch("app.api.routes.scan.OcrService.recognize_image", new_callable=AsyncMock) as ocr, \
             patch.object(IngredientResolver, "_match_codes", new_callable=AsyncMock, return_value={}), \
             patch.object(IngredientResolver, "_match_aliases", new_callable=AsyncMock,
                          return_value={"фруктоза": row}) as aliases, \
             patch.object(IngredientResolver, "_load_rules", new_callable=AsyncMock, return_value={}):
            ocr.return_value = OcrResult(status="success", compositionText="фруктоза, неизвестный")
            for source in ("browser_camera", "native_camera"):
                response = client.post("/api/scan/composition", json={
                    "imageBase64": base64.b64encode(b"image").decode(), "captureSource": source})
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()
                self.assertEqual(data["captureSource"], source)
                self.assertEqual(data["analysis"]["matched"][0]["ingredient_id"], "ingredient-uuid")
                self.assertEqual(data["analysis"]["matched"][0]["severity"], "unknown")
                self.assertEqual(data["analysis"]["unmatched"], ["неизвестный"])
                ocr.assert_awaited_with(base64.b64encode(b"image").decode(), source=source)
            self.assertEqual(aliases.await_count, 2)
            ocr.return_value = OcrResult(status="needs_retake", rawText="noise")
            response = client.post("/api/scan/composition", json={"imageBase64": "aW1hZ2U="})
            self.assertIsNone(response.json()["analysis"])
            self.assertEqual(aliases.await_count, 2)


class OcrTokenTests(unittest.IsolatedAsyncioTestCase):
    def test_nested_ingredients_and_subtypes(self):
        parts = split_ingredients_text("шоколад (сахар, какао, эмульгатор E322(i)), соль")
        self.assertEqual(parts, ["шоколад", "сахар", "какао", "эмульгатор E322(i)", "соль"])
        self.assertEqual(normalize_e_code("Е924а"), "E924a")
        self.assertNotEqual(normalize_e_code("E450(i)"), normalize_e_code("E450(ii)"))

    def test_invalid_subtype_is_not_repaired_to_parent(self):
        for value in ("E450(ixyz)", "E450(viii"):
            self.assertEqual(split_ingredients_text(value), [value])
            self.assertIsNone(normalize_e_code(value))

    async def test_negative_claim_does_not_match_banned_code(self):
        resolver = IngredientResolver(AsyncMock())
        resolver._match_codes = AsyncMock(return_value={})
        resolver._match_aliases = AsyncMock(return_value={})
        resolver._load_rules = AsyncMock(return_value={})
        result = await resolver.resolve("без E216, E211")
        resolver._match_codes.assert_awaited_once_with(["e211"])
        self.assertEqual(result.matched, [])
        self.assertEqual(result.unmatched, ["без E216", "E211"])


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in
                        ("cv2", "numpy", "pytesseract", "Levenshtein")), "OCR extras not installed")
class OcrEngineTests(unittest.TestCase):
    def test_facility_warning_is_separate_from_ingredients(self):
        from app.services import ocr_engine as engine

        raw = "Состав: вода, сахар. Произведено на предприятии, где используется арахис."
        self.assertEqual(engine.extract_composition_block(raw), "вода, сахар")
        self.assertIn("арахис", engine.extract_allergens_block(raw))
        self.assertEqual(engine.extract_composition_block(raw.removeprefix("Состав: ")), "вода, сахар")

    def test_float_confidence_and_tesseract_timeout(self):
        from app.services import ocr_engine as engine

        data = {"text": ["", "вода", "сахар"], "conf": [-1, "90.5", "80.5"],
                "block_num": [0, 1, 1], "par_num": [0, 1, 1], "line_num": [0, 1, 1]}
        with patch.object(engine.pytesseract, "image_to_data", return_value=data) as ocr:
            text, confidence = engine.run_ocr(object())
        self.assertEqual(text, "вода сахар")
        self.assertEqual(confidence, 0.855)
        self.assertEqual(ocr.call_args.kwargs["timeout"], 25)

    def test_adaptive_output_size_is_bounded(self):
        import numpy as np
        from app.services import ocr_preprocessing as prep

        image = np.zeros((500, 2000, 3), dtype=np.uint8)
        with patch.object(prep, "estimate_median_word_height", return_value=5):
            processed, metrics = prep.adaptive_enhance(image)
        self.assertLessEqual(max(processed.shape), 3000)
        self.assertEqual(metrics["upscale_factor"], 1.5)

    def test_image_decoding_rejects_non_image(self):
        from app.services import ocr_engine as engine

        self.assertIsNone(engine.decode_base64_image(base64.b64encode(b"not an image").decode()))

    def test_pixel_limit_checked_before_opencv(self):
        from app.services import ocr_engine as engine

        header = MagicMock(width=10000, height=10000, format="JPEG")
        with patch.object(engine.Image, "open") as image, patch.object(engine.cv2, "imdecode") as decode:
            image.return_value.__enter__.return_value = header
            self.assertIsNone(engine.decode_base64_image("aW1hZ2U="))
        decode.assert_not_called()


if __name__ == "__main__":
    unittest.main()
