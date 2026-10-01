"""Offline OFF taxonomy parsing. Taxonomy claims never create regulatory rules."""

import copy
import csv
import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

from app.db.build_catalog import flat_row, stable_id, validate_catalog
from app.db.merge_proe_regulatory import strict_code
from app.services.normalization import normalize_text

OFF_CODE = "OPEN_FOOD_FACTS_INGREDIENTS"
OFF_URL = "https://github.com/openfoodfacts/openfoodfacts-server/blob/main/taxonomies/ingredients.txt"
LANGUAGE = r"[a-z]{2,3}(?:[-_][a-z]+)*"
NAME_RE = re.compile(rf"^({LANGUAGE}):\s*(.+)$")
PROPERTY_RE = re.compile(rf"^([\w.-]+):({LANGUAGE}):\s*(.*)$")
CODE_LIKE_RE = re.compile(r"^[e\u0435][\s-]*\d{3,4}[a-z]*(?:\([ivx]+\))?$", re.I)


@dataclass
class Entry:
    key: str
    line: int
    names: dict = field(default_factory=dict)
    parents: set = field(default_factory=set)
    properties: set = field(default_factory=set)


def split_names(value):
    # OFF escapes literal commas inside a synonym with a backslash.
    return [name.strip() for name in next(csv.reader(
        [value], escapechar="\\", quoting=csv.QUOTE_NONE)) if name.strip()]


def taxonomy_key(language, name):
    name = " ".join(unicodedata.normalize("NFKC", name).casefold().split())
    return f"{language}:{name}"


def parse_taxonomy(text):
    entries = {}
    report = dict(duplicate_blocks=[], ignored_directives=0, unparsed_lines=[])
    block = []

    def flush():
        names, parents, properties = {}, set(), set()
        for line_number, line in block:
            if line.startswith(("synonyms:", "stopwords:")):
                report["ignored_directives"] += 1
            elif line.startswith("<"):
                match = NAME_RE.fullmatch(line[1:].strip())
                if match:
                    parents.add((match[1], match[2].strip()))
                else:
                    report["unparsed_lines"].append(dict(line=line_number, text=line))
            elif match := PROPERTY_RE.fullmatch(line):
                properties.add((match[1], match[2], match[3].strip()))
            elif match := NAME_RE.fullmatch(line):
                values = split_names(match[2])
                if not values:
                    raise ValueError(f"Empty taxonomy names at line {line_number}")
                names.setdefault(match[1], []).extend(values)
            else:
                report["unparsed_lines"].append(dict(line=line_number, text=line))
        if not names:
            return
        language = "en" if "en" in names else "ru" if "ru" in names else sorted(names)[0]
        key = taxonomy_key(language, names[language][0])
        if key in entries:
            report["duplicate_blocks"].append(dict(taxonomy_key=key, line=block[0][0]))
        entry = entries.setdefault(key, Entry(key=key, line=block[0][0]))
        for language, values in names.items():
            entry.names[language] = list(dict.fromkeys(entry.names.get(language, []) + values))
        entry.parents.update(parents)
        entry.properties.update(properties)

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            flush()
            block.clear()
        elif not line.startswith("#"):
            block.append((number, line))
    flush()
    return list(entries.values()), report


def new_alias(ingredient_id, language, name, source):
    return dict(id=stable_id("alias", ingredient_id, language, name),
                ingredient_id=ingredient_id, alias=name, normalized_alias=normalize_text(name),
                language=language, source=source, confidence=1)


def alias_owners(bundle):
    owners = defaultdict(set)
    for row in bundle["ingredient_aliases"]:
        owners[normalize_text(row["alias"])].add(row["ingredient_id"])
    for row in bundle["ingredients"]:
        for field_name in ("canonical_name_ru", "canonical_name_en"):
            if row[field_name]:
                owners[normalize_text(row[field_name])].add(row["id"])
    return owners


def quarantine_aliases(bundle):
    owners = alias_owners(bundle)
    ingredients = {r["id"]: r for r in bundle["ingredients"]}
    conflicts = {name: ids for name, ids in owners.items() if len(ids) > 1}
    active, review, seen = [], [], set()
    for row in bundle["ingredient_aliases"]:
        row["normalized_alias"] = normalize_text(row["alias"])
        key = (row["ingredient_id"], row["language"], row["normalized_alias"])
        if key in seen:
            continue
        seen.add(key)
        code = strict_code(row["alias"])
        reason = None
        if row["normalized_alias"] in conflicts:
            reason = "ambiguous_name"
        elif code and code != strict_code(ingredients[row["ingredient_id"]]["e_code"]):
            reason = "e_code_mismatch"
        elif not row["normalized_alias"]:
            reason = "empty_normalized_alias"
        elif sum(c.isalpha() for c in row["normalized_alias"]) < 2 and not code:
            reason = "name_fragment"
        if reason:
            review.append(dict(row, reason=reason))
        else:
            active.append(row)
    bundle["ingredient_aliases"] = active
    bundle["ingredient_alias_review"] = review
    return [dict(normalized_alias=name, candidates=[
        dict(ingredient_id=i, name=ingredients[i]["canonical_name_ru"], e_code=ingredients[i]["e_code"])
        for i in sorted(ids)]) for name, ids in sorted(conflicts.items())]


