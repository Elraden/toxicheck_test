import copy
import unittest
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

from app.db.apply_catalog_supplement import delta, update
from app.db.build_catalog import COLUMNS, validate_catalog
from app.db.catalog_supplements import supplement_catalog
from app.services.ingredient_resolver import IngredientResolver
from app.schemas.ingredients import IngredientRuleOut


def empty_catalog():
    return {**{table: [] for table in COLUMNS}, "metadata": {"dataset": "toxicheck_catalog", "schema_version": 2}}


class SupplementTests(unittest.TestCase):
    def test_complete_source_linked_catalog_and_idempotence(self):
        bundle = supplement_catalog(empty_catalog())
        counts = validate_catalog(bundle)
        self.assertEqual(counts["ingredients"], 8)
        self.assertEqual(counts["ingredient_rules"], 8)
        self.assertEqual(counts["rule_evidence"], 8)
        self.assertEqual(supplement_catalog(bundle), bundle)
        self.assertTrue(all(row["full_description"] for row in bundle["ingredients"]))
        self.assertTrue(all(row["jurisdiction"] == "RU" for row in bundle["ingredient_rules"]))
        self.assertTrue(all(row["pdf_page"] == 4 and row["locator"] for row in bundle["rule_evidence"]))
        self.assertEqual({r["ingredient_id"] for r in bundle["ingredient_references"]},
                         {r["id"] for r in bundle["ingredients"]})

    def test_existing_ids_rules_and_unrelated_data_are_preserved(self):
        original = supplement_catalog(empty_catalog())
        original["ingredients"][0]["id"] = "97d6c4c8-99ab-43a0-82fd-37c74b6f62ca"
        original["ingredients"][0]["is_active"] = False
        original["ingredients"][0]["full_description"] = "Old text"
        # Keep a realistic existing identity without its obsolete children for this fixture.
        existing = copy.deepcopy(original["ingredients"][0])
        original = empty_catalog()
        original["ingredients"] = [existing]
        before = copy.deepcopy(original)
        updated = supplement_catalog(original)
        self.assertEqual(original, before)
        row = next(r for r in updated["ingredients"] if r["e_code"] == "E121")
        self.assertEqual(row["id"], existing["id"])
        self.assertFalse(row["is_active"])
        self.assertEqual(len(updated["ingredients"]), 8)
        validate_catalog(updated)

    def test_plant_name_is_not_added_as_alias_for_dye(self):
        bundle = supplement_catalog(empty_catalog())
        dye = next(r for r in bundle["ingredients"] if r["e_code"] == "E123")
        aliases = [r["normalized_alias"] for r in bundle["ingredient_aliases"] if r["ingredient_id"] == dye["id"]]
        self.assertNotIn("амарант", aliases)
        self.assertNotIn("amaranth", aliases)
        self.assertIn("краситель амарант", aliases)

    def test_historical_publication_is_not_promoted_to_verified_current_law(self):
        for row in supplement_catalog(empty_catalog())["ingredient_rules"]:
            self.assertTrue(row["primary_basis_required"])
            self.assertFalse(row["production_ready"])
            self.assertIsNone(row["effective_from"])
            rule = IngredientRuleOut(id=row["id"], title=row["title"], rule_type="regulatory_status",
                severity="attention", conditions={"regulatory_status": "BANNED", "primary_basis_required": True})
            assessed = IngredientResolver.assess_rules([rule], "e_code")[0]
            self.assertEqual(assessed.assessment_severity, "forbidden")
            self.assertTrue(assessed.conditions["primary_basis_required"])


class SupplementWriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_requires_backup_and_verifies_transaction(self):
        connection = MagicMock()
        connection.transaction.return_value = AsyncMock()
        connection.execute = AsyncMock()
        connection.executemany = AsyncMock()
        before = empty_catalog()
        after = supplement_catalog(before)
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "backup.json"

            async def check_backup(*args):
                self.assertTrue(backup.exists())

            connection.executemany.side_effect = check_backup
            with patch("app.db.apply_catalog_supplement.snapshot", new=AsyncMock(side_effect=[before, after])):
                report = await update(connection, True, backup)
            self.assertEqual(len(report["new_codes"]), 8)
            for call in connection.executemany.await_args_list:
                self.assertTrue(call.args[0].startswith("INSERT INTO catalog."))
                self.assertNotIn("DELETE", call.args[0])
            connection.executemany.reset_mock()
            with patch("app.db.apply_catalog_supplement.snapshot", new=AsyncMock(return_value=after)):
                report = await update(connection, False, backup)
            connection.executemany.assert_not_awaited()
            self.assertFalse(any(report["changed_rows"].values()))
            self.assertFalse(any(delta(after, supplement_catalog(after)).values()))


if __name__ == "__main__":
    unittest.main()
