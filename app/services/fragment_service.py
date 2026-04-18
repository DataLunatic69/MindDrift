import logging
import random
import uuid

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.fragments import FragmentCreate, FragmentUpdate
from app.models.fragment import Fragment, FragmentStatus, FragmentType
from app.services.storage_service import StorageService

logger = logging.getLogger(__name__)


def _looks_like_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


class FragmentService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.storage = StorageService()

    async def create_text_fragment(
        self, owner_id: uuid.UUID, data: FragmentCreate
    ) -> Fragment:
        fragment = Fragment(
            owner_id=owner_id,
            fragment_type=data.fragment_type,
            title=data.title,
            text_content=data.text_content,
            tags=data.tags,
            status=FragmentStatus.PENDING,
            canvas_x=data.canvas_x or random.uniform(-500, 500),
            canvas_y=data.canvas_y or random.uniform(-500, 500),
        )
        self.db.add(fragment)
        await self.db.flush()
        return fragment

    async def create_media_fragment(
        self,
        owner_id: uuid.UUID,
        files: list[UploadFile],
        title: str | None = None,
        text_content: str | None = None,
        tags: list[str] | None = None,
    ) -> Fragment:
        # Determine type from first file
        first_mime = files[0].content_type or ""
        if first_mime.startswith("image/"):
            ftype = FragmentType.IMAGE
        elif first_mime.startswith("audio/"):
            ftype = FragmentType.AUDIO
        elif first_mime.startswith("video/"):
            ftype = FragmentType.VIDEO
        else:
            ftype = FragmentType.MIXED

        # Upload files to Supabase Storage
        media_urls = []
        for file in files:
            url = await self.storage.upload_file(owner_id, file)
            media_urls.append(url)

        fragment = Fragment(
            owner_id=owner_id,
            fragment_type=ftype,
            title=title,
            text_content=text_content,
            tags=tags,
            media_urls=media_urls,
            status=FragmentStatus.PENDING,
            canvas_x=random.uniform(-500, 500),
            canvas_y=random.uniform(-500, 500),
        )
        self.db.add(fragment)
        await self.db.flush()
        return fragment

    async def get_fragment(
        self, fragment_id: uuid.UUID, owner_id: uuid.UUID
    ) -> Fragment | None:
        result = await self.db.execute(
            select(Fragment).where(
                Fragment.id == fragment_id,
                Fragment.owner_id == owner_id,
            )
        )
        fragment = result.scalar_one_or_none()
        if fragment:
            self._resolve_media_urls(fragment)
        return fragment

    def _resolve_media_urls(self, fragment: Fragment) -> None:
        """
        Replace storage paths on `media_urls` with short-lived signed URLs so
        the frontend <img> can fetch them directly. The fragment is detached
        from the session first so this transient rewrite never gets persisted
        back to Postgres (signed URLs would otherwise expire in the DB row).
        """
        # Detach so mutations don't flush on commit. Safe even if called twice.
        try:
            self.db.expunge(fragment)
        except Exception:
            pass

        if fragment.media_urls:
            resolved: list[str] = []
            for item in fragment.media_urls:
                if not item or _looks_like_url(item):
                    resolved.append(item)
                    continue
                try:
                    resolved.append(self.storage.get_signed_url(item, expires_in=3600))
                except Exception as e:
                    logger.warning(f"signed URL failed for {item}: {e}")
                    resolved.append(item)
            fragment.media_urls = resolved

        if fragment.thumbnail_url and not _looks_like_url(fragment.thumbnail_url):
            try:
                fragment.thumbnail_url = self.storage.get_signed_url(
                    fragment.thumbnail_url, expires_in=3600
                )
            except Exception as e:
                logger.warning(f"thumbnail signed URL failed: {e}")

    async def list_fragments(
        self,
        owner_id: uuid.UUID,
        offset: int = 0,
        limit: int = 50,
        status_filter: FragmentStatus | None = None,
    ) -> tuple[list[Fragment], int]:
        query = select(Fragment).where(Fragment.owner_id == owner_id)
        count_query = select(func.count(Fragment.id)).where(Fragment.owner_id == owner_id)

        if status_filter:
            query = query.where(Fragment.status == status_filter)
            count_query = count_query.where(Fragment.status == status_filter)

        query = query.order_by(Fragment.created_at.desc()).offset(offset).limit(limit)

        result = await self.db.execute(query)
        fragments = list(result.scalars().all())

        count_result = await self.db.execute(count_query)
        total = count_result.scalar() or 0

        for f in fragments:
            self._resolve_media_urls(f)
        return fragments, total

    async def get_canvas_fragments(self, owner_id: uuid.UUID) -> list[Fragment]:
        """Get all active fragments for canvas rendering."""
        result = await self.db.execute(
            select(Fragment).where(
                Fragment.owner_id == owner_id,
                Fragment.status == FragmentStatus.ACTIVE,
            )
        )
        fragments = list(result.scalars().all())
        for f in fragments:
            self._resolve_media_urls(f)
        return fragments

    async def update_fragment(
        self,
        fragment_id: uuid.UUID,
        owner_id: uuid.UUID,
        data: FragmentUpdate,
    ) -> Fragment | None:
        fragment = await self.get_fragment(fragment_id, owner_id)
        if not fragment:
            return None

        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(fragment, field, value)

        await self.db.flush()
        return fragment

    async def delete_fragment(
        self, fragment_id: uuid.UUID, owner_id: uuid.UUID
    ) -> bool:
        fragment = await self.get_fragment(fragment_id, owner_id)
        if not fragment:
            return False

        # Clean up storage
        if fragment.media_urls:
            for url in fragment.media_urls:
                await self.storage.delete_file(url)

        await self.db.delete(fragment)
        await self.db.flush()
        return True
