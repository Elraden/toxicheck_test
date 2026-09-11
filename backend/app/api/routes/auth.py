from fastapi import APIRouter

from app.schemas.auth import AuthFeatureState

router = APIRouter()


@router.get("/state", response_model=AuthFeatureState)
async def auth_state() -> AuthFeatureState:
    return AuthFeatureState()

