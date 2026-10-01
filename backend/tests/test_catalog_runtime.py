import unittest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.db.session import get_catalog_session
from app.main import create_app
from app.schemas.analysis import AnalysisPreferences
from app.schemas.ingredients import MatchedIngredientOut
from app.services.ingredient_resolver import IngredientResolver
from app.services.normalization import normalize_e_code
from app.services.verdict_engine import VerdictEngine


def result_rows(rows):
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    return result


class RuntimeSettingsTests(unittest.TestCase):
    def test_dedicated_catalog_connection_and_asyncpg_ssl(self):
        with patch.dict("os.environ", {}, clear=True):
            settings = Settings(_env_file=None, database_url="postgresql://u:p@old/app",
                TOXICHECK_CATALOG_DATABASE_URL="postgresql://u:p%40ss@new/catalog?sslmode=require")
            self.assertEqual(settings.catalog_url, "postgresql+asyncpg://u:p%40ss@new/catalog?ssl=require")
            self.assertIn("@old/app", settings.database_url)
            settings = Settings(_env_file=None, database_url="postgres://u:p@new/catalog", TOXICHECK_CATALOG_DATABASE_URL="")
            self.assertEqual(settings.catalog_url, "postgresql+asyncpg://u:p@new/catalog")

    def test_precise_subtypes_and_cyrillic_codes(self):
        for raw, expected in (("E450(viii)", "E450(viii)"), ("E-322 (I)", "E322(i)"),
                              ("emulsifier (E322(i))", "E322(i)"), ("E450(xv)", "E450(xv)")):
            self.assertEqual(normalize_e_code(raw), expected)
        for raw in ("E450(viii", "E450(ixyz)", "E12345", "E123abc"):
            self.assertIsNone(normalize_e_code(raw), raw)

    def test_database_error_does_not_expose_connection_string(self):
        app = create_app()

        async def unavailable():
            raise SQLAlchemyError("postgresql://secret:password@private.example/db")
            yield

        app.dependency_overrides[get_catalog_session] = unavailable
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/api/analysis", json={"ingredientsText": "water"})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("password", response.text)
            self.assertNotIn("private.example", response.text)


class RuntimeResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_fuzzy_fallback_no_parent_code_and_deduplication(self):
        resolver = IngredientResolver(AsyncMock())
        water = dict(ingredient_id="water", name="Water", code=None, matched_by="alias", match_score=1)
        resolver._match_codes = AsyncMock(return_value={})
        resolver._match_aliases = AsyncMock(return_value={"water": water})
        resolver._load_rules = AsyncMock(return_value={})
        response = await resolver.resolve("water, E450(xv), watter, water")
        self.assertEqual(len(response.matched), 1)
        self.assertEqual(response.matched[0].severity, "unknown")
        self.assertEqual(response.unmatched, ["E450(xv)", "watter"])
        resolver._match_codes.assert_awaited_once_with(["e450(xv)"])
        resolver._match_aliases.assert_awaited_once_with(["water", "watter"])
        resolver._load_rules.assert_awaited_once_with(["water"])

    async def test_ambiguous_alias_rows_do_not_choose_first(self):
        session = AsyncMock()
        session.execute.return_value = result_rows([
            dict(normalized_alias="ambiguous", ingredient_id="one"),
            dict(normalized_alias="ambiguous", ingredient_id="two"),
            dict(normalized_alias="unique", ingredient_id="three"),
        ])
        result = await IngredientResolver(session)._match_aliases(["ambiguous", "unique"])
        self.assertEqual(set(result), {"unique"})
        sql = str(session.execute.await_args.args[0])
        self.assertIn("catalog.ingredient_alias_review", sql)
        self.assertNotIn("LIMIT 1", sql)
        self.assertNotIn("similarity", sql)

    async def test_flat_rules_evidence_and_transition_preserve_api_contract(self):
        row = dict(id="rule", ingredient_id="ingredient", source_id="source", source_code="FIXTURE",
            source_title="Test source", source_url="https://example.invalid/source", title="Test rule",
            explanation=None, citation="Test section", regulatory_status="PHASE_OUT", scope="ingredient",
            jurisdiction=None, verification_status="verified_primary", evaluation="notice",
            match_policy="exact_only", primary_basis_required=False, production_ready=None,
            product_compliance_assessed=False)
        refs = [dict(id="evidence", rule_id="rule", source_id="source", table_label=None,
                     url="https://example.invalid/source", locator="Test section")]
        transition = dict(rule_id="rule", transition_end=date(2030, 1, 1))
        session = AsyncMock()
        session.execute.side_effect = [result_rows([row]), result_rows(refs), result_rows([transition])]
        result = await IngredientResolver(session)._load_rules([row["ingredient_id"]])
        output = result[row["ingredient_id"]][0]
        self.assertEqual(output.conditions["regulatory_status"], "PHASE_OUT")
        self.assertEqual(output.conditions["evidence"][0]["locator"], refs[0]["locator"])
        self.assertEqual(output.conditions["transition"]["transition_end"], transition["transition_end"].isoformat())
        self.assertEqual(output.severity, "attention")
        self.assertEqual(session.execute.await_count, 3)
        for call in session.execute.await_args_list:
            self.assertIn("catalog.", str(call.args[0]))
            self.assertNotIn("r.conditions", str(call.args[0]))
        self.assertIn("CURRENT_DATE", str(session.execute.await_args_list[0].args[0]))
        output.model_dump_json()

    async def test_empty_composition_does_not_query_database(self):
        session = AsyncMock()
        result = await IngredientResolver(session).resolve(" , ; ")
        self.assertEqual(result.matched, [])
        self.assertEqual(result.unmatched, [])
        session.execute.assert_not_awaited()

    async def test_old_or_empty_catalog_is_not_ready(self):
        session = AsyncMock()
        session.scalar.return_value = 1
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=session)
        context.__aexit__ = AsyncMock(return_value=False)
        with patch("app.db.session.CatalogSessionLocal", return_value=context):
            with self.assertRaises(HTTPException) as caught:
                await anext(get_catalog_session())
        self.assertEqual(caught.exception.status_code, 503)


class RuntimeVerdictTests(unittest.TestCase):
    def test_found_food_without_rules_is_not_a_safety_verdict(self):
        water = MatchedIngredientOut(ingredient_id="water", name="Water", raw_text="water",
                                    matched_by="alias", match_score=1, severity="unknown")
        engine = VerdictEngine()
        result = engine.build_verdict([water], [], AnalysisPreferences())
        self.assertEqual(result.level, "unknown")
        self.assertEqual(result.reasons, [])
        result = engine.build_verdict([water], [], AnalysisPreferences(excludedIngredientIds=["water"]))
        self.assertEqual(result.level, "risky_for_user")
        self.assertEqual([r.severity for r in result.reasons], ["personal"])
        self.assertEqual(engine.build_verdict([], [], AnalysisPreferences()).level, "unknown")


if __name__ == "__main__":
    unittest.main()
