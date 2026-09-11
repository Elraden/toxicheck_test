from fastapi import APIRouter

from app.schemas.compare import CompareProductsRequest, CompareProductsResponse
from app.services.compare_service import CompareService

router = APIRouter()


@router.post("", response_model=CompareProductsResponse)
async def compare_products(
    payload: CompareProductsRequest,
) -> CompareProductsResponse:
    service = CompareService()

    return service.compare(payload.products)

