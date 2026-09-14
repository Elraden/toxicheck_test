"""Build a traceable import bundle; never treat extracted prose as risk scores."""

import argparse
import copy
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.services.normalization import normalize_text


ROOT = Path(__file__).resolve().parents[3]
INPUT_NAMES = ("toxicheck_tr_ts_029_2012.json", "toxicheck_tr_ts_022_2011_rules.json")
OUTPUT_NAME = "toxicheck_regulatory_enriched.json"
AMENDMENT_PDF = (
    "https://docs.eaeunion.org/upload/iblock/765/"
    "txjkhhqysqqmwvgazt2db8yxclc0oiju/err_31082023_84_att.pdf"
)
LABEL_PDF = "https://eec.eaeunion.org/upload/medialibrary/9db/TrTsPishevkaMarkirovka.pdf"
TRANSITION_URL = "https://docs.eaeunion.org/documents/429/7931/"
FRAGMENT_TYPES = {"regulatory_text", "source_metadata", "source_amendment_note", "source_table_note"}
TABLES = ("regulatory_sources", "ingredients", "ingredient_aliases", "ingredient_rules")


def stable_id(kind: str, key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"toxicheck/regulatory/v1/{kind}/{key}"))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def evidence(source: dict, locator: str, status: str, *, url: str | None = None,
             role: str = "normative_act", **coordinates) -> dict:
    return {
        "source_id": source["id"], "source_code": source["code"],
        "url": url or source["url"], "locator": locator,
        "document_role": role, "verification_status": status, **coordinates,
    }


def enrich_reference(rule: dict, source: dict) -> None:
    conditions = rule.setdefault("conditions", {})
    original = rule.get("citation", "")
    conditions["original_citation"] = original
    conditions["evaluation"] = "reference_only"
    conditions["medical_risk_assessed"] = False
    conditions["source_text_sha256"] = hashlib.sha256(
        (conditions.get("source_text") or rule.get("explanation") or "").encode("utf-8")
    ).hexdigest()
    fragment = rule["rule_type"] in FRAGMENT_TYPES
    removed = bool(re.search(r"позици[яи].{0,20}исключен", rule.get("explanation") or "", re.I))
    appendix = conditions.get("appendix")
    if fragment or removed:
        conditions["record_kind"] = "source_fragment"
        status = "needs_review"
        locator = original
    elif appendix is not None:
        locator = f"{source['code']}, приложение {appendix}"
        if conditions.get("table") is not None:
            locator += f", таблица {conditions['table']}"
        # A named row survives changes in the extractor's row numbering.
        row_text = conditions.get("source_text") or rule.get("explanation") or rule["title"]
        locator += f", позиция: {row_text}"
        conditions["record_kind"] = "normative_reference"
        status = "extracted_not_verified"
    elif re.search(r"пункт \d", original):
        locator = original
        status = "extracted_not_verified"
        conditions["record_kind"] = "normative_reference"
    else:
        locator = original or "Место в документе требует проверки"
        status = "needs_review"
        conditions["record_kind"] = "source_fragment"
    conditions["verification_status"] = status
    conditions["evidence"] = [evidence(source, locator, status,
        role="consolidated_copy", appendix=appendix, table=conditions.get("table"),
        extraction_row=conditions.get("row"))]
    rule["citation"] = f"{locator} | {source['url']}"


