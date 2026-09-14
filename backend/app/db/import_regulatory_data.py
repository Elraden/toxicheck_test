import argparse
import asyncio
import json
from getpass import getpass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg

from app.core.config import settings
from app.db.build_regulatory_data import OUTPUT_NAME, validate_bundle


BACKEND_DIR = Path(__file__).resolve().parents[2]
DATA_FILE_NAMES = (OUTPUT_NAME,)


def _get_asyncpg_url() -> str:
    return settings.database_url.replace(
        "postgresql+asyncpg://",
        "postgresql://",
        1,
    )


def _as_jsonb(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def _as_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _as_timestamp(value: str | None) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else datetime.now(timezone.utc)


async def _apply_schema(connection: asyncpg.Connection) -> None:
    schema_path = BACKEND_DIR / "db" / "schema.sql"
    await connection.execute(schema_path.read_text(encoding="utf-8"))

def _candidate_dirs() -> list[Path]:
    return [
        Path.cwd(),
        Path.cwd().parent,
        BACKEND_DIR,
        BACKEND_DIR.parent,
    ]


def _find_optional_file(file_name: str) -> Path:
    for directory in _candidate_dirs():
        path = directory / file_name
        if path.exists():
            return path

    return Path(file_name)


def _default_data_files() -> list[Path]:
    return [_find_optional_file(file_name) for file_name in DATA_FILE_NAMES]


def _load_payload(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    required_keys = {
        "regulatory_sources",
        "ingredients",
        "ingredient_aliases",
        "ingredient_rules",
    }
    missing = required_keys - payload.keys()
    if missing:
        raise ValueError(f"{path} is missing keys: {', '.join(sorted(missing))}")

    return payload


async def _upsert_regulatory_sources(
    connection: asyncpg.Connection,
    rows: list[dict[str, Any]],
) -> None:
    await connection.executemany(
        """
        INSERT INTO regulatory_sources (
          id, code, title, url, version_date, effective_from, effective_to,
          created_at, updated_at
        )
        VALUES (
          $1::uuid, $2, $3, $4, $5::date, $6::date, $7::date,
          $8::timestamptz, $9::timestamptz
        )
        ON CONFLICT (id) DO UPDATE SET
          code = EXCLUDED.code,
          title = EXCLUDED.title,
          url = EXCLUDED.url,
          version_date = EXCLUDED.version_date,
          effective_from = EXCLUDED.effective_from,
          effective_to = EXCLUDED.effective_to,
          updated_at = EXCLUDED.updated_at
        """,
        [
            (
                row["id"],
                row["code"],
                row["title"],
                row.get("url"),
                _as_date(row.get("version_date")),
                _as_date(row.get("effective_from")),
                _as_date(row.get("effective_to")),
                _as_timestamp(row.get("created_at")),
                _as_timestamp(row.get("updated_at")),
            )
            for row in rows
        ],
    )


async def _upsert_ingredients(
    connection: asyncpg.Connection,
    rows: list[dict[str, Any]],
) -> None:
    await connection.executemany(
        """
        INSERT INTO ingredients (
          id, kind, e_code, canonical_name_ru, canonical_name_en, category,
          description, is_active, created_at, updated_at
        )
        VALUES (
          $1::uuid, $2, $3, $4, $5, $6, $7, $8,
          $9::timestamptz, $10::timestamptz
        )
        ON CONFLICT (id) DO UPDATE SET
          kind = EXCLUDED.kind,
          e_code = EXCLUDED.e_code,
          canonical_name_ru = EXCLUDED.canonical_name_ru,
          canonical_name_en = EXCLUDED.canonical_name_en,
          category = EXCLUDED.category,
          description = EXCLUDED.description,
          is_active = EXCLUDED.is_active,
          updated_at = EXCLUDED.updated_at
        """,
        [
            (
                row["id"],
                row["kind"],
                row.get("e_code"),
                row["canonical_name_ru"],
                row.get("canonical_name_en"),
                row.get("category"),
                row.get("description"),
                row.get("is_active", True),
                _as_timestamp(row.get("created_at")),
                _as_timestamp(row.get("updated_at")),
            )
            for row in rows
        ],
    )


async def _upsert_aliases(
    connection: asyncpg.Connection,
    rows: list[dict[str, Any]],
) -> None:
    await connection.executemany(
        """
        INSERT INTO ingredient_aliases (
          id, ingredient_id, alias, normalized_alias, language, source,
          confidence, created_at, updated_at
        )
        VALUES (
          $1::uuid, $2::uuid, $3, $4, $5, $6, $7::numeric(3, 2),
          $8::timestamptz, $9::timestamptz
        )
        ON CONFLICT (id) DO UPDATE SET
          ingredient_id = EXCLUDED.ingredient_id,
          alias = EXCLUDED.alias,
          normalized_alias = EXCLUDED.normalized_alias,
          language = EXCLUDED.language,
          source = EXCLUDED.source,
          confidence = EXCLUDED.confidence,
          updated_at = EXCLUDED.updated_at
        """,
        [
            (
                row["id"],
                row["ingredient_id"],
                row["alias"],
                row["normalized_alias"],
                row.get("language"),
                row.get("source"),
                Decimal(str(row.get("confidence", 1))),
                _as_timestamp(row.get("created_at")),
                _as_timestamp(row.get("updated_at")),
            )
            for row in rows
        ],
    )


async def _upsert_rules(
    connection: asyncpg.Connection,
    rows: list[dict[str, Any]],
) -> None:
    await connection.executemany(
        """
        INSERT INTO ingredient_rules (
          id, ingredient_id, source_id, rule_type, severity, title, explanation,
          conditions, citation, effective_from, effective_to, created_at, updated_at
        )
        VALUES (
          $1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8::jsonb, $9,
          $10::date, $11::date, $12::timestamptz, $13::timestamptz
        )
        ON CONFLICT (id) DO UPDATE SET
          ingredient_id = EXCLUDED.ingredient_id,
          source_id = EXCLUDED.source_id,
          rule_type = EXCLUDED.rule_type,
          severity = EXCLUDED.severity,
          title = EXCLUDED.title,
          explanation = EXCLUDED.explanation,
          conditions = EXCLUDED.conditions,
          citation = EXCLUDED.citation,
          effective_from = EXCLUDED.effective_from,
          effective_to = EXCLUDED.effective_to,
          updated_at = EXCLUDED.updated_at
        """,
        [
            (
                row["id"],
                row.get("ingredient_id"),
                row["source_id"],
                row["rule_type"],
                row["severity"],
                row["title"],
                row.get("explanation"),
                _as_jsonb(row.get("conditions")),
                row.get("citation"),
                _as_date(row.get("effective_from")),
                _as_date(row.get("effective_to")),
                _as_timestamp(row.get("created_at")),
                _as_timestamp(row.get("updated_at")),
            )
            for row in rows
        ],
    )


async def import_file(connection: asyncpg.Connection, path: Path) -> dict[str, int]:
    payload = _load_payload(path)

    async with connection.transaction():
        await _upsert_regulatory_sources(connection, payload["regulatory_sources"])
        await _upsert_ingredients(connection, payload["ingredients"])
        await _upsert_aliases(connection, payload["ingredient_aliases"])
        await _upsert_rules(connection, payload["ingredient_rules"])
        await connection.executemany(
            """INSERT INTO regulatory_source_fragments (id, source_id, payload)
               VALUES ($1::uuid, $2::uuid, $3::jsonb)
               ON CONFLICT (id) DO UPDATE SET source_id = EXCLUDED.source_id,
                 payload = EXCLUDED.payload, updated_at = now()""",
            [(row["id"], row["source_id"], _as_jsonb(row))
             for row in payload.get("source_fragments", [])],
        )
        # Retire only known extraction artifacts if an older dump was imported.
        await connection.executemany(
            "UPDATE ingredient_rules SET conditions = conditions || $2::jsonb WHERE id = $1::uuid",
            [(row["id"], _as_jsonb(row["conditions"])) for row in payload.get("source_fragments", [])],
        )
        await connection.executemany(
            "DELETE FROM ingredient_aliases WHERE id = $1::uuid",
            [(identifier,) for identifier in payload.get("retired_alias_ids", [])],
        )

    return {
        "regulatory_sources": len(payload["regulatory_sources"]),
        "ingredients": len(payload["ingredients"]),
        "ingredient_aliases": len(payload["ingredient_aliases"]),
        "ingredient_rules": len(payload["ingredient_rules"]),
        "regulatory_source_fragments": len(payload.get("source_fragments", [])),
    }


async def count_imported_rows(connection: asyncpg.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in (
        "regulatory_sources",
        "ingredients",
        "ingredient_aliases",
        "ingredient_rules",
        "regulatory_source_fragments",
    ):
        counts[table] = await connection.fetchval(f"SELECT count(*) FROM {table}")

    return counts


async def run_import(paths: list[Path], *, apply_schema: bool) -> None:
    for path in paths:
        payload = _load_payload(path)
        if payload.get("metadata", {}).get("dataset") == "toxicheck_regulatory_enriched":
            validate_bundle(payload)
    connection = await asyncpg.connect(_get_asyncpg_url(), timeout=30)

    try:
        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock(873024119)")
            if apply_schema:
                await _apply_schema(connection)
            for path in paths:
                stats = await import_file(connection, path)
                print(f"Staged {path.name}: " + ", ".join(f"{key}={value}" for key, value in stats.items()))

        counts = await count_imported_rows(connection)
        print(
            "Database totals: "
            + ", ".join(f"{table}={count}" for table, count in counts.items())
        )
    finally:
        await connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import ToxiCheck regulatory JSON datasets into PostgreSQL."
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="JSON files to import. Defaults to the enriched regulatory bundle.",
    )
    parser.add_argument(
        "--validate-only", action="store_true",
        help="Validate the enriched bundle without connecting to PostgreSQL.",
    )
    parser.add_argument(
        "--prompt-url", action="store_true",
        help="Read a connection URL without echoing or saving credentials.",
    )
    parser.add_argument(
        "--inspect", action="store_true",
        help="Only list existing public tables and their row counts; do not write.",
    )
    parser.add_argument(
        "--skip-schema",
        action="store_true",
        help="Do not apply backend/db/schema.sql before importing.",
    )

    return parser.parse_args()


async def inspect_database() -> None:
    connection = await asyncpg.connect(_get_asyncpg_url(), timeout=30)
    try:
        tables = await connection.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
        print(f"Connected. Public tables: {len(tables)}")
        for row in tables:
            name = row["tablename"]
            quoted = '"' + name.replace('"', '""') + '"'
            count = await connection.fetchval(f"SELECT count(*) FROM public.{quoted}")
            print(f"{name}: {count}")
    finally:
        await connection.close()


def main() -> None:
    args = parse_args()
    if args.prompt_url:
        settings.database_url = getpass("Database connection URL (hidden): ").strip()
    if args.inspect:
        asyncio.run(inspect_database())
        return
    paths = [path.resolve() for path in (args.files or _default_data_files())]
    if args.validate_only:
        for path in paths:
            report = validate_bundle(_load_payload(path))
            print(f"Valid {path.name}: rules={report['rules_with_evidence']}, review={report['rules_requiring_review']}")
        return
    asyncio.run(run_import(paths, apply_schema=not args.skip_schema))


if __name__ == "__main__":
    main()
