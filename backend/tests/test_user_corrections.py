import hashlib
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes.corrections import _windows
from app.api.identity_dependencies import guest_write
from app.schemas.identity import SessionUser
from app.services.identity import Principal
from app.db.session import get_annotation_session
from app.main import create_app
from app.schemas.analysis import AnalysisPreferences, AnalyzeIngredientsResponse, ProductVerdict
from app.schemas.corrections import SaveCorrectionsRequest
from app.schemas.ingredients import MatchedIngredientOut
from app.services.correction_service import CorrectionService, apply_corrections
from app.services.verdict_engine import VerdictEngine


def payload(raw="водда, E202", original="водда", corrected="вода", index=0):
    return SaveCorrectionsRequest(requestId=uuid4(), ingredientsText=raw, corrections=[{
        "id": uuid4(), "tokenIndex": index, "tokenRaw": original, "correctedText": corrected,
    }])


def analysis_result():
    return AnalyzeIngredientsResponse(matched=[], unmatched=["вода"], tokens=["вода", "E202"],
        verdict=ProductVerdict(level="unknown", risk_score=0, title="Нет оценки", description="Нет оценки"))


class CorrectionValidationTests(unittest.TestCase):
    def test_replace_keeps_other_tokens_and_subtypes(self):
        data = payload("шоколад (водда, E322(i)), соль", index=1)
        self.assertEqual(apply_corrections(data), "шоколад, вода, E322(i), соль")

    def test_empty_ocr_can_be_entered_manually(self):
        self.assertEqual(apply_corrections(payload("", "", "вода, E202")), "вода, E202")

    def test_repeated_words_are_addressed_by_position(self):
        self.assertEqual(apply_corrections(payload("водда, водда", index=1)), "водда, вода")

    def test_stale_token_or_index_is_rejected(self):
        for data in (payload(original="соль"), payload(index=4)):
            with self.assertRaises(HTTPException) as error:
                apply_corrections(data)
            self.assertEqual(error.exception.status_code, 422)

    def test_bounds_and_duplicate_items(self):
        data = payload().model_dump(by_alias=True)
        for corrections in ([], data["corrections"] * 2, data["corrections"] * 51,
                            [{**data["corrections"][0], "correctedText": "   "}],
                            [{**data["corrections"][0], "correctedText": "a" * 501}]):
            with self.assertRaises(ValidationError):
                SaveCorrectionsRequest.model_validate({**data, "corrections": corrections})


class CorrectionPersistenceTests(unittest.IsolatedAsyncioTestCase):
    def session(self):
        session = MagicMock()
        session.begin.return_value = AsyncMock()
        session.scalar = AsyncMock(return_value=uuid4())
        session.execute = AsyncMock()
        return session

    async def test_anonymous_feedback_stays_pending_and_uses_single_transaction(self):
        session = self.session()
        data = payload()
        with patch("app.services.correction_service.AnalysisService.analyze", new_callable=AsyncMock,
                   return_value=analysis_result()) as analyze:
            result = await CorrectionService(session).save(data)
        self.assertEqual(result.status, "pending_review")
        self.assertEqual(result.saved, 1)
        analyze.assert_awaited_once_with("вода, E202", data.preferences)
        session.begin.assert_called_once()
        sql = str(session.scalar.call_args.args[0])
        self.assertIn("user_corrections", sql)
        sql = str(session.execute.call_args.args[0])
        self.assertIn("review_queue", sql)
        self.assertNotIn("training_data", sql)
        self.assertNotIn("INSERT INTO catalog", sql)
        session.begin.return_value.__aexit__.assert_awaited_once_with(None, None, None)

    async def test_exact_retry_does_not_add_duplicate_review_row(self):
        session = self.session()
        data = payload()
        item = data.corrections[0]
        session.scalar.return_value = None
        result = MagicMock()
        result.mappings.return_value.one.return_value = {
            "request_id": data.request_id, "token_index": 0, "token_raw": item.token_raw,
            "corrected_text": item.corrected_text,
            "input_sha256": hashlib.sha256(data.ingredients_text.encode()).hexdigest(),
        }
        session.execute.return_value = result
        with patch("app.services.correction_service.AnalysisService.analyze", new_callable=AsyncMock,
                   return_value=analysis_result()):
            saved = await CorrectionService(session).save(data)
            self.assertEqual(saved.saved, 1)
            self.assertEqual(session.execute.await_count, 1)  # SELECT only, no second queue entry.
            result.mappings.return_value.one.return_value["corrected_text"] = "соль"
            with self.assertRaises(HTTPException) as error:
                await CorrectionService(session).save(data)
            self.assertEqual(error.exception.status_code, 409)

    async def test_queue_failure_rolls_back_correction(self):
        session = self.session()
        session.execute.side_effect = SQLAlchemyError("write failed")
        with patch("app.services.correction_service.AnalysisService.analyze", new_callable=AsyncMock,
                   return_value=analysis_result()):
            with self.assertRaises(SQLAlchemyError):
                await CorrectionService(session).save(payload())
        self.assertIs(session.begin.return_value.__aexit__.call_args.args[0], SQLAlchemyError)


