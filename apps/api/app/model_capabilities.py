"""Modality contracts and local provider status; no automatic provider fallback."""
from typing import Literal, Protocol
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from .model import SUPPORTED_MODELS, ModelNotConfigured


class VisionImage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    # A registered artifact ID, never arbitrary URLs, base64 bodies or local paths.
    artifact_id: UUID
    mime_type: Literal['image/png', 'image/jpeg']
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class VisionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: str = Field(min_length=1, max_length=4000)
    images: list[VisionImage] = Field(min_length=1, max_length=3)
    max_cost_cny: float = Field(gt=0, le=1)


class VisionAdapter(Protocol):
    def describe(self, request: VisionRequest) -> dict: ...


def vision_adapter() -> VisionAdapter:
    from .vision import AliyunVisionAdapter
    return AliyunVisionAdapter()


def catalog():
    from .vision import status
    return {'text': {'provider': 'deepseek', 'models': list(SUPPORTED_MODELS), 'integrated': True},
            'vision_understanding': {**status(), 'uploads_enabled': True},
            'image_generation': {'integrated': False, 'status': 'out_of_scope'},
            'automatic_provider_fallback': False}
