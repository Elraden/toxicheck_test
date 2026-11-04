"""Preview or apply reviewed additions without replacing/deleting catalog rows.

From backend: python -m app.db.apply_catalog_supplement [--apply]
--sync-local updates the flat export and matching descriptions in source exports.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import json

import asyncpg

from app.db.build_catalog import COLUMNS, ROOT, validate_catalog
from app.db.catalog_supplements import SUPPLEMENT, supplement_catalog
from app.db.import_catalog import configured_database_url, normalize_database_url, sql_value
from app.db.update_descriptions import backup_json, read_json
from app.db.schema_contract import CATALOG_DATA_TABLES

BACKUPS = ROOT / ".catalog-supplement-backups.local"
TABLES = ("regulatory_sources", "ingredients", "ingredient_aliases", "ingredient_rules", "rule_evidence", "ingredient_tags")


def delta(before, after):
    changes = {}
    for table in TABLES:
        old = {row["id"]: row for row in before[table]}
        changes[table] = [row for row in after[table] if row != old.get(row["id"])]
        if not old.keys() <= {row["id"] for row in after[table]}:
            raise ValueError(f"Supplement must not delete rows: {table}")
    return changes


async def snapshot(connection):
    bundle = {}
    for table, columns in COLUMNS.items():
        if table not in CATALOG_DATA_TABLES:
            bundle[table] = []
            continue
        rows = await connection.fetch(f"SELECT {', '.join(columns)} FROM catalog.{table}")
        bundle[table] = json.loads(json.dumps([dict(row) for row in rows], default=str))
    bundle["metadata"] = dict(dataset="toxicheck_catalog", schema_version=2)
    validate_catalog(bundle, require_parser_profiles=False)
    return bundle


async def update(connection, apply, backup_path):
    async with connection.transaction():
        await connection.execute("SET LOCAL lock_timeout = '10s'")
        await connection.execute("SET LOCAL statement_timeout = '60s'")
        if apply:
            await connection.execute("SELECT pg_advisory_xact_lock(873024120)")
            await connection.execute("LOCK TABLE " + ", ".join(f"catalog.{t}" for t in TABLES)
                                     + " IN SHARE ROW EXCLUSIVE MODE")
        before = await snapshot(connection)
        after = supplement_catalog(before)
        counts = validate_catalog(after, require_parser_profiles=False)
        changes = delta(before, after)
        existing = {row["e_code"] for row in before["ingredients"]}
        seed = read_json(SUPPLEMENT)
        report = dict(mode="apply" if apply else "preview",
                      new_codes=[i["e_code"] for i in seed["ingredients"] if i["e_code"] not in existing],
                      changed_rows={table: len(rows) for table, rows in changes.items()},
                      ingredient_count=counts["ingredients"])
        if apply and any(changes.values()):
            backup_json(backup_path, before)
            for table, rows in changes.items():
                if not rows:
                    continue
                columns = COLUMNS[table]
                placeholders = ", ".join(f"${n}" for n in range(1, len(columns) + 1))
                updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != "id")
                await connection.executemany(
                    f"INSERT INTO catalog.{table} ({', '.join(columns)}) VALUES ({placeholders}) "
                    f"ON CONFLICT (id) DO UPDATE SET {updates}",
                    [tuple(sql_value(key, row[key]) for key in columns) for row in rows],
                )
            actual = await snapshot(connection)
            for table in CATALOG_DATA_TABLES:
                pk = "rule_id" if table == "rule_transitions" else "ingredient_id" if table == "ingredient_matches" else "id"
                # asyncpg serializes NUMERIC as Decimal; compare canonical numeric values.
                def keyed(rows):
                    return {r[pk]: {k: float(v) if k == "confidence" else v for k, v in r.items()} for r in rows}
                if keyed(actual[table]) != keyed(after[table]):
                    raise ValueError(f"Verification failed for {table}; rolling back")
            report["backup"] = str(backup_path)
        return report


def sync_local(backup_dir):
    seed = read_json(SUPPLEMENT)
    items = {i["e_code"]: i for i in seed["ingredients"]}
    catalog_path = ROOT / "toxicheck_catalog.json"
    catalog = supplement_catalog(read_json(catalog_path))
    validate_catalog(catalog)
    updates = {catalog_path: catalog}
    for name in ("toxicheck_proe.json", "toxicheck_proe_merged.json"):
        path = ROOT / name
        bundle = read_json(path)
        for table in ("ingredients", "source_fragments"):
            for row in bundle.get(table, []):
                item = items.get(row.get("e_code"))
                if item and row.get("full_description") and (table == "ingredients" or row.get("record_kind") == "additive_profile"):
                    row.update(description=item["description"], full_description=item["full_description"])
        updates[path] = bundle
    for name in ("ingredient_descriptions.json", "proe_descriptions.json"):
        path = ROOT / "backend/db" / name
        descriptions = read_json(path)
        for code in descriptions.keys() & items.keys():
            descriptions[code] = items[code]["full_description"]
        updates[path] = descriptions
    changed = {p: value for p, value in updates.items() if read_json(p) != value}
    for path in changed:
        backup_json(backup_dir / path.name, read_json(path))
    for path, value in changed.items():
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"synchronized": [str(p.relative_to(ROOT)) for p in changed]}


async def run(apply, backup_path):
    connection = await asyncpg.connect(normalize_database_url(configured_database_url()), timeout=30)
    try:
        return await update(connection, apply, backup_path)
    finally:
        await connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true")
    group.add_argument("--sync-local", action="store_true")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report = (sync_local(BACKUPS / stamp) if args.sync_local
              else asyncio.run(run(args.apply, BACKUPS / f"remote-{stamp}.json")))
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
