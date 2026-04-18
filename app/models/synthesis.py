import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SynthesisKind(str, enum.Enum):
    PHYSICS = "physics"  # user-triggered, N seeds + vector math + memory
    # Reserved for future: MEMORY_SUMMARY, DAILY_DIGEST, etc.


class SynthesisStatus(str, enum.Enum):
    PENDING = "pending"        # queued for generation
    PROPOSED = "proposed"      # LLM produced output, awaiting user review
    ACCEPTED = "accepted"      # user kept it (may spawn a new fragment)
    DISMISSED = "dismissed"    # user discarded


class Synthesis(Base):
    """
    A user-triggered, N-way idea generated via vector math + LLM.

    Separate from Collision (which is pairwise, auto-detected). A Synthesis
    carries seed fragments + a perturbed vector that was used to generate it,
    so the user can see which of their ideas sat in the neighborhood of the
    result — and so the system can re-generate with similar parameters.
    """
    __tablename__ = "syntheses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    drift_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drifts.id", ondelete="SET NULL"), index=True
    )

    kind: Mapped[SynthesisKind] = mapped_column(
        Enum(SynthesisKind), default=SynthesisKind.PHYSICS
    )
    status: Mapped[SynthesisStatus] = mapped_column(
        Enum(SynthesisStatus), default=SynthesisStatus.PENDING, index=True
    )

    # ── Seeds used (list of fragment UUIDs as strings in a JSON array) ──
    seed_fragment_ids: Mapped[list[str] | None] = mapped_column(JSONB)
    # Optional neighbor fragment IDs the LLM was grounded on
    neighbor_fragment_ids: Mapped[list[str] | None] = mapped_column(JSONB)
    # Memory entry IDs consumed
    memory_ids: Mapped[list[str] | None] = mapped_column(JSONB)

    # ── Physics params for reproducibility / debugging ──
    perturbation_magnitude: Mapped[float | None] = mapped_column(Float)
    temperature: Mapped[float | None] = mapped_column(Float)

    # ── LLM output ──
    title: Mapped[str | None] = mapped_column(String(300))
    synthesis_text: Mapped[str | None] = mapped_column(Text)
    reasoning: Mapped[str | None] = mapped_column(Text)

    # ── Resulting fragment (if accepted, this synthesis spawns a fragment) ──
    spawned_fragment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fragments.id", ondelete="SET NULL")
    )

    # ── Timestamps ──
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    drift: Mapped["Drift | None"] = relationship()  # noqa: F821

    def __repr__(self) -> str:
        return f"<Synthesis {self.id} [{self.status.value}] '{self.title}'>"
