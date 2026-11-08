from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict, model_validator

Mode = Literal['photo_sequential','photo_adaptive','video_sequential','video_parallel','video_adaptive']

class Config(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    barcodeMaxMs: int = Field(default=4000, ge=500, le=15000)
    photoHintMs: int = Field(default=1500, ge=0, le=14000)
    ocrHedgeMs: int = Field(default=1500, ge=0, le=14000)
    lookupMaxMs: int = Field(default=8000, ge=500, le=15000)
    runMaxMs: int = Field(default=30000, ge=10000, le=120000)
    frameMinIntervalMs: int = Field(default=1500, ge=1000, le=10000)
    maxOcrRequests: int = Field(default=3, ge=1, le=3)
    maxOcrInFlight: Literal[1] = 1

    @model_validator(mode='after')
    def coherent(self):
        if not (self.photoHintMs < self.barcodeMaxMs < self.runMaxMs and
                self.ocrHedgeMs < self.barcodeMaxMs and self.lookupMaxMs < self.runMaxMs):
            raise ValueError('Пороги должны предшествовать пределу barcode, а тайм-ауты — общему пределу.')
        return self

class CaseInput(BaseModel):
    product_label: str = Field(default='', max_length=200)
    expected_barcode: str | None = Field(default=None, pattern=r'^\d{8,14}$')
    label_layout: Literal['same_side','different_side','unknown'] = 'unknown'
    notes: str = Field(default='', max_length=2000)

class RunInput(BaseModel):
    id: UUID
    case_id: UUID
    mode: Mode
    config: Config = Field(default_factory=Config)
    environment: dict = Field(default_factory=dict)

class Event(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    seq: int = Field(ge=0, le=4999)
    event_name: str = Field(pattern=r'^[a-z_]{1,64}$')
    relative_ms: float = Field(ge=0, lt=3600000)
    details: dict = Field(default_factory=dict)

class EventsInput(BaseModel):
    events: list[Event] = Field(max_length=100)

class TaskInput(BaseModel):
    id: UUID
    barcode: str | None = Field(default=None, pattern=r'^\d{8,14}$')
    image_base64: str | None = Field(default=None, max_length=17000000)

class FinishInput(BaseModel):
    outcome: Literal['valid_barcode','valid_ocr','incomplete','timeout','api_error','ocr_error','cancelled','technical_failure']
    winning_task_id: UUID | None = None
    summary: dict = Field(default_factory=dict)

class ReviewInput(BaseModel):
    needs_review: bool