class CorrectionRouteTests(unittest.TestCase):
    def test_save_validation_db_failure_and_throttle(self):
        app = create_app()
        async def session():
            yield MagicMock()
        app.dependency_overrides[get_annotation_session] = session
        app.dependency_overrides[guest_write] = lambda: Principal(str(uuid4()), SessionUser(
            id=uuid4(), role="user", account_kind="guest"), "csrf", "guest")
        _windows.clear()
        try:
            with TestClient(app) as client, patch("app.api.routes.corrections.CorrectionService.save",
                    new_callable=AsyncMock) as save:
                data = payload().model_dump(mode="json", by_alias=True)
                save.return_value = {"saved": 1, "ingredientsText": "вода, E202", "analysis": analysis_result()}
                response = client.post("/api/scan/corrections", json=data)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "pending_review")
                bad = {**data, "corrections": []}
                self.assertEqual(client.post("/api/scan/corrections", json=bad).status_code, 422)
                save.side_effect = SQLAlchemyError("private database detail")
                response = client.post("/api/scan/corrections", json=data)
                self.assertEqual(response.status_code, 503)
                self.assertNotIn("private", response.text)
                for _ in range(30):
                    response = client.post("/api/scan/corrections", json=data)
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response.headers["retry-after"], "60")
        finally:
            _windows.clear()


class StrictExclusionTests(unittest.TestCase):
    def ingredient(self, severity="neutral"):
        return MatchedIngredientOut(ingredient_id="sorbate", name="Сорбат калия", code="E202",
            raw_text="E202", matched_by="e_code", match_score=1, severity=severity)

    def test_personal_exclusion_does_not_change_regulatory_status(self):
        ingredient = self.ingredient()
        result = VerdictEngine().build_verdict([ingredient], [], AnalysisPreferences(excludedIngredientIds=["sorbate"]))
        self.assertEqual(result.level, "risky_for_user")
        self.assertEqual(result.description, "Найдены ингредиенты из ваших исключений: E202 Сорбат калия")
        self.assertEqual(ingredient.severity, "neutral")
        self.assertGreaterEqual(result.risk_score, 95)

    def test_exclusion_notice_remains_with_regulatory_prohibition(self):
        result = VerdictEngine().build_verdict([self.ingredient("forbidden")], ["noise"],
            AnalysisPreferences(excludedIngredientIds=["sorbate"]))
        self.assertEqual(result.level, "risky_for_user")
        self.assertEqual({r.severity for r in result.reasons}, {"forbidden", "personal"})

    def test_no_personal_warning_for_unrelated_exclusion(self):
        result = VerdictEngine().build_verdict([self.ingredient()], [], AnalysisPreferences(excludedIngredientIds=["other"]))
        self.assertNotEqual(result.level, "risky_for_user")
