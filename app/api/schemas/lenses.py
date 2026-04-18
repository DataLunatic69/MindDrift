import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.lens import LensKind, LensStatus


class LensCreate(BaseModel):
    kind: LensKind


class LensRead(BaseModel):
    id: uuid.UUID
    fragment_id: uuid.UUID
    kind: LensKind
    status: LensStatus
    text_content: str | None
    # Signed URL when ready; null when the lens is still generating or isn't
    # an image lens. Frontend should render a shimmer placeholder if null
    # while status == PENDING.
    media_url: str | None
    data: dict | None
    provider: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_model(cls, lens) -> "LensRead":
        # `media_path` on the detached ORM object has already been rewritten
        # to a signed URL by LensService._resolve_media.
        return cls(
            id=lens.id,
            fragment_id=lens.fragment_id,
            kind=lens.kind,
            status=lens.status,
            text_content=lens.text_content,
            media_url=lens.media_path,
            data=lens.data,
            provider=lens.provider,
            error=lens.error,
            created_at=lens.created_at,
            updated_at=lens.updated_at,
        )