def enrich_catalog(base, raw):
    validate_catalog(base)
    if base["taxonomy_sources"]:
        raise ValueError("Rebuild from the original additive catalog, not a previously enriched export")
    bundle = copy.deepcopy(base)
    entries, parsing = parse_taxonomy(raw.decode("utf-8-sig"))
    source_id = stable_id("taxonomy_source", OFF_CODE)
    digest = hashlib.sha256(raw).hexdigest()
    bundle["taxonomy_sources"].append(dict(id=source_id, code=OFF_CODE,
        title="Open Food Facts ingredients taxonomy", url=OFF_URL, file_sha256=digest))
    by_code = {strict_code(r["e_code"]): r["id"] for r in bundle["ingredients"] if r["e_code"]}
    report = dict(source_sha256=digest, database_uploaded=False, parsing=parsing,
                  code_review=[], property_conflicts=[], unresolved_parents=[],
                  untranslated_entries=0, added_ingredients=0, exact_code_matches=0)
    entry_ids = {e.key: stable_id("off_entry", e.key) for e in entries}
    parent_lookup = defaultdict(set)
    for entry in entries:
        for language, names in entry.names.items():
            for name in names:
                parent_lookup[(language, normalize_text(name))].add(entry_ids[entry.key])
    for ingredient in bundle["ingredients"]:
        for language in ("ru", "en"):
            if name := ingredient[f"canonical_name_{language}"]:
                bundle["ingredient_aliases"].append(new_alias(ingredient["id"], language, name, None))

    for entry in entries:
        entry_id = entry_ids[entry.key]
        names = [name for values in entry.names.values() for name in values]
        codes = {strict_code(name) for name in names if strict_code(name)}
        code_like = {name for name in names if CODE_LIKE_RE.fullmatch(name)}
        ingredient_id, method = None, "untranslated"
        ru = entry.names.get("ru", [None])[0]
        en = entry.names.get("en", [None])[0]
        if len(codes) == 1 and next(iter(codes)) in by_code:
            ingredient_id = by_code[next(iter(codes))]
            method = "exact_e_code"
            report["exact_code_matches"] += 1
        elif codes or code_like:
            method = "code_review_required"
            report["code_review"].append(dict(taxonomy_key=entry.key, line=entry.line,
                                             codes=sorted(codes), original_codes=sorted(code_like)))
        elif ru:
            method = "new_ordinary_ingredient"
            ingredient_id = stable_id("off_ingredient", entry.key)
            bundle["ingredients"].append(flat_row("ingredients", dict(id=ingredient_id,
                kind="ingredient", canonical_name_ru=ru, canonical_name_en=en, is_active=True)))
            report["added_ingredients"] += 1
        else:
            report["untranslated_entries"] += 1
        bundle["taxonomy_entries"].append(dict(id=entry_id, taxonomy_source_id=source_id,
            taxonomy_key=entry.key, canonical_name_ru=ru, canonical_name_en=en,
            ingredient_id=ingredient_id, match_method=method, source_line=entry.line))
        if ingredient_id:
            for language in ("ru", "en"):
                for name in entry.names.get(language, []):
                    # Never turn an unsupported spelling of an E-code into a name match.
                    if CODE_LIKE_RE.fullmatch(name) and strict_code(name) not in by_code:
                        continue
                    bundle["ingredient_aliases"].append(new_alias(ingredient_id, language, name, OFF_CODE))
        for language, name in sorted(entry.parents):
            candidates = parent_lookup[(language, normalize_text(name))]
            parent_id = next(iter(candidates)) if len(candidates) == 1 else None
            reference = f"{language}:{name}"
            bundle["taxonomy_parents"].append(dict(id=stable_id("off_parent", entry.key, reference),
                entry_id=entry_id, parent_entry_id=parent_id, parent_reference=reference))
            if parent_id is None:
                report["unresolved_parents"].append(dict(taxonomy_key=entry.key, parent=reference,
                                                       reason="missing" if not candidates else "ambiguous"))
        property_values = defaultdict(set)
        for prop, language, value in sorted(entry.properties):
            bundle["taxonomy_properties"].append(dict(
                id=stable_id("off_property", entry.key, prop, language, value), entry_id=entry_id,
                property=prop, language=language, value=value, verification_status="reference_only"))
            property_values[(prop, language)].add(value)
        for (prop, language), values in sorted(property_values.items()):
            if len(values) > 1:
                report["property_conflicts"].append(dict(taxonomy_key=entry.key, property=prop,
                                                        language=language, values=sorted(values)))
    report["ambiguous_aliases"] = quarantine_aliases(bundle)
    bundle["metadata"].update(off_source_sha256=digest, alias_matching_policy="unique_exact_only",
                              taxonomy_claims_verified=False)
    report["counts"] = validate_catalog(bundle)
    report["quarantined_aliases"] = len(bundle["ingredient_alias_review"])
    return bundle, report


def validate_off_catalog(bundle):
    owners = alias_owners(bundle)
    ingredients = {r["id"]: r for r in bundle["ingredients"]}
    review_names = {r["normalized_alias"] for r in bundle["ingredient_alias_review"]}
    for row in bundle["ingredient_aliases"]:
        name = normalize_text(row["alias"])
        if not name or name != row["normalized_alias"] or len(owners[name]) > 1 or name in review_names:
            raise ValueError("Ambiguous/unreviewed alias is enabled for automatic matching")
        code = strict_code(row["alias"])
        if code and code != strict_code(ingredients[row["ingredient_id"]]["e_code"]):
            raise ValueError("Alias E-code does not match its ingredient")
        if not code and sum(c.isalpha() for c in name) < 2:
            raise ValueError("Name fragment is enabled for automatic matching")
    for row in bundle["taxonomy_properties"]:
        if row["verification_status"] != "reference_only":
            raise ValueError("Taxonomy claims must not be presented as verified regulatory evidence")
    new_ids = {r["ingredient_id"] for r in bundle["taxonomy_entries"]
               if r["match_method"] == "new_ordinary_ingredient"}
    if any(r["ingredient_id"] in new_ids for r in bundle["ingredient_rules"]):
        raise ValueError("OFF ordinary ingredients must not receive fabricated regulatory rules")
