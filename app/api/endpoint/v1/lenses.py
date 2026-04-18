import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.lenses import LensCreate, LensRead
from app.core.database import get_db
from app.models.fragment import Fragment
from app.models.user import User
from app.security import get_current_user
from app.services.lens_service import LensService

router = APIRouter(prefix="/fragments", tags=["lenses"])


@router.get("/{fragment_id}/lenses", response_model=list[LensRead])
async def list_lenses(
    fragment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = LensService(db)
    lenses = await service.list_for_fragment(fragment_id, user.id)
    return [LensRead.from_model(l) for l in lenses]


@router.post(
    "/{fragment_id}/lenses",
    response_model=LensRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_lens(
    fragment_id: uuid.UUID,
    data: LensCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Enqueue generation of a lens for this fragment. Returns immediately with a
    PENDING lens row. The frontend should poll GET /lenses or watch the drift
    websocket for the READY/FAILED update.
    """
    owned = await db.execute(
        select(Fragment.id).where(
            Fragment.id == fragment_id, Fragment.owner_id == user.id
        )
    )
    if owned.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Fragment not found")

    service = LensService(db)
    lens = await service.create_pending(fragment_id, data.kind)
    await db.commit()

    # Fire the Celery task after commit so the worker can read the row.
    from app.workers.tasks import generate_lens_task
    generate_lens_task.delay(
        str(lens.id), str(fragment_id), str(user.id), data.kind.value
    )

    # Re-load the detached view for the response.
    lenses = await service.list_for_fragment(fragment_id, user.id)
    target = next((l for l in lenses if l.id == lens.id), None)
    if target is None:
        raise HTTPException(status_code=500, detail="lens row vanished")
    return LensRead.from_model(target)
