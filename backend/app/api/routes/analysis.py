from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.analysis import AnalyzeIngredientsRequest, AnalyzeIngredientsResponse
from app.services.analysis_service import AnalysisService

router = APIRouter()


@router.post("", response_model=AnalyzeIngredientsResponse)
async def analyze_ingredients(
    payload: AnalyzeIngredientsRequest,
    session: AsyncSession = Depends(get_db_session),
) -> AnalyzeIngredientsResponse:
    service = AnalysisService(session)

    return await service.analyze(
        ingredients_text=payload.ingredients_text,
        preferences=payload.preferences,
        product=payload.product,
    )

