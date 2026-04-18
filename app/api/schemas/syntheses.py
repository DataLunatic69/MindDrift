import enum
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.synthesis import SynthesisKind, SynthesisStatus


class SynthesisMode(str, enum.Enum):
    # Vector math + Gaussian perturbation + k-NN retrieval → LLM. Emergent when
    # the user has a dense fragment cloud; noisy when they don't.
    PHYSICS = "physics"
    # Skip vector math — just prompt the LLM with seed texts + user memory.
    # The sensible default for users with few fragments.
    DIRECT = "direct"


class SynthesisCreate(BaseModel):
    seed_fragment_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=20)
    temperature: float = Field(0.3, ge=0.0, le=1.0)
    mode: SynthesisMode = SynthesisMode.DIRECT


class SynthesisResolve(BaseModel):
    status: SynthesisStatus  # ACCEPTED or DISMISSED
    spawn_fragment: bool = False  # on ACCEPTED, whether to create a real fragment


class SynthesisRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    drift_id: uuid.UUID | None
    kind: SynthesisKind
    status: SynthesisStatus
    seed_fragment_ids: list[str] | None
    neighbor_fragment_ids: list[str] | None
    memory_ids: list[str] | None
    perturbation_magnitude: float | None
    temperature: float | None
    title: str | None
    synthesis_text: str | None
    reasoning: str | None
    spawned_fragment_id: uuid.UUID | None
    created_at: datetime
    resolved_at: datetime | None

    model_config = {"from_attributes": True}
