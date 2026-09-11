from pydantic import BaseModel, ConfigDict, Field

from app.schemas.analysis import AnalyzeIngredientsResponse, AnalysisPreferences


class BarcodeScanRequest(BaseModel):
    barcode: str = Field(min_length=4, max_length=32)
    preferences: AnalysisPreferences = Field(default_factory=AnalysisPreferences)


class BarcodeScanResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    barcode: str
    product_found: bool = Field(alias="productFound")
    product_name: str | None = Field(default=None, alias="productName")
    brand: str | None = None
    ingredients_text: str | None = Field(default=None, alias="ingredientsText")
    analysis: AnalyzeIngredientsResponse | None = None
    message: str | None = None


class CompositionScanRequest(BaseModel):
    image_base64: str = Field(alias="imageBase64", min_length=1)
    preferences: AnalysisPreferences = Field(default_factory=AnalysisPreferences)


class CompositionScanResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    job_id: str | None = Field(default=None, alias="jobId")
    status: str
    message: str
    recognized_text: str | None = Field(default=None, alias="recognizedText")
    ingredients_text: str | None = Field(default=None, alias="ingredientsText")
    allergens_text: str | None = Field(default=None, alias="allergensText")
    confidence: float | None = None
    processing_time_ms: int | None = Field(default=None, alias="processingTimeMs")


class ScanJobResponse(BaseModel):
    job_id: str = Field(alias="jobId")
    status: str
    recognized_text: str | None = Field(default=None, alias="recognizedText")
    analysis: AnalyzeIngredientsResponse | None = None
    message: str | None = None
