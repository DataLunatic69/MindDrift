import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MemoryKind(str, enum.Enum):
    PROFILE = "profile"                        # long-lived trait / user-authored note
    EVENT = "event"                            # interaction event (view, accept, dismiss)
    SYNTHESIS_LEARNING = "synthesis_learning"  # distilled insight from accepted syntheses


class UserMemory(Base):
    """
    Central per-user memory. Powers hyper-personalization:
    - PROFILE rows → permanent attractors in physics-based synthesis.
    - EVENT rows → signals for threshold tuning.
    - SYNTHESIS_LEARNING rows → distilled taste vectors.
    """
    __tablename__ = "user_memory"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[MemoryKind] = mapped_column(Enum(MemoryKind), index=True)

    content: Mapped[str | None] = mapped_column(Text)
    # Pointer into the user_memory Qdrant collection (UUID string).
    embedding_point_id: Mapped[str | None] = mapped_column(String(64))

    # Weight decays over time; boosted by positive signals (accepted syntheses, repeat views).
    weight: Mapped[float] = mapped_column(Float, default=1.0)

    extra_metadata: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<UserMemory {self.id} [{self.kind.value}] w={self.weight:.2f}>"
