from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.ingredients import IngredientDetail, IngredientPage, IngredientSummary
from app.services.ingredient_resolver import IngredientResolver
from app.services.normalization import normalize_e_code, normalize_text


class IngredientCatalog:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.resolver = IngredientResolver(session)

    async def list(self, query: str, kind: str, limit: int, offset: int, *, status: str = "all") -> IngredientPage:
        # Position searches treat SQL wildcard characters as ordinary text.
        where = """
            i.is_active
            AND (:kind = 'all' OR (:kind = 'additives' AND i.e_code IS NOT NULL)
                 OR (:kind = 'foods' AND i.e_code IS NULL))
            AND (:query = '' OR strpos(lower(i.canonical_name_ru), :query) > 0
                 OR strpos(lower(coalesce(i.canonical_name_en, '')), :query) > 0
                 OR strpos(lower(coalesce(i.e_code, '')), :code) > 0
                 OR (:normalized <> '' AND EXISTS (
                     SELECT 1 FROM catalog.ingredient_aliases a WHERE a.ingredient_id = i.id
                     AND strpos(a.normalized_alias, :normalized) > 0)))
        """
        params = {"query": query.strip().lower(), "normalized": normalize_text(query),
                  "code": (normalize_e_code(query) or query.strip()).lower(), "kind": kind}
        rules = None
        if status != "all":
            # Assess the entire matching set before pagination using the same rules as detail/scan.
            candidates = await self.session.execute(text(
                f"SELECT i.id::text AS id FROM catalog.ingredients i WHERE {where}"
            ), params)
            ids = list(candidates.scalars().all())
            rules = await self.resolver.catalog_rules(ids)
            severities = {"avoid", "forbidden"} if status == "restricted" else {status}
            matching_ids = [identifier for identifier in ids
                            if self.resolver.highest_severity(rules.get(identifier, [])) in severities]
            total = len(matching_ids)
            if not total:
                return IngredientPage(total=0, limit=limit, offset=offset, items=[])
            where += " AND i.id = ANY(CAST(:matching_ids AS uuid[]))"
            params["matching_ids"] = matching_ids
        else:
            total = await self.session.scalar(text(f"SELECT count(*) FROM catalog.ingredients i WHERE {where}"), params)
        result = await self.session.execute(text(f"""
            SELECT i.id::text AS id, i.canonical_name_ru AS name, i.e_code AS code, i.category
            FROM catalog.ingredients i WHERE {where}
            ORDER BY lower(i.canonical_name_ru), i.id LIMIT :limit OFFSET :offset
        """), {**params, "limit": limit, "offset": offset})
        rows = [dict(row) for row in result.mappings().all()]
        if rules is None:
            rules = await self.resolver.catalog_rules([row["id"] for row in rows])
        return IngredientPage(total=total, limit=limit, offset=offset, items=[
            IngredientSummary(**row, severity=self.resolver.highest_severity(rules.get(row["id"], [])))
            for row in rows
        ])

    async def detail(self, ingredient_id: str) -> IngredientDetail | None:
        result = await self.session.execute(text("""
            SELECT id::text AS id, canonical_name_ru AS name, canonical_name_en AS name_en,
                   e_code AS code, category, description, full_description
            FROM catalog.ingredients WHERE id = CAST(:id AS uuid) AND is_active
        """), {"id": ingredient_id})
        row = result.mappings().first()
        if row is None:
            return None
        tags = await self.session.execute(text("""
            SELECT DISTINCT tag_type, name_ru FROM catalog.ingredient_tags
            WHERE ingredient_id = CAST(:id AS uuid) ORDER BY tag_type, name_ru
        """), {"id": ingredient_id})
        tags = tags.mappings().all()
        aliases = await self.session.execute(text("""
            SELECT DISTINCT alias FROM catalog.ingredient_aliases
            WHERE ingredient_id = CAST(:id AS uuid) ORDER BY alias
        """), {"id": ingredient_id})
        rules = (await self.resolver.catalog_rules([ingredient_id])).get(ingredient_id, [])
        return IngredientDetail(**dict(row), severity=self.resolver.highest_severity(rules), rules=rules,
            functions=[tag["name_ru"] for tag in tags if tag["tag_type"] == "category"],
            origins=[tag["name_ru"] for tag in tags if tag["tag_type"] == "origin"],
            aliases=list(aliases.scalars().all()))
