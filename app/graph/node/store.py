import logging

from sqlalchemy import select

from app.core.database import async_session_factory
from app.graph.state import IngestState
from app.models.fragment import Fragment, FragmentStatus
from app.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)
embedding_service = EmbeddingService()


async def store_node(state: IngestState) -> dict:
    """Persist processing results to Postgres and Qdrant."""
    fragment_id = state["fragment_id"]
    owner_id = state["owner_id"]
    embedding = state.get("embedding", [])

    if not embedding:
        return {"error": "No embedding to store"}

    try:
        # Store vector in Qdrant
        tags = state.get("extracted_tags", [])
        point_id = await embedding_service.upsert_fragment_vector(
            fragment_id=fragment_id,
            owner_id=owner_id,
            embedding=embedding,
            payload={
                "tags": tags,
                "fragment_type": state.get("fragment_type", "text"),
                "has_media": bool(state.get("media_urls")),
            },
        )

        # Update fragment in Postgres
        async with async_session_factory() as session:
            result = await session.execute(
                select(Fragment).where(Fragment.id == fragment_id)
            )
            fragment = result.scalar_one_or_none()

            if fragment:
                fragment.status = FragmentStatus.ACTIVE
                fragment.transcription = state.get("transcription")
                fragment.image_description = state.get("image_description")
                fragment.tags = tags
                fragment.entities = state.get("extracted_entities", {})
                fragment.qdrant_point_id = point_id

                # Merge rich creative artifacts into extra_metadata. We keep
                # whatever other keys already exist in the dict (user-added
                # metadata, external integrations) and only overwrite the two
                # ingest-owned keys below.
                meta = dict(fragment.extra_metadata or {})
                vision = state.get("image_insight")
                spark = state.get("text_spark")
                if vision:
                    meta["vision"] = vision
                if spark:
                    meta["spark"] = spark
                if meta:
                    fragment.extra_metadata = meta

                # Auto-generate title if missing. Prefer a crafted one-liner
                # (from text sparks or image headline) over a truncated dump.
                if not fragment.title:
                    crafted = (
                        (spark or {}).get("one_liner")
                        or (vision or {}).get("headline")
                    )
                    if crafted:
                        fragment.title = str(crafted)[:300].strip()
                    else:
                        combined = state.get("combined_text", "")
                        fragment.title = combined[:80].strip() + (
                            "..." if len(combined) > 80 else ""
                        )

                await session.commit()

        return {"qdrant_point_id": point_id}

    except Exception as e:
        logger.error(f"Store failed for fragment {fragment_id}: {e}")
        return {"error": str(e)}
