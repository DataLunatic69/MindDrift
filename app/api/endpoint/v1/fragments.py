import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.common import PaginatedResponse
from app.api.schemas.fragments import FragmentCompact, FragmentCreate, FragmentRead, FragmentUpdate
from app.core.database import get_db
from app.models.fragment import FragmentStatus
from app.models.user import User
from app.security import get_current_user
from app.services.fragment_service import FragmentService
from app.workers.tasks import ingest_fragment

router = APIRouter(prefix="/fragments", tags=["fragments"])


@router.post("/", response_model=FragmentRead, status_code=status.HTTP_201_CREATED)
async def create_text_fragment(
    data: FragmentCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a text-only fragment."""
    service = FragmentService(db)
    fragment = await service.create_text_fragment(user.id, data)
    await db.commit()

    # Kick off async ingestion pipeline
    ingest_fragment.delay(str(fragment.id), str(user.id))

    return fragment


@router.post("/upload", response_model=FragmentRead, status_code=status.HTTP_201_CREATED)
async def upload_media_fragment(
    files: list[UploadFile] = File(...),
    title: str | None = Form(None),
    text_content: str | None = Form(None),
    tags: str | None = Form(None),  # comma-separated
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload images, audio, or video as a fragment."""
    # Validate file types
    allowed_prefixes = ("image/", "audio/", "video/")
    for f in files:
        if not f.content_type or not f.content_type.startswith(allowed_prefixes):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported file type: {f.content_type}",
            )

    parsed_tags = [t.strip() for t in tags.split(",")] if tags else None

    service = FragmentService(db)
    fragment = await service.create_media_fragment(
        owner_id=user.id,
        files=files,
        title=title,
        text_content=text_content,
        tags=parsed_tags,
    )
    await db.commit()

    ingest_fragment.delay(str(fragment.id), str(user.id))

    return fragment


@router.get("/", response_model=PaginatedResponse[FragmentRead])
async def list_fragments(
    offset: int = 0,
    limit: int = 50,
    status_filter: FragmentStatus | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = FragmentService(db)
    fragments, total = await service.list_fragments(
        user.id, offset=offset, limit=limit, status_filter=status_filter
    )
    return PaginatedResponse(items=fragments, total=total, offset=offset, limit=limit)


@router.get("/canvas", response_model=list[FragmentCompact])
async def get_canvas_fragments(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all active fragments for canvas rendering (compact format)."""
    service = FragmentService(db)
    return await service.get_canvas_fragments(user.id)


@router.get("/{fragment_id}", response_model=FragmentRead)
async def get_fragment(
    fragment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = FragmentService(db)
    fragment = await service.get_fragment(fragment_id, user.id)
    if not fragment:
        raise HTTPException(status_code=404, detail="Fragment not found")
    return fragment


@router.patch("/{fragment_id}", response_model=FragmentRead)
async def update_fragment(
    fragment_id: uuid.UUID,
    data: FragmentUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = FragmentService(db)
    fragment = await service.update_fragment(fragment_id, user.id, data)
    if not fragment:
        raise HTTPException(status_code=404, detail="Fragment not found")
    return fragment


@router.delete("/{fragment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fragment(
    fragment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = FragmentService(db)
    deleted = await service.delete_fragment(fragment_id, user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Fragment not found")
