import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    fragments: Mapped[list["Fragment"]] = relationship(back_populates="owner")  # noqa: F821
    collisions: Mapped[list["Collision"]] = relationship(back_populates="user")  # noqa: F821
    drifts: Mapped[list["Drift"]] = relationship(  # noqa: F821
        cascade="all, delete-orphan",
        primaryjoin="User.id == foreign(Drift.owner_id)",
    )

    def __repr__(self) -> str:
        return f"<User {self.email}>"
