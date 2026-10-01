"""Convert the edited merged export into flat relational tables without rebuilding it."""

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from app.db.merge_proe_regulatory import strict_code, validate_bundle as validate_merged

ROOT = Path(__file__).resolve().parents[3]
DATASET = "toxicheck_catalog"
DEFAULT_INPUT = ROOT / "toxicheck_proe_merged.json"
DEFAULT_OUTPUT = ROOT / "toxicheck_catalog.json"
DEFAULT_OFF_INPUT = ROOT / "backend/db/off/ingredients.txt"
DEFAULT_OFF_REPORT = ROOT / "off_catalog_review_report.json"
SCHEMA_VERSION = 2
# Column allowlists double as the import contract. All records contain scalars only.
COLUMNS = {
    "regulatory_sources": "id code title url version_date effective_from effective_to".split(),
    "ingredients": "id kind e_code canonical_name_ru canonical_name_en category description full_description is_active".split(),
    "ingredient_aliases": "id ingredient_id alias normalized_alias language source confidence".split(),
    "ingredient_rules": "id ingredient_id source_id regulatory_status title explanation citation scope jurisdiction verification_status evaluation match_policy primary_basis_required production_ready product_compliance_assessed effective_from effective_to".split(),
    "rule_evidence": "id rule_id source_id url locator document_role verification_status pdf_page appendix table_label".split(),
    "rule_transitions": "rule_id effective_from months last_production_date transition_end boundary_basis requires_preexisting_conformity_documents legacy_circulation_until_expiry".split(),
    "ingredient_profiles": "id ingredient_id source_id source_url fetched_at has_article full_description_status".split(),
    "ingredient_tags": "id ingredient_id tag_type name_ru url".split(),
    "documents": "id source_id title url verification_status fetched_at".split(),
    "document_links": "id document_id relation url".split(),
    "ingredient_references": "id ingredient_id document_id title url relation organization".split(),
    "ingredient_relations": "id ingredient_id related_ingredient_id related_url".split(),
    "ingredient_matches": "ingredient_id legacy_ingredient_id match_method".split(),
    "taxonomy_sources": "id code title url file_sha256".split(),
    "taxonomy_entries": "id taxonomy_source_id taxonomy_key canonical_name_ru canonical_name_en ingredient_id match_method source_line".split(),
    "taxonomy_parents": "id entry_id parent_entry_id parent_reference".split(),
    "taxonomy_properties": "id entry_id property language value verification_status".split(),
    "ingredient_alias_review": "id ingredient_id alias normalized_alias language source confidence reason".split(),
}


def stable_id(kind, *parts):
    return str(uuid5(NAMESPACE_URL, "toxicheck/catalog/v1/" + kind + "/" + "/".join(map(str, parts))))


def flat_row(table, row):
    return {key: row.get(key) for key in COLUMNS[table]}


def is_secondary_url(value):
    host = (urlsplit(value or "").hostname or "").lower()
    return host == "proe.info" or host.endswith(".proe.info")


def clean_description(value):
    if not value:
        return value
    # Drop attributed claims as whole sentences, not just their attribution.
    sentences = re.split(r"(?<=[.!?])\s+", value)
    return " ".join(s for s in sentences if not re.search(r"\b(?:proe|прое)\b", s, re.I)) or None


def remove_secondary_source(bundle):
    excluded = {r["id"] for r in bundle["regulatory_sources"]
                if r["code"] == "PROE_INFO" or is_secondary_url(r.get("url"))}
    if any(r["source_id"] in excluded for t in ("ingredient_rules", "rule_evidence") for r in bundle[t]):
        raise ValueError("A regulatory rule depends on the excluded source; review its evidence first")
    bundle["regulatory_sources"] = [r for r in bundle["regulatory_sources"] if r["id"] not in excluded]
    for row in bundle["ingredients"]:
        for key in ("description", "full_description"):
            row[key] = clean_description(row[key])
    for row in bundle["ingredient_aliases"]:
        if row["source"] == "PROE_INFO":
            row["source"] = None
    for table in ("ingredient_profiles", "documents"):
        for row in bundle[table]:
            if row["source_id"] in excluded:
                row["source_id"] = None
    for table, field in (("ingredient_profiles", "source_url"), ("documents", "url"),
                         ("ingredient_tags", "url"), ("ingredient_references", "url"),
                         ("ingredient_relations", "related_url")):
        for row in bundle[table]:
            if is_secondary_url(row[field]):
                row[field] = None
    bundle["document_links"] = [r for r in bundle["document_links"] if not is_secondary_url(r["url"])]
    bundle["ingredient_relations"] = [r for r in bundle["ingredient_relations"]
                                      if r["related_ingredient_id"] or r["related_url"]]


