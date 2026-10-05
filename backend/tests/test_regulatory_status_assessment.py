import unittest
from unittest.mock import AsyncMock, MagicMock

from app.schemas.analysis import AnalysisPreferences
from app.schemas.ingredients import IngredientRuleOut, MatchedIngredientOut
from app.services.ingredient_catalog import IngredientCatalog
from app.services.ingredient_resolver import IngredientResolver
from app.services.regulatory_rules import assess_rule
from app.services.verdict_engine import VerdictEngine


def banned_rule(**conditions):
    return IngredientRuleOut(
        id="rule", rule_type="regulatory_status", severity="attention", title="Restriction",
        explanation="Official publication from 2021; current legal basis needs verification.",
        citation="Page 4, item 1", conditions={
            "regulatory_status": "BANNED", "evaluation": "notice", "match_policy": "exact_only",
            "primary_basis_required": True, "production_ready": False,
            "verification_status": "verified_official_publication",
            "evidence": [{"url": "https://example.org/source.pdf#page=4", "locator": "Page 4, item 1"}],
            **conditions,
        },
    )


class AssessmentTests(unittest.TestCase):
    def test_ban_is_restriction_independent_of_verification_level(self):
        for needs_review in (True, False):
            for matched_by in ("ingredient_id", "e_code", "alias"):
                with self.subTest(needs_review=needs_review, matched_by=matched_by):
                    original = banned_rule(primary_basis_required=needs_review)
                    assessed = assess_rule(original, matched_by=matched_by)
                    self.assertEqual(assessed.assessment_severity, "forbidden")
                    self.assertEqual(assessed.conditions, original.conditions)
                    self.assertEqual(assessed.assessment_note, original.explanation)
                    self.assertEqual(assessed.citation, original.citation)
                    self.assertIsNone(original.assessment_severity)

    def test_similarity_and_reference_only_do_not_trigger_restrictions(self):
        self.assertEqual(assess_rule(banned_rule(), matched_by="similarity").assessment_severity, "neutral")
        self.assertEqual(assess_rule(banned_rule(evaluation="reference_only"),
                                    matched_by="e_code").assessment_severity, "neutral")

    def test_phase_out_remains_attention(self):
        assessed = assess_rule(banned_rule(regulatory_status="PHASE_OUT"), matched_by="e_code")
        self.assertEqual(assessed.assessment_severity, "attention")

    def test_scan_verdict_retains_qualified_explanation(self):
        assessed = assess_rule(banned_rule(), matched_by="e_code")
        matched = MatchedIngredientOut(ingredient_id="ingredient", name="Ingredient", code="E121",
            raw_text="E121", matched_by="e_code", match_score=1,
            severity=IngredientResolver.highest_severity([assessed]), rules=[assessed])
        verdict = VerdictEngine().build_verdict([matched], [], AnalysisPreferences())
        self.assertEqual(verdict.title, "Есть ингредиенты с ограничениями")
        self.assertEqual(verdict.reasons[0].severity, "forbidden")
        self.assertEqual(verdict.reasons[0].explanation, assessed.assessment_note)


class FilterAssessmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_restrictions_filter_uses_real_assessment_and_preserves_warnings(self):
        session = AsyncMock()
        candidates = MagicMock()
        candidates.scalars.return_value.all.return_value = ["banned", "phase-out", "unknown"]
        page_rows = MagicMock()
        page_rows.mappings.return_value.all.return_value = [
            dict(id="banned", name="Ingredient", code="E121", category=None),
        ]
        session.execute.side_effect = [candidates, page_rows]
        catalog = IngredientCatalog(session)
        catalog.resolver._load_rules = AsyncMock(return_value={
            "banned": [banned_rule()],
            "phase-out": [banned_rule(regulatory_status="PHASE_OUT")],
        })
        page = await catalog.list("", "all", 30, 0, status="restricted")
        self.assertEqual(page.total, 1)
        self.assertEqual(page.items[0].severity, "forbidden")
        self.assertEqual(session.execute.await_args.args[1]["matching_ids"], ["banned"])
        catalog.resolver._load_rules.assert_awaited_once_with(["banned", "phase-out", "unknown"])


if __name__ == "__main__":
    unittest.main()
