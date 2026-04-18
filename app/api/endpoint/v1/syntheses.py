import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.syntheses import SynthesisCreate, SynthesisRead, SynthesisResolve
from app.core.database import get_db
from app.drift.synthesis_engine import SynthesisEngine
from app.models.drift import DriftMode
from app.models.synthesis import SynthesisStatus
from app.models.user import User
from app.security import get_current_user
from app.services.drift_service import DriftLimitError, DriftService
from app.services.synthesis_service import SynthesisService
from app.workers.tasks import ingest_fragment, run_drift_for_drift

drifts_router = APIRouter(prefix="/drifts", tags=["syntheses"])
syntheses_router = APIRouter(prefix="/syntheses", tags=["syntheses"])


@drifts_router.post(
    "/{drift_id}/synthesize",
    response_model=SynthesisRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_synthesis(
    drift_id: uuid.UUID,
    data: SynthesisCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a new idea via physics-based vector synthesis over the given seed
    fragments, attached to this drift. See docs/multi-drift-and-memory-plan.md §8.
    """
    drift_service = DriftService(db)
    drift = await drift_service.get_drift(drift_id, user.id)
    if not drift:
        raise HTTPException(status_code=404, detail="Drift not found")

    engine = SynthesisEngine(db)
    synthesis = await engine.synthesize(
        user_id=user.id,
        seed_fragment_ids=data.seed_fragment_ids,
        drift_id=drift_id,
        temperature=data.temperature,
        mode=data.mode.value,
    )

    # Notify the drift's socket so any open tab of this drift refreshes.
    try:
        from app.api.endpoint.v1.ws import push_drift_event
        await push_drift_event(
            str(drift_id),
            {
                "event": "synthesis",
                "synthesis_id": str(synthesis.id),
                "drift_id": str(drift_id),
                "title": synthesis.title,
            },
        )
    except Exception:
        pass

    return synthesis


@syntheses_router.get("/", response_model=list[SynthesisRead])
async def list_syntheses(
    drift_id: uuid.UUID | None = None,
    status_filter: SynthesisStatus | None = None,
    offset: int = 0,
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = SynthesisService(db)
    return await service.list_syntheses(
        user.id, drift_id=drift_id, status_filter=status_filter,
        offset=offset, limit=limit,
    )


@syntheses_router.get("/{synthesis_id}", response_model=SynthesisRead)
async def get_synthesis(
    synthesis_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = SynthesisService(db)
    s = await service.get(synthesis_id, user.id)
    if not s:
        raise HTTPException(status_code=404, detail="Synthesis not found")
    return s


@syntheses_router.patch("/{synthesis_id}", response_model=SynthesisRead)
async def resolve_synthesis(
    synthesis_id: uuid.UUID,
    data: SynthesisResolve,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if data.status not in (SynthesisStatus.ACCEPTED, SynthesisStatus.DISMISSED):
        raise HTTPException(
            status_code=400, detail="status must be ACCEPTED or DISMISSED"
        )

    service = SynthesisService(db)
    synthesis = await service.resolve(
        synthesis_id, user.id, data.status, spawn_fragment=data.spawn_fragment,
    )
    if not synthesis:
        raise HTTPException(status_code=404, detail="Synthesis not found")

    # If the user accepted and opted to spawn a fragment, enqueue ingestion +
    # add it to the same drift, and (if LIVE) tick so it begins drifting.
    if (
        data.status == SynthesisStatus.ACCEPTED
        and synthesis.spawned_fragment_id
        and synthesis.drift_id
    ):
        ingest_fragment.delay(
            str(synthesis.spawned_fragment_id), str(user.id)
        )
        drift_service = DriftService(db)
        drift = await drift_service.get_drift(synthesis.drift_id, user.id)
        if drift:
            try:
                await drift_service.add_members(
                    synthesis.drift_id,
                    user.id,
                    [synthesis.spawned_fragment_id],
                )
            except DriftLimitError:
                pass  # soft: the synthesis is accepted, drift is just full
            if drift.mode == DriftMode.LIVE:
                run_drift_for_drift.delay(str(synthesis.drift_id))

    # Feedback loop for central memory — fire-and-forget task.
    try:
        from app.workers.tasks import update_user_memory_from_synthesis
        update_user_memory_from_synthesis.delay(
            str(synthesis.id), str(user.id), data.status.value
        )
    except Exception:
        pass

    return synthesis