def build_bundle(root: Path = ROOT) -> tuple[dict, dict]:
    seed = read_json(ROOT / "backend/db/regulatory_status_seed.json")
    timestamp = seed["checked_on"] + "T00:00:00Z"
    records = {table: {} for table in TABLES}
    inputs = {}
    for name in INPUT_NAMES:
        path = root / name
        inputs[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        payload = read_json(path)
        for table in TABLES:
            for row in payload[table]:
                # The 022 dataset uses short E-code names for shared ingredients.
                records[table].setdefault(row["id"], copy.deepcopy(row))
    sources = records["regulatory_sources"]
    ingredients = records["ingredients"]
    aliases = records["ingredient_aliases"]
    rules = records["ingredient_rules"]
    by_code = {i["e_code"].upper(): i for i in ingredients.values() if i.get("e_code")}
    by_source_code = {s["code"]: s for s in sources.values()}

    def add_source(code, title, url, version, effective=None):
        row = dict(id=stable_id("source", code), code=code, title=title, url=url,
                   version_date=version, effective_from=effective, effective_to=None,
                   created_at=timestamp, updated_at=timestamp)
        sources[row["id"]] = row
        by_source_code[code] = row
        return row

    amendment = add_source("EEC_COUNCIL_84_2023", "Решение Совета ЕЭК № 84 от 29.08.2023",
        "https://docs.eaeunion.org/documents/418/7616/", "2023-08-29", "2024-02-27")
    transition = add_source("EEC_BOARD_8_2024", "Решение Коллегии ЕЭК № 8 от 23.01.2024",
        TRANSITION_URL, "2024-01-23", "2024-02-27")
    rpn = add_source("RPN_VORONEZH_2025_04_21", "Публикация Роспотребнадзора от 21.04.2025 (seed)",
        "https://36.rospotrebnadzor.ru/news/28626", "2025-04-21")
    confirmed_rpn = add_source("RPN_ALTAI_2021_07_01", "Роспотребнадзор: О пищевых добавках, 01.07.2021",
        "https://04.rospotrebnadzor.ru/index.php/component/content/article/41-faq/14896-01072021.pdf", "2021-07-01")
    add_source("EEC_MONITORING_2_4_4", "Разъяснения ЕЭК по изменениям № 2 (доклад)",
        "https://eec.eaeunion.org/comission/department/dobd/doklady-o-monitoringe-resheniya-voprosov-biznesa/Doklad%202-4-4.pdf", None)
    for rule in rules.values():
        enrich_reference(rule, sources[rule["source_id"]])

    special_ids = set()
    for status, key in (("BANNED", "banned"), ("PHASE_OUT", "phase_out")):
        for code, name, english, category in seed[key]:
            item_key = code.upper() if code else ("stevia" if "Stevia" in name else "red_rice")
            ingredient = by_code.get(item_key)
            if ingredient is None:
                ingredient = next((i for i in ingredients.values()
                    if normalize_text(i["canonical_name_ru"]) == normalize_text(name)), None)
            if ingredient is None:
                ingredient = dict(id=stable_id("ingredient", item_key), created_at=timestamp)
            ingredient.update(kind="food_additive", e_code=code, canonical_name_ru=name,
                canonical_name_en=english, category=category, description=f"{name} ({english})",
                is_active=True, updated_at=timestamp)
            ingredients[ingredient["id"]] = ingredient
            special_ids.add(ingredient["id"])
            if code:
                by_code[code.upper()] = ingredient

            names = [name, english, *seed["additional_aliases"].get(code or item_key, [])]
            if code:
                names += [code, code.replace("E", "Е", 1), "E-" + code[1:], "E " + code[1:]]
            for alias in names:
                if normalize_text(alias) in {normalize_text(a) for a in seed["ambiguous_aliases"]}:
                    continue
                alias_id = stable_id("alias", ingredient["id"] + "/" + normalize_text(alias))
                aliases[alias_id] = dict(id=alias_id, ingredient_id=ingredient["id"], alias=alias,
                    normalized_alias=normalize_text(alias), language="ru" if re.search("[а-яА-Я]", alias) else "en",
                    source="regulatory_status_seed", confidence=1, created_at=timestamp, updated_at=timestamp)

            source = amendment if status == "PHASE_OUT" else rpn
            if status == "PHASE_OUT":
                locator = f"Решение № 84, приложение, пункт 2, подпункт 7(а); приложение 2 ТР ТС 029/2012, позиция {code or name}"
                refs = [evidence(amendment, locator, "verified_primary", url=AMENDMENT_PDF + "#page=40", pdf_page=40),
                    evidence(transition, "Решение № 8, пункт 1, подпункты а, б, в", "needs_full_text_review")]
                explanation = ("Позиция исключена из перечня пищевых добавок с 27.02.2024. "
                    "Проверьте дату изготовления и документы об оценке соответствия: действуют переходные положения Решения № 8. "
                    "Законно выпущенная продукция может обращаться до окончания срока годности.")
                extra = {"transition": {"effective_from": "2024-02-27", "months": 36,
                    "last_production_date": "2027-02-26", "transition_end": "2027-02-27",
                    "boundary_basis": "calendar_calculation_from_effective_date",
                    "requires_preexisting_conformity_documents": True,
                    "legacy_circulation_until_expiry": True,
                    "required_context": ["manufactured_at", "expires_at", "conformity_documents"]},
                    "scope": "use_as_food_additive", "verification_status": "verified_primary"}
                effective = "2024-02-27"
            else:
                locator = f"Публикация 21.04.2025, перечень запрещенных добавок, позиция {code}; требуется сверка текста и первичного акта"
                position = {"E121": 1, "E123": 2, "E128": 3, "E216": 4, "E217": 5, "E240": 6, "E924A": 6, "E924B": 7}[code.upper()]
                source = confirmed_rpn
                locator = f"О пищевых добавках, 01.07.2021, стр. 4, перечень запрещенных добавок, позиция {position}: {code}"
                refs = [evidence(confirmed_rpn, locator, "verified_official_publication",
                    url=confirmed_rpn["url"] + "#page=4", role="official_explanation", pdf_page=4)]
                explanation = (f"{code} указан как запрещенный при производстве пищевых продуктов в России "
                    "в публикации Роспотребнадзора от 01.07.2021. Действующее нормативное основание требует отдельной сверки.")
                extra = {"verification_status": "verified_official_publication", "primary_basis_required": True,
                         "scope": "use_as_food_additive", "production_ready": False}
                effective = None
            rule_id = stable_id("rule", f"{status}/{item_key}")
            rules[rule_id] = dict(id=rule_id, ingredient_id=ingredient["id"], source_id=source["id"],
                rule_type="regulatory_status", severity="attention", title=f"{status}: {name}",
                explanation=explanation, citation=f"{locator} | {refs[0]['url']}",
                conditions={"regulatory_status": status, "evaluation": "notice", "evidence": refs,
                    "medical_risk_assessed": False, "match_policy": "exact_only",
                    "record_kind": "status_rule", **extra},
                effective_from=effective, effective_to=None, created_at=timestamp, updated_at=timestamp)

    # Preserve the former preference catalog and its IDs as migration aliases.
    for old in read_json(ROOT / "backend/db/preference_ingredients_seed.json"):
        code = old.get("code")
        ingredient = by_code.get(code.upper()) if code else next((i for i in ingredients.values()
            if normalize_text(i["canonical_name_ru"]) == normalize_text(old["name"])), None)
        if ingredient is None:
            ingredient = dict(id=stable_id("ingredient", "preference/" + old["id"]), kind="ingredient",
                e_code=code, canonical_name_ru=old["name"], canonical_name_en=None,
                category=old["category"], description=old["description"], is_active=True,
                created_at=timestamp, updated_at=timestamp)
            ingredients[ingredient["id"]] = ingredient
        for value, alias_source in ((old["name"], "preference_name"), (old["id"], "legacy_preference_id")):
            alias_id = stable_id("alias", ingredient["id"] + "/preference/" + value)
            aliases[alias_id] = dict(id=alias_id, ingredient_id=ingredient["id"], alias=value,
                normalized_alias=normalize_text(value), language="ru", source=alias_source,
                confidence=1, created_at=timestamp, updated_at=timestamp)

    ambiguous = {normalize_text(a) for a in seed["ambiguous_aliases"]}
    rejected_aliases = []
    for alias_id, alias in list(aliases.items()):
        normalized = normalize_text(alias["alias"])
        if alias["ingredient_id"] in special_ids and (
            normalized in ambiguous or "позиция исключена" in normalized
        ):
            rejected_aliases.append(alias)
            del aliases[alias_id]
        else:
            alias["normalized_alias"] = normalized

    label_source = by_source_code["TR_TS_022_2011"]
    for rule in rules.values():
        conditions = rule["conditions"]
        if rule["rule_type"] == "mandatory_warning_label":
            code = (ingredients[rule["ingredient_id"]].get("e_code") or "").upper()
            if code in seed["colour_warning_codes"] + seed["phenylalanine_warning_codes"]:
                point = 18 if code in seed["colour_warning_codes"] else 15
                locator = f"ТР ТС 022/2011, статья 4, часть 4.4, пункт {point}"
                conditions.update(evaluation="notice", verification_status="verified_primary",
                    regulatory_status="ATTENTION", match_policy="exact_only",
                    evidence=[evidence(label_source, locator, "verified_primary", url=LABEL_PDF + "#page=11", pdf_page=11)])
                rule.update(severity="attention", citation=f"{locator} | {LABEL_PDF}#page=11")
        if rule["rule_type"] == "permitted_food_additive" and rule["ingredient_id"] not in special_ids:
            conditions["regulatory_status"] = "PERMITTED_WITH_CONDITIONS"
            conditions["required_context"] = ["food_category", "concentration", "technological_function"]
            conditions["does_not_prove_product_compliance"] = True
            code = (ingredients[rule["ingredient_id"]].get("e_code") or "").upper()
            if code in seed["introduced_e_codes"]:
                locator = f"Решение № 84, приложение, пункт 2, подпункт 7(б); позиция {code}"
                conditions["evidence"].append(evidence(amendment, locator, "verified_primary", url=AMENDMENT_PDF + "#page=41", pdf_page=41))
                rule["effective_from"] = "2024-02-27"

    bundle = {table: list(rows.values()) for table, rows in records.items()}
    bundle["source_fragments"] = [r for r in bundle["ingredient_rules"]
                                  if r["conditions"]["record_kind"] == "source_fragment"]
    bundle["ingredient_rules"] = [r for r in bundle["ingredient_rules"]
                                  if r["conditions"]["record_kind"] != "source_fragment"]
    bundle["retired_alias_ids"] = [row["id"] for row in rejected_aliases]
    bundle["metadata"] = {"dataset": "toxicheck_regulatory_enriched", "generated_at": timestamp,
        "input_sha256": inputs, "source_priority": ["normative_act", "transition_act", "eec_explanation", "official_explanation"],
        "medical_risk_policy": "Not inferred from regulatory permission or E-code presence",
        "seed_sha256": hashlib.sha256((ROOT / "backend/db/regulatory_status_seed.json").read_bytes()).hexdigest(),
        "preferences_seed_sha256": hashlib.sha256((ROOT / "backend/db/preference_ingredients_seed.json").read_bytes()).hexdigest()}
    report = validate_bundle(bundle)
    report["rejected_aliases"] = rejected_aliases
    report["introduced_e_codes_present"] = [code for code in seed["introduced_e_codes"] if code in by_code]
    bundle["validation"] = {key: value for key, value in report.items() if key not in {"review_queue", "rejected_aliases"}}
    return bundle, report


def validate_bundle(bundle: dict) -> dict:
    for table in TABLES:
        ids = [row["id"] for row in bundle[table]]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate IDs in {table}")
    ingredient_ids = {i["id"] for i in bundle["ingredients"]}
    source_ids = {s["id"] for s in bundle["regulatory_sources"]}
    codes = [i["e_code"].upper() for i in bundle["ingredients"] if i.get("e_code")]
    if len(codes) != len(set(codes)):
        raise ValueError("Duplicate E-codes")
    for alias in bundle["ingredient_aliases"]:
        if alias["ingredient_id"] not in ingredient_ids:
            raise ValueError(f"Orphan alias {alias['id']}")
    retired_aliases = set(bundle.get("retired_alias_ids", []))
    if retired_aliases & {a["id"] for a in bundle["ingredient_aliases"]}:
        raise ValueError("A retired alias is also present in active aliases")
    rule_ids = {r["id"] for r in bundle["ingredient_rules"]}
    fragments = bundle.get("source_fragments", [])
    if rule_ids & {r["id"] for r in fragments}:
        raise ValueError("A source fragment is also present in active rules")
    for fragment in fragments:
        if (fragment["source_id"] not in source_ids
                or fragment["conditions"].get("record_kind") != "source_fragment"
                or fragment["conditions"].get("evaluation") != "reference_only"):
            raise ValueError(f"Invalid source fragment {fragment['id']}")
    review = []
    for rule in bundle["ingredient_rules"]:
        if rule.get("ingredient_id") is not None and rule["ingredient_id"] not in ingredient_ids:
            raise ValueError(f"Orphan rule {rule['id']}")
        if rule["source_id"] not in source_ids or not rule.get("citation"):
            raise ValueError(f"Missing source/citation for {rule['id']}")
        refs = rule.get("conditions", {}).get("evidence", [])
        if not refs or any(not r.get("locator") or not r.get("url") or r["source_id"] not in source_ids for r in refs):
            raise ValueError(f"Incomplete evidence for {rule['id']}")
        if any(r["verification_status"] != "verified_primary" for r in refs):
            review.append({"rule_id": rule["id"], "citation": rule["citation"],
                           "statuses": [r["verification_status"] for r in refs]})
    return {**{table: len(bundle[table]) for table in TABLES}, "foreign_keys_valid": True,
        "source_fragments": len(bundle.get("source_fragments", [])),
        "rules_with_evidence": len(bundle["ingredient_rules"]),
        "status_counts": dict(Counter(r["conditions"].get("regulatory_status", "REFERENCE") for r in bundle["ingredient_rules"])),
        "rules_requiring_review": len(review), "review_queue": review}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    bundle, report = build_bundle(args.root)
    for name, payload in ((OUTPUT_NAME, bundle), ("regulatory_review_report.json", report)):
        (args.root / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(bundle["validation"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
