import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ALLOWED_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp"}


class RoomImageUploadRequest(BaseModel):
    image_base64: str = Field(min_length=10)
    media_type: str = Field(default="image/jpeg", max_length=64)


class MerchantProductImageUploadRequest(BaseModel):
    image_base64: str = Field(min_length=10)
    media_type: str = Field(default="image/jpeg", max_length=64)


class MerchantProductImageOut(BaseModel):
    id: uuid.UUID
    url: str
    byte_size: int
    media_type: str
    source: str
    created_at: datetime


class RoomImageOut(BaseModel):
    id: uuid.UUID
    byte_size: int
    media_type: str
    source: str
    created_at: datetime


class VisualizeRequest(BaseModel):
    session_id: uuid.UUID
    room_image_id: uuid.UUID

    # Single-product path (kept for backwards compat)
    product_id: uuid.UUID | None = None

    # Multi-product path: overrides product_id when non-empty
    product_ids: list[uuid.UUID] = Field(default_factory=list)

    # Optional placement hint from the user, e.g. "left wall", "centre of the room"
    placement: str | None = Field(default=None, max_length=200)


class VisualizeResponse(BaseModel):
    """Initial 202 response for a SINGLE product: a task_id the client can poll."""

    task_id: uuid.UUID
    status: str = "pending"


class VisualizeJobRef(BaseModel):
    """One entry in a multi-job response."""

    task_id: uuid.UUID
    product_id: uuid.UUID | None  # None for composite-scene jobs
    status: Literal["pending"] = "pending"
    scene_type: Literal["individual", "composite"] = "individual"


class VisualizeMultiResponse(BaseModel):
    """202 response when multiple products are requested."""

    jobs: list[VisualizeJobRef]
    scene_type: Literal["individual", "composite"]


class VisualizeJobOut(BaseModel):
    """Polling response for GET /visualize/{task_id}."""

    task_id: uuid.UUID
    status: str  # pending | done | failed
    image_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None
    preview_url: str | None = None
    error: str | None = None
