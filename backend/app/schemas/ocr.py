from uuid import uuid4
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

OCR_VERSION = "embedded-ml-20261110"

class HazardFlag(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    token_raw: str = Field(alias="tokenRaw")
    e_code: str = Field(alias="eCode")
    name: str
    ingredient_id: str = Field(alias="ingredientId")
    certainty: Literal["high", "needs_verification"]
    citations: list[str] = Field(default_factory=list)

class OcrResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: str
    request_id: str = Field(default_factory=lambda: str(uuid4()), alias="requestId")
    engine_version: str = Field(default=OCR_VERSION, alias="engineVersion")
    preprocessing: dict = Field(default_factory=dict)
    flagged_hazards: list[HazardFlag] = Field(default_factory=list, alias="flaggedHazards")
    queued_for_review: int = Field(default=0, alias="queuedForReview")
    review_queue_status: str = Field(default="available", alias="reviewQueueStatus")
    raw_text: str | None = Field(default=None, alias="rawText")
    composition_text: str | None = Field(default=None, alias="compositionText")
    allergens_text: str | None = Field(default=None, alias="allergensText")
    confidence: float | None = None
    processing_time_ms: int | None = Field(default=None, alias="processingTimeMs")
    message: str | None = None
    job_id: str | None = Field(default=None, alias="jobId")
