from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.canvas import BatchPositionUpdate
from app.core.database import get_db
from app.models.fragment import Fragment
from app.models.user import User
from app.security import get_current_user

router = APIRouter(prefix="/canvas", tags=["canvas"])


@router.put("/positions")
async def update_positions(
    data: BatchPositionUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Batch update fragment positions after user drags them on canvas."""
    updated = 0
    for pos in data.positions:
        result = await db.execute(
            select(Fragment).where(
                Fragment.id == pos.fragment_id,
                Fragment.owner_id == user.id,
            )
        )
        fragment = result.scalar_one_or_none()
        if fragment:
            fragment.canvas_x = pos.canvas_x
            fragment.canvas_y = pos.canvas_y
            fragment.drift_vx = 0.0  # reset velocity on manual move
            fragment.drift_vy = 0.0
            updated += 1

    return {"updated": updated}
