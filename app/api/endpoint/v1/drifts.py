import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.drifts import (
    DriftBatchPositionUpdate,
    DriftCreate,
    DriftMemberAdd,
    DriftMemberRead,
    DriftRead,
    DriftUpdate,
    TickResponse,
)
from app.core.database import get_db
from app.models.drift import DriftMode
from app.models.user import User
from app.security import get_current_user
from app.services.drift_service import DriftLimitError, DriftService

router = APIRouter(prefix="/drifts", tags=["drifts"])


def _to_read(drift, member_count: int) -> DriftRead:
    return DriftRead(
        id=drift.id,
        owner_id=drift.owner_id,
        name=drift.name,
        description=drift.description,
        mode=drift.mode,
        physics_profile=drift.physics_profile,
        created_at=drift.created_at,
        updated_at=drift.updated_at,
        last_ticked_at=drift.last_ticked_at,
        member_count=member_count,
    )


@router.post("/", response_model=DriftRead, status_code=status.HTTP_201_CREATED)
async def create_drift(
    data: DriftCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = DriftService(db)
    try:
        drift = await service.create_drift(user.id, data)
    except DriftLimitError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _to_read(drift, 0)


@router.get("/", response_model=list[DriftRead])
async def list_drifts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = DriftService(db)
    pairs = await service.list_drifts(user.id)
    return [_to_read(d, c) for d, c in pairs]


@router.get("/{drift_id}", response_model=DriftRead)
async def get_drift(
    drift_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = DriftService(db)
    drift = await service.get_drift(drift_id, user.id)
    if not drift:
        raise HTTPException(status_code=404, detail="Drift not found")
    count = await service.member_count(drift_id)
    return _to_read(drift, count)


@router.patch("/{drift_id}", response_model=DriftRead)
async def update_drift(
    drift_id: uuid.UUID,
    data: DriftUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = DriftService(db)
    drift = await service.update_drift(drift_id, user.id, data)
    if not drift:
        raise HTTPException(status_code=404, detail="Drift not found")
    count = await service.member_count(drift_id)
    return _to_read(drift, count)


@router.delete("/{drift_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_drift(
    drift_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = DriftService(db)
    deleted = await service.delete_drift(drift_id, user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Drift not found")


# ── Members ──

@router.get("/{drift_id}/members", response_model=list[DriftMemberRead])
async def list_members(
    drift_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = DriftService(db)
    drift = await service.get_drift(drift_id, user.id)
    if not drift:
        raise HTTPException(status_code=404, detail="Drift not found")
    members = await service.list_members(drift_id, user.id)
    return members


@router.post(
    "/{drift_id}/members",
    response_model=list[DriftMemberRead],
    status_code=status.HTTP_201_CREATED,
)
async def add_members(
    drift_id: uuid.UUID,
    data: DriftMemberAdd,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Bulk-add fragments to a drift. Used by 'send to drift' on fragment cards —
    the user can drop N past works into different drifts in one gesture.
    """
    service = DriftService(db)
    try:
        members = await service.add_members(
            drift_id,
            user.id,
            data.fragment_ids,
            seed_x=data.canvas_x,
            seed_y=data.canvas_y,
        )
    except DriftLimitError as e:
        raise HTTPException(status_code=409, detail=str(e))

    # If the drift is LIVE, kick off a tick so the new fragments start moving immediately.
    drift = await service.get_drift(drift_id, user.id)
    if drift and drift.mode == DriftMode.LIVE and members:
        from app.workers.tasks import run_drift_for_drift
        run_drift_for_drift.delay(str(drift_id))

    return members


@router.delete(
    "/{drift_id}/members/{fragment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    drift_id: uuid.UUID,
    fragment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = DriftService(db)
    ok = await service.remove_member(drift_id, fragment_id, user.id)
    if not ok:
        raise HTTPException(status_code=404, detail="Member not found")


# ── Positions (drag-and-drop) ──

@router.put("/{drift_id}/positions")
async def update_positions(
    drift_id: uuid.UUID,
    data: DriftBatchPositionUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = DriftService(db)
    updated = await service.apply_batch_positions(drift_id, user.id, data.positions)
    return {"updated": updated}


# ── On-demand tick (real-time physics) ──

@router.post("/{drift_id}/tick", response_model=TickResponse)
async def tick_drift(
    drift_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Fire a physics tick for this drift right now, outside the beat schedule.
    This is how 'dump a scrape and watch it drift in real time' works.
    """
    service = DriftService(db)
    drift = await service.get_drift(drift_id, user.id)
    if not drift:
        raise HTTPException(status_code=404, detail="Drift not found")
    if drift.mode == DriftMode.FROZEN:
        return TickResponse(drift_id=drift_id, enqueued=False, reason="drift is frozen")

    from app.workers.tasks import run_drift_for_drift
    run_drift_for_drift.delay(str(drift_id))
    return TickResponse(drift_id=drift_id, enqueued=True)
