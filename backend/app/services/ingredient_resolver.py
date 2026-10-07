import re
from collections import defaultdict
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.ingredients import IngredientRuleOut, MatchedIngredientOut, ResolveIngredientsResponse
from app.services.normalization import normalize_e_code, normalize_text, split_ingredients_text
from app.services.regulatory_rules import assess_rule


class IngredientResolver:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve(self, ingredients_text: str) -> ResolveIngredientsResponse:
        parts = [(raw, None if re.search(r"\b(?:без|without|free\s+from)\b", raw, re.IGNORECASE)
                  else normalize_e_code(raw), normalize_text(raw))
                 for raw in split_ingredients_text(ingredients_text)]
        codes = await self._match_codes(sorted({code.lower() for _, code, _ in parts if code}))
        aliases = await self._match_aliases(sorted({alias for _, code, alias in parts if not code and alias}))
        matches, unmatched, seen = [], [], set()
        for raw, code, alias in parts:
            # An unknown subtype must not silently fall back to its parent code or a name.
            match = codes.get(code.lower()) if code else aliases.get(alias)
            if match is None:
                unmatched.append(raw)
                continue
            identifier = str(match["ingredient_id"])
            if identifier not in seen:
                seen.add(identifier)
                matches.append((raw, match))
        rules_by_ingredient = await self._load_rules(sorted(seen))
        matched = []
        for raw, match in matches:
            identifier = str(match["ingredient_id"])
            rules = self.assess_rules(rules_by_ingredient.get(identifier, []), match["matched_by"])
            matched.append(MatchedIngredientOut(
                ingredient_id=identifier, name=match["name"], code=match["code"],
                raw_text=raw, matched_by=match["matched_by"], match_score=match["match_score"],
                severity=self.highest_severity(rules), rules=rules,
            ))
        return ResolveIngredientsResponse(matched=matched, unmatched=unmatched)

    async def catalog_rules(self, ingredient_ids: list[str]) -> dict[str, list[IngredientRuleOut]]:
        return {identifier: self.assess_rules(rules, "ingredient_id")
                for identifier, rules in (await self._load_rules(ingredient_ids)).items()}

    @staticmethod
    def assess_rules(rules: list[IngredientRuleOut], matched_by: str) -> list[IngredientRuleOut]:
        assessed = [assess_rule(rule, matched_by=matched_by) for rule in rules]
        return sorted(assessed, key=lambda r: {"forbidden": 3, "avoid": 2, "attention": 1}.get(
            r.assessment_severity, 0), reverse=True)

    async def _match_codes(self, codes: list[str]) -> dict:
        if not codes:
            return {}
        result = await self.session.execute(text("""
            SELECT id::text AS ingredient_id, canonical_name_ru AS name, e_code AS code,
                   'e_code' AS matched_by, 1.0 AS match_score
            FROM catalog.ingredients
            WHERE lower(e_code) = ANY(CAST(:codes AS text[])) AND is_active
        """), {"codes": codes})
        return {r["code"].lower(): dict(r) for r in result.mappings().all()}

    async def _match_aliases(self, aliases: list[str]) -> dict:
        if not aliases:
            return {}
        result = await self.session.execute(text("""
            SELECT a.normalized_alias, i.id::text AS ingredient_id,
                   i.canonical_name_ru AS name, i.e_code AS code,
                   'alias' AS matched_by, max(a.confidence)::float AS match_score
            FROM catalog.ingredient_aliases a
            JOIN catalog.ingredients i ON i.id = a.ingredient_id
            WHERE a.normalized_alias = ANY(CAST(:aliases AS text[])) AND i.is_active
              AND NOT EXISTS (SELECT 1 FROM catalog.ingredient_alias_review review
                              WHERE review.normalized_alias = a.normalized_alias)
            GROUP BY a.normalized_alias, i.id
        """), {"aliases": aliases})
        candidates = defaultdict(list)
        for row in result.mappings().all():
            candidates[row["normalized_alias"]].append(dict(row))
        return {alias: rows[0] for alias, rows in candidates.items() if len(rows) == 1}

    async def _load_rules(self, ingredient_ids: list[str]) -> dict[str, list[IngredientRuleOut]]:
        if not ingredient_ids:
            return {}
        result = await self.session.execute(text("""
            SELECT r.*, s.code AS source_code, s.title AS source_title, s.url AS source_url
            FROM catalog.ingredient_rules r
            JOIN catalog.regulatory_sources s ON s.id = r.source_id
            WHERE r.ingredient_id = ANY(CAST(:ids AS uuid[]))
              AND (r.effective_from IS NULL OR r.effective_from <= CURRENT_DATE)
              AND (r.effective_to IS NULL OR r.effective_to >= CURRENT_DATE)
              AND (s.effective_from IS NULL OR s.effective_from <= CURRENT_DATE)
              AND (s.effective_to IS NULL OR s.effective_to >= CURRENT_DATE)
            ORDER BY r.ingredient_id, r.id
        """), {"ids": ingredient_ids})
        rows = [dict(row) for row in result.mappings().all()]
        if not rows:
            return {}
        ids = [str(row["id"]) for row in rows]
        evidence = defaultdict(list)
        references = await self.session.execute(text("""
            SELECT e.*, s.title AS source_title, s.code AS source_code
            FROM catalog.rule_evidence e JOIN catalog.regulatory_sources s ON s.id = e.source_id
            WHERE e.rule_id = ANY(CAST(:ids AS uuid[])) ORDER BY e.rule_id, e.id
        """), {"ids": ids})
        for ref in references.mappings().all():
            record = dict(ref)
            rule_id = str(record.pop("rule_id"))
            record["id"] = str(record["id"])
            record["source_id"] = str(record["source_id"])
            record["table"] = record.pop("table_label")
            evidence[rule_id].append(record)
        result = await self.session.execute(text("""
            SELECT * FROM catalog.rule_transitions WHERE rule_id = ANY(CAST(:ids AS uuid[]))
        """), {"ids": ids})
        transitions = {}
        for row in result.mappings().all():
            record = dict(row)
            rule_id = str(record.pop("rule_id"))
            transitions[rule_id] = {key: value.isoformat() if isinstance(value, date) else value
                                    for key, value in record.items()}
        rules = defaultdict(list)
        for row in rows:
            rule_id = str(row["id"])
            # Keep the existing API shape; conditions are assembled, never read from legacy JSONB.
            conditions = {key: row[key] for key in (
                "regulatory_status", "scope", "jurisdiction", "verification_status", "evaluation",
                "match_policy", "primary_basis_required", "production_ready", "product_compliance_assessed",
            )}
            conditions["evidence"] = evidence[rule_id]
            if rule_id in transitions:
                conditions["transition"] = transitions[rule_id]
            severity = {"BANNED": "forbidden", "PHASE_OUT": "attention"}.get(row["regulatory_status"], "regulatory")
            rules[str(row["ingredient_id"])].append(IngredientRuleOut(
                id=rule_id, rule_type="regulatory_status", severity=severity,
                title=row["title"], explanation=row["explanation"], citation=row["citation"],
                source_id=str(row["source_id"]), source_code=row["source_code"],
                source_title=row["source_title"], source_url=row["source_url"], conditions=conditions,
            ))
        return rules

    @staticmethod
    def highest_severity(rules: list[IngredientRuleOut]) -> str:
        weights = {"unknown": -1, "neutral": 0, "attention": 1, "avoid": 2, "forbidden": 3}
        return max(
            (rule.assessment_severity or ("neutral" if rule.severity == "regulatory" else rule.severity)
             for rule in rules),
            key=lambda severity: weights.get(severity, -1), default="unknown",
        )
