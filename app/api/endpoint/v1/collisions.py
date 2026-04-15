import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.collisions import CollisionRead, CollisionResolve
from app.core.database import get_db
from app.models.collision import CollisionStatus
from app.models.user import User
from app.security import get_current_user
from app.services.collision_service import CollisionService

router = APIRouter(prefix="/collisions", tags=["collisions"])


@router.get("/", response_model=list[CollisionRead])
async def list_collisions(
    status_filter: CollisionStatus | None = None,
    offset: int = 0,
    limit: int = 20,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = CollisionService(db)
    return await service.list_collisions(
        user.id, status_filter=status_filter, offset=offset, limit=limit
    )


@router.patch("/{collision_id}", response_model=CollisionRead)
async def resolve_collision(
    collision_id: uuid.UUID,
    data: CollisionResolve,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if data.status not in (CollisionStatus.ACCEPTED, CollisionStatus.DISMISSED):
        raise HTTPException(
            status_code=400,
            detail="Status must be 'accepted' or 'dismissed'",
        )

    service = CollisionService(db)
    collision = await service.resolve_collision(collision_id, user.id, data.status)
    if not collision:
        raise HTTPException(status_code=404, detail="Collision not found")
    return collision
