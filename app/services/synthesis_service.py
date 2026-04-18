import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fragment import Fragment, FragmentStatus, FragmentType
from app.models.synthesis import Synthesis, SynthesisStatus


class SynthesisService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_syntheses(
        self,
        user_id: uuid.UUID,
        drift_id: uuid.UUID | None = None,
        status_filter: SynthesisStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Synthesis]:
        query = select(Synthesis).where(Synthesis.user_id == user_id)
        if drift_id:
            query = query.where(Synthesis.drift_id == drift_id)
        if status_filter:
            query = query.where(Synthesis.status == status_filter)
        query = query.order_by(Synthesis.created_at.desc()).offset(offset).limit(limit)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get(
        self, synthesis_id: uuid.UUID, user_id: uuid.UUID
    ) -> Synthesis | None:
        result = await self.db.execute(
            select(Synthesis).where(
                Synthesis.id == synthesis_id, Synthesis.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def resolve(
        self,
        synthesis_id: uuid.UUID,
        user_id: uuid.UUID,
        new_status: SynthesisStatus,
        spawn_fragment: bool = False,
    ) -> Synthesis | None:
        synthesis = await self.get(synthesis_id, user_id)
        if not synthesis:
            return None
        synthesis.status = new_status
        synthesis.resolved_at = datetime.now(timezone.utc)

        if new_status == SynthesisStatus.ACCEPTED and spawn_fragment and synthesis.title:
            # Materialize the proposal as a new pending fragment so it can enter
            # the ingest graph and become a real, embeddable citizen of the drift.
            combined = "\n\n".join(
                p for p in [synthesis.title, synthesis.synthesis_text] if p
            )
            spawned = Fragment(
                owner_id=user_id,
                fragment_type=FragmentType.TEXT,
                status=FragmentStatus.PENDING,
                title=synthesis.title,
                text_content=combined,
                tags=["synthesis", "drift"],
            )
            self.db.add(spawned)
            await self.db.flush()
            synthesis.spawned_fragment_id = spawned.id

        await self.db.flush()
        return synthesis
