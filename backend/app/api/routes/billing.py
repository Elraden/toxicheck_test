from fastapi import APIRouter

from app.schemas.billing import BillingFeatureState

router = APIRouter()


@router.get("/state", response_model=BillingFeatureState)
async def billing_state() -> BillingFeatureState:
    return BillingFeatureState()

