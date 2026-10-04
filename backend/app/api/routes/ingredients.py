from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_catalog_session
from app.schemas.ingredients import IngredientDetail, IngredientPage, ResolveIngredientsRequest, ResolveIngredientsResponse
from app.services.ingredient_catalog import IngredientCatalog
from app.services.ingredient_resolver import IngredientResolver

router = APIRouter()


@router.post("/resolve", response_model=ResolveIngredientsResponse)
async def resolve_ingredients(
    payload: ResolveIngredientsRequest,
    session: AsyncSession = Depends(get_catalog_session),
) -> ResolveIngredientsResponse:
    resolver = IngredientResolver(session)

    return await resolver.resolve(payload.ingredients_text)


@router.get("", response_model=IngredientPage)
async def list_ingredients(
    q: str = Query(default="", max_length=200),
    kind: Literal["all", "additives", "foods"] = "all",
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_catalog_session),
) -> IngredientPage:
    return await IngredientCatalog(session).list(q, kind, limit, offset)


@router.get("/{ingredient_id}", response_model=IngredientDetail)
async def get_ingredient(
    ingredient_id: UUID,
    session: AsyncSession = Depends(get_catalog_session),
) -> IngredientDetail:
    ingredient = await IngredientCatalog(session).detail(str(ingredient_id))
    if ingredient is None:
        raise HTTPException(status_code=404, detail="Ингредиент не найден в базе")
    return ingredient
