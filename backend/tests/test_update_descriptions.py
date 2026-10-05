import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

from app.db.update_descriptions import (
    DESCRIPTIONS, plan_changes, read_json, update_bundle, update_database, validate_descriptions,
)


class DescriptionTests(unittest.TestCase):
    def setUp(self):
        self.descriptions = {"E202": read_json(DESCRIPTIONS)["E202"]}
        self.row = {"id": "one", "e_code": "E202", "canonical_name_ru": "Name",
                    "description": "Brief", "full_description": "Previous text"}
        self.expected = {"E202": copy.deepcopy(self.row)}

    def test_all_reviewed_descriptions_are_standalone(self):
        descriptions = read_json(DESCRIPTIONS)
        self.assertEqual(len(descriptions), 311)
        validate_descriptions(descriptions)

    def test_stale_or_missing_row_aborts(self):
        with self.assertRaises(ValueError):
            plan_changes([], self.descriptions, self.expected)
        self.row["full_description"] = "Concurrent edit"
        with self.assertRaises(ValueError):
            plan_changes([self.row], self.descriptions, self.expected)
        self.row["full_description"] = "Previous text"
        self.row["id"] = "another-database"
        with self.assertRaises(ValueError):
            plan_changes([self.row], self.descriptions, self.expected)

    def test_update_is_idempotent(self):
        self.assertEqual(plan_changes([self.row], self.descriptions, self.expected),
                         [("one", self.descriptions["E202"])])
        self.row["full_description"] = self.descriptions["E202"]
        self.assertEqual(plan_changes([self.row], self.descriptions, self.expected), [])

    def test_only_description_fields_change_in_local_export(self):
        original = {"ingredients": [self.row, {"e_code": None, "full_description": None}],
                    "source_fragments": [{"record_kind": "additive_profile", "e_code": "E202",
                                          "full_description": "Old"}],
                    "ingredient_aliases": [{"alias": "E 202"}], "ingredient_rules": [{"title": "Rule"}]}
        before = copy.deepcopy(original)
        updated = update_bundle(original, self.descriptions)
        expected = copy.deepcopy(original)
        expected["ingredients"][0]["full_description"] = self.descriptions["E202"]
        expected["source_fragments"][0]["full_description"] = self.descriptions["E202"]
        self.assertEqual(updated, expected)
        self.assertEqual(original, before)


class DatabaseDescriptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_never_writes_and_apply_backs_up(self):
        descriptions = {"E202": read_json(DESCRIPTIONS)["E202"]}
        old = {"id": "one", "e_code": "E202", "canonical_name_ru": "Name",
               "description": "Brief", "full_description": "Old"}
        new = dict(old, full_description=descriptions["E202"])
        connection = MagicMock()
        connection.transaction.return_value = AsyncMock()
        connection.execute = AsyncMock()
        connection.executemany = AsyncMock()
        connection.fetchval = AsyncMock(return_value=1)
        connection.fetch = AsyncMock(return_value=[old])
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "backup.json"
            preview = await update_database(connection, descriptions, {"E202": old}, apply=False, backup_path=backup)
            self.assertEqual(preview["changed"], 1)
            self.assertFalse(backup.exists())
            connection.executemany.assert_not_awaited()
            connection.fetch.side_effect = [[old], [new]]

            async def assert_backup_exists(*args):
                self.assertEqual(read_json(backup)["ingredients"], [old])

            connection.executemany.side_effect = assert_backup_exists
            await update_database(connection, descriptions, {"E202": old}, apply=True, backup_path=backup)
            connection.executemany.assert_awaited_once_with(
                "UPDATE catalog.ingredients SET full_description = $2 WHERE id = $1",
                [("one", descriptions["E202"])],
            )

    async def test_unexpected_change_raises_inside_transaction(self):
        descriptions = {"E202": read_json(DESCRIPTIONS)["E202"]}
        old = {"id": "one", "e_code": "E202", "full_description": "Old"}
        connection = MagicMock()
        transaction = AsyncMock()
        connection.transaction.return_value = transaction
        connection.execute = AsyncMock()
        connection.executemany = AsyncMock()
        connection.fetchval = AsyncMock(return_value=1)
        connection.fetch = AsyncMock(side_effect=[[old], [dict(old, full_description="Wrong")]])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                await update_database(connection, descriptions, {"E202": old}, apply=True,
                                      backup_path=Path(directory) / "backup.json")
        self.assertIs(transaction.__aexit__.call_args.args[0], ValueError)


if __name__ == "__main__":
    unittest.main()
