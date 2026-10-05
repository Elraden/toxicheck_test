"""Back up and update ingredient descriptions without replacing the catalog.

Run from backend: python -m app.db.update_descriptions [--apply] [--sync-local]
The default is a read-only preview. Local synchronization is a separate action.
"""

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import asyncpg

from app.db.build_catalog import COLUMNS, ROOT
from app.db.import_catalog import configured_database_url, normalize_database_url

DESCRIPTIONS = ROOT / "backend/db/ingredient_descriptions.json"
CATALOG = ROOT / "toxicheck_catalog.json"
BACKUPS = ROOT / ".description-backups.local"
META_TEXT = re.compile(
    r"\b(?:стать[яеию]|статьёй|статьей|карточк\w*|публикаци\w*|пересказ\w*|сайт\w*|proe)\b",
    re.IGNORECASE,
)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def validate_descriptions(descriptions):
    if not isinstance(descriptions, dict) or not descriptions:
        raise ValueError("Expected a non-empty description dictionary")
    for code, text in descriptions.items():
        if not re.fullmatch(r"E\d{3,4}[a-z]?(?:\([ivx]+\))?", code):
            raise ValueError(f"Invalid ingredient code: {code}")
        if not isinstance(text, str) or len(text.split()) < 20 or text != text.strip():
            raise ValueError(f"Incomplete ingredient description: {code}")
        if META_TEXT.search(text):
            raise ValueError(f"Description discusses a publication: {code}")


def plan_changes(rows, descriptions, expected):
    validate_descriptions(descriptions)
    by_code = {}
    for row in rows:
        code = row.get("e_code")
        if code in descriptions:
            if code in by_code:
                raise ValueError(f"Duplicate ingredient code: {code}")
            by_code[code] = row
    missing = descriptions.keys() - by_code.keys()
    if missing:
        raise ValueError("Missing ingredient codes: " + ", ".join(sorted(missing)))
    changes = []
    for code, text in descriptions.items():
        row = by_code[code]
        if row.get("full_description") == text:
            continue
        # Do not overwrite somebody else's newer edits or a different catalog.
        old = expected.get(code)
        if (not old or str(old["id"]) != str(row["id"])
                or old.get("full_description") != row.get("full_description")):
            raise ValueError(f"Description changed since the local snapshot: {code}")
        changes.append((row["id"], text))
    return changes


def backup_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2, default=str)
        file.write("\n")


async def update_database(connection, descriptions, expected, *, apply, backup_path):
    async with connection.transaction():
        await connection.execute("SET LOCAL lock_timeout = '10s'")
        await connection.execute("SET LOCAL statement_timeout = '60s'")
        if apply:
            await connection.execute("SELECT pg_advisory_xact_lock(873024120)")
        rows = [dict(row) for row in await connection.fetch(
            "SELECT id, e_code, canonical_name_ru, description, full_description "
            "FROM catalog.ingredients ORDER BY id" + (" FOR UPDATE" if apply else "")
        )]
        changes = plan_changes(rows, descriptions, expected)
        counts = {table: await connection.fetchval(f"SELECT count(*) FROM catalog.{table}")
                  for table in COLUMNS}
        report = {"mode": "applied" if apply else "preview", "total_ingredients": len(rows),
                  "reviewed_full_descriptions": len(descriptions), "changed": len(changes),
                  "already_current": len(descriptions) - len(changes), "table_counts": counts}
        if apply and changes:
            backup_json(backup_path, {"created_at": datetime.now(timezone.utc).isoformat(),
                                     "ingredients": rows, "table_counts": counts})
            await connection.executemany(
                "UPDATE catalog.ingredients SET full_description = $2 WHERE id = $1", changes,
            )
            updated = [dict(row) for row in await connection.fetch(
                "SELECT id, e_code, canonical_name_ru, description, full_description "
                "FROM catalog.ingredients ORDER BY id"
            )]
            for before, after in zip(rows, updated, strict=True):
                expected_row = dict(before)
                if before["e_code"] in descriptions:
                    expected_row["full_description"] = descriptions[before["e_code"]]
                if after != expected_row:
                    raise ValueError("Post-update verification failed; transaction rolled back")
            for table, count in counts.items():
                if await connection.fetchval(f"SELECT count(*) FROM catalog.{table}") != count:
                    raise ValueError(f"Unexpected row count change in {table}")
            report["backup"] = str(backup_path)
        return report


def update_bundle(bundle, descriptions):
    updated = deepcopy(bundle)
    for table in ("ingredients", "source_fragments"):
        for row in updated.get(table, []):
            code = row.get("e_code")
            if code in descriptions and (table == "ingredients" or row.get("record_kind") == "additive_profile"):
                row["full_description"] = descriptions[code]
    return updated


def sync_local(descriptions, backup_dir):
    validate_descriptions(descriptions)
    files = [CATALOG, ROOT / "toxicheck_proe.json", ROOT / "toxicheck_proe_merged.json",
             ROOT / "backend/db/proe_descriptions.json"]
    updates = []
    for path in files:
        original = read_json(path)
        if path.name == "proe_descriptions.json":
            if original.keys() != descriptions.keys():
                raise ValueError("Authored description codes differ from the reviewed set")
            updated = descriptions
        else:
            updated = update_bundle(original, descriptions)
        if updated != original:
            updates.append((path, original, updated))
    # Preserve all originals before writing any regenerated export.
    for path, original, _ in updates:
        backup_json(backup_dir / path.name, original)
    for path, _, updated in updates:
        path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"synchronized_files": [str(path.relative_to(ROOT)) for path, _, _ in updates]}


async def run(descriptions, expected, apply, backup_path):
    url = normalize_database_url(configured_database_url())
    connection = await asyncpg.connect(url, timeout=30)
    try:
        return await update_database(connection, descriptions, expected, apply=apply, backup_path=backup_path)
    finally:
        await connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--sync-local", action="store_true", help="Only synchronize local exports; no DB connection")
    args = parser.parse_args()
    if args.apply and args.sync_local:
        parser.error("Apply the DB update first, then synchronize local exports separately")
    descriptions = read_json(DESCRIPTIONS)
    validate_descriptions(descriptions)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if args.sync_local:
        report = sync_local(descriptions, BACKUPS / stamp)
    else:
        expected = {row["e_code"]: row for row in read_json(CATALOG)["ingredients"] if row.get("e_code")}
        report = asyncio.run(run(descriptions, expected, args.apply, BACKUPS / f"remote-{stamp}.json"))
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
