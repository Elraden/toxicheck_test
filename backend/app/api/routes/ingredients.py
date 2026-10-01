from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_catalog_session
from app.schemas.ingredients import ResolveIngredientsRequest, ResolveIngredientsResponse
from app.services.ingredient_resolver import IngredientResolver

router = APIRouter()


@router.post("/resolve", response_model=ResolveIngredientsResponse)
async def resolve_ingredients(
    payload: ResolveIngredientsRequest,
    session: AsyncSession = Depends(get_catalog_session),
) -> ResolveIngredientsResponse:
    resolver = IngredientResolver(session)

    return await resolver.resolve(payload.ingredients_text)
