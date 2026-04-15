import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.fragment import FragmentStatus, FragmentType


# ── Create ──
class FragmentCreate(BaseModel):
    title: str | None = Field(None, max_length=300)
    text_content: str | None = None
    fragment_type: FragmentType = FragmentType.TEXT
    tags: list[str] | None = None
    canvas_x: float = 0.0
    canvas_y: float = 0.0


# ── Update ──
class FragmentUpdate(BaseModel):
    title: str | None = None
    text_content: str | None = None
    tags: list[str] | None = None
    status: FragmentStatus | None = None
    canvas_x: float | None = None
    canvas_y: float | None = None


# ── Read ──
class FragmentRead(BaseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    fragment_type: FragmentType
    status: FragmentStatus
    title: str | None
    text_content: str | None
    transcription: str | None
    image_description: str | None
    media_urls: list[str] | None
    thumbnail_url: str | None
    tags: list[str] | None
    canvas_x: float
    canvas_y: float
    drift_vx: float
    drift_vy: float
    created_at: datetime
    updated_at: datetime
    last_drifted_at: datetime | None

    model_config = {"from_attributes": True}


# ── Compact read (for canvas rendering) ──
class FragmentCompact(BaseModel):
    id: uuid.UUID
    fragment_type: FragmentType
    status: FragmentStatus
    title: str | None
    thumbnail_url: str | None
    tags: list[str] | None
    canvas_x: float
    canvas_y: float
    drift_vx: float
    drift_vy: float

    model_config = {"from_attributes": True}
