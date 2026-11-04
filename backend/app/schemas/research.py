from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid')


class StudyInput(Payload):
    title: str = Field(min_length=1, max_length=200)
    instruction: str = Field(min_length=1, max_length=10000)
    scenario: Literal['comparison', 'ocr_quality']
    protocol_version: str = Field(default='1', min_length=1, max_length=40)


class StudyState(Payload):
    is_open: bool


class RunInput(Payload):
    id: UUID
    study_id: UUID
    title: str = Field(min_length=1, max_length=200)
    consent: Literal[True]


class StageInput(Payload):
    revision: int = Field(ge=0)
    action: Literal['start_manual', 'save_manual', 'finish_manual', 'start_app', 'save_app', 'finish_app', 'annotate', 'abort']
    answers: dict[str, Literal['present', 'absent', 'unknown']] = Field(default_factory=dict, max_length=2000)
    external_search_used: bool | None = None
    selected_attempt_id: UUID | None = None
    user_label_text: str = Field(default='', max_length=30000)
    skip_label: bool = False
    reason: str = Field(default='', max_length=1000)


class PhotoInput(Payload):
    id: UUID
    image_base64: str = Field(min_length=1, max_length=16 * 1024 * 1024 + 128)


class AttemptInput(Payload):
    id: UUID
    photo_id: UUID
    capture_source: Literal['browser_camera', 'native_camera', 'file'] = 'file'


class DisplayInput(Payload):
    app_e2e_ms: int = Field(ge=0, le=3600000)


class ValidationInput(Payload):
    revision: int = Field(ge=0)
    status: Literal['unreviewed', 'confirmed', 'clarification', 'unverifiable']
    verified_label_text: str = Field(default='', max_length=30000)
    ground_truth_e_codes: list[str] = Field(default_factory=list, max_length=2000)
    comment: str = Field(default='', max_length=10000)


class LinkCaseInput(Payload):
    case_id: UUID
