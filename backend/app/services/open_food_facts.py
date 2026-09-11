import httpx

from app.core.config import settings


class OpenFoodFactsClient:
    async def get_product(self, barcode: str) -> dict:
        fields = ",".join(
            [
                "code",
                "product_name",
                "generic_name",
                "brands",
                "quantity",
                "categories",
                "categories_tags",
                "ingredients",
                "ingredients_text",
                "additives_tags",
                "additives_original_tags",
                "ingredients_analysis_tags",
                "allergens",
                "nutriments",
                "image_front_url",
            ]
        )
        url = (
            f"{settings.open_food_facts_base_url.rstrip('/')}"
            f"/api/v3.6/product/{barcode}.json"
        )

        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                url,
                params={"fields": fields},
                headers={"Accept": "application/json"},
            )

        if response.status_code == 404:
            return {
                "code": barcode,
                "status": "not_found",
                "product": None,
            }

        response.raise_for_status()
        return response.json()

