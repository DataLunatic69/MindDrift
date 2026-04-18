import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.collision import Collision, CollisionStatus


class CollisionService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_collisions(
        self,
        user_id: uuid.UUID,
        status_filter: CollisionStatus | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[Collision]:
        query = (
            select(Collision)
            .where(Collision.user_id == user_id)
            .options(
                selectinload(Collision.fragment_a),
                selectinload(Collision.fragment_b),
            )
            .order_by(Collision.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        if status_filter:
            query = query.where(Collision.status == status_filter)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def resolve_collision(
        self,
        collision_id: uuid.UUID,
        user_id: uuid.UUID,
        new_status: CollisionStatus,
    ) -> Collision | None:
        result = await self.db.execute(
            select(Collision).where(
                Collision.id == collision_id,
                Collision.user_id == user_id,
            )
        )
        collision = result.scalar_one_or_none()
        if not collision:
            return None

        collision.status = new_status
        collision.resolved_at = datetime.now(timezone.utc)
        await self.db.flush()
        return collision

    async def collision_exists(
        self,
        user_id: uuid.UUID,
        fragment_a_id: uuid.UUID,
        fragment_b_id: uuid.UUID,
    ) -> bool:
        """Check if a collision between these two fragments already exists."""
        result = await self.db.execute(
            select(Collision.id).where(
                Collision.user_id == user_id,
                (
                    (
                        (Collision.fragment_a_id == fragment_a_id)
                        & (Collision.fragment_b_id == fragment_b_id)
                    )
                    | (
                        (Collision.fragment_a_id == fragment_b_id)
                        & (Collision.fragment_b_id == fragment_a_id)
                    )
                ),
            )
        )
        return result.scalar_one_or_none() is not None
