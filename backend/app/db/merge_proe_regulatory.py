"""Match the proE catalog to local TR TS snapshots without changing either input."""

import argparse
import copy
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.db.build_regulatory_data import (
    INPUT_NAMES, ROOT, build_bundle as build_regulatory, read_json,
    validate_bundle as validate_regulatory,
)
from app.services.normalization import normalize_text

DATASET = "toxicheck_proe_regulatory"
OUTPUT_NAME = "toxicheck_proe_merged.json"
REPORT_NAME = "proe_regulatory_match_report.json"
CODE_RE = re.compile(r"E\d{3,4}[A-Z]?(?:\([IVX]+\))?", re.I)
CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")


def is_status_rule(row):
    """Select ingredient permission/status, never dose, purity or labeling rules."""
    if row.get("ingredient_id") is None:
        return False
    status = row.get("conditions", {}).get("regulatory_status")
    return (
        row.get("rule_type") == "permitted_food_additive" and status == "PERMITTED_WITH_CONDITIONS"
    ) or (
        row.get("rule_type") == "regulatory_status" and status in {"BANNED", "PHASE_OUT"}
    )


def strict_code(value):
    # Full match is essential: E100(i) and E100 are different catalog entries.
    value = re.sub(r"[\s-]", "", value or "").upper().replace("Е", "E")
    return value if CODE_RE.fullmatch(value) else None


def stable_id(kind, key):
    return str(uuid5(NAMESPACE_URL, f"toxicheck/proe-regulatory/v1/{kind}/{key}"))


def english_name(value):
    """Keep a clean leading English name; never translate or copy a mixed table row."""
    if not value:
        return None
    value = value.strip()
    depth = 0
    for index, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                value = value[:index]
                break
            depth -= 1
    # The old extractor sometimes dropped the final closing parenthesis. Keep
    # the complete primary name before that incomplete optional synonym.
    if value.count("(") > value.count(")"):
        value = value[:value.index("(")].strip()
    value = value.strip(" ,;:.")
    if (CYRILLIC_RE.search(value) or len(value) > 180
            or not re.search(r"[a-zA-Z]{3}", value) or strict_code(value)
            or value.count("(") != value.count(")")):
        return None
    return value


def identity(row):
    return {key: row.get(key) for key in (
        "id", "e_code", "kind", "canonical_name_ru", "canonical_name_en")}


def remap_record(row, crosswalk):
    result = copy.deepcopy(row)
    old_id = result.get("ingredient_id")
    if old_id:
        result["ingredient_id"] = crosswalk[old_id]
    conditions = result.get("conditions", {})
    # These are references too, not just the top-level FK. Preserve original IDs
    # explicitly as provenance, but only put destination IDs in active fields.
    if "resolved_ingredient_ids" in conditions:
        original = conditions["resolved_ingredient_ids"]
        conditions["legacy_resolved_ingredient_ids"] = original
        conditions["resolved_ingredient_ids"] = list(dict.fromkeys(
            crosswalk[item] for item in original if item in crosswalk))
        if any(item not in crosswalk for item in original):
            conditions["resolved_ingredient_ids_scope"] = "matched_catalog_subset"
    if "group_ingredient_id" in conditions:
        original = conditions.pop("group_ingredient_id")
        conditions["legacy_group_ingredient_id"] = original
        if original in crosswalk:
            conditions["group_ingredient_id"] = crosswalk[original]
    return result


