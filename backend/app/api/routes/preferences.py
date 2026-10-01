from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_catalog_session

from app.schemas.preferences import (
    PreferenceIngredientOut,
    SaveAnonymousPreferencesRequest,
    SaveAnonymousPreferencesResponse,
)

router = APIRouter()


@router.get("/catalog", response_model=list[PreferenceIngredientOut])
async def get_preferences_catalog(
    session: AsyncSession = Depends(get_catalog_session),
) -> list[PreferenceIngredientOut]:
    result = await session.execute(text("""
        SELECT i.id::text, i.canonical_name_ru AS name, i.e_code AS code,
               COALESCE(i.category, '') AS category,
               COALESCE(i.description, '') AS description,
               ARRAY(SELECT a.alias FROM catalog.ingredient_aliases a
                     WHERE a.ingredient_id = i.id AND a.source = 'legacy_preference_id'
                     UNION
                     SELECT m.legacy_ingredient_id::text FROM catalog.ingredient_matches m
                     WHERE m.ingredient_id = i.id
                     UNION
                     SELECT a.alias FROM catalog.ingredient_alias_review a
                     WHERE a.ingredient_id = i.id AND a.source = 'legacy_preference_id'
                     ORDER BY 1) AS legacy_ids
        FROM catalog.ingredients i WHERE i.is_active = true
        ORDER BY i.canonical_name_ru, i.id
    """))
    return [PreferenceIngredientOut(**dict(row)) for row in result.mappings().all()]


@router.post("/anonymous", response_model=SaveAnonymousPreferencesResponse)
async def save_anonymous_preferences(
    payload: SaveAnonymousPreferencesRequest,
) -> SaveAnonymousPreferencesResponse:
    return SaveAnonymousPreferencesResponse(
        status="client_storage",
        message=(
            "Anonymous preferences should stay in localStorage until accounts "
            f"are enabled. Received {len(payload.excluded_ingredient_ids)} ids."
        ),
    )
