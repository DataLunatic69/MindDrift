import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.drift import DriftMode, PhysicsProfile


class DriftCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    mode: DriftMode = DriftMode.LIVE
    physics_profile: PhysicsProfile = PhysicsProfile.BALANCED


class DriftUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    mode: DriftMode | None = None
    physics_profile: PhysicsProfile | None = None


class DriftRead(BaseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    description: str | None
    mode: DriftMode
    physics_profile: PhysicsProfile
    created_at: datetime
    updated_at: datetime
    last_ticked_at: datetime | None
    member_count: int = 0

    model_config = {"from_attributes": True}


class DriftMemberPosition(BaseModel):
    fragment_id: uuid.UUID
    canvas_x: float
    canvas_y: float
    drift_vx: float = 0.0
    drift_vy: float = 0.0
    pinned: bool = False


class DriftMemberAdd(BaseModel):
    fragment_ids: list[uuid.UUID] = Field(..., min_length=1)
    # Optional seed position — if provided, all added members start near this point.
    canvas_x: float | None = None
    canvas_y: float | None = None


class DriftMemberRead(BaseModel):
    drift_id: uuid.UUID
    fragment_id: uuid.UUID
    canvas_x: float
    canvas_y: float
    drift_vx: float
    drift_vy: float
    pinned: bool
    added_at: datetime
    last_drifted_at: datetime | None

    model_config = {"from_attributes": True}


class DriftBatchPositionUpdate(BaseModel):
    positions: list[DriftMemberPosition] = Field(..., min_length=1, max_length=500)


class TickResponse(BaseModel):
    drift_id: uuid.UUID
    enqueued: bool
    reason: str | None = None
