import copy
import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock

from app.db.build_regulatory_data import build_bundle, validate_bundle
from app.db.import_regulatory_data import _apply_schema, _as_date, _as_timestamp
from app.schemas.ingredients import IngredientRuleOut
from app.services.ingredient_resolver import IngredientResolver
from app.services.normalization import normalize_e_code, normalize_text
from app.services.regulatory_rules import assess_rule


class RegulatoryDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle, cls.report = build_bundle()
        cls.by_code = {i["e_code"].upper(): i for i in cls.bundle["ingredients"] if i.get("e_code")}

    def rules_for(self, code):
        identifier = self.by_code[code.upper()]["id"]
        return [r for r in self.bundle["ingredient_rules"] if r["ingredient_id"] == identifier]

    def test_every_rule_has_checkable_evidence(self):
        report = validate_bundle(self.bundle)
        self.assertEqual(report["rules_with_evidence"], len(self.bundle["ingredient_rules"]))
        for rule in self.bundle["ingredient_rules"]:
            for ref in rule["conditions"]["evidence"]:
                self.assertTrue(ref["url"].startswith("https://"))
                if rule["conditions"]["record_kind"] != "source_fragment":
                    self.assertRegex(ref["locator"], r"пункт|приложение|Приложение|позици")

    def test_status_counts_and_seed_is_not_a_verified_prohibition(self):
        counts = self.report["status_counts"]
        self.assertEqual(counts["BANNED"], 8)
        self.assertEqual(counts["PHASE_OUT"], 19)
        self.assertEqual(counts["ATTENTION"], 8)
        for rule in self.bundle["ingredient_rules"]:
            if rule["conditions"].get("regulatory_status") == "BANNED":
                self.assertTrue(rule["conditions"]["primary_basis_required"])
                self.assertFalse(rule["conditions"]["production_ready"])
                self.assertEqual(rule["severity"], "attention")

    def test_six_introduced_additives_have_primary_amendment_evidence(self):
        for code in ("E243", "E423", "E1205", "E1206", "E1207", "E1209"):
            rules = [r for r in self.rules_for(code) if r["rule_type"] == "permitted_food_additive"]
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0]["effective_from"], "2024-02-27")
            self.assertTrue(any(e["url"].endswith("#page=41") for e in rules[0]["conditions"]["evidence"]))

    def test_steviol_glycosides_are_not_leaf_extracts(self):
        self.assertFalse(any(r["conditions"].get("regulatory_status") == "PHASE_OUT" for r in self.rules_for("E960")))

    def test_ambiguous_amaranth_alias_is_not_a_banned_dye(self):
        identifier = self.by_code["E123"]["id"]
        names = {a["normalized_alias"] for a in self.bundle["ingredient_aliases"] if a["ingredient_id"] == identifier}
        self.assertNotIn(normalize_text("амарант"), names)
        self.assertNotIn("amaranth", names)
        self.assertIn(normalize_text("краситель амарант"), names)

    def test_fragment_cannot_contribute_a_risk_level(self):
        fragments = self.bundle["source_fragments"]
        self.assertGreater(len(fragments), 0)
        self.assertTrue(all(r["conditions"]["evaluation"] == "reference_only" for r in fragments))
        self.assertFalse({r["id"] for r in fragments} & {r["id"] for r in self.bundle["ingredient_rules"]})

    def test_phase_out_before_and_after_boundary_is_attention(self):
        raw = next(r for r in self.rules_for("E161g") if r["rule_type"] == "regulatory_status")
        for today in (date(2027, 2, 26), date(2027, 2, 27), date(2030, 1, 1)):
            rule = assess_rule(IngredientRuleOut(**raw), matched_by="e_code", today=today)
            self.assertEqual(rule.assessment_severity, "attention")
            self.assertIn("срока годности", rule.assessment_note)
        self.assertIn("завершен", assess_rule(IngredientRuleOut(**raw), matched_by="e_code", today=date(2027, 2, 27)).assessment_note)

    def test_fuzzy_match_cannot_trigger_strict_status(self):
        raw = next(r for r in self.rules_for("E123") if r["rule_type"] == "regulatory_status")
        rule = assess_rule(IngredientRuleOut(**raw), matched_by="similarity")
        self.assertEqual(rule.assessment_severity, "neutral")

    def test_regulatory_reference_is_not_risk_severity(self):
        rule = IngredientRuleOut(id="test", rule_type="maximum_use_level", severity="regulatory", title="Limit")
        self.assertEqual(IngredientResolver._highest_severity([rule]), "neutral")
        self.assertEqual(assess_rule(rule, matched_by="e_code").assessment_severity, "neutral")

    def test_missing_citation_or_source_is_rejected(self):
        bundle = {**self.bundle, "ingredient_rules": [copy.deepcopy(self.bundle["ingredient_rules"][0])]}
        bundle["ingredient_rules"][0]["conditions"]["evidence"][0]["source_id"] = "missing"
        with self.assertRaises(ValueError):
            validate_bundle(bundle)

    def test_retirement_cannot_remove_an_active_alias(self):
        bundle = {**self.bundle, "retired_alias_ids": [self.bundle["ingredient_aliases"][0]["id"]]}
        with self.assertRaises(ValueError):
            validate_bundle(bundle)

    def test_rebuilding_is_deterministic(self):
        again, _ = build_bundle()
        self.assertEqual(again, self.bundle)

    def test_additive_name_is_not_parser_note(self):
        self.assertEqual(self.by_code["E161G"]["canonical_name_ru"], "Кантаксантин")
        self.assertEqual(self.by_code["E181"]["canonical_name_ru"], "Танины пищевые")

    def test_asyncpg_dates_are_native_types(self):
        self.assertIsInstance(_as_date("2024-02-27"), date)
        self.assertIsInstance(_as_timestamp("2026-09-14T00:00:00Z"), datetime)
        self.assertIsNotNone(_as_timestamp(None).tzinfo)


class ImportSchemaTests(unittest.IsolatedAsyncioTestCase):
    async def test_schema_does_not_commit_the_import_transaction(self):
        connection = AsyncMock()
        await _apply_schema(connection)
        connection.execute.assert_awaited_once()
        sql = connection.execute.call_args.args[0]
        self.assertIn("ALTER COLUMN ingredient_id DROP NOT NULL", sql)
        self.assertNotRegex(sql, r"(?im)^\s*(BEGIN|COMMIT|ROLLBACK)\s*;")


class ECodeTests(unittest.TestCase):
    def test_cyrillic_hyphen_and_suffix(self):
        for value in ("Е 161g", "E-161G", "краситель (Е161g)"):
            self.assertEqual(normalize_e_code(value), "E161g")
        self.assertEqual(normalize_e_code("Е924а"), "E924a")

    def test_no_partial_code_match(self):
        for value in ("E12345", "name123", "E123abc"):
            self.assertIsNone(normalize_e_code(value))


if __name__ == "__main__":
    unittest.main()
