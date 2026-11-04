import copy
import unittest

from app.db.build_regulatory_data import ROOT, build_bundle, read_json
from app.db.regulatory_payload import validate_import_payload
from app.db.merge_proe_regulatory import english_name, merge_bundles, strict_code, validate_bundle


def ingredient(identifier, code, name, english=None):
    return dict(id=identifier, e_code=code, canonical_name_ru=name,
                canonical_name_en=english, kind="food_additive", category=None,
                description="Short description", full_description="Longer original description")


def fixtures():
    proe = dict(metadata={"dataset": "toxicheck_proe"},
        regulatory_sources=[dict(id="proe", code="PROE_INFO")],
        ingredients=[ingredient("p100", "E100", "Куркумины"),
                     ingredient("p100i", "E100(i)", "Куркумин"),
                     ingredient("p999", "E999", "Тест")],
        ingredient_aliases=[], ingredient_rules=[], source_fragments=[])
    for row in proe["ingredients"]:
        proe["source_fragments"].append(dict(id="profile-" + row["id"], source_id="proe",
            ingredient_id=row["id"], record_kind="additive_profile", full_description=row["full_description"]))
    rule = dict(id="rule", ingredient_id="old100", source_id="tr", rule_type="permitted_food_additive",
        severity="regulatory", citation="TR TS, appendix 2", effective_from="2024-02-27",
        conditions=dict(evaluation="reference_only", verification_status="extracted_not_verified",
            regulatory_status="PERMITTED_WITH_CONDITIONS",
            required_context=["food_category", "concentration"], resolved_ingredient_ids=["old100", "outside"],
            group_ingredient_id="outside", evidence=[dict(source_id="tr", url="https://example.test/tr",
                locator="appendix 2", verification_status="extracted_not_verified")]))
    legacy = dict(metadata={}, regulatory_sources=[dict(id="tr", code="TR_TS_029_2012")],
        ingredients=[ingredient("old100", "E100", "Куркумин", "CURCUMIN"),
                     ingredient("outside", None, "Другое"),
                     ingredient("name-a", None, "Тест"), ingredient("name-b", None, "Тест")],
        ingredient_aliases=[], ingredient_rules=[rule, dict(copy.deepcopy(rule), id="global", ingredient_id=None)],
        source_fragments=[])
    return proe, legacy


