import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.common import PaginatedResponse
from app.api.schemas.fragments import FragmentCompact, FragmentCreate, FragmentRead, FragmentUpdate
from app.core.database import get_db
from app.models.drift import DriftMode
from app.models.fragment import FragmentStatus
from app.models.user import User
from app.security import get_current_user
from app.services.drift_service import DriftLimitError, DriftService
from app.services.fragment_service import FragmentService
from app.workers.tasks import ingest_fragment, run_drift_for_drift

router = APIRouter(prefix="/fragments", tags=["fragments"])


async def _attach_to_drift_if_requested(
    db: AsyncSession,
    drift_id: uuid.UUID | None,
    owner_id: uuid.UUID,
    fragment_id: uuid.UUID,
    seed_x: float | None = None,
    seed_y: float | None = None,
) -> None:
    """Place the new fragment into the target drift and, if it's LIVE, kick a tick."""
    if drift_id is None:
        return
    drift_service = DriftService(db)
    drift = await drift_service.get_drift(drift_id, owner_id)
    if not drift:
        raise HTTPException(status_code=404, detail="Drift not found")
    try:
        await drift_service.add_members(
            drift_id, owner_id, [fragment_id], seed_x=seed_x, seed_y=seed_y
        )
    except DriftLimitError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if drift.mode == DriftMode.LIVE:
        run_drift_for_drift.delay(str(drift_id))


@router.post("/", response_model=FragmentRead, status_code=status.HTTP_201_CREATED)
async def create_text_fragment(
    data: FragmentCreate,
    drift_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a text-only fragment. If `drift_id` is set, also add it to that drift
    and (for LIVE drifts) trigger an immediate physics tick — the path behind
    real-time dumping of a scrape into a drift.
    """
    service = FragmentService(db)
    fragment = await service.create_text_fragment(user.id, data)

    await _attach_to_drift_if_requested(
        db, drift_id, user.id, fragment.id,
        seed_x=data.canvas_x, seed_y=data.canvas_y,
    )
    await db.commit()

    ingest_fragment.delay(str(fragment.id), str(user.id))
    return fragment


@router.post("/upload", response_model=FragmentRead, status_code=status.HTTP_201_CREATED)
async def upload_media_fragment(
    files: list[UploadFile] = File(...),
    title: str | None = Form(None),
    text_content: str | None = Form(None),
    tags: str | None = Form(None),
    drift_id: uuid.UUID | None = Form(None),
    canvas_x: float | None = Form(None),
    canvas_y: float | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload images, audio (voice), or video. `drift_id` optional — if set, the
    media fragment lands directly in that drift. Real-time voice: a browser-
    recorded audio blob hitting this endpoint with drift_id=X will be ingested
    (Whisper → embed → Qdrant) and start drifting within seconds because the
    ingest pipeline flips the fragment to ACTIVE and we trigger a tick on drop.
    """
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

    await _attach_to_drift_if_requested(
        db, drift_id, user.id, fragment.id,
        seed_x=canvas_x, seed_y=canvas_y,
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
    """Legacy: all active fragments with positions on the Fragment row itself."""
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
