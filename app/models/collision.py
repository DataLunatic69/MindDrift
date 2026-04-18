import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class CollisionStatus(str, enum.Enum):
    PROPOSED = "proposed"    # drift engine found a match
    ACCEPTED = "accepted"    # user found it valuable
    DISMISSED = "dismissed"  # user dismissed it
    SYNTHESIZED = "synthesized"  # LLM generated a merged idea


class Collision(Base):
    __tablename__ = "collisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    fragment_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fragments.id", ondelete="CASCADE")
    )
    fragment_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fragments.id", ondelete="CASCADE")
    )

    similarity_score: Mapped[float] = mapped_column(Float)
    status: Mapped[CollisionStatus] = mapped_column(
        Enum(CollisionStatus), default=CollisionStatus.PROPOSED, index=True
    )

    # ── Synthesis (LLM-generated merge of the two fragments) ──
    synthesis_title: Mapped[str | None] = mapped_column(String(300))
    synthesis_text: Mapped[str | None] = mapped_column(Text)
    synthesis_reasoning: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Relationships ──
    user: Mapped["User"] = relationship(back_populates="collisions")  # noqa: F821
    fragment_a: Mapped["Fragment"] = relationship(foreign_keys=[fragment_a_id])  # noqa: F821
    fragment_b: Mapped["Fragment"] = relationship(foreign_keys=[fragment_b_id])  # noqa: F821

    def __repr__(self) -> str:
        return f"<Collision {self.id} [{self.status.value}] score={self.similarity_score:.3f}>"
