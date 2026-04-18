import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class DriftMode(str, enum.Enum):
    LIVE = "live"              # real-time physics on demand
    SCHEDULED = "scheduled"    # Celery beat ticks this drift periodically
    FROZEN = "frozen"          # read-only snapshot, no physics


class PhysicsProfile(str, enum.Enum):
    GENTLE = "gentle"        # low jitter, heavy damping
    BALANCED = "balanced"    # defaults
    ENERGETIC = "energetic"  # more movement, more attraction
    CHAOTIC = "chaotic"      # high jitter, low damping — surreal drift


class Drift(Base):
    __tablename__ = "drifts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    mode: Mapped[DriftMode] = mapped_column(Enum(DriftMode), default=DriftMode.LIVE)
    physics_profile: Mapped[PhysicsProfile] = mapped_column(
        Enum(PhysicsProfile), default=PhysicsProfile.BALANCED
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_ticked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    members: Mapped[list["DriftMember"]] = relationship(
        back_populates="drift", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Drift {self.id} '{self.name}' [{self.mode.value}]>"


class DriftMember(Base):
    """Join row: fragment placed in a drift, with per-drift position + velocity."""
    __tablename__ = "drift_members"

    drift_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("drifts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    fragment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fragments.id", ondelete="CASCADE"),
        primary_key=True,
    )

    canvas_x: Mapped[float] = mapped_column(Float, default=0.0)
    canvas_y: Mapped[float] = mapped_column(Float, default=0.0)
    drift_vx: Mapped[float] = mapped_column(Float, default=0.0)
    drift_vy: Mapped[float] = mapped_column(Float, default=0.0)

    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_drifted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    drift: Mapped[Drift] = relationship(back_populates="members")
    fragment: Mapped["Fragment"] = relationship()  # noqa: F821

    def __repr__(self) -> str:
        return f"<DriftMember drift={self.drift_id} fragment={self.fragment_id}>"
