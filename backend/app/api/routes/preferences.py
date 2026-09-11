from fastapi import APIRouter

from app.schemas.preferences import (
    PreferenceIngredientOut,
    SaveAnonymousPreferencesRequest,
    SaveAnonymousPreferencesResponse,
)

router = APIRouter()


@router.get("/catalog", response_model=list[PreferenceIngredientOut])
async def get_preferences_catalog() -> list[PreferenceIngredientOut]:
    return [
        PreferenceIngredientOut(
            id="titanium-dioxide",
            name="Диоксид титана",
            code="E171",
            category="Красители",
            description="Белый краситель.",
        ),
        PreferenceIngredientOut(
            id="sodium-benzoate",
            name="Бензоат натрия",
            code="E211",
            category="Консерванты",
            description="Консервант в напитках и соусах.",
        ),
        PreferenceIngredientOut(
            id="gluten",
            name="Глютен",
            category="Аллергены",
            description="Белок злаковых культур.",
        ),
        PreferenceIngredientOut(
            id="lactose",
            name="Лактоза",
            category="Аллергены",
            description="Молочный сахар.",
        ),
    ]


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

