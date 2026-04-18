"""
Vision node.

This is the user's first impression of the system's intelligence when they
drop an image in. A generic "describe the image" prompt produces a book-report
output that feels like a catalogue — we want the opposite: a creative
collaborator that sees *into* an image, not just at it.

We produce a structured JSON — multiple distinct angles — and persist it:
  - full JSON in Fragment.extra_metadata["vision"] for the UI to render as a
    rich card, and
  - a natural-language concatenation in Fragment.image_description so the
    embed+store pipeline still produces strong semantic search signal.
"""

from __future__ import annotations

import base64
import json
import logging

from openai import AsyncOpenAI

from app.config import get_settings
from app.graph.state import IngestState
from app.services.storage_service import StorageService

logger = logging.getLogger(__name__)
settings = get_settings()
openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
storage = StorageService()


DESCRIBE_PROMPT = """You are MindDrift's VISION — a creative collaborator, not
a cataloguer. When you look at a user's image, don't describe it. See into it.
Find what the user themselves might not consciously see. Provide angles that
make them say "oh, I hadn't thought of that."

Rules:
- No hedging phrases like "this image appears to" / "seems to" / "might suggest".
  Assert. Be confident.
- No markdown headers or bold in your strings. Pure prose.
- Be specific and concrete. Vague sentiment is worse than silence.
- If the image contains readable text, include it verbatim in `literal`.
- No clichés ("serene", "striking", "thought-provoking", "captivating").

Respond with ONLY valid JSON (no markdown fences, no commentary):
{
  "headline": "One arresting sentence that captures the heart of the image. Novelist's voice. 14-22 words. The sentence a reader would screenshot.",
  "literal": "A compact catalogue of what's literally present — subjects, setting, colors, any visible text verbatim. 2-3 sentences. Dense, useful for search.",
  "hidden_details": [
    "Three concrete things a first glance misses.",
    "Not interpretations — observations.",
    "Be specific: location in frame, relationships, tiny anomalies."
  ],
  "plot_twists": [
    "Two or three speculative reframes — 'what if this is actually…'",
    "Push against the obvious reading. Dare to be wrong.",
    "Each reframe is one sentence."
  ],
  "curious_facts": [
    "Two or three surprising, verifiable facts the image connects to.",
    "Historical, scientific, etymological, cultural — the user should not already know them.",
    "Each fact one sentence, attributed to its domain (science:, history:, etymology:, etc.)."
  ],
  "connects_to": [
    "3-5 ideas from other domains this image suggests.",
    "Each entry is 'domain → idea' — e.g., 'architecture → Brutalism's moral weight'.",
    "Be brave with leaps."
  ],
  "mood_signature": "5-9 words. Color + atmosphere + tension. No clichés, no lists. Write it like a band name.",
  "a_question": "One unanswered question the image raises. Sharp. Under 18 words."
}
"""


async def describe_node(state: IngestState) -> dict:
    """Generate rich vision artifacts for image files."""
    media_urls = state.get("media_urls", [])
    if not media_urls:
        return {"image_description": None, "image_insight": None}

    # We only run a rich analysis on the first image to keep latency bounded;
    # multi-image fragments still get a basic description on each, concatenated.
    # The RICH insight is for the primary (first) image.
    insights: list[dict] = []
    plain_descriptions: list[str] = []

    for idx, path in enumerate(media_urls):
        try:
            file_bytes = await storage.download_file(path)
            b64 = base64.b64encode(file_bytes).decode("utf-8")

            ext = path.rsplit(".", 1)[-1].lower()
            mime_map = {
                "jpg": "jpeg",
                "jpeg": "jpeg",
                "png": "png",
                "gif": "gif",
                "webp": "webp",
            }
            mime = f"image/{mime_map.get(ext, 'jpeg')}"

            response = await openai_client.chat.completions.create(
                model=settings.vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": DESCRIBE_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime};base64,{b64}"},
                            },
                        ],
                    }
                ],
                max_tokens=1100,
                temperature=0.85,  # we want creative leaps, not just safe prose.
            )
            raw = (response.choices[0].message.content or "").strip()
            parsed = _safe_parse_json(raw)
            if parsed:
                insights.append(parsed)
                plain_descriptions.append(_insight_to_plain_text(parsed))
            else:
                # Model went off-script: keep the raw text so search still works.
                logger.warning(
                    f"vision JSON parse failed for {path}; using raw text."
                )
                plain_descriptions.append(raw)

        except Exception as e:
            logger.warning(f"Image description failed for {path}: {e}")

    # First (primary) insight is what the UI renders. Others' plain text still
    # flows into the embedding so semantic search stays comprehensive.
    primary_insight = insights[0] if insights else None
    combined = "\n\n".join(d for d in plain_descriptions if d)

    return {
        "image_description": combined or None,
        "image_insight": primary_insight,
    }


def _safe_parse_json(raw: str) -> dict | None:
    """Tolerate stray ```json fences or leading prose."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1 :]
        if s.endswith("```"):
            s = s[:-3]
    # Some models preface with a natural-language paragraph; grab the first
    # {...} block if plain json.loads fails.
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(s[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _insight_to_plain_text(insight: dict) -> str:
    """
    Flatten the rich JSON into a paragraph tuned for embeddings + keyword search.
    Keep signal-dense keys (headline, literal, hidden_details, connects_to);
    drop prompt-scaffolding keys that don't help retrieval.
    """
    parts: list[str] = []
    if insight.get("headline"):
        parts.append(str(insight["headline"]))
    if insight.get("literal"):
        parts.append(str(insight["literal"]))
    if isinstance(insight.get("hidden_details"), list):
        parts.extend(str(x) for x in insight["hidden_details"])
    if isinstance(insight.get("connects_to"), list):
        parts.extend(str(x) for x in insight["connects_to"])
    if isinstance(insight.get("curious_facts"), list):
        parts.extend(str(x) for x in insight["curious_facts"])
    if insight.get("mood_signature"):
        parts.append(f"Mood: {insight['mood_signature']}")
    if insight.get("a_question"):
        parts.append(f"Open question: {insight['a_question']}")
    return "\n".join(p for p in parts if p)
