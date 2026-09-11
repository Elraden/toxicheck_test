from pydantic import BaseModel, ConfigDict, Field


class ResolveIngredientsRequest(BaseModel):
    ingredients_text: str = Field(alias="ingredientsText", min_length=1)


class IngredientRuleOut(BaseModel):
    id: str
    rule_type: str
    severity: str
    title: str
    explanation: str | None = None
    citation: str | None = None


class MatchedIngredientOut(BaseModel):
    ingredient_id: str
    name: str
    code: str | None = None
    raw_text: str
    matched_by: str
    match_score: float
    severity: str
    rules: list[IngredientRuleOut] = Field(default_factory=list)


class ResolveIngredientsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    matched: list[MatchedIngredientOut]
    unmatched: list[str]
