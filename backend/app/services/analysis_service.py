from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from fastapi import HTTPException

from app.schemas.analysis import (
    AnalysisPreferences,
    AnalyzeIngredientsResponse,
    ProductContext,
)
from app.services.ingredient_resolver import IngredientResolver
from app.services.verdict_engine import VerdictEngine


class AnalysisService:
    def __init__(self, session: AsyncSession) -> None:
        self.resolver = IngredientResolver(session)
        self.verdict_engine = VerdictEngine()

    async def analyze(
        self,
        ingredients_text: str,
        preferences: AnalysisPreferences,
        product: ProductContext | None = None,
    ) -> AnalyzeIngredientsResponse:
        if not await self.resolver.session.scalar(text("SELECT EXISTS (SELECT 1 FROM ingredient_rules)")):
            raise HTTPException(status_code=503, detail="Справочник ингредиентов ещё не загружен.")
        resolved = await self.resolver.resolve(ingredients_text)
        if preferences.excluded_ingredient_ids:
            legacy_ids = await self.resolver.session.scalars(text("""
                SELECT DISTINCT ingredient_id::text FROM ingredient_aliases
                WHERE source = 'legacy_preference_id' AND alias = ANY(:ids)
            """), {"ids": preferences.excluded_ingredient_ids})
            preferences = preferences.model_copy(update={"excluded_ingredient_ids":
                list(set(preferences.excluded_ingredient_ids) | set(legacy_ids.all()))})
        verdict = self.verdict_engine.build_verdict(
            matched=resolved.matched,
            unmatched=resolved.unmatched,
            preferences=preferences,
        )

        return AnalyzeIngredientsResponse(
            product=product,
            verdict=verdict,
            matched=resolved.matched,
            unmatched=resolved.unmatched,
        )
