import uuid

from openai import AsyncOpenAI
from qdrant_client import models

from app.config import get_settings
from app.core.qdrant import COLLECTION_NAME, qdrant_client

settings = get_settings()
openai_client = AsyncOpenAI(api_key=settings.openai_api_key)


class EmbeddingService:
    async def generate_embedding(self, text: str) -> list[float]:
        """Generate an embedding vector for the given text."""
        response = await openai_client.embeddings.create(
            model=settings.embedding_model,
            input=text,
            dimensions=settings.embedding_dimensions,
        )
        return response.data[0].embedding

    async def upsert_fragment_vector(
        self,
        fragment_id: uuid.UUID,
        owner_id: uuid.UUID,
        embedding: list[float],
        payload: dict | None = None,
    ) -> str:
        """Store or update a fragment's vector in Qdrant."""
        point_id = str(fragment_id)

        point_payload = {
            "fragment_id": str(fragment_id),
            "owner_id": str(owner_id),
            **(payload or {}),
        }

        await qdrant_client.upsert(
            collection_name=COLLECTION_NAME,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector=embedding,
                    payload=point_payload,
                )
            ],
        )
        return point_id

    async def search_similar(
        self,
        embedding: list[float],
        owner_id: uuid.UUID,
        limit: int = 20,
        score_threshold: float = 0.5,
        exclude_ids: list[str] | None = None,
    ) -> list[models.ScoredPoint]:
        """Find fragments similar to the given embedding vector."""
        must_filters = [
            models.FieldCondition(
                key="owner_id",
                match=models.MatchValue(value=str(owner_id)),
            )
        ]

        must_not_filters = []
        if exclude_ids:
            must_not_filters = [
                models.HasIdCondition(has_id=exclude_ids),
            ]

        results = await qdrant_client.query_points(
            collection_name=COLLECTION_NAME,
            query=embedding,
            query_filter=models.Filter(
                must=must_filters,
                must_not=must_not_filters if must_not_filters else None,
            ),
            limit=limit,
            score_threshold=score_threshold,
        )
        return results.points

    async def delete_fragment_vector(self, fragment_id: uuid.UUID) -> None:
        await qdrant_client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=models.PointIdsList(points=[str(fragment_id)]),
        )
