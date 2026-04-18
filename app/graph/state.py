import uuid
from typing import TypedDict

from app.models.fragment import FragmentType


class IngestState(TypedDict, total=False):
    # ── Input ──
    fragment_id: uuid.UUID
    owner_id: uuid.UUID
    fragment_type: FragmentType
    text_content: str | None
    media_urls: list[str]

    # ── Accumulated during processing ──
    transcription: str | None       # audio/video → text
    image_description: str | None   # image → caption (flattened text for search)
    image_insight: dict | None      # image → rich JSON (headline, twists, facts, …)
    text_spark: dict | None         # text → tensions + spark ideas + question
    extracted_tags: list[str]
    extracted_entities: dict

    # ── Final ──
    combined_text: str              # all text merged for embedding
    embedding: list[float]
    qdrant_point_id: str

    # ── Control ──
    error: str | None
