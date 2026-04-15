import uuid

from pydantic import BaseModel


class PositionUpdate(BaseModel):
    fragment_id: uuid.UUID
    canvas_x: float
    canvas_y: float


class BatchPositionUpdate(BaseModel):
    positions: list[PositionUpdate]


class DriftEvent(BaseModel):
    """Pushed via WebSocket when the drift engine moves fragments."""
    event: str = "drift"
    fragment_id: uuid.UUID
    canvas_x: float
    canvas_y: float
    drift_vx: float
    drift_vy: float


class CollisionEvent(BaseModel):
    """Pushed via WebSocket when a new collision is detected."""
    event: str = "collision"
    collision_id: uuid.UUID
    fragment_a_id: uuid.UUID
    fragment_b_id: uuid.UUID
    similarity_score: float
    synthesis_title: str | None = None
