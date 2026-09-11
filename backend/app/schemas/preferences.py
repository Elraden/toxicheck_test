from pydantic import BaseModel, Field


class PreferenceIngredientOut(BaseModel):
    id: str
    name: str
    code: str | None = None
    category: str
    description: str


class SaveAnonymousPreferencesRequest(BaseModel):
    excluded_ingredient_ids: list[str] = Field(alias="excludedIngredientIds")


class SaveAnonymousPreferencesResponse(BaseModel):
    status: str
    message: str

