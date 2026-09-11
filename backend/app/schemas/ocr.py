from pydantic import BaseModel, ConfigDict, Field


class OcrResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str
    raw_text: str | None = Field(default=None, alias="rawText")
    composition_text: str | None = Field(default=None, alias="compositionText")
    allergens_text: str | None = Field(default=None, alias="allergensText")
    confidence: float | None = None
    processing_time_ms: int | None = Field(default=None, alias="processingTimeMs")
    message: str | None = None
    job_id: str | None = Field(default=None, alias="jobId")
