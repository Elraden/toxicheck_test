"""Install the bundled catalog once, without replacing an existing catalog."""

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app.db.build_catalog import DEFAULT_OUTPUT, SCHEMA_VERSION, validate_catalog
from app.db.import_catalog import STORED_COLUMNS, sql_value


class CatalogBootstrapError(ValueError):
    """A safe, actionable error that contains no connection credentials."""


@dataclass(frozen=True)
class CatalogSeed:
    path: Path
    sha256: str
    bundle: dict
    counts: dict[str, int]


def load_seed(path: Path = DEFAULT_OUTPUT) -> CatalogSeed:
    try:
        raw = path.read_bytes()
    except OSError:
        raise CatalogBootstrapError(
            "В образе отсутствует доступный toxicheck_catalog.json. Пересоберите образ API."
        ) from None
    try:
        bundle = json.loads(raw.decode("utf-8-sig"))
        validated = validate_catalog(bundle)
        if not all(validated[t] for t in (
            "ingredients", "ingredient_aliases", "ingredient_rules", "regulatory_sources"
        )) or not any(row["is_active"] for row in bundle["ingredients"]):
            raise ValueError("Empty seed")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise CatalogBootstrapError(
            "Снимок справочника не прошёл проверку: нужна полная версия 2 с активными ингредиентами, "
            "синонимами, правилами и источниками. Пересоберите образ с корректным снимком."
        ) from None
    return CatalogSeed(path, hashlib.sha256(raw).hexdigest(), bundle,
                       {name: validated[name] for name in STORED_COLUMNS})


async def check_existing_schema(connection) -> None:
    """Check before DDL: never disguise an unknown version as a supported one."""
    if await connection.fetchval("SELECT to_regclass('public.ingredients') IS NOT NULL"):
        raise CatalogBootstrapError(
            "Обнаружен старый public.ingredients. Нужна явная миграция; автоматическая загрузка отменена."
        )
    versions = []
    if await connection.fetchval("SELECT to_regclass('catalog.schema_version') IS NOT NULL"):
        versions = [r["version"] for r in await connection.fetch(
            "SELECT version FROM catalog.schema_version ORDER BY version"
        )]
    # schema_catalog.sql explicitly migrates v1 to v2 (nullable E-code).
    if versions not in ([], [1], [SCHEMA_VERSION]):
        raise CatalogBootstrapError(
            "Несовместимая версия catalog.schema_version. Автоматическая миграция отменена; "
            "существующие данные сохранены."
        )
    if not versions and await connection.fetchval(
        "SELECT to_regclass('catalog.ingredients') IS NOT NULL"
    ) and await connection.fetchval("SELECT EXISTS (SELECT 1 FROM catalog.ingredients)"):
        raise CatalogBootstrapError(
            "В каталоге есть ингредиенты, но нет версии схемы. Нужна проверка миграции вручную."
        )


async def catalog_counts(connection) -> dict[str, int]:
    # Table names come only from the application allowlist, never from the JSON.
    return {table: await connection.fetchval(f"SELECT count(*) FROM catalog.{table}")
            for table in STORED_COLUMNS}


async def bootstrap_catalog(connection, seed: CatalogSeed) -> dict:
    """Called inside initialize's transaction and shared advisory lock.

    An existing active catalog is authoritative, even if it differs from the
    release snapshot. A partially populated, inactive catalog needs manual review.
    This path never updates, prunes or truncates catalog or user records.
    """
    if not connection.is_in_transaction():
        raise RuntimeError("Catalog bootstrap requires an enclosing transaction")
    counts = await catalog_counts(connection)
    if any(counts.values()):
        if await connection.fetchval("SELECT EXISTS (SELECT 1 FROM catalog.ingredients WHERE is_active)"):
            return {"status": "preserved", "counts": counts}
        raise CatalogBootstrapError(
            "Каталог заполнен частично или все ингредиенты неактивны. Автоматическое заполнение "
            "отменено, чтобы не перезаписать данные. Проверьте каталог и восстановите корректный снимок."
        )

    for table, columns in STORED_COLUMNS.items():
        pk = "rule_id" if table == "rule_transitions" else "ingredient_id" if table == "ingredient_matches" else "id"
        placeholders = ", ".join(f"${i}" for i in range(1, len(columns) + 1))
        await connection.executemany(
            f"INSERT INTO catalog.{table} ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT ({pk}) DO NOTHING",
            [tuple(sql_value(key, row[key]) for key in columns) for row in seed.bundle[table]],
        )
    actual = await catalog_counts(connection)
    if actual != seed.counts:
        raise CatalogBootstrapError("Количество записей не совпало со снимком. Загрузка отменена целиком.")
    await connection.execute(
        "INSERT INTO catalog.import_runs (input_filename, input_sha256, ingredient_count, rule_count) "
        "VALUES ($1, $2, $3, $4)",
        seed.path.name, seed.sha256, actual["ingredients"], actual["ingredient_rules"],
    )
    return {"status": "loaded", "counts": actual, "sha256": seed.sha256}


def main():
    parser = argparse.ArgumentParser(description="Validate the bundled bootstrap catalog without database access")
    parser.add_argument("--file", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        seed = load_seed(args.file)
    except CatalogBootstrapError as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps({"schema_version": SCHEMA_VERSION, "sha256": seed.sha256, "counts": seed.counts}))


if __name__ == "__main__":
    main()
