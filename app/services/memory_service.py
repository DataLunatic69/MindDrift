import logging
import uuid

from qdrant_client import models as qmodels
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.qdrant import MEMORY_COLLECTION_NAME, qdrant_client
from app.models.user_memory import MemoryKind, UserMemory
from app.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


class MemoryService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.embedder = EmbeddingService()

    async def list_memories(
        self,
        user_id: uuid.UUID,
        kind: MemoryKind | None = None,
        limit: int = 100,
    ) -> list[UserMemory]:
        query = select(UserMemory).where(UserMemory.user_id == user_id)
        if kind:
            query = query.where(UserMemory.kind == kind)
        query = query.order_by(UserMemory.updated_at.desc()).limit(limit)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def create_memory(
        self,
        user_id: uuid.UUID,
        kind: MemoryKind,
        content: str,
        metadata: dict | None = None,
        weight: float = 1.0,
    ) -> UserMemory:
        memory = UserMemory(
            user_id=user_id,
            kind=kind,
            content=content,
            weight=weight,
            extra_metadata=metadata,
        )
        self.db.add(memory)
        await self.db.flush()

        try:
            embedding = await self.embedder.generate_embedding(content)
            point_id = str(memory.id)
            await qdrant_client.upsert(
                collection_name=MEMORY_COLLECTION_NAME,
                points=[
                    qmodels.PointStruct(
                        id=point_id,
                        vector=embedding,
                        payload={
                            "memory_id": str(memory.id),
                            "user_id": str(user_id),
                            "kind": kind.value,
                            "weight": weight,
                        },
                    )
                ],
            )
            memory.embedding_point_id = point_id
            await self.db.flush()
        except Exception as e:
            logger.warning(f"Memory embedding skipped for {memory.id}: {e}")

        return memory

    async def delete_memory(
        self, memory_id: uuid.UUID, user_id: uuid.UUID
    ) -> bool:
        result = await self.db.execute(
            select(UserMemory).where(
                UserMemory.id == memory_id,
                UserMemory.user_id == user_id,
            )
        )
        memory = result.scalar_one_or_none()
        if not memory:
            return False

        if memory.embedding_point_id:
            try:
                await qdrant_client.delete(
                    collection_name=MEMORY_COLLECTION_NAME,
                    points_selector=qmodels.PointIdsList(
                        points=[memory.embedding_point_id]
                    ),
                )
            except Exception as e:
                logger.warning(f"Memory qdrant delete skipped: {e}")

        await self.db.delete(memory)
        await self.db.flush()
        return True

    async def adjust_weight(
        self, memory_id: uuid.UUID, user_id: uuid.UUID, delta: float
    ) -> UserMemory | None:
        result = await self.db.execute(
            select(UserMemory).where(
                UserMemory.id == memory_id,
                UserMemory.user_id == user_id,
            )
        )
        memory = result.scalar_one_or_none()
        if not memory:
            return None
        memory.weight = max(0.0, min(10.0, memory.weight + delta))
        await self.db.flush()
        return memory
