import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LensKind(str, enum.Enum):
    # Generated image capturing the emotional atmosphere of the fragment
    # (not a literal illustration).
    MOOD_IMAGE = "mood_image"
    # Short evocative verse/haiku.
    ECHO = "echo"
    # Shadow-side reframe of the fragment.
    COUNTER = "counter"
    # Steps toward the intent implied by the fragment.
    ROADMAP = "roadmap"
    # Graph of nearby fragments in embedding space.
    LINEAGE = "lineage"
    # Three Socratic questions this fragment raises.
    SOCRATIC = "socratic"
    # Hierarchical mindmap (root -> branches -> leaves) expanding this fragment.
    MINDMAP = "mindmap"


class LensStatus(str, enum.Enum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class FragmentLens(Base):
    """
    A generative artifact derived from a Fragment. Each lens is a different
    creative stance on the same seed — not a summary.
    """
    __tablename__ = "fragment_lenses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    fragment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fragments.id", ondelete="CASCADE"),
        index=True,
    )
    kind: Mapped[LensKind] = mapped_column(Enum(LensKind), index=True)
    status: Mapped[LensStatus] = mapped_column(
        Enum(LensStatus), default=LensStatus.PENDING, index=True
    )

    # Textual output (echo / counter / socratic / JSON-stringified roadmap).
    text_content: Mapped[str | None] = mapped_column(Text)
    # Supabase storage path for image lenses (resolved to signed URL on read).
    media_path: Mapped[str | None] = mapped_column(String(512))
    # Raw JSON artifacts (roadmap nodes/edges, lineage graph, etc.).
    data: Mapped[dict | None] = mapped_column(JSONB)
    # Which provider produced this (e.g., "gemini-2.5-flash-image", "gpt-image-1").
    provider: Mapped[str | None] = mapped_column(String(100))

    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<FragmentLens {self.id} [{self.kind.value}:{self.status.value}]>"
