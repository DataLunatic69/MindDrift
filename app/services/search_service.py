import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fragment import Fragment, FragmentStatus
from app.services.embedding_service import EmbeddingService


class SearchService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.embeddings = EmbeddingService()

    async def semantic_search(
        self,
        query: str,
        owner_id: uuid.UUID,
        limit: int = 20,
    ) -> list[dict]:
        """Search fragments by meaning using vector similarity."""
        query_embedding = await self.embeddings.generate_embedding(query)
        scored_points = await self.embeddings.search_similar(
            embedding=query_embedding,
            owner_id=owner_id,
            limit=limit,
            score_threshold=0.4,
        )

        results = []
        for point in scored_points:
            frag_id = uuid.UUID(point.payload["fragment_id"])
            result = await self.db.execute(
                select(Fragment).where(Fragment.id == frag_id)
            )
            fragment = result.scalar_one_or_none()
            if fragment:
                results.append({
                    "fragment": fragment,
                    "score": point.score,
                })

        return results

    async def keyword_search(
        self,
        query: str,
        owner_id: uuid.UUID,
        limit: int = 20,
    ) -> list[Fragment]:
        """Fallback keyword search against text fields."""
        pattern = f"%{query}%"
        result = await self.db.execute(
            select(Fragment)
            .where(
                Fragment.owner_id == owner_id,
                Fragment.status == FragmentStatus.ACTIVE,
                or_(
                    Fragment.title.ilike(pattern),
                    Fragment.text_content.ilike(pattern),
                    Fragment.transcription.ilike(pattern),
                    Fragment.image_description.ilike(pattern),
                ),
            )
            .limit(limit)
        )
        return list(result.scalars().all())

    async def hybrid_search(
        self,
        query: str,
        owner_id: uuid.UUID,
        limit: int = 20,
    ) -> list[dict]:
        """Combine semantic and keyword search, deduplicated."""
        semantic_results = await self.semantic_search(query, owner_id, limit)
        keyword_results = await self.keyword_search(query, owner_id, limit)

        seen_ids = set()
        merged = []

        for item in semantic_results:
            fid = item["fragment"].id
            if fid not in seen_ids:
                seen_ids.add(fid)
                merged.append(item)

        for frag in keyword_results:
            if frag.id not in seen_ids:
                seen_ids.add(frag.id)
                merged.append({"fragment": frag, "score": 0.0})

        return merged[:limit]