def build_catalog(merged):
    validate_merged(merged)
    if any(r.get("record_kind") not in {"additive_profile", "document_reference", "regulatory_match"}
           for r in merged["source_fragments"]):
        raise ValueError("Use the status-only merged dataset, not the former complete regulatory export")
    result = {table: [] for table in COLUMNS}
    for table in ("regulatory_sources", "ingredients", "ingredient_aliases"):
        result[table] = [flat_row(table, r) for r in merged[table]]
    for row in merged["ingredient_rules"]:
        c = row["conditions"]
        rule = flat_row("ingredient_rules", row)
        for key in ("regulatory_status", "scope", "jurisdiction", "verification_status", "evaluation",
                    "match_policy", "primary_basis_required", "production_ready"):
            rule[key] = c.get(key)
        rule["product_compliance_assessed"] = False
        result["ingredient_rules"].append(rule)
        for index, evidence in enumerate(c["evidence"]):
            ref = flat_row("rule_evidence", evidence)
            ref.update(id=stable_id("evidence", row["id"], index), rule_id=row["id"],
                       appendix=str(evidence["appendix"]) if evidence.get("appendix") is not None else None,
                       table_label=str(evidence["table"]) if evidence.get("table") is not None else None)
            result["rule_evidence"].append(ref)
        if c.get("transition"):
            result["rule_transitions"].append(flat_row("rule_transitions", dict(c["transition"], rule_id=row["id"])))
    fragments = merged["source_fragments"]
    docs = {r["url"]: r["id"] for r in fragments if r["record_kind"] == "document_reference"}
    profiles = {r["source_url"]: r["ingredient_id"] for r in fragments if r["record_kind"] == "additive_profile"}
    for row in fragments:
        kind = row["record_kind"]
        if kind == "document_reference":
            result["documents"].append(flat_row("documents", row))
            for key, relation in (("external_urls", "external"), ("file_urls", "file")):
                for url in dict.fromkeys(row.get(key, [])):
                    result["document_links"].append(dict(id=stable_id("document_link", row["id"], relation, url),
                        document_id=row["id"], relation=relation, url=url))
        elif kind == "additive_profile":
            result["ingredient_profiles"].append(flat_row("ingredient_profiles", row))
            for key, tag_type in (("categories", "category"), ("origins", "origin")):
                tags = {(tag["title"], tag.get("url")): tag for tag in row.get(key, [])}
                for tag in tags.values():
                    result["ingredient_tags"].append(dict(id=stable_id("tag", row["ingredient_id"], tag_type, tag["title"], tag.get("url")),
                        ingredient_id=row["ingredient_id"], tag_type=tag_type, name_ru=tag["title"], url=tag.get("url")))
            for index, ref in enumerate(row.get("references", [])):
                result["ingredient_references"].append(flat_row("ingredient_references", dict(ref,
                    id=stable_id("reference", row["ingredient_id"], index), ingredient_id=row["ingredient_id"],
                    document_id=docs.get(ref["url"]))))
            for url in dict.fromkeys(row.get("related_additive_urls", [])):
                result["ingredient_relations"].append(dict(id=stable_id("relation", row["ingredient_id"], url),
                    ingredient_id=row["ingredient_id"], related_url=url, related_ingredient_id=profiles.get(url)))
        elif kind == "regulatory_match":
            result["ingredient_matches"].append(dict(ingredient_id=row["ingredient_id"],
                legacy_ingredient_id=row["legacy_id"], match_method=row["method"]))
    result["metadata"] = dict(dataset=DATASET, schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        quantity_assessment_enabled=False, legal_revision_verified=False)
    remove_secondary_source(result)
    validate_catalog(result)
    return result


