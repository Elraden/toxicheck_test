from pydantic import BaseModel, ConfigDict, Field

from app.schemas.ingredients import MatchedIngredientOut


class ProductContext(BaseModel):
    barcode: str | None = None
    product_name: str | None = Field(default=None, alias="productName")
    brand: str | None = None
    source: str | None = None


class AnalysisPreferences(BaseModel):
    excluded_ingredient_ids: list[str] = Field(
        default_factory=list,
        alias="excludedIngredientIds",
    )
    excluded_names: list[str] = Field(default_factory=list, alias="excludedNames")


class AnalyzeIngredientsRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ingredients_text: str = Field(alias="ingredientsText", min_length=1)
    product: ProductContext | None = None
    preferences: AnalysisPreferences = Field(default_factory=AnalysisPreferences)


class VerdictReason(BaseModel):
    severity: str
    title: str
    explanation: str | None = None
    ingredient_id: str | None = None
    raw_text: str | None = None


class ProductVerdict(BaseModel):
    level: str
    risk_score: int
    title: str
    description: str
    reasons: list[VerdictReason] = Field(default_factory=list)


class AnalyzeIngredientsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    product: ProductContext | None = None
    verdict: ProductVerdict
    matched: list[MatchedIngredientOut]
    unmatched: list[str]

