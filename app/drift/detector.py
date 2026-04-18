import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.qdrant import COLLECTION_NAME, qdrant_client
from app.models.collision import Collision, CollisionStatus
from app.models.fragment import Fragment, FragmentStatus
from app.services.collision_service import CollisionService

logger = logging.getLogger(__name__)
settings = get_settings()


class CollisionDetector:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.collision_service = CollisionService(db)

    async def detect_collisions_for_user(self, user_id: uuid.UUID) -> list[Collision]:
        """
        Find fragment pairs that are semantically similar but were created
        far apart in time — the "near-miss" ideas worth reconnecting.
        """
        # Get all active fragments for this user
        result = await self.db.execute(
            select(Fragment).where(
                Fragment.owner_id == user_id,
                Fragment.status == FragmentStatus.ACTIVE,
                Fragment.qdrant_point_id.isnot(None),
            )
        )
        fragments = list(result.scalars().all())

        if len(fragments) < 2:
            return []

        min_time_gap = timedelta(hours=settings.collision_min_time_gap_hours)
        threshold = settings.collision_similarity_threshold
        new_collisions = []

        for fragment in fragments:
            # Search Qdrant for similar vectors
            try:
                similar_points = await qdrant_client.query_points(
                    collection_name=COLLECTION_NAME,
                    query=str(fragment.id),  # query by point ID
                    using=None,  # use stored vector
                    limit=10,
                    score_threshold=threshold,
                )
            except Exception as e:
                logger.warning(f"Qdrant search failed for {fragment.id}: {e}")
                continue

            for point in similar_points.points:
                other_id = uuid.UUID(point.payload["fragment_id"])

                if other_id == fragment.id:
                    continue

                # Check time gap — we only want collisions between temporally distant fragments
                other_result = await self.db.execute(
                    select(Fragment).where(Fragment.id == other_id)
                )
                other = other_result.scalar_one_or_none()
                if not other:
                    continue

                time_diff = abs(fragment.created_at - other.created_at)
                if time_diff < min_time_gap:
                    continue

                # Check if this collision already exists
                exists = await self.collision_service.collision_exists(
                    user_id, fragment.id, other.id
                )
                if exists:
                    continue

                # Create new collision
                collision = Collision(
                    user_id=user_id,
                    fragment_a_id=fragment.id,
                    fragment_b_id=other.id,
                    similarity_score=point.score,
                    status=CollisionStatus.PROPOSED,
                )
                self.db.add(collision)
                new_collisions.append(collision)

        if new_collisions:
            await self.db.flush()
            logger.info(
                f"Detected {len(new_collisions)} new collisions for user {user_id}"
            )

        return new_collisions
