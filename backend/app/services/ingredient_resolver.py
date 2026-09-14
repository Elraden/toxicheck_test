from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.ingredients import (
    IngredientRuleOut,
    MatchedIngredientOut,
    ResolveIngredientsResponse,
)
from app.services.normalization import (
    normalize_e_code,
    normalize_text,
    split_ingredients_text,
)
from app.services.regulatory_rules import assess_rule


class IngredientResolver:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve(self, ingredients_text: str) -> ResolveIngredientsResponse:
        matched: list[MatchedIngredientOut] = []
        unmatched: list[str] = []
        seen_ingredient_ids: set[str] = set()

        for raw_part in split_ingredients_text(ingredients_text):
            match = await self._match_part(raw_part)

            if not match:
                unmatched.append(raw_part)
                continue

            ingredient_id = str(match["ingredient_id"])

            if ingredient_id in seen_ingredient_ids:
                continue

            seen_ingredient_ids.add(ingredient_id)

            rules = [assess_rule(rule, matched_by=match["matched_by"])
                     for rule in await self._load_rules(ingredient_id)]
            rules.sort(key=lambda rule: {"forbidden": 3, "avoid": 2, "attention": 1}.get(
                rule.assessment_severity, 0), reverse=True)
            severity = self._highest_severity(rules)

            matched.append(
                MatchedIngredientOut(
                    ingredient_id=ingredient_id,
                    name=match["name"],
                    code=match["code"],
                    raw_text=raw_part,
                    matched_by=match["matched_by"],
                    match_score=match["match_score"],
                    severity=severity,
                    rules=rules,
                )
            )

        return ResolveIngredientsResponse(matched=matched, unmatched=unmatched)

    async def _match_part(self, raw_part: str) -> dict | None:
        e_code = normalize_e_code(raw_part)

        if e_code:
            exact_code_match = await self._match_by_e_code(e_code)

            if exact_code_match:
                return exact_code_match

        normalized = normalize_text(raw_part)

        exact_alias_match = await self._match_by_alias(normalized)

        if exact_alias_match:
            return exact_alias_match

        return await self._match_by_similarity(normalized)

    async def _match_by_e_code(self, e_code: str) -> dict | None:
        result = await self.session.execute(
            text(
                """
                SELECT
                  id AS ingredient_id,
                  canonical_name_ru AS name,
                  e_code AS code,
                  'e_code' AS matched_by,
                  1.0 AS match_score
                FROM ingredients
                WHERE lower(e_code) = lower(:e_code)
                  AND is_active = true
                LIMIT 1
                """
            ),
            {"e_code": e_code},
        )

        row = result.mappings().first()
        return dict(row) if row else None

    async def _match_by_alias(self, normalized_alias: str) -> dict | None:
        result = await self.session.execute(
            text(
                """
                SELECT
                  i.id AS ingredient_id,
                  i.canonical_name_ru AS name,
                  i.e_code AS code,
                  'alias' AS matched_by,
                  a.confidence::float AS match_score
                FROM ingredient_aliases a
                JOIN ingredients i ON i.id = a.ingredient_id
                WHERE a.normalized_alias = :normalized_alias
                  AND i.is_active = true
                ORDER BY a.confidence DESC
                LIMIT 1
                """
            ),
            {"normalized_alias": normalized_alias},
        )

        row = result.mappings().first()
        return dict(row) if row else None

    async def _match_by_similarity(self, normalized_alias: str) -> dict | None:
        if len(normalized_alias) < 4:
            return None

        result = await self.session.execute(
            text(
                """
                SELECT
                  i.id AS ingredient_id,
                  i.canonical_name_ru AS name,
                  i.e_code AS code,
                  'similarity' AS matched_by,
                  similarity(a.normalized_alias, :normalized_alias)::float
                    AS match_score
                FROM ingredient_aliases a
                JOIN ingredients i ON i.id = a.ingredient_id
                WHERE a.normalized_alias % :normalized_alias
                  AND i.is_active = true
                  AND NOT EXISTS (
                    SELECT 1 FROM ingredient_rules r
                    WHERE r.ingredient_id = i.id
                      AND r.conditions ->> 'match_policy' = 'exact_only'
                  )
                ORDER BY similarity(a.normalized_alias, :normalized_alias) DESC
                LIMIT 1
                """
            ),
            {"normalized_alias": normalized_alias},
        )

        row = result.mappings().first()

        if not row or row["match_score"] < 0.65:
            return None

        return dict(row)

    async def _load_rules(self, ingredient_id: str) -> list[IngredientRuleOut]:
        result = await self.session.execute(
            text(
                """
                SELECT
                  r.id::text,
                  r.rule_type,
                  r.severity,
                  r.title,
                  r.explanation,
                  r.citation,
                  r.conditions,
                  s.id::text AS source_id,
                  s.code AS source_code,
                  s.title AS source_title,
                  s.url AS source_url
                FROM ingredient_rules r
                JOIN regulatory_sources s ON s.id = r.source_id
                WHERE r.ingredient_id = :ingredient_id
                  AND (r.effective_from IS NULL OR r.effective_from <= CURRENT_DATE)
                  AND (r.effective_to IS NULL OR r.effective_to >= CURRENT_DATE)
                  AND (s.effective_from IS NULL OR s.effective_from <= CURRENT_DATE)
                  AND (s.effective_to IS NULL OR s.effective_to >= CURRENT_DATE)
                  AND COALESCE(r.conditions ->> 'record_kind', '') <> 'source_fragment'
                ORDER BY
                  CASE r.severity
                    WHEN 'forbidden' THEN 4
                    WHEN 'avoid' THEN 3
                    WHEN 'attention' THEN 2
                    ELSE 1
                  END DESC
                """
            ),
            {"ingredient_id": ingredient_id},
        )

        return [
            IngredientRuleOut(**dict(row))
            for row in result.mappings().all()
        ]

    @staticmethod
    def _highest_severity(rules: list[IngredientRuleOut]) -> str:
        weights = {
            "neutral": 0,
            "attention": 1,
            "avoid": 2,
            "forbidden": 3,
        }

        return max(
            (rule.assessment_severity or ("neutral" if rule.severity == "regulatory" else rule.severity) for rule in rules),
            key=lambda severity: weights.get(severity, 0),
            default="neutral",
        )
