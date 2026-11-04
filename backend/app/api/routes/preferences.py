from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_catalog_session
from app.db.session import get_identity_session
from app.api.identity_dependencies import guest_user, guest_write
from app.services.identity import Principal
from app.services.personal_preferences import PersonalPreferenceService
from app.schemas.identity import PersonalPreferences, SavePersonalPreferences, ImportPersonalPreferences

from app.schemas.preferences import (
    PreferenceIngredientOut,
    SaveAnonymousPreferencesRequest,
    SaveAnonymousPreferencesResponse,
)

router = APIRouter()


@router.get("/me", response_model=PersonalPreferences)
async def my_preferences(principal: Principal = Depends(guest_user), db: AsyncSession = Depends(get_identity_session)):
    return await PersonalPreferenceService(db, principal.user.id).read()


@router.put("/me", response_model=PersonalPreferences)
async def update_my_preferences(payload: SavePersonalPreferences, principal: Principal = Depends(guest_write),
                                db: AsyncSession = Depends(get_identity_session)):
    return await PersonalPreferenceService(db, principal.user.id).save(payload.excludedIngredientIds, payload.revision)


@router.post("/me/import", response_model=PersonalPreferences)
async def import_my_preferences(payload: ImportPersonalPreferences, principal: Principal = Depends(guest_write),
                                db: AsyncSession = Depends(get_identity_session)):
    return await PersonalPreferenceService(db, principal.user.id).import_local(payload.excludedIngredientIds)


@router.get("/catalog", response_model=list[PreferenceIngredientOut])
async def get_preferences_catalog(
    ids: list[str] | None = Query(default=None, max_length=100),
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
          AND (CAST(:ids AS text[]) IS NULL OR i.id::text = ANY(CAST(:ids AS text[]))
               OR EXISTS (SELECT 1 FROM catalog.ingredient_aliases a WHERE a.ingredient_id = i.id
                          AND a.source = 'legacy_preference_id' AND a.alias = ANY(CAST(:ids AS text[])))
               OR EXISTS (SELECT 1 FROM catalog.ingredient_matches m WHERE m.ingredient_id = i.id
                          AND m.legacy_ingredient_id::text = ANY(CAST(:ids AS text[])))
               OR EXISTS (SELECT 1 FROM catalog.ingredient_alias_review a WHERE a.ingredient_id = i.id
                          AND a.source = 'legacy_preference_id' AND a.alias = ANY(CAST(:ids AS text[]))))
        ORDER BY i.canonical_name_ru, i.id
    """), {"ids": ids})
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
