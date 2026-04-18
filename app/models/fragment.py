import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class FragmentType(str, enum.Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    MIXED = "mixed"


class FragmentStatus(str, enum.Enum):
    PENDING = "pending"        # uploaded, not yet processed
    PROCESSING = "processing"  # in the LangGraph pipeline
    ACTIVE = "active"          # processed, drifting on canvas
    ARCHIVED = "archived"      # user archived it
    COLLIDED = "collided"      # merged into a synthesis


class Fragment(Base):
    __tablename__ = "fragments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    fragment_type: Mapped[FragmentType] = mapped_column(Enum(FragmentType), default=FragmentType.TEXT)
    status: Mapped[FragmentStatus] = mapped_column(
        Enum(FragmentStatus), default=FragmentStatus.PENDING, index=True
    )

    # ── Content ──
    title: Mapped[str | None] = mapped_column(String(300))
    text_content: Mapped[str | None] = mapped_column(Text)
    transcription: Mapped[str | None] = mapped_column(Text)       # from audio/video
    image_description: Mapped[str | None] = mapped_column(Text)   # from vision model

    # ── Media references (Supabase Storage paths) ──
    media_urls: Mapped[list[str] | None] = mapped_column(ARRAY(String(512)))
    thumbnail_url: Mapped[str | None] = mapped_column(String(512))

    # ── Extracted metadata ──
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String(100)))
    entities: Mapped[dict | None] = mapped_column(JSONB)
    extra_metadata: Mapped[dict | None] = mapped_column(JSONB)

    # ── Spatial position on canvas ──
    canvas_x: Mapped[float] = mapped_column(Float, default=0.0)
    canvas_y: Mapped[float] = mapped_column(Float, default=0.0)
    drift_vx: Mapped[float] = mapped_column(Float, default=0.0)   # velocity x
    drift_vy: Mapped[float] = mapped_column(Float, default=0.0)   # velocity y

    # ── Vector reference ──
    qdrant_point_id: Mapped[str | None] = mapped_column(String(64))

    # ── Timestamps ──
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_drifted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Relationships ──
    owner: Mapped["User"] = relationship(back_populates="fragments")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Fragment {self.id} [{self.fragment_type.value}] '{self.title}'>"
