import asyncio
import copy
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg

from app.db.build_catalog import COLUMNS, DEFAULT_INPUT, build_catalog, clean_description, validate_catalog
from app.db.import_catalog import configured_database_url, import_catalog, normalize_database_url, run_import, STORED_COLUMNS


class CatalogConnectionInputTests(unittest.TestCase):
    def test_dedicated_env_file_url_and_environment_priority(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://main"}, clear=True), \
                patch("app.db.import_catalog.dotenv_values") as read_env:
            read_env.return_value = {"DATABASE_URL": "postgresql://main"}
            self.assertIsNone(configured_database_url(Path("unused.env")))
            read_env.return_value["TOXICHECK_CATALOG_DATABASE_URL"] = "postgresql://catalog/${UNCHANGED}"
            self.assertEqual(configured_database_url(Path("unused.env")), "postgresql://catalog/${UNCHANGED}")
            read_env.assert_called_with(Path("unused.env"), interpolate=False)
            read_env.reset_mock()
            os.environ["TOXICHECK_CATALOG_DATABASE_URL"] = "postgresql://explicit"
            self.assertEqual(configured_database_url(Path("unused.env")), "postgresql://explicit")
            read_env.assert_not_called()

    def test_complete_urls_and_wrapping_quotes(self):
        url = "postgresql://postgres:test-password@db.example:5432/railway?sslmode=require"
        self.assertEqual(normalize_database_url("  '" + url + "'  "), url)
        self.assertEqual(normalize_database_url(url.replace("postgresql://", "postgresql+asyncpg://")), url)

    def test_bad_paste_has_actionable_error_without_credentials(self):
        for value in ("", "DATABASE_PUBLIC_URL", "db.example:24753", "test-password", "\x16",
                      "postgresql://postgres:test-password@db.example:invalid/railway",
                      "postgresql://postgres:test-password@db.example/railway\nextra"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as caught:
                    normalize_database_url(value)
                self.assertIn("DATABASE_PUBLIC_URL", str(caught.exception))
                self.assertNotIn("test-password", str(caught.exception))

    def test_invalid_url_never_connects(self):
        with patch("app.db.import_catalog.asyncpg.connect", new_callable=AsyncMock) as connect:
            with self.assertRaises(ValueError):
                asyncio.run(run_import("db.example:24753", {}, "unused.json", "0" * 64))
            connect.assert_not_called()


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = json.loads(DEFAULT_INPUT.read_text(encoding="utf-8"))
        cls.catalog = build_catalog(cls.original)

    def test_flat_english_fields_and_unchanged_user_names(self):
        original = copy.deepcopy(self.original)
        original["ingredients"][0]["canonical_name_ru"] = "Название исправлено пользователем"
        for rule in original["ingredient_rules"]:
            c = rule["conditions"]
            if "Основные технологи- ческие функции" in c:
                c["Основные технологические функции"] = c.pop("Основные технологи- ческие функции")
        result = build_catalog(original)
        self.assertEqual(result["ingredients"][0]["canonical_name_ru"], "Название исправлено пользователем")
        for table in COLUMNS:
            for row in result[table]:
                self.assertTrue(all(key.isascii() for key in row))
                self.assertFalse(any(isinstance(value, (dict, list)) for value in row.values()))
        self.assertNotIn("source_fragments", result)

    def test_evidence_and_transition_terms_survive(self):
        self.assertEqual(len(self.catalog["rule_evidence"]),
                         sum(len(r["conditions"]["evidence"]) for r in self.original["ingredient_rules"]))
        transitions = {r["rule_id"]: r for r in self.catalog["rule_transitions"]}
        for rule in self.original["ingredient_rules"]:
            transition = rule["conditions"].get("transition")
            if transition:
                for field in ("last_production_date", "transition_end", "requires_preexisting_conformity_documents", "legacy_circulation_until_expiry"):
                    self.assertEqual(transitions[rule["id"]][field], transition[field])
        self.assertEqual({r["id"]: r["full_description"] for r in self.catalog["ingredients"]},
                         {r["id"]: clean_description(r["full_description"]) for r in self.original["ingredients"]})

    def test_secondary_source_is_removed_without_reattributing_documents(self):
        self.assertFalse(any(s["code"] == "PROE_INFO" for s in self.catalog["regulatory_sources"]))
        self.assertTrue(all(p["source_id"] is None and p["source_url"] is None
                            for p in self.catalog["ingredient_profiles"]))
        self.assertTrue(all(d["source_id"] is None for d in self.catalog["documents"]))
        for table in COLUMNS:
            for row in self.catalog[table]:
                self.assertNotRegex(json.dumps(row, ensure_ascii=False).lower(), r"proe|прое")
        bad = copy.deepcopy(self.catalog)
        bad["ingredient_aliases"][0]["source"] = "PROE_INFO"
        with self.assertRaises(ValueError):
            validate_catalog(bad)

    def test_attributed_sentence_is_removed_not_presented_as_fact(self):
        self.assertEqual(clean_description("Описание. proE утверждает обратное. Требует проверки."),
                         "Описание. Требует проверки.")

    def test_invalid_status_nested_data_or_missing_evidence_are_rejected(self):
        for mutation in (lambda b: b["ingredient_rules"][0].update(regulatory_status="ATTENTION"),
                         lambda b: b["ingredient_rules"][0].update(explanation={"nested": True}),
                         lambda b: b["rule_evidence"].clear(),
                         lambda b: b["rule_transitions"].clear(),
                         lambda b: b["ingredient_aliases"][0].update(ingredient_id="00000000-0000-0000-0000-000000000000")):
            bundle = copy.deepcopy(self.catalog)
            mutation(bundle)
            with self.assertRaises(ValueError):
                validate_catalog(bundle)

    def test_stable_ids_and_no_mutation(self):
        original = copy.deepcopy(self.original)
        again = build_catalog(original)
        self.assertEqual(original, self.original)
        for table in COLUMNS:
            self.assertEqual(again[table], self.catalog[table])

    def test_explicit_reset_is_transactional_and_keeps_import_history(self):
        connection = MagicMock()
        transaction = MagicMock()
        transaction.__aenter__ = AsyncMock()
        transaction.__aexit__ = AsyncMock(return_value=False)
        connection.transaction.return_value = transaction
        connection.fetchval = AsyncMock(side_effect=[False, False] +
                                       [len(self.catalog[t]) for t in STORED_COLUMNS])
        connection.execute = AsyncMock()
        connection.executemany = AsyncMock()
        asyncio.run(import_catalog(connection, self.catalog, filename="test.json",
                                   file_sha256="0" * 64, replace_data=True))
        sql = [call.args[0] for call in connection.execute.await_args_list]
        truncate = next(s for s in sql if s.startswith("TRUNCATE"))
        self.assertEqual(truncate, "TRUNCATE TABLE " +
                         ", ".join(f"catalog.{t}" for t in STORED_COLUMNS) + " RESTRICT")
        self.assertNotIn("import_runs", truncate)
        transaction.__aenter__.assert_awaited_once()
        transaction.__aexit__.assert_awaited_once_with(None, None, None)
        connection.transaction.assert_called_once()

        connection.reset_mock()
        connection.fetchval.side_effect = [False, False]
        connection.executemany.side_effect = RuntimeError("insertion failed")
        with self.assertRaisesRegex(RuntimeError, "insertion failed"):
            asyncio.run(import_catalog(connection, self.catalog, filename="test.json",
                                       file_sha256="0" * 64, replace_data=True))
        self.assertIs(transaction.__aexit__.await_args.args[0], RuntimeError)
        self.assertFalse(any("INSERT INTO catalog.import_runs" in call.args[0]
                             for call in connection.execute.await_args_list))


@unittest.skipUnless(os.environ.get("TOXICHECK_TEST_POSTGRES_BIN"), "Set TOXICHECK_TEST_POSTGRES_BIN to run an isolated temporary PostgreSQL")
class CatalogPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bin = Path(os.environ["TOXICHECK_TEST_POSTGRES_BIN"])
        cls.directory = Path(tempfile.mkdtemp(prefix="toxicheck-catalog-test-"))
        cls.started = False
        cls.addClassCleanup(cls.cleanup)
        cls.data = cls.directory / "data"
        cls.run_pg("initdb", "-D", str(cls.data), "-U", "postgres", "--auth=trust", "--encoding=UTF8", "--locale=C")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.started = True
        cls.run_pg("pg_ctl", "-D", str(cls.data), "-l", str(cls.directory / "postgres.log"),
                   "-o", f"-h 127.0.0.1 -p {cls.port} -F", "-w", "-t", "20", "start")

    @classmethod
    def run_pg(cls, program, *args):
        executable = cls.bin / (program + (".exe" if os.name == "nt" else ""))
        # A Windows server process may inherit pipe handles and prevent
        # communicate() from returning after pg_ctl exits. Use a real file.
        log = cls.directory / (program + ".log")
        with log.open("wb") as output:
            result = subprocess.run([str(executable), *args], stdout=output, stderr=subprocess.STDOUT,
                                    timeout=45, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError(log.read_text(encoding="utf-8", errors="replace"))

    @classmethod
    def cleanup(cls):
        if cls.started:
            cls.run_pg("pg_ctl", "-D", str(cls.data), "-m", "fast", "-w", "stop")
        directory = cls.directory.resolve()
        if directory.parent != Path(tempfile.gettempdir()).resolve() or not directory.name.startswith("toxicheck-catalog-test-"):
            raise ValueError("Refusing to remove a path outside the test temporary directory")
        shutil.rmtree(directory)

    def test_real_postgres_schema_import_repeat_pruning_and_rollback(self):
        asyncio.run(self.check_database())

    async def check_database(self):
        bundle = build_catalog(json.loads(DEFAULT_INPUT.read_text(encoding="utf-8")))
        connection = await asyncpg.connect(host="127.0.0.1", port=self.port, user="postgres", database="postgres")
        kwargs = dict(filename="test.json", file_sha256="0" * 64)
        try:
            await import_catalog(connection, bundle, **kwargs)
            await import_catalog(connection, bundle, **kwargs)
            self.assertEqual(await connection.fetchval("SELECT count(*) FROM catalog.import_runs"), 2)
            self.assertEqual(await connection.fetchval(
                "SELECT count(*) FROM information_schema.columns WHERE table_schema='catalog' AND data_type IN ('json', 'jsonb')"), 0)
            bad = copy.deepcopy(bundle)
            bad["ingredients"][0]["canonical_name_ru"] = "Must roll back"
            bad["ingredient_aliases"][0]["alias"] = None
            with self.assertRaises(asyncpg.NotNullViolationError):
                await import_catalog(connection, bad, **kwargs)
            self.assertEqual(await connection.fetchval("SELECT canonical_name_ru FROM catalog.ingredients WHERE id=$1", bundle["ingredients"][0]["id"]),
                             bundle["ingredients"][0]["canonical_name_ru"])
            self.assertEqual(await connection.fetchval("SELECT count(*) FROM catalog.import_runs"), 2)
            pruned = copy.deepcopy(bundle)
            rule_id = pruned["ingredient_rules"].pop()["id"]
            pruned["rule_evidence"] = [r for r in pruned["rule_evidence"] if r["rule_id"] != rule_id]
            pruned["rule_transitions"] = [r for r in pruned["rule_transitions"] if r["rule_id"] != rule_id]
            await import_catalog(connection, pruned, **kwargs)
            self.assertFalse(await connection.fetchval("SELECT EXISTS (SELECT 1 FROM catalog.ingredient_rules WHERE id=$1)", rule_id))
            await connection.execute("CREATE TABLE public.ingredients (id integer)")
            with self.assertRaisesRegex(ValueError, "legacy application database"):
                await import_catalog(connection, bundle, **kwargs)
        finally:
            await connection.close()


if __name__ == "__main__":
    unittest.main()
