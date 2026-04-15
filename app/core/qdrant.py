from qdrant_client import AsyncQdrantClient, models

from app.config import get_settings

settings = get_settings()

qdrant_client = AsyncQdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)

COLLECTION_NAME = settings.qdrant_collection


async def ensure_collection() -> None:
    """Create the fragments vector collection if it doesn't exist."""
    collections = await qdrant_client.get_collections()
    existing = [c.name for c in collections.collections]

    if COLLECTION_NAME not in existing:
        await qdrant_client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=models.VectorParams(
                size=settings.embedding_dimensions,
                distance=models.Distance.COSINE,
            ),
        )


async def close_qdrant() -> None:
    await qdrant_client.close()