class MatchingTests(unittest.TestCase):
    def test_strict_codes_preserve_subtypes_and_reject_extraction_suffixes(self):
        self.assertEqual(strict_code("Е-160a(ii)"), "E160A(II)")
        self.assertNotEqual(strict_code("E100(i)"), strict_code("E100"))
        self.assertNotEqual(strict_code("E101i"), strict_code("E101(i)"))
        self.assertIsNone(strict_code("E386э"))
        self.assertIsNone(strict_code("contains E100"))

    def test_english_cleanup_keeps_name_not_table(self):
        self.assertEqual(english_name("RIBOFLAVINS): (i) Рибофлавин (Riboflavin)"), "RIBOFLAVINS")
        self.assertEqual(english_name("KONJAC (KONJAC FLOUR)): (i) Камедь"), "KONJAC (KONJAC FLOUR)")
        self.assertEqual(english_name("GUM ARABIC (ACACIA GUM"), "GUM ARABIC")
        self.assertIsNone(english_name("C30) этиловый эфир"))
        self.assertIsNone(english_name("E100"))

    def test_rules_do_not_flow_from_parent_to_subtype_or_ambiguous_name(self):
        proe, legacy = fixtures()
        merged, report = merge_bundles(proe, legacy)
        self.assertEqual(report["summary"]["matched"], 1)
        self.assertEqual({r["ingredient_id"] for r in merged["ingredient_rules"]}, {"p100"})
        self.assertEqual({r["e_code"] for r in report["unmatched_proe"]}, {"E100(i)", "E999"})
        self.assertEqual(merged["ingredients"][0]["canonical_name_en"], "CURCUMIN")
        self.assertIsNone(merged["ingredients"][1]["canonical_name_en"])

    def test_nested_references_are_mapped_with_originals_archived(self):
        merged, _ = merge_bundles(*fixtures())
        rule = merged["ingredient_rules"][0]
        self.assertEqual(rule["conditions"]["resolved_ingredient_ids"], ["p100"])
        self.assertEqual(rule["conditions"]["legacy_resolved_ingredient_ids"], ["old100", "outside"])
        self.assertNotIn("group_ingredient_id", rule["conditions"])
        self.assertEqual(rule["conditions"]["legacy_group_ingredient_id"], "outside")
        self.assertEqual(rule["conditions"]["evaluation"], "reference_only")
        self.assertEqual(rule["effective_from"], "2024-02-27")
        self.assertEqual(rule["conditions"]["required_context"], ["food_category", "concentration"])

    def test_only_fill_empty_fields_and_do_not_mutate_inputs(self):
        proe, legacy = fixtures()
        proe["ingredients"][0]["canonical_name_en"] = "Existing name"
        before = copy.deepcopy((proe, legacy))
        merged, _ = merge_bundles(proe, legacy)
        self.assertEqual((proe, legacy), before)
        self.assertEqual(merged["ingredients"][0]["canonical_name_en"], "Existing name")
        self.assertEqual(merged["ingredients"][0]["full_description"], proe["ingredients"][0]["full_description"])

    def test_ambiguous_alias_is_retired_for_strict_rules(self):
        proe, legacy = fixtures()
        proe["ingredient_aliases"] = [dict(id="ambiguous", ingredient_id="p100", alias="amaranth")]
        legacy["ingredient_rules"][0]["conditions"]["match_policy"] = "exact_only"
        merged, report = merge_bundles(proe, legacy, ambiguous_aliases=["amaranth"])
        self.assertIn("ambiguous", merged["retired_alias_ids"])
        self.assertNotIn("ambiguous", {a["id"] for a in merged["ingredient_aliases"]})
        self.assertEqual(report["rejected_aliases"][0]["reason"], "ambiguous_for_strict_status_rule")

    def test_import_validation_rejects_nested_orphan_and_publisher_rule(self):
        merged, _ = merge_bundles(*fixtures())
        self.assertEqual(validate_import_payload(merged)["rules_with_evidence"], 1)
        orphan = copy.deepcopy(merged)
        orphan["ingredient_rules"][0]["conditions"]["resolved_ingredient_ids"] = ["old100"]
        with self.assertRaisesRegex(ValueError, "nested"):
            validate_bundle(orphan)
        merged["ingredient_rules"][0]["source_id"] = "proe"
        with self.assertRaisesRegex(ValueError, "proE"):
            validate_import_payload(merged)

    def test_repeat_merge_is_stable(self):
        first, _ = merge_bundles(*fixtures())
        second, _ = merge_bundles(*fixtures())
        for key in ("ingredients", "ingredient_rules", "ingredient_aliases", "source_fragments"):
            self.assertEqual(first[key], second[key])

    def test_doses_labeling_and_purity_are_excluded_even_with_status_field(self):
        proe, legacy = fixtures()
        for kind in ("maximum_use_level", "sweetener_use", "additive_purity_criteria", "mandatory_warning_label"):
            legacy["ingredient_rules"].append(dict(copy.deepcopy(legacy["ingredient_rules"][0]), id=kind, rule_type=kind))
        for status in ("BANNED", "PHASE_OUT"):
            row = copy.deepcopy(legacy["ingredient_rules"][0])
            row.update(id=status, rule_type="regulatory_status")
            row["conditions"]["regulatory_status"] = status
            legacy["ingredient_rules"].append(row)
        merged, report = merge_bundles(proe, legacy)
        self.assertEqual({r["id"] for r in merged["ingredient_rules"]}, {"rule", "BANNED", "PHASE_OUT"})
        self.assertEqual(report["excluded_rules"]["count"], 5)
        self.assertEqual(report["legacy_rules_outside_proe_catalog"], 0)
        self.assertEqual(report["matches"][0]["rules_copied"], 3)

    def test_validator_prevents_reintroducing_quantity_rules(self):
        merged, _ = merge_bundles(*fixtures())
        merged["ingredient_rules"][0]["rule_type"] = "maximum_use_level"
        with self.assertRaisesRegex(ValueError, "Only ingredient permission"):
            validate_import_payload(merged)


class SnapshotMergeTests(unittest.TestCase):
    def test_real_snapshots_preserve_descriptions_and_rule_evidence(self):
        proe = read_json(ROOT / "toxicheck_proe.json")
        legacy, _ = build_bundle()
        seed = read_json(ROOT / "backend/db/regulatory_status_seed.json")
        merged, report = merge_bundles(proe, legacy, ambiguous_aliases=seed["ambiguous_aliases"])
        self.assertEqual(len(merged["ingredients"]), len(proe["ingredients"]))
        self.assertGreater(report["summary"]["filled_fields"]["canonical_name_en"], 300)
        self.assertEqual({i["id"]: i["full_description"] for i in merged["ingredients"]},
                         {i["id"]: i["full_description"] for i in proe["ingredients"]})
        old_rules = {r["id"]: r for r in legacy["ingredient_rules"]}
        for rule in merged["ingredient_rules"]:
            old = old_rules[rule["id"]]
            for field in ("source_id", "severity", "citation", "effective_from", "effective_to"):
                self.assertEqual(rule.get(field), old.get(field))
            self.assertEqual(rule["conditions"]["evidence"], old["conditions"]["evidence"])
        self.assertEqual(validate_bundle(merged)["rules_with_evidence"], len(merged["ingredient_rules"]))


if __name__ == "__main__":
    unittest.main()
