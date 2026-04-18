import random
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.drifts import DriftCreate, DriftUpdate
from app.config import get_settings
from app.models.drift import Drift, DriftMember, DriftMode
from app.models.fragment import Fragment

settings = get_settings()


class DriftLimitError(Exception):
    """Raised when a user hits the max-drifts or max-members cap."""


class DriftService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_drift(
        self, owner_id: uuid.UUID, data: DriftCreate
    ) -> Drift:
        count_result = await self.db.execute(
            select(func.count(Drift.id)).where(Drift.owner_id == owner_id)
        )
        count = count_result.scalar() or 0
        if count >= settings.max_drifts_per_user:
            raise DriftLimitError(
                f"Drift cap reached ({settings.max_drifts_per_user}). "
                "Delete one before creating another."
            )

        drift = Drift(
            owner_id=owner_id,
            name=data.name,
            description=data.description,
            mode=data.mode,
            physics_profile=data.physics_profile,
        )
        self.db.add(drift)
        await self.db.flush()
        return drift

    async def get_drift(
        self, drift_id: uuid.UUID, owner_id: uuid.UUID
    ) -> Drift | None:
        result = await self.db.execute(
            select(Drift).where(
                Drift.id == drift_id, Drift.owner_id == owner_id
            )
        )
        return result.scalar_one_or_none()

    async def list_drifts(self, owner_id: uuid.UUID) -> list[tuple[Drift, int]]:
        """Return (drift, member_count) tuples."""
        result = await self.db.execute(
            select(Drift, func.count(DriftMember.fragment_id))
            .outerjoin(DriftMember, DriftMember.drift_id == Drift.id)
            .where(Drift.owner_id == owner_id)
            .group_by(Drift.id)
            .order_by(Drift.created_at.desc())
        )
        return [(d, c or 0) for d, c in result.all()]

    async def update_drift(
        self,
        drift_id: uuid.UUID,
        owner_id: uuid.UUID,
        data: DriftUpdate,
    ) -> Drift | None:
        drift = await self.get_drift(drift_id, owner_id)
        if not drift:
            return None
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(drift, field, value)
        await self.db.flush()
        return drift

    async def delete_drift(
        self, drift_id: uuid.UUID, owner_id: uuid.UUID
    ) -> bool:
        drift = await self.get_drift(drift_id, owner_id)
        if not drift:
            return False
        await self.db.delete(drift)
        await self.db.flush()
        return True

    # ── Membership ──

    async def member_count(self, drift_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count(DriftMember.fragment_id)).where(
                DriftMember.drift_id == drift_id
            )
        )
        return result.scalar() or 0

    async def add_members(
        self,
        drift_id: uuid.UUID,
        owner_id: uuid.UUID,
        fragment_ids: list[uuid.UUID],
        seed_x: float | None = None,
        seed_y: float | None = None,
    ) -> list[DriftMember]:
        drift = await self.get_drift(drift_id, owner_id)
        if not drift:
            return []

        # Ownership check on fragments
        frag_result = await self.db.execute(
            select(Fragment.id).where(
                Fragment.id.in_(fragment_ids),
                Fragment.owner_id == owner_id,
            )
        )
        valid_ids = {row[0] for row in frag_result.all()}

        existing_result = await self.db.execute(
            select(DriftMember.fragment_id).where(
                DriftMember.drift_id == drift_id,
                DriftMember.fragment_id.in_(valid_ids),
            )
        )
        existing = {row[0] for row in existing_result.all()}

        to_add = valid_ids - existing
        if not to_add:
            return []

        current = await self.member_count(drift_id)
        if current + len(to_add) > settings.max_members_per_drift:
            raise DriftLimitError(
                f"Member cap reached ({settings.max_members_per_drift}) for this drift."
            )

        members = []
        for fid in to_add:
            # Place near seed point if given, otherwise scatter randomly.
            if seed_x is not None and seed_y is not None:
                cx = seed_x + random.uniform(-30.0, 30.0)
                cy = seed_y + random.uniform(-30.0, 30.0)
            else:
                cx = random.uniform(-400.0, 400.0)
                cy = random.uniform(-400.0, 400.0)

            member = DriftMember(
                drift_id=drift_id,
                fragment_id=fid,
                canvas_x=cx,
                canvas_y=cy,
            )
            self.db.add(member)
            members.append(member)

        await self.db.flush()
        return members

    async def remove_member(
        self,
        drift_id: uuid.UUID,
        fragment_id: uuid.UUID,
        owner_id: uuid.UUID,
    ) -> bool:
        drift = await self.get_drift(drift_id, owner_id)
        if not drift:
            return False
        result = await self.db.execute(
            delete(DriftMember).where(
                DriftMember.drift_id == drift_id,
                DriftMember.fragment_id == fragment_id,
            )
        )
        await self.db.flush()
        return (result.rowcount or 0) > 0

    async def list_members(
        self, drift_id: uuid.UUID, owner_id: uuid.UUID
    ) -> list[DriftMember]:
        drift = await self.get_drift(drift_id, owner_id)
        if not drift:
            return []
        result = await self.db.execute(
            select(DriftMember).where(DriftMember.drift_id == drift_id)
        )
        return list(result.scalars().all())

    async def apply_batch_positions(
        self,
        drift_id: uuid.UUID,
        owner_id: uuid.UUID,
        positions: list,  # list[DriftMemberPosition]
    ) -> int:
        drift = await self.get_drift(drift_id, owner_id)
        if not drift:
            return 0

        updated = 0
        for pos in positions:
            result = await self.db.execute(
                select(DriftMember).where(
                    DriftMember.drift_id == drift_id,
                    DriftMember.fragment_id == pos.fragment_id,
                )
            )
            member = result.scalar_one_or_none()
            if member:
                member.canvas_x = pos.canvas_x
                member.canvas_y = pos.canvas_y
                # Reset velocity on manual move — matches legacy canvas UX.
                member.drift_vx = 0.0
                member.drift_vy = 0.0
                member.pinned = pos.pinned
                updated += 1
        await self.db.flush()
        return updated

    async def mark_ticked(self, drift_id: uuid.UUID) -> None:
        await self.db.execute(
            select(Drift).where(Drift.id == drift_id)
        )
        drift = (
            await self.db.execute(select(Drift).where(Drift.id == drift_id))
        ).scalar_one_or_none()
        if drift:
            drift.last_ticked_at = datetime.now(timezone.utc)
            await self.db.flush()

    async def ensure_default_drift(self, owner_id: uuid.UUID) -> Drift:
        """Return the user's 'Main' drift, creating it if it doesn't exist."""
        result = await self.db.execute(
            select(Drift)
            .where(Drift.owner_id == owner_id, Drift.name == "Main")
            .limit(1)
        )
        drift = result.scalar_one_or_none()
        if drift:
            return drift
        drift = Drift(
            owner_id=owner_id,
            name="Main",
            description="Your default drift — everything lands here unless you pick another.",
            mode=DriftMode.LIVE,
        )
        self.db.add(drift)
        await self.db.flush()
        return drift
