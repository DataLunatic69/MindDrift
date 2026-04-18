from qdrant_client import AsyncQdrantClient, models

from app.config import get_settings

settings = get_settings()

qdrant_client_kwargs = {
    "check_compatibility": settings.qdrant_check_compatibility,
}

if settings.qdrant_url:
    qdrant_client_kwargs["url"] = settings.qdrant_url
    if settings.qdrant_api_key:
        qdrant_client_kwargs["api_key"] = settings.qdrant_api_key
else:
    qdrant_client_kwargs["host"] = settings.qdrant_host
    qdrant_client_kwargs["port"] = settings.qdrant_port

qdrant_client = AsyncQdrantClient(**qdrant_client_kwargs)

COLLECTION_NAME = settings.qdrant_collection
MEMORY_COLLECTION_NAME = settings.qdrant_memory_collection


async def _ensure_one(name: str) -> None:
    collections = await qdrant_client.get_collections()
    existing = {c.name for c in collections.collections}
    if name not in existing:
        await qdrant_client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(
                size=settings.embedding_dimensions,
                distance=models.Distance.COSINE,
            ),
        )


async def ensure_collection() -> None:
    """Create both the fragments and user_memory Qdrant collections if missing."""
    await _ensure_one(COLLECTION_NAME)
    await _ensure_one(MEMORY_COLLECTION_NAME)


async def close_qdrant() -> None:
    await qdrant_client.close()
