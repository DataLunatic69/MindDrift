import uuid
from datetime import timedelta

from fastapi import UploadFile

from app.config import get_settings
from app.core.supabase import get_supabase

settings = get_settings()


class StorageService:
    def __init__(self):
        self.client = get_supabase()
        self.bucket = settings.supabase_storage_bucket

    async def upload_file(self, owner_id: uuid.UUID, file: UploadFile) -> str:
        """Upload a file to Supabase Storage. Returns the storage path."""
        ext = file.filename.rsplit(".", 1)[-1] if file.filename else "bin"
        file_id = uuid.uuid4().hex[:12]
        path = f"{owner_id}/{file_id}.{ext}"

        content = await file.read()
        self.client.storage.from_(self.bucket).upload(
            path=path,
            file=content,
            file_options={"content-type": file.content_type or "application/octet-stream"},
        )
        return path

    def upload_bytes(
        self,
        owner_id: uuid.UUID,
        data: bytes,
        *,
        subdir: str = "lenses",
        ext: str = "png",
        content_type: str = "image/png",
    ) -> str:
        """Upload raw bytes (e.g., a generated image) and return the storage path."""
        file_id = uuid.uuid4().hex[:12]
        path = f"{owner_id}/{subdir}/{file_id}.{ext}"
        self.client.storage.from_(self.bucket).upload(
            path=path,
            file=data,
            file_options={"content-type": content_type},
        )
        return path

    def get_signed_url(self, path: str, expires_in: int = 3600) -> str:
        """Generate a signed URL for temporary access."""
        result = self.client.storage.from_(self.bucket).create_signed_url(
            path, expires_in=expires_in
        )
        return result["signedURL"]

    def get_public_url(self, path: str) -> str:
        result = self.client.storage.from_(self.bucket).get_public_url(path)
        return result

    async def delete_file(self, path: str) -> None:
        self.client.storage.from_(self.bucket).remove([path])

    async def download_file(self, path: str) -> bytes:
        result = self.client.storage.from_(self.bucket).download(path)
        return result
