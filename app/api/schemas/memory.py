import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.user_memory import MemoryKind


class MemoryNoteCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)
    metadata: dict | None = None


class MemoryRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    kind: MemoryKind
    content: str | None
    embedding_point_id: str | None
    weight: float
    extra_metadata: dict | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
