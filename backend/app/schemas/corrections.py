from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.analysis import AnalysisPreferences, AnalyzeIngredientsResponse


class CorrectionItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="forbid")

    id: UUID
    token_index: int = Field(alias="tokenIndex", ge=0, le=1000)
    token_raw: str = Field(alias="tokenRaw", max_length=4000)
    corrected_text: str = Field(alias="correctedText", min_length=1, max_length=500)


class SaveCorrectionsRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    request_id: UUID = Field(alias="requestId")
    ingredients_text: str = Field(alias="ingredientsText", max_length=30000)
    corrections: list[CorrectionItem] = Field(min_length=1, max_length=50)
    preferences: AnalysisPreferences = Field(default_factory=AnalysisPreferences)

    @model_validator(mode="after")
    def unique_items(self):
        if len({item.id for item in self.corrections}) != len(self.corrections):
            raise ValueError("Duplicate correction IDs")
        if len({item.token_index for item in self.corrections}) != len(self.corrections):
            raise ValueError("Duplicate token indexes")
        return self


class SaveCorrectionsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    saved: int
    status: str = "pending_review"
    ingredients_text: str = Field(alias="ingredientsText")
    analysis: AnalyzeIngredientsResponse
