from sqlalchemy.ext.asyncio import AsyncSession

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
        resolved = await self.resolver.resolve(ingredients_text)
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

