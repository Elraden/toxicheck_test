"""Load a complete flat catalog into its dedicated PostgreSQL database."""

import argparse
import asyncio
import hashlib
import json
import os
from datetime import date, datetime
from decimal import Decimal
from getpass import getpass
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
from dotenv import dotenv_values

from app.db.build_catalog import COLUMNS, DEFAULT_OUTPUT, ROOT, SCHEMA_VERSION, validate_catalog

SCHEMA = ROOT / "backend/db/schema_catalog.sql"
URL_VARIABLE = "TOXICHECK_CATALOG_DATABASE_URL"


def configured_database_url(env_file=ROOT / "backend/.env"):
    # Read only the dedicated catalog variable; do not load/override app settings.
    if URL_VARIABLE in os.environ:
        return os.environ[URL_VARIABLE]
    return dotenv_values(env_file, interpolate=False).get(URL_VARIABLE)


def normalize_database_url(value):
    message = (
        "Вставьте полное значение DATABASE_PUBLIC_URL из Railway: "
        "postgresql://USER:PASSWORD@HOST:PORT/DATABASE. "
        "Нужна вся ссылка, а не имя переменной, отдельный пароль или адрес сервера."
    )
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    if value.startswith("postgresql+asyncpg://"):
        value = "postgresql://" + value[len("postgresql+asyncpg://"):]
    # Reject pasted control characters before urlsplit can silently remove them.
    if not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(message)
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"postgresql", "postgres"} and parsed.hostname
                 and parsed.username and parsed.path not in {"", "/"}
                 and not parsed.fragment and (parsed.port is None or parsed.port > 0))
    except ValueError:
        # URL parsing errors can contain credential fragments: never echo them.
        raise ValueError(message) from None
    if not valid:
        raise ValueError(message)
    return value


def sql_value(key, value):
    if value is None:
        return None
    if key in {"version_date", "effective_from", "effective_to", "last_production_date", "transition_end"}:
        return date.fromisoformat(value)
    if key == "fetched_at":
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    if key == "confidence":
        return Decimal(str(value))
    return value


async def import_catalog(connection, bundle, *, filename, file_sha256, replace_data=False):
    counts = validate_catalog(bundle)
    async with connection.transaction():
        await connection.execute("SELECT pg_advisory_xact_lock(873024120)")
        if await connection.fetchval("SELECT to_regclass('public.ingredients') IS NOT NULL"):
            raise ValueError("This is a legacy application database; use a new empty database")
        if await connection.fetchval("SELECT to_regclass('catalog.schema_version') IS NOT NULL"):
            versions = await connection.fetch("SELECT version FROM catalog.schema_version")
            if [r["version"] for r in versions] not in ([1], [SCHEMA_VERSION]):
                raise ValueError("Unsupported database schema version")
        await connection.execute(SCHEMA.read_text(encoding="utf-8"))
        if replace_data:
            # No CASCADE: an unexpected external dependency must abort the reset.
            tables = ", ".join(f"catalog.{table}" for table in COLUMNS)
            await connection.execute(f"TRUNCATE TABLE {tables} RESTRICT")
        for table, columns in COLUMNS.items():
            pk = "rule_id" if table == "rule_transitions" else "ingredient_id" if table == "ingredient_matches" else "id"
            column_sql = ", ".join(columns)
            placeholders = ", ".join(f"${i}" for i in range(1, len(columns) + 1))
            updates = ", ".join(f"{key} = EXCLUDED.{key}" for key in columns if key != pk)
            await connection.executemany(
                f"INSERT INTO catalog.{table} ({column_sql}) VALUES ({placeholders}) "
                f"ON CONFLICT ({pk}) DO UPDATE SET {updates}",
                [tuple(sql_value(key, row[key]) for key in columns) for row in bundle[table]],
            )
        # A full snapshot replaces obsolete catalog rows, including removed rules.
        # Children are pruned first; user/application tables are never touched.
        for table in reversed(COLUMNS):
            pk = "rule_id" if table == "rule_transitions" else "ingredient_id" if table == "ingredient_matches" else "id"
            await connection.execute(f"DELETE FROM catalog.{table} WHERE NOT ({pk} = ANY($1::uuid[]))",
                                     [row[pk] for row in bundle[table]])
        await connection.execute(
            "INSERT INTO catalog.import_runs (input_filename, input_sha256, ingredient_count, rule_count) "
            "VALUES ($1, $2, $3, $4)", filename, file_sha256, counts["ingredients"], counts["ingredient_rules"],
        )
        for table, expected in counts.items():
            actual = await connection.fetchval(f"SELECT count(*) FROM catalog.{table}")
            if actual != expected:
                raise ValueError(f"Unexpected row count in {table}: {actual} != {expected}")
    return counts


async def run_import(url, bundle, filename, file_sha256, *, replace_data=False):
    url = normalize_database_url(url)
    connection = await asyncpg.connect(url, timeout=30)
    try:
        return await import_catalog(connection, bundle, filename=filename, file_sha256=file_sha256,
                                    replace_data=replace_data)
    finally:
        await connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--prompt-url", action="store_true", help="Read the new database URL without echoing it")
    parser.add_argument("--replace-data", action="store_true",
                        help="Empty catalog data tables before import in the same transaction; retain import history")
    args = parser.parse_args()
    raw = args.file.read_bytes()
    bundle = json.loads(raw.decode("utf-8-sig"))
    counts = validate_catalog(bundle)
    if args.validate_only:
        print(json.dumps(counts, ensure_ascii=False, indent=2))
        return
    url = getpass("New catalog database URL (hidden): ").strip() if args.prompt_url else configured_database_url()
    if not url:
        parser.error(f"Use --prompt-url or set {URL_VARIABLE} in the environment or backend/.env; DATABASE_URL is not used")
    try:
        url = normalize_database_url(url)
    except ValueError as error:
        parser.error(str(error))
    counts = asyncio.run(run_import(url, bundle, args.file.name, hashlib.sha256(raw).hexdigest(),
                                   replace_data=args.replace_data))
    print("Catalog imported successfully: " + json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
