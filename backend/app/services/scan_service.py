from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.analysis import ProductContext
from app.schemas.scan import BarcodeScanResponse
from app.services.analysis_service import AnalysisService
from app.services.open_food_facts import OpenFoodFactsClient


class ScanService:
    def __init__(self, session: AsyncSession) -> None:
        self.analysis_service = AnalysisService(session)
        self.open_food_facts = OpenFoodFactsClient()

    async def scan_barcode(self, barcode: str, preferences) -> BarcodeScanResponse:
        data = await self.open_food_facts.get_product(barcode)
        product = data.get("product")

        if not isinstance(product, dict):
            return BarcodeScanResponse(
                barcode=barcode,
                productFound=False,
                message="Товар не найден в Open Food Facts.",
            )

        product_name = (
            product.get("product_name")
            or product.get("generic_name")
            or "Продукт без названия"
        )
        brand = product.get("brands")
        ingredients_text = product.get("ingredients_text")

        if not isinstance(ingredients_text, str) or not ingredients_text.strip():
            return BarcodeScanResponse(
                barcode=barcode,
                productFound=True,
                productName=str(product_name),
                brand=str(brand) if brand else None,
                message="Состав не определен.",
            )

        analysis = await self.analysis_service.analyze(
            ingredients_text=ingredients_text,
            preferences=preferences,
            product=ProductContext(
                barcode=barcode,
                productName=str(product_name),
                brand=str(brand) if brand else None,
                source="open_food_facts",
            ),
        )

        return BarcodeScanResponse(
            barcode=barcode,
            productFound=True,
            productName=str(product_name),
            brand=str(brand) if brand else None,
            ingredientsText=ingredients_text,
            analysis=analysis,
        )