def validate_catalog(bundle):
    if bundle.get("metadata", {}).get("dataset") != DATASET or bundle["metadata"].get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unexpected catalog dataset/version")
    if set(bundle) != set(COLUMNS) | {"metadata"}:
        raise ValueError("Unexpected or missing tables")
    identifiers = {}
    for table, columns in COLUMNS.items():
        pk = "rule_id" if table == "rule_transitions" else "ingredient_id" if table == "ingredient_matches" else "id"
        identifiers[table] = set()
        for row in bundle[table]:
            if set(row) != set(columns) or any(isinstance(v, (dict, list)) for v in row.values()):
                raise ValueError(f"Expected flat records with known English keys: {table}")
            for key, value in row.items():
                if value is not None and (key == "id" or key.endswith("_id")):
                    UUID(value)
            if row[pk] in identifiers[table]:
                raise ValueError(f"Duplicate ID: {table}")
            identifiers[table].add(row[pk])
    refs = {"ingredient_id": "ingredients", "related_ingredient_id": "ingredients",
            "rule_id": "ingredient_rules", "source_id": "regulatory_sources", "document_id": "documents",
            "taxonomy_source_id": "taxonomy_sources", "entry_id": "taxonomy_entries",
            "parent_entry_id": "taxonomy_entries"}
    for table in COLUMNS:
        for row in bundle[table]:
            if any(isinstance(value, str) and re.search(r"proe|прое", value, re.I) for value in row.values()):
                raise ValueError(f"Excluded secondary source remains in {table}")
            for key, target in refs.items():
                if row.get(key) is not None and row[key] not in identifiers[target]:
                    raise ValueError(f"Orphan {table}.{key}")
    codes = [strict_code(r["e_code"]) for r in bundle["ingredients"] if r["e_code"] is not None]
    if None in codes or len(set(codes)) != len(codes):
        raise ValueError("Invalid/duplicate E-code")
    if any(not r["e_code"] and r["kind"] != "ingredient" for r in bundle["ingredients"]):
        raise ValueError("Only ordinary ingredients can omit E-code")
    for rule in bundle["ingredient_rules"]:
        if rule["regulatory_status"] not in {"PERMITTED_WITH_CONDITIONS", "BANNED", "PHASE_OUT"}:
            raise ValueError("Only permission/prohibition statuses are allowed")
        if rule["product_compliance_assessed"] is not False:
            raise ValueError("Product compliance cannot be inferred")
        if not rule["ingredient_id"] or not rule["source_id"] or not rule["verification_status"]:
            raise ValueError("Rule identity/source/verification is required")
    if {r["rule_id"] for r in bundle["rule_evidence"]} != identifiers["ingredient_rules"]:
        raise ValueError("Every rule needs evidence")
    for ref in bundle["rule_evidence"]:
        if not ref["url"] or not ref["locator"] or not ref["verification_status"]:
            raise ValueError("Incomplete rule evidence")
    phase_out = {r["id"] for r in bundle["ingredient_rules"] if r["regulatory_status"] == "PHASE_OUT"}
    if {r["rule_id"] for r in bundle["rule_transitions"]} != phase_out:
        raise ValueError("PHASE_OUT rules must have transition terms")
    profiled = {r["ingredient_id"] for r in bundle["ingredient_profiles"]}
    additives = {r["id"] for r in bundle["ingredients"] if r["e_code"] is not None}
    if not additives.issubset(profiled):
        raise ValueError("Missing ingredient profiles")
    if bundle["taxonomy_sources"]:
        from app.db.off_taxonomy import validate_off_catalog
        validate_off_catalog(bundle)
    return {table: len(bundle[table]) for table in COLUMNS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--off-input", type=Path, default=DEFAULT_OFF_INPUT)
    parser.add_argument("--off-report", type=Path, default=DEFAULT_OFF_REPORT)
    parser.add_argument("--without-off", action="store_true", help="Build only the original additive catalog")
    args = parser.parse_args()
    inputs = {args.input.resolve(), args.off_input.resolve()}
    outputs = {args.output.resolve(), args.off_report.resolve()}
    if inputs & outputs or len(outputs) != 2:
        raise ValueError("Inputs and outputs must use different paths")
    raw = args.input.read_bytes()
    bundle = build_catalog(json.loads(raw.decode("utf-8-sig")))
    bundle["metadata"].update(input_sha256=hashlib.sha256(raw).hexdigest())
    if not args.without_off:
        from app.db.off_taxonomy import enrich_catalog
        bundle, report = enrich_catalog(bundle, args.off_input.read_bytes())
        report_temp = args.off_report.with_suffix(args.off_report.suffix + ".tmp")
        report_temp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report_temp.replace(args.off_report)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    temp.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(args.output)
    print(json.dumps(validate_catalog(bundle), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