def merge_bundles(proe, legacy, *, ambiguous_aliases=(), raw_rules=()):
    validate_regulatory(legacy)
    if proe.get("metadata", {}).get("dataset") != "toxicheck_proe" or proe["ingredient_rules"]:
        raise ValueError("Expected a pure proE export without publisher ratings")
    result = copy.deepcopy(proe)
    result.pop("validation", None)
    sources = {s["id"]: s for s in result["regulatory_sources"]}
    sources.update({s["id"]: copy.deepcopy(s) for s in legacy["regulatory_sources"]})
    result["regulatory_sources"] = list(sources.values())
    by_code, by_name = defaultdict(list), defaultdict(list)
    for row in legacy["ingredients"]:
        if code := strict_code(row.get("e_code")):
            by_code[code].append(row)
        by_name[normalize_text(row["canonical_name_ru"])].append(row)

    report = {"matches": [], "unmatched_proe": [], "field_changes": [],
              "name_differences": [], "english_needs_review": [],
              "rejected_aliases": [], "rule_changes_from_raw_tr_ts": []}
    crosswalk, matched_rows = {}, {}
    for row in result["ingredients"]:
        code = strict_code(row["e_code"])
        candidates = by_code.get(code, [])
        method = "exact_e_code"
        if not candidates:
            candidates = by_name.get(normalize_text(row["canonical_name_ru"]), [])
            method = "unique_exact_name_without_legacy_code"
            # Group/subtype equivalence and alternative codes require review.
            if len(candidates) != 1 or candidates[0].get("e_code"):
                candidates = []
        if len(candidates) != 1 or candidates[0]["id"] in crosswalk:
            related = by_name.get(normalize_text(row["canonical_name_ru"]), [])[:]
            parent = re.sub(r"\([IVX]+\)$", "", code)
            if parent != code:
                related += by_code.get(parent, [])
                related += by_code.get(code.replace("(", "").replace(")", ""), [])
            related += by_code.get(code, [])
            report["unmatched_proe"].append({
                **identity(row), "reason": "ambiguous_or_requires_review" if related else "no_match",
                "candidates": list({r["id"]: identity(r) for r in related}.values()),
                "rules_inherited": False,
            })
            continue
        old = candidates[0]
        crosswalk[old["id"]] = row["id"]
        matched_rows[row["id"]] = old
        report["matches"].append({"proe_id": row["id"], "legacy_id": old["id"],
                                  "e_code": row["e_code"], "method": method})
        if normalize_text(old["canonical_name_ru"]) != normalize_text(row["canonical_name_ru"]):
            report["name_differences"].append({"e_code": row["e_code"],
                "proe": row["canonical_name_ru"], "tr_ts": old["canonical_name_ru"],
                "interpretation": "naming_difference_not_a_confirmed_regulatory_change"})
        for field in ("canonical_name_en", "category"):
            if row.get(field) or not old.get(field):
                continue
            value = english_name(old[field]) if field == "canonical_name_en" else old[field]
            # E304's extracted English field starts with the first of two esters,
            # not a collective name. Do not assign that first subtype to the group.
            if field == "canonical_name_en" and ") (ii)" in old[field] and "(i)" not in old[field]:
                value = None
            if value:
                report["field_changes"].append({"ingredient_id": row["id"],
                    "e_code": row["e_code"], "field": field, "before": row.get(field),
                    "after": value, "legacy_id": old["id"], "source_value": old[field],
                    "cleanup_applied": value != old[field]})
                row[field] = value
            else:
                report["english_needs_review"].append({"e_code": row["e_code"],
                    "legacy_id": old["id"], "source_value": old[field]})

    catalog_rules = [r for r in legacy["ingredient_rules"]
        if r.get("ingredient_id") is None or r["ingredient_id"] in crosswalk]
    excluded = [r for r in catalog_rules if not is_status_rule(r)]
    report["excluded_rules"] = dict(
        policy="ingredient_permission_and_prohibition_only",
        count=len(excluded), by_rule_type=dict(Counter(r["rule_type"] for r in excluded)),
        rule_ids=[r["id"] for r in excluded],
    )
    result["ingredient_rules"] = [remap_record(r, crosswalk) for r in catalog_rules if is_status_rule(r)]
    # Full extracted regulatory tables stay in the original TR TS exports.
    # This bundle keeps proE profiles, match provenance and selected rule evidence.
    result["retired_alias_ids"] = list(legacy.get("retired_alias_ids", []))

    restricted = {r["ingredient_id"] for r in result["ingredient_rules"]
                  if r.get("conditions", {}).get("match_policy") == "exact_only"}
    ambiguous = {normalize_text(a) for a in ambiguous_aliases}
    aliases = []
    for alias in result["ingredient_aliases"]:
        if alias["ingredient_id"] in restricted and normalize_text(alias["alias"]) in ambiguous:
            report["rejected_aliases"].append({**alias, "reason": "ambiguous_for_strict_status_rule"})
            result["retired_alias_ids"].append(alias["id"])
        else:
            aliases.append(alias)
    seen = {(a["ingredient_id"], normalize_text(a["alias"])) for a in aliases}

    def add_alias(alias):
        key = (alias["ingredient_id"], normalize_text(alias["alias"]))
        if key[0] in restricted and key[1] in ambiguous:
            report["rejected_aliases"].append({**alias, "reason": "ambiguous_for_strict_status_rule"})
        elif key not in seen:
            aliases.append(alias)
            seen.add(key)

    for old_alias in legacy["ingredient_aliases"]:
        if old_alias["ingredient_id"] not in crosswalk:
            continue
        alias = copy.deepcopy(old_alias)
        alias["ingredient_id"] = crosswalk[old_alias["ingredient_id"]]
        # Very long/mixed-language extraction rows are evidence, not synonyms.
        if len(alias["alias"]) > 180 or (alias.get("language") == "en" and CYRILLIC_RE.search(alias["alias"])):
            report["rejected_aliases"].append({**alias, "reason": "extraction_row_not_a_clean_alias"})
            continue
        add_alias(alias)
    for row in result["ingredients"]:
        for field, language in (("canonical_name_en", "en"),):
            if not row.get(field):
                continue
            add_alias(dict(id=stable_id("alias", row["id"] + "/" + normalize_text(row[field])),
                ingredient_id=row["id"], alias=row[field], normalized_alias=normalize_text(row[field]),
                language=language, source="tr_ts_match", confidence=1.0))
    result["ingredient_aliases"] = aliases

    rule_counts = Counter(r.get("ingredient_id") for r in result["ingredient_rules"])
    source_id = next(s["id"] for s in legacy["regulatory_sources"] if s["code"] == "TR_TS_029_2012")
    for match in report["matches"]:
        match["rules_copied"] = rule_counts[match["proe_id"]]
        result["source_fragments"].append(dict(
            id=stable_id("match", match["proe_id"]), record_kind="regulatory_match",
            source_id=source_id, ingredient_id=match["proe_id"], **match,
            legacy_ingredient=identity(matched_rows[match["proe_id"]]),
            field_changes=[c for c in report["field_changes"] if c["ingredient_id"] == match["proe_id"]],
            legal_revision_verified=False,
        ))
    raw_by_id = {r["id"]: r for r in raw_rules}
    for row in result["ingredient_rules"]:
        old = raw_by_id.get(row["id"])
        changes = {}
        for field in ("rule_type", "severity", "effective_from", "effective_to"):
            if old is not None and old.get(field) != row.get(field):
                changes[field] = {"before": old.get(field), "after": row.get(field)}
        for field in ("regulatory_status", "evaluation", "verification_status", "match_policy"):
            before = old.get("conditions", {}).get(field) if old else None
            after = row.get("conditions", {}).get(field)
            if before != after:
                changes["conditions." + field] = {"before": before, "after": after}
        if old is None or changes:
            report["rule_changes_from_raw_tr_ts"].append({"rule_id": row["id"],
                "ingredient_id": row.get("ingredient_id"), "rule_type": row["rule_type"],
                "change_kind": "local_enrichment" if old else "local_seed_rule", "changes": changes})
    report["legacy_only_coded_ingredients"] = [identity(r) for r in legacy["ingredients"]
        if r.get("e_code") and r["id"] not in crosswalk]
    report["legacy_rules_outside_proe_catalog"] = len(legacy["ingredient_rules"]) - len(catalog_rules)
    report["summary"] = dict(
        ingredients=len(result["ingredients"]), matched=len(crosswalk),
        match_methods=dict(Counter(m["method"] for m in report["matches"])),
        unmatched=len(report["unmatched_proe"]),
        filled_fields=dict(Counter(c["field"] for c in report["field_changes"])),
        ingredient_rules=len(result["ingredient_rules"]), global_rules=rule_counts[None],
        excluded_rules=len(excluded),
        status_counts=dict(Counter(r["conditions"]["regulatory_status"] for r in result["ingredient_rules"])),
        name_differences=len(report["name_differences"]),
        english_remaining_null=sum(not r.get("canonical_name_en") for r in result["ingredients"]),
        legacy_only_codes=len(report["legacy_only_coded_ingredients"]),
    )
    result["metadata"] = dict(dataset=DATASET, schema_version=2,
        generated_at=datetime.now(timezone.utc).isoformat(), target_database="toxicheck_proe",
        proe_snapshot=copy.deepcopy(proe["metadata"]),
        regulatory_snapshot=copy.deepcopy(legacy.get("metadata", {})),
        matching_policy="strict_e_code_or_unique_exact_name_without_legacy_code; no_parent_rule_inheritance",
        legal_revision_verified=False, publisher_ratings_included=False,
        scope="supplied proE catalog plus ingredient permission and prohibition rules",
        rules_policy="ingredient_permission_and_prohibition_only",
        quantity_assessment_enabled=False,
        matching_summary=report["summary"])
    report["metadata"] = copy.deepcopy(result["metadata"])
    result["validation"] = validate_bundle(result)
    return result, report


