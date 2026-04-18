import logging

from openai import AsyncOpenAI

from app.config import get_settings
from app.graph.state import IngestState
from app.services.storage_service import StorageService

logger = logging.getLogger(__name__)
settings = get_settings()
openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
storage = StorageService()


async def transcribe_node(state: IngestState) -> dict:
    """Transcribe audio/video files using Whisper."""
    media_urls = state.get("media_urls", [])
    if not media_urls:
        return {"transcription": None}

    transcriptions = []
    for path in media_urls:
        try:
            file_bytes = await storage.download_file(path)
            # Determine filename for Whisper
            ext = path.rsplit(".", 1)[-1] if "." in path else "mp3"
            response = await openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=(f"audio.{ext}", file_bytes),
                response_format="text",
            )
            transcriptions.append(response)
        except Exception as e:
            logger.warning(f"Transcription failed for {path}: {e}")

    combined = "\n".join(t for t in transcriptions if t)
    return {"transcription": combined or None}
