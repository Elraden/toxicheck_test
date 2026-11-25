"""Regression tests for the November ML adapter; all writes use disposable local DBs."""
import base64
import importlib.util
import os
import shutil
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import asyncpg
import httpx
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.api.identity_dependencies import require_admin, admin_write
from app.db.initialize import SCHEMAS, DB_DIR
from app.db.session import get_annotation_session, get_catalog_session, get_identity_session
from app.main import create_app
from app.schemas.analysis import AnalysisPreferences
from app.schemas.ocr import OcrResult, OCR_VERSION
from app.services.ocr_catalog import (
    OcrIngredientResolver, find_hazards, rank_candidates, enqueue_reviews, process_ocr_result,
)
from app.services.ocr_matching import match_score
from app.services.ocr_service import OcrService

OCR_EXTRAS = all(importlib.util.find_spec(n) for n in ("cv2", "pytesseract", "Levenshtein"))


@unittest.skipUnless(OCR_EXTRAS, "OCR extras not installed")
class SectionTests(unittest.TestCase):
    def test_tail_sections_do_not_become_ingredients(self):
        from app.services.ocr_engine import extract_composition_block, extract_allergens_block
        for suffix in (
            "Пищевая ценноcть: 100 ккал", "Срок годности: 2 месяца",
            "Масса нетто: 100 г", "Белки: 5,2 г Жиры: 3 г",
            "Упаковано на предприятии, где используется арахис",
            "Аллергены: арахис", "Следы арахиса возможны",
            "Наименование и адрес изготовителя: Москва",
        ):
            with self.subTest(suffix=suffix):
                raw = "Состав: вода, фруктоза. " + suffix
                self.assertEqual(extract_composition_block(raw), "вода, фруктоза")
        raw = "Состав: вода. Может содержать следы арахиса. Пищевая ценность: 100 ккал"
        self.assertIn("арахиса", extract_allergens_block(raw))
        self.assertNotIn("100", extract_allergens_block(raw))

    def test_food_words_are_not_section_markers(self):
        from app.services.ocr_engine import extract_composition_block
        for value in (
            "сливы, сок малины, сахар", "белок яичный, жир кондитерский, соль",
            "ароматизатор пищевой, добавка пищевая, вода",
            "сливки, соль, вода", "масло",
        ):
            with self.subTest(value=value):
                self.assertEqual(extract_composition_block("Состав: " + value), value)
                self.assertEqual(extract_composition_block("Состав: " + value + ". Пищевая ценность: 50 ккал"), value)


class MatchingTests(unittest.TestCase):
    def test_score_and_e_code_subtype_guards(self):
        self.assertEqual(match_score("фруктоза", "фруктоза"), 1)
        self.assertGreater(match_score("фруктоаз", "фруктоза"), .55)
        for a, b in (("E211", "E216"), ("E450(i)", "E450(ii)"), ("E924a", "E924b")):
            self.assertEqual(match_score(a, b), 0)

    def test_aliases_of_same_ingredient_do_not_create_ambiguity(self):
        rows = [dict(ingredient_id="1", normalized_alias="фруктоза")] * 2
        self.assertFalse(rank_candidates("фруктоаз", rows)[2])
        self.assertTrue(rank_candidates("фруктоаз", rows + [
            dict(ingredient_id="2", normalized_alias="фруктоза")])[2])

    def test_noisy_hazard_is_uncertain_and_real_other_code_is_not_replaced(self):
        banned = {"e216": dict(code="E216", name="Пропилпарабен",
                    ingredient_id=str(uuid4()), citations=["Пункт 1"]),
                  "e924a": dict(code="E924a", name="Бромат калия",
                    ingredient_id=str(uuid4()), citations=["Пункт 2"])}
        known = {"e211", "e216", "e924a"}
        for raw in ("E211", "без E216", "не содержит E216", "E216(ixyz)", "E216123"):
            self.assertEqual(find_hazards(raw, banned, known, .9), [], raw)
        for raw, code in (("E?216", "E216"), ("E9Э24a", "E924a")):
            found = find_hazards(raw, banned, known, .9)
            self.assertEqual([f.e_code for f in found], [code])
            self.assertEqual(found[0].certainty, "needs_verification")
        self.assertEqual(find_hazards("E216", banned, known, .9)[0].certainty, "high")
        self.assertEqual(find_hazards("E216", banned, known, .2)[0].certainty, "needs_verification")


class ReviewAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_guest_and_admin_without_csrf_cannot_label(self):
        app = create_app()
        async def session():
            yield AsyncMock()
        app.dependency_overrides[get_identity_session] = session
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch("app.services.identity.IdentityService.read", AsyncMock(return_value=None)):
                for method, path in (("GET", "/api/admin/ocr/reviews"),
                                     ("PUT", "/api/admin/ocr/reviews/" + str(uuid4()))):
                    response = await client.request(method, path, json={"isCorrect": True})
                    self.assertEqual(response.status_code, 403)
            app.dependency_overrides[require_admin] = lambda: SimpleNamespace(
                user=SimpleNamespace(id=uuid4()), csrf="secret")
            response = await client.put("/api/admin/ocr/reviews/" + str(uuid4()), json={"isCorrect": True})
            self.assertEqual(response.status_code, 403)


@unittest.skipUnless(os.environ.get("TOXICHECK_BOOTSTRAP_TEST") == "1", "Isolated loopback PostgreSQL on 55440")
class OcrDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = await asyncpg.connect(host="127.0.0.1", port=55440, user="bootstrap_test", database="postgres")
        self.dbname = "bootstrap_test_" + uuid4().hex
        await self.root.execute(f"CREATE DATABASE {self.dbname}")
        self.addAsyncCleanup(self.cleanup_database)
        self.conn = await asyncpg.connect(host="127.0.0.1", port=55440, user="bootstrap_test", database=self.dbname)
        self.addAsyncCleanup(self.conn.close)
        for name in SCHEMAS:
            await self.conn.execute((DB_DIR / name).read_text(encoding="utf-8"))
        engine = create_async_engine(f"postgresql+asyncpg://bootstrap_test@127.0.0.1:55440/{self.dbname}")
        self.addAsyncCleanup(engine.dispose)
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.ids = {}
        for name, code in (("Вода", None), ("Фруктоза", None), ("Пропилпарабен", "E216"), ("Бензоат натрия", "E211")):
            id = uuid4()
            self.ids[name] = str(id)
            await self.conn.execute("""INSERT INTO catalog.ingredients(id,kind,e_code,canonical_name_ru)
                VALUES($1,'ingredient',$2,$3)""", id, code, name)
            await self.conn.execute("""INSERT INTO catalog.ingredient_aliases(id,ingredient_id,alias,normalized_alias,confidence)
                VALUES($1,$2,$3,$4,1)""", uuid4(), id, name, name.lower())
        source, rule = uuid4(), uuid4()
        await self.conn.execute("INSERT INTO catalog.regulatory_sources(id,code,title) VALUES($1,'test','Test source')", source)
        await self.conn.execute("""INSERT INTO catalog.ingredient_rules
            (id,ingredient_id,source_id,regulatory_status,title,citation,verification_status,evaluation,match_policy)
            VALUES($1,$2,$3,'BANNED','Test ban','Test paragraph','verified','notice','exact_only')""",
            rule, self.ids["Пропилпарабен"], source)
        self.app = create_app()
        self.actor = SimpleNamespace(user=SimpleNamespace(id=uuid4()), csrf="secret")
        async def session():
            async with self.sessions() as db:
                yield db
        self.app.dependency_overrides[get_annotation_session] = session
        self.app.dependency_overrides[get_catalog_session] = session
        self.app.dependency_overrides[require_admin] = lambda: self.actor
        self.app.dependency_overrides[admin_write] = lambda: self.actor
        for item in (patch("app.services.ocr_catalog.AnnotationSessionLocal", self.sessions),
                     patch("app.api.routes.ocr_review.throttle", AsyncMock())):
            item.start()
            self.addCleanup(item.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def cleanup_database(self):
        assert self.dbname.startswith("bootstrap_test_") and len(self.dbname) == 47
        await self.root.execute(f"DROP DATABASE {self.dbname} WITH (FORCE)")
        await self.root.close()

    async def test_real_catalog_exact_fuzzy_and_unchanged_label_mode(self):
        for source, expected in (("label", ["Вода"]), ("ocr", ["Вода", "Фруктоза"])):
            response = await self.client.post("/api/analysis", json={
                "ingredientsText": "вода, фруктоаз, E216(ixyz), E924b", "source": source})
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual([i["name"] for i in result["matched"]], expected)
            self.assertIn("E216(ixyz)", result["unmatched"])
            self.assertIn("E924b", result["unmatched"])
            if source == "ocr":
                self.assertEqual(result["matched"][1]["matched_by"], "similarity")

    async def test_catalog_ban_low_confidence_queue_and_idempotent_migration(self):
        for _ in range(2):
            await self.conn.execute((DB_DIR / "migrations/20261110_ocr_matching.sql").read_text())
        result = OcrResult(status="needs_retake", rawText="E216", confidence=.2)
        for _ in range(2):
            async with self.sessions() as db:
                analysis = await process_ocr_result(db, result, AnalysisPreferences())
            self.assertIsNone(analysis)
            self.assertEqual(result.flagged_hazards[0].e_code, "E216")
            self.assertEqual(result.flagged_hazards[0].certainty, "needs_verification")
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM decision_tree.review_queue"), 1)
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM decision_tree.training_data"), 0)
        # Expired source must not yield a current warning.
        await self.conn.execute("UPDATE catalog.regulatory_sources SET effective_to='2000-01-01'")
        async with self.sessions() as db:
            await process_ocr_result(db, result, AnalysisPreferences())
        self.assertEqual(result.flagged_hazards, [])

    async def test_both_camera_sources_keep_catalog_matches_and_diagnostics(self):
        for source in ("native_camera", "browser_camera"):
            result = OcrResult(status="success", compositionText="вода, фруктоаз", rawText="Состав: вода, фруктоаз", confidence=.9)
            with patch("app.api.routes.scan.OcrService.recognize_image", AsyncMock(return_value=result)):
                response = await self.client.post("/api/scan/composition", json={
                    "imageBase64": "aW1hZ2U=", "captureSource": source})
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()
            self.assertEqual(data["engineVersion"], OCR_VERSION)
            self.assertEqual(data["requestId"], result.request_id)
            self.assertEqual(data["captureSource"], source)
            self.assertEqual(len(data["analysis"]["matched"]), 2)

    async def test_queue_outage_does_not_discard_ocr_or_warning(self):
        result = OcrResult(status="needs_retake", rawText="E?216", confidence=.9)
        with patch("app.services.ocr_catalog.enqueue_reviews", AsyncMock(side_effect=RuntimeError("private"))):
            async with self.sessions() as db:
                await process_ocr_result(db, result, AnalysisPreferences())
        self.assertEqual(result.review_queue_status, "unavailable")
        self.assertEqual(result.raw_text, "E?216")
        self.assertEqual(result.flagged_hazards[0].e_code, "E216")
        self.assertNotIn("private", result.model_dump_json())

    async def test_review_labels_are_atomic_idempotent_and_catalog_bound(self):
        item = dict(token_raw="фруктоаз", candidate_canonical_name="Вода", match_score=.6,
                    was_accepted=True, is_hazard=False)
        for _ in range(2):
            await enqueue_reviews("one-photo", [item])
        response = await self.client.get("/api/admin/ocr/reviews")
        self.assertEqual(response.json()["total"], 1)
        id = response.json()["items"][0]["review_id"]
        url = "/api/admin/ocr/reviews/" + id
        response = await self.client.put(url, json={"isCorrect": False, "correctedIngredientId": str(uuid4())})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM decision_tree.training_data"), 0)
        for _ in range(2):
            response = await self.client.put(url, json={
                "isCorrect": False, "correctedIngredientId": self.ids["Фруктоза"]})
            self.assertEqual(response.status_code, 200, response.text)
        examples = await self.conn.fetch("SELECT canonical_name,label,source FROM decision_tree.training_data ORDER BY id")
        self.assertEqual([(r["canonical_name"], r["label"]) for r in examples], [("Вода", 0), ("Фруктоза", 1)])
        self.assertTrue(all(str(self.actor.user.id) in r["source"] for r in examples))
        self.assertEqual((await self.client.put(url, json={"isCorrect": True})).status_code, 409)
        self.assertEqual((await self.client.get("/api/admin/ocr/reviews")).json()["total"], 0)

    async def test_unknown_user_proposal_cannot_become_positive_training_label(self):
        await enqueue_reviews("untrusted", [dict(token_raw="noise", candidate_canonical_name="arbitrary suggestion",
            match_score=.5, was_accepted=False, is_hazard=False)])
        row = (await self.client.get("/api/admin/ocr/reviews")).json()["items"][0]
        response = await self.client.put("/api/admin/ocr/reviews/" + row["review_id"], json={"isCorrect": True})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM decision_tree.training_data"), 0)


@unittest.skipUnless(OCR_EXTRAS and shutil.which("tesseract"), "Requires the server OCR image with Tesseract")
class RealTesseractTests(unittest.IsolatedAsyncioTestCase):
    async def test_russian_photo_to_composition_without_mocking_recognition(self):
        from PIL import Image, ImageDraw, ImageFont
        font = next((p for p in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ) if os.path.exists(p)), None)
        if not font:
            self.skipTest("Cyrillic fixture font not installed")
        image = Image.new("RGB", (1600, 550), "white")
        draw = ImageDraw.Draw(image)
        draw.multiline_text((60, 50), "Состав: вода, фруктоза.\nПищевая ценность: 100 ккал",
                            font=ImageFont.truetype(font, 60), fill="black", spacing=35)
        output = BytesIO()
        image.save(output, format="PNG")
        result = await OcrService()._recognize_embedded(base64.b64encode(output.getvalue()).decode(), "test")
        self.assertEqual(result.status, "success", result.model_dump())
        self.assertIn("вода", result.composition_text.lower())
        self.assertIn("фруктоза", result.composition_text.lower())
        self.assertNotIn("ккал", result.composition_text.lower())
        self.assertTrue(result.preprocessing)
