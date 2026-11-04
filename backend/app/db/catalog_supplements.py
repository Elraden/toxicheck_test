"""Reviewed, source-linked additions to the flat ingredient catalog."""

from copy import deepcopy
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.services.normalization import normalize_text

SUPPLEMENT = Path(__file__).resolve().parents[2] / "db/rpn_banned_additives.json"


def stable_id(kind, key):
    return str(uuid5(NAMESPACE_URL, f"toxicheck/catalog/supplement/v1/{kind}/{key}"))


def ensure_row(rows, candidate, keys):
    matches = [row for row in rows if all(row[key] == candidate[key] for key in keys)]
    if len(matches) > 1:
        raise ValueError(f"Ambiguous supplement match: {keys}")
    if matches:
        return matches[0]
    rows.append(candidate)
    return candidate


def supplement_catalog(original, seed=None):
    seed = seed or json.loads(SUPPLEMENT.read_text(encoding="utf-8"))
    bundle = deepcopy(original)
    source = ensure_row(bundle["regulatory_sources"], dict(
        id=stable_id("source", seed["source_code"]), code=seed["source_code"],
        title=seed["source_title"], url=seed["source_url"], version_date=seed["source_date"],
        effective_from=None, effective_to=None,
    ), ("code",))
    by_code = {row["e_code"].lower(): row for row in bundle["ingredients"] if row["e_code"]}
    for item in seed["ingredients"]:
        code = item["e_code"]
        row = by_code.get(code.lower())
        if row is None:
            row = dict(id=stable_id("ingredient", code), kind="food_additive", e_code=code,
                       canonical_name_ru=item["name_ru"], canonical_name_en=item["name_en"],
                       category=item["category"], description=None, full_description=None, is_active=True)
            bundle["ingredients"].append(row)
            by_code[code.lower()] = row
        old_name = row["canonical_name_ru"]
        row.update(canonical_name_ru=item["name_ru"], canonical_name_en=item["name_en"],
                   category=item["category"], description=item["description"],
                   full_description=item["full_description"])
        identifier = row["id"]
        names = [code, *item["aliases"]]
        # The bare plant name must never be introduced as an alias of the dye.
        if code != "E123":
            names.extend([old_name, item["name_ru"], item["name_en"]])
        for alias in dict.fromkeys(names):
            normalized = normalize_text(alias)
            if any(a["ingredient_id"] == identifier and a["normalized_alias"] == normalized
                   for a in bundle["ingredient_aliases"]):
                continue
            bundle["ingredient_aliases"].append(dict(
                id=stable_id("alias", f"{identifier}/{normalized}"), ingredient_id=identifier,
                alias=alias, normalized_alias=normalized, language="en" if alias.isascii() else "ru",
                source=None, confidence=1,
            ))
        profile = ensure_row(bundle["ingredient_profiles"], dict(
            id=stable_id("profile", identifier), ingredient_id=identifier, source_id=None,
            source_url=item["references"][0]["url"], fetched_at=None,
            has_article=True, full_description_status="reviewed_primary_references",
        ), ("ingredient_id",))
        profile["full_description_status"] = "reviewed_primary_references"
        for tag_type, name in (("category", item["category"]), ("origin", item["origin"])):
            if name and not any(t["ingredient_id"] == identifier and t["tag_type"] == tag_type
                                for t in bundle["ingredient_tags"]):
                bundle["ingredient_tags"].append(dict(
                    id=stable_id("tag", f"{identifier}/{tag_type}/{name}"), ingredient_id=identifier,
                    tag_type=tag_type, name_ru=name, url=None,
                ))
        locator = (f"О пищевых добавках, 01.07.2021, стр. 4, перечень запрещенных добавок, "
                   f"позиция {item['position']}: {code}")
        rule = ensure_row(bundle["ingredient_rules"], dict(
            id=stable_id("rule", code), ingredient_id=identifier, source_id=source["id"],
            regulatory_status="BANNED", title=f"Запрет применения в пищевых продуктах: {item['name_ru']}",
            explanation=None, citation=None, scope="use_as_food_additive", jurisdiction="RU",
            verification_status="verified_official_publication", evaluation="notice", match_policy="exact_only",
            primary_basis_required=True, production_ready=False, product_compliance_assessed=False,
            effective_from=None, effective_to=None,
        ), ("ingredient_id", "source_id", "regulatory_status"))
        rule.update(title=f"Запрет применения в пищевых продуктах: {item['name_ru']}",
                    explanation=(f"{code} включён Роспотребнадзором в перечень добавок, запрещённых "
                                 "при производстве пищевых продуктов в России в 2021 году. "
                                 "Действующее нормативное основание требует отдельной сверки."),
                    citation=f"{locator} | {seed['pdf_url']}", jurisdiction="RU")
        evidence = ensure_row(bundle["rule_evidence"], dict(
            id=stable_id("evidence", code), rule_id=rule["id"], source_id=source["id"],
            url=seed["pdf_url"], locator=locator, document_role="official_explanation",
            verification_status="verified_official_publication", pdf_page=4, appendix=None, table_label=None,
        ), ("rule_id", "source_id", "url"))
        evidence["locator"] = locator
        for ref in item["references"]:
            document = ensure_row(bundle["documents"], dict(
                id=stable_id("document", ref["url"]), source_id=None, title=ref["title"], url=ref["url"],
                verification_status="verified_description_source", fetched_at=None,
            ), ("url",))
            ensure_row(bundle["ingredient_references"], dict(
                id=stable_id("reference", f"{identifier}/{ref['url']}"), ingredient_id=identifier,
                document_id=document["id"], title=f"{ref['title']}. {ref['locator']}", url=ref["url"],
                relation="description_source", organization=ref["organization"],
            ), ("ingredient_id", "url", "relation"))
    bundle["metadata"]["rpn_banned_supplement_reviewed_on"] = seed["reviewed_on"]
    return bundle
