import uuid
from datetime import datetime

from pydantic import BaseModel

from app.api.schemas.fragments import FragmentCompact
from app.models.collision import CollisionStatus


class CollisionRead(BaseModel):
    id: uuid.UUID
    fragment_a: FragmentCompact
    fragment_b: FragmentCompact
    similarity_score: float
    status: CollisionStatus
    synthesis_title: str | None
    synthesis_text: str | None
    synthesis_reasoning: str | None
    created_at: datetime
    resolved_at: datetime | None

    model_config = {"from_attributes": True}


class CollisionResolve(BaseModel):
    status: CollisionStatus  # accepted or dismissed
