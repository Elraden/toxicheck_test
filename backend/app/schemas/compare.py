from pydantic import BaseModel, Field

from app.schemas.analysis import AnalyzeIngredientsResponse


class CompareProductsRequest(BaseModel):
    products: list[AnalyzeIngredientsResponse] = Field(min_length=2, max_length=4)


class ComparedProductOut(BaseModel):
    index: int
    title: str
    risk_score: int = Field(alias="riskScore")
    level: str


class CompareProductsResponse(BaseModel):
    winner_index: int = Field(alias="winnerIndex")
    products: list[ComparedProductOut]
    summary: str

