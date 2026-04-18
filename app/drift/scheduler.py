import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.qdrant import COLLECTION_NAME, qdrant_client
from app.drift.physics import (
    PhysicsParams,
    apply_drift,
    compute_attraction,
    compute_repulsion,
    get_params,
)
from app.models.drift import Drift, DriftMember
from app.models.fragment import Fragment, FragmentStatus

logger = logging.getLogger(__name__)


class DriftScheduler:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def run_drift_tick(self, user_id: uuid.UUID) -> int:
        """
        Legacy path — run a drift tick over all of a user's ACTIVE fragments
        using the position fields on Fragment itself. Kept for the existing
        canvas endpoint / legacy beat job. New code should use
        `run_drift_tick_for_drift` instead.
        """
        result = await self.db.execute(
            select(Fragment).where(
                Fragment.owner_id == user_id,
                Fragment.status == FragmentStatus.ACTIVE,
                Fragment.qdrant_point_id.isnot(None),
            )
        )
        fragments = list(result.scalars().all())
        if len(fragments) < 2:
            return 0

        params = PhysicsParams()
        similarity_map = await self._build_similarity_map([f.id for f in fragments])

        forces: dict[uuid.UUID, tuple[float, float]] = {f.id: (0.0, 0.0) for f in fragments}
        frag_map = {f.id: f for f in fragments}
        self._apply_pairwise_forces(frag_map, similarity_map, forces, params,
                                    pos_getter=lambda f: (f.canvas_x, f.canvas_y))

        moved = 0
        now = datetime.now(timezone.utc)
        for frag in fragments:
            fx, fy = forces[frag.id]
            nx, ny, nvx, nvy = apply_drift(
                frag.canvas_x, frag.canvas_y,
                frag.drift_vx, frag.drift_vy,
                fx, fy, params,
            )
            frag.canvas_x, frag.canvas_y = nx, ny
            frag.drift_vx, frag.drift_vy = nvx, nvy
            frag.last_drifted_at = now
            moved += 1
        await self.db.flush()
        logger.info(f"Legacy drift tick: moved {moved} fragments for user {user_id}")
        return moved

    async def run_drift_tick_for_drift(self, drift_id: uuid.UUID) -> int:
        """Run a physics tick scoped to a single drift's members."""
        drift = (
            await self.db.execute(select(Drift).where(Drift.id == drift_id))
        ).scalar_one_or_none()
        if not drift:
            return 0

        result = await self.db.execute(
            select(DriftMember).where(DriftMember.drift_id == drift_id)
        )
        members = list(result.scalars().all())
        if len(members) < 2:
            drift.last_ticked_at = datetime.now(timezone.utc)
            await self.db.flush()
            return 0

        params = get_params(drift.physics_profile)
        similarity_map = await self._build_similarity_map(
            [m.fragment_id for m in members]
        )

        member_map = {m.fragment_id: m for m in members}
        forces: dict[uuid.UUID, tuple[float, float]] = {
            m.fragment_id: (0.0, 0.0) for m in members
        }
        self._apply_pairwise_forces(
            member_map, similarity_map, forces, params,
            pos_getter=lambda m: (m.canvas_x, m.canvas_y),
        )

        moved = 0
        now = datetime.now(timezone.utc)
        for member in members:
            if member.pinned:
                continue
            fx, fy = forces[member.fragment_id]
            nx, ny, nvx, nvy = apply_drift(
                member.canvas_x, member.canvas_y,
                member.drift_vx, member.drift_vy,
                fx, fy, params,
            )
            member.canvas_x, member.canvas_y = nx, ny
            member.drift_vx, member.drift_vy = nvx, nvy
            member.last_drifted_at = now
            moved += 1

        drift.last_ticked_at = now
        await self.db.flush()
        logger.info(f"Drift tick: moved {moved} members for drift {drift_id}")
        return moved

    # ── Helpers ──

    async def _build_similarity_map(
        self, fragment_ids: list[uuid.UUID]
    ) -> dict[tuple[uuid.UUID, uuid.UUID], float]:
        """Query Qdrant for pairwise similarities among the given fragments."""
        sim: dict[tuple[uuid.UUID, uuid.UUID], float] = {}
        id_set = set(fragment_ids)
        for fid in fragment_ids:
            try:
                neighbors = await qdrant_client.query_points(
                    collection_name=COLLECTION_NAME,
                    query=str(fid),
                    limit=min(len(fragment_ids), 20),
                    score_threshold=0.3,
                )
                for point in neighbors.points:
                    other_id = uuid.UUID(point.payload["fragment_id"])
                    if other_id == fid or other_id not in id_set:
                        continue
                    pair = tuple(sorted([fid, other_id]))
                    sim[pair] = max(sim.get(pair, 0), point.score)
            except Exception:
                continue
        return sim

    def _apply_pairwise_forces(
        self,
        entity_map: dict,
        similarity_map: dict[tuple[uuid.UUID, uuid.UUID], float],
        forces: dict[uuid.UUID, tuple[float, float]],
        params: PhysicsParams,
        pos_getter,
    ) -> None:
        for (id_a, id_b), similarity in similarity_map.items():
            a = entity_map.get(id_a)
            b = entity_map.get(id_b)
            if not a or not b:
                continue
            ax, ay = pos_getter(a)
            bx, by = pos_getter(b)

            fax, fay = compute_attraction(ax, ay, bx, by, similarity, params)
            forces[id_a] = (forces[id_a][0] + fax, forces[id_a][1] + fay)
            forces[id_b] = (forces[id_b][0] - fax, forces[id_b][1] - fay)

            rx, ry = compute_repulsion(ax, ay, bx, by, params)
            forces[id_a] = (forces[id_a][0] + rx, forces[id_a][1] + ry)
            forces[id_b] = (forces[id_b][0] - rx, forces[id_b][1] - ry)
