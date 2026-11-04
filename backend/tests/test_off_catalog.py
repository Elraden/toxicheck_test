import copy
import json
import unittest
from unittest.mock import patch

from app.db.build_catalog import COLUMNS, DEFAULT_INPUT, DEFAULT_OFF_INPUT, build_catalog, validate_catalog
from app.db.off_taxonomy import enrich_catalog, new_alias, parse_taxonomy
from app.services.normalization import normalize_text


class OffParserTests(unittest.TestCase):
    def test_comments_directives_escaped_commas_duplicates_and_properties(self):
        text = (
            "# Only data, never instructions\n"
            "synonyms:en: colour, color\n\n"
            "stopwords:en: of\n\n"
            "en: water, water\\, filtered\nru: \u0412\u043e\u0434\u0430\n"
            "< en: ingredient\nvegan:en: yes\n\n"
            "en: Water, drinking water\nvegan:en: maybe\n"
            "sco: watter\nzh_yue: water\n"
        )
        entries, report = parse_taxonomy(text)
        self.assertEqual(len(entries), 1)
        self.assertIn("water, filtered", entries[0].names["en"])
        self.assertIn("drinking water", entries[0].names["en"])
        self.assertEqual(entries[0].parents, {("en", "ingredient")})
        self.assertEqual(entries[0].properties, {("vegan", "en", "yes"), ("vegan", "en", "maybe")})
        self.assertEqual(report["ignored_directives"], 2)
        self.assertEqual(len(report["duplicate_blocks"]), 1)
        self.assertEqual(report["unparsed_lines"], [])

    def test_unknown_lines_are_reported_and_empty_names_rejected(self):
        _, report = parse_taxonomy("en: water\nnot understood\n")
        self.assertEqual(report["unparsed_lines"], [dict(line=2, text="not understood")])
        with self.assertRaisesRegex(ValueError, "Empty taxonomy names"):
            parse_taxonomy("en: ,\n")


class OffCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = build_catalog(json.loads(DEFAULT_INPUT.read_text(encoding="utf-8")))
        cls.raw = DEFAULT_OFF_INPUT.read_bytes()
        cls.bundle, cls.report = enrich_catalog(cls.base, cls.raw)

    def lookup(self, name):
        return {r["ingredient_id"] for r in self.bundle["ingredient_aliases"]
                if r["normalized_alias"] == normalize_text(name)}

    def test_water_fructose_and_ordinary_foods_have_no_invented_rules(self):
        by_id = {r["id"]: r for r in self.bundle["ingredients"]}
        rule_ids = {r["ingredient_id"] for r in self.bundle["ingredient_rules"]}
        for name in ("water", "\u0432\u043e\u0434\u0430", "fructose", "\u0444\u0440\u0443\u043a\u0442\u043e\u0437\u0430", "wheat flour", "milk"):
            ids = self.lookup(name)
            self.assertEqual(len(ids), 1, name)
            ingredient = by_id[next(iter(ids))]
            self.assertIsNone(ingredient["e_code"])
            self.assertEqual(ingredient["kind"], "ingredient")
            self.assertNotIn(ingredient["id"], rule_ids)
        for table in ("ingredient_rules", "rule_evidence", "rule_transitions", "regulatory_sources"):
            self.assertEqual(self.bundle[table], self.base[table])
        self.assertGreater(self.report["added_ingredients"], 900)

    def test_amaranth_and_starch_are_not_automatically_treated_as_additives(self):
        for name in ("amaranth", "\u0430\u043c\u0430\u0440\u0430\u043d\u0442", "\u043a\u0440\u0430\u0445\u043c\u0430\u043b"):
            self.assertFalse(self.lookup(name))
            candidates = [r for r in self.bundle["ingredient_alias_review"]
                          if r["normalized_alias"] == normalize_text(name)]
            self.assertGreater(len({r["ingredient_id"] for r in candidates}), 1)
        self.assertEqual(len(self.lookup("E123")), 1)

    def test_parent_is_not_alias_or_regulatory_inheritance(self):
        entry = next(r for r in self.bundle["taxonomy_entries"] if r["taxonomy_key"] == "en:soya lecithin")
        ingredient = next(r for r in self.bundle["ingredients"] if r["id"] == entry["ingredient_id"])
        self.assertIsNone(ingredient["e_code"])
        self.assertTrue(any(p["entry_id"] == entry["id"] and p["parent_reference"] == "en:E322i"
                            for p in self.bundle["taxonomy_parents"]))
        properties = [p for p in self.bundle["taxonomy_properties"] if p["entry_id"] == entry["id"]]
        self.assertTrue(any(p["property"] == "allergens" and p["value"] == "en:soybeans" for p in properties))
        self.assertTrue(all(p["verification_status"] == "reference_only" for p in properties))

    def test_only_exact_e_codes_merge_and_untranslated_nodes_are_preserved(self):
        raw = (
            "en: custom acid, E-330\nru: acid name\n\n"
            "en: parented food\nru: new food\n< en: custom acid\n\n"
            "en: conflicting codes, E330, E123\nru: ambiguous\n\n"
            "en: unknown code, E9999\nru: unknown\n\n"
            "en: no translation\n"
        ).encode()
        bundle, report = enrich_catalog(self.base, raw)
        entries = {r["taxonomy_key"]: r for r in bundle["taxonomy_entries"]}
        acid = next(r for r in self.base["ingredients"] if r["e_code"] == "E330")
        self.assertEqual(entries["en:custom acid"]["ingredient_id"], acid["id"])
        self.assertEqual(entries["en:parented food"]["match_method"], "new_ordinary_ingredient")
        for name in ("en:conflicting codes", "en:unknown code", "en:no translation"):
            self.assertIsNone(entries[name]["ingredient_id"])
        self.assertEqual(report["exact_code_matches"], 1)
        self.assertEqual(len(report["code_review"]), 2)
        parent = bundle["taxonomy_parents"][0]
        self.assertEqual(parent["parent_entry_id"], entries["en:custom acid"]["id"])

    def test_duplicate_properties_preserved_for_review(self):
        bundle, report = enrich_catalog(self.base, b"en: water\nru: water\nvegan:en: yes\n\nen: water\nvegan:en: no\n")
        self.assertEqual(report["added_ingredients"], 1)
        self.assertEqual(report["property_conflicts"][0]["values"], ["no", "yes"])
        self.assertEqual(len(bundle["taxonomy_properties"]), 2)

    def test_rebuild_is_deterministic_input_unchanged_and_never_connects(self):
        before = copy.deepcopy(self.base)
        with patch("asyncpg.connect", side_effect=AssertionError("No database access allowed")), \
                patch("socket.socket", side_effect=AssertionError("No network access allowed")):
            again, report = enrich_catalog(self.base, self.raw)
        self.assertEqual(self.base, before)
        self.assertEqual(again, self.bundle)
        self.assertEqual(report, self.report)
        self.assertFalse(report["database_uploaded"])
        self.assertEqual(report["parsing"]["unparsed_lines"], [])
        with self.assertRaisesRegex(ValueError, "Rebuild from"):
            enrich_catalog(self.bundle, self.raw)

    def test_flat_contract_and_invalid_data_rejected(self):
        validate_catalog(self.bundle)
        for table in COLUMNS:
            self.assertTrue(all(set(r) == set(COLUMNS[table]) for r in self.bundle[table]))
        bad = copy.deepcopy(self.bundle)
        candidate = bad["ingredient_alias_review"][0]
        bad["ingredient_aliases"].append(new_alias(candidate["ingredient_id"], "en", candidate["alias"], None))
        with self.assertRaises(ValueError):
            validate_catalog(bad)
        bad = copy.deepcopy(self.bundle)
        bad["taxonomy_parents"][0]["parent_entry_id"] = "00000000-0000-0000-0000-000000000000"
        with self.assertRaisesRegex(ValueError, "Orphan"):
            validate_catalog(bad)
        bad = copy.deepcopy(self.bundle)
        bad["taxonomy_properties"][0]["verification_status"] = "verified"
        with self.assertRaises(ValueError):
            validate_catalog(bad)


if __name__ == "__main__":
    unittest.main()
