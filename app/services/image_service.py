"""
Image generation service.

Primary: Google's Nano Banana (`gemini-2.5-flash-image`). Cheap, fast, strong
on evocative/painterly output which is what we want for mood lenses.

Fallback: OpenAI `gpt-image-1`. Fires when GEMINI_API_KEY is missing, the
Gemini call errors out, or returns no image parts.

Callers get PNG bytes plus the provider string (for provenance on the lens row).
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes         # PNG bytes
    provider: str       # e.g., "gemini-2.5-flash-image" or "gpt-image-1"
    mime_type: str = "image/png"


class ImageGenerationError(Exception):
    """Raised when every configured provider fails."""


class ImageGenerationService:
    def __init__(self) -> None:
        self._openai: AsyncOpenAI | None = (
            AsyncOpenAI(api_key=settings.openai_api_key)
            if settings.openai_api_key else None
        )

    async def generate(self, prompt: str) -> GeneratedImage:
        errors: list[str] = []

        if settings.gemini_api_key:
            try:
                return await self._generate_gemini(prompt)
            except Exception as e:
                logger.warning(f"Gemini image gen failed, falling back: {e}")
                errors.append(f"gemini: {e}")

        if self._openai is not None:
            try:
                return await self._generate_openai(prompt)
            except Exception as e:
                logger.error(f"OpenAI image gen failed: {e}")
                errors.append(f"openai: {e}")

        raise ImageGenerationError(
            "no image provider available or all providers failed — "
            + " | ".join(errors or ["no providers configured"])
        )

    # ── Gemini (Nano Banana) ───────────────────────────────────────────

    async def _generate_gemini(self, prompt: str) -> GeneratedImage:
        """
        Call `gemini-2.5-flash-image` via the google-genai SDK. The SDK call is
        synchronous, so we run it in a thread to avoid blocking the event loop.
        """
        def _call() -> bytes:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=settings.gemini_api_key)
            response = client.models.generate_content(
                model=settings.gemini_image_model,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    # Ask for an image response (no text).
                    response_modalities=["IMAGE"],
                ),
            )
            # Walk candidate parts, grab the first inline image.
            for cand in response.candidates or []:
                content = getattr(cand, "content", None)
                if not content:
                    continue
                for part in content.parts or []:
                    inline = getattr(part, "inline_data", None)
                    if inline and inline.data:
                        raw = inline.data
                        # Some SDK versions return already-decoded bytes,
                        # others return base64 strings. Normalize to bytes.
                        if isinstance(raw, str):
                            return base64.b64decode(raw)
                        return bytes(raw)
            raise RuntimeError("Gemini returned no inline image parts")

        data = await asyncio.to_thread(_call)
        return GeneratedImage(
            data=data,
            provider=settings.gemini_image_model,
        )

    # ── OpenAI fallback ────────────────────────────────────────────────

    async def _generate_openai(self, prompt: str) -> GeneratedImage:
        if self._openai is None:
            raise RuntimeError("openai client not configured")
        response = await self._openai.images.generate(
            model=settings.openai_image_model,
            prompt=prompt,
            size="1024x1024",
            n=1,
        )
        item = response.data[0]
        if getattr(item, "b64_json", None):
            data = base64.b64decode(item.b64_json)
        elif getattr(item, "url", None):
            # gpt-image-1 returns b64 by default, but handle URL mode for safety.
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(item.url)
                r.raise_for_status()
                data = r.content
        else:
            raise RuntimeError("OpenAI image response had no b64_json or url")
        return GeneratedImage(
            data=data,
            provider=settings.openai_image_model,
        )

    # Convenience used by the lens pipeline for quick-testing.
    @staticmethod
    def data_to_buffer(data: bytes) -> io.BytesIO:
        return io.BytesIO(data)
