from app.schemas.compare import (
    ComparedProductOut,
    CompareProductsResponse,
)
from app.schemas.analysis import AnalyzeIngredientsResponse


class CompareService:
    def compare(
        self,
        products: list[AnalyzeIngredientsResponse],
    ) -> CompareProductsResponse:
        compared = [
            ComparedProductOut(
                index=index,
                title=product.product.product_name
                if product.product and product.product.product_name
                else f"Товар {index + 1}",
                riskScore=product.verdict.risk_score,
                level=product.verdict.level,
            )
            for index, product in enumerate(products)
        ]
        winner = min(compared, key=lambda item: item.risk_score)

        return CompareProductsResponse(
            winnerIndex=winner.index,
            products=compared,
            summary=f"Лучший вариант: {winner.title}.",
        )