def validate_bundle(bundle):
    if bundle.get("metadata", {}).get("dataset") != DATASET:
        raise ValueError("Unexpected merged dataset")
    fragments = bundle.get("source_fragments", [])
    regulatory = dict(bundle, source_fragments=[r for r in fragments if "conditions" in r])
    report = validate_regulatory(regulatory)
    ids = {r["id"] for r in bundle["ingredients"]}
    sources = {r["id"] for r in bundle["regulatory_sources"]}
    proe_sources = {r["id"] for r in bundle["regulatory_sources"] if r["code"] == "PROE_INFO"}
    if len({r["id"] for r in fragments}) != len(fragments):
        raise ValueError("Duplicate fragment IDs")
    if any(strict_code(r.get("e_code")) is None for r in bundle["ingredients"]):
        raise ValueError("Invalid E-code")
    for row in bundle["ingredient_rules"] + fragments:
        if row["source_id"] not in sources or (row.get("ingredient_id") is not None and row["ingredient_id"] not in ids):
            raise ValueError("Orphan fragment/rule")
        conditions = row.get("conditions", {})
        refs = conditions.get("resolved_ingredient_ids", []) + ([conditions["group_ingredient_id"]] if conditions.get("group_ingredient_id") else [])
        if not set(refs) <= ids:
            raise ValueError("Unmapped nested ingredient reference")
        if "danger" in row or row.get("rule_type") == "publisher_risk_rating":
            raise ValueError("Publisher rating present")
    if any(r["source_id"] in proe_sources for r in bundle["ingredient_rules"]):
        raise ValueError("proE must not supply regulatory rules")
    if bundle["metadata"].get("schema_version", 1) >= 2 and any(
        not is_status_rule(r) for r in bundle["ingredient_rules"]
    ):
        raise ValueError("Only ingredient permission and prohibition rules are allowed")
    profiles = [r for r in fragments if r.get("record_kind") == "additive_profile"]
    if len(profiles) != len(ids) or {r["ingredient_id"] for r in profiles} != ids:
        raise ValueError("Incomplete proE profiles")
    by_id = {r["id"]: r for r in bundle["ingredients"]}
    if any(p.get("full_description") != by_id[p["ingredient_id"]].get("full_description") for p in profiles):
        raise ValueError("Descriptions changed during merge")
    report.pop("review_queue", None)
    report["source_fragments"] = len(fragments)
    report["publisher_ratings_included"] = False
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proe", type=Path, default=ROOT / "toxicheck_proe.json")
    parser.add_argument("--output", type=Path, default=ROOT / OUTPUT_NAME)
    parser.add_argument("--report", type=Path, default=ROOT / REPORT_NAME)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        print(json.dumps(validate_bundle(read_json(args.output)), ensure_ascii=False, indent=2))
        return
    # Rebuild in memory to include current local seed corrections, preserving all
    # existing source exports and avoiding reliance on a stale generated bundle.
    legacy, _ = build_regulatory()
    seed = read_json(ROOT / "backend/db/regulatory_status_seed.json")
    raw = [r for name in INPUT_NAMES for r in read_json(ROOT / name)["ingredient_rules"]]
    bundle, report = merge_bundles(read_json(args.proe), legacy,
        ambiguous_aliases=seed["ambiguous_aliases"], raw_rules=raw)
    inputs = [args.proe, *(ROOT / name for name in INPUT_NAMES),
              ROOT / "backend/db/regulatory_status_seed.json", ROOT / "backend/db/preference_ingredients_seed.json"]
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    for payload in (bundle, report):
        payload["metadata"]["input_sha256"] = hashes
    protected = {p.resolve() for p in inputs}
    if args.output.resolve() in protected or args.report.resolve() in protected or args.output.resolve() == args.report.resolve():
        raise ValueError("Output/report must be distinct from input files and each other")
    for path, payload in ((args.output, bundle), (args.report, report)):
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
