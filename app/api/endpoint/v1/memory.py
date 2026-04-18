import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.memory import MemoryNoteCreate, MemoryRead
from app.core.database import get_db
from app.models.user import User
from app.models.user_memory import MemoryKind
from app.security import get_current_user
from app.services.memory_service import MemoryService

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/", response_model=list[MemoryRead])
async def list_memory(
    kind: MemoryKind | None = None,
    limit: int = 100,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read the user's central memory — profile notes, events, and learnings."""
    service = MemoryService(db)
    return await service.list_memories(user.id, kind=kind, limit=limit)


@router.post("/notes", response_model=MemoryRead, status_code=status.HTTP_201_CREATED)
async def create_memory_note(
    data: MemoryNoteCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    User-authored profile note — 'I tend to connect music theory to systems
    programming.' These become permanent attractors in physics-based synthesis.
    """
    service = MemoryService(db)
    return await service.create_memory(
        user.id,
        kind=MemoryKind.PROFILE,
        content=data.content,
        metadata=data.metadata,
    )


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = MemoryService(db)
    ok = await service.delete_memory(memory_id, user.id)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")
