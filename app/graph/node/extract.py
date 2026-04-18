"""
Extraction node.

Previously: flat tags + entities only. That's useful for search but reads like
a library-cataloguing intern wrote it. For a creative knowledge system, the
extraction step should also surface what makes a thought *alive* —
the paradoxes inside it, the spark ideas it suggests, and the one question
it leaves hanging.

We still return tags+entities for search, but also a `text_spark` dict that
powers a rich UI card and seeds later synthesis.
"""

from __future__ import annotations

import json
import logging

from openai import AsyncOpenAI

from app.config import get_settings
from app.graph.state import IngestState

logger = logging.getLogger(__name__)
settings = get_settings()
openai_client = AsyncOpenAI(api_key=settings.openai_api_key)


EXTRACT_PROMPT = """You read a thought the user just captured and pull out
multiple angles at once. You're not a summarizer — you're a reader who notices
what the author didn't consciously intend.

Rules:
- No hedging ("seems", "perhaps", "might"). Assert.
- No clichés, no motivational-poster phrases.
- Be specific. If you can't be specific, say nothing.
- If the content is too short or empty, still return the JSON shape, but use
  empty arrays rather than inventing material.

Return ONLY valid JSON (no fences):
{{
  "tags": ["3-8 short, lowercase tags (single words or short phrases)"],
  "entities": {{
    "people": [],
    "places": [],
    "topics": [],
    "tools": []
  }},
  "sparks": [
    "2-3 'spark ideas' — adjacent thoughts the author didn't write but that live next door to what they did.",
    "Each one sentence, concrete, surprising."
  ],
  "tensions": [
    "1-2 internal tensions / paradoxes inside the thought.",
    "Frame as 'X pulls against Y.'"
  ],
  "a_question": "One sharp question this content raises but doesn't answer. Under 18 words.",
  "one_liner": "A single arresting sentence (14-22 words) that captures the gravitational center. Novelist's voice, not summarizer's."
}}

Content:
{text}
"""


async def extract_node(state: IngestState) -> dict:
    parts = [
        state.get("text_content") or "",
        state.get("transcription") or "",
        state.get("image_description") or "",
    ]
    combined = "\n".join(p for p in parts if p).strip()

    if not combined:
        return {
            "extracted_tags": [],
            "extracted_entities": {},
            "combined_text": "",
            "text_spark": None,
        }

    try:
        response = await openai_client.chat.completions.create(
            model=settings.synthesis_model,
            messages=[
                {
                    "role": "user",
                    "content": EXTRACT_PROMPT.format(text=combined[:3500]),
                }
            ],
            max_tokens=700,
            # Temperature is a tension here — tags want determinism, sparks
            # want creativity. 0.55 is the compromise that gave best results
            # in spot-checks: stable tags, non-trivial sparks.
            temperature=0.55,
        )
        raw = (response.choices[0].message.content or "").strip()
        data = _parse_json(raw) or {}

        tags = data.get("tags") or []
        entities = data.get("entities") or {}

        spark = {
            "sparks": _string_list(data.get("sparks")),
            "tensions": _string_list(data.get("tensions")),
            "a_question": (data.get("a_question") or "").strip() or None,
            "one_liner": (data.get("one_liner") or "").strip() or None,
        }
        # Drop the whole spark if it's completely empty — avoids rendering a
        # scaffold with no substance.
        if not any(v for v in spark.values()):
            spark_out = None
        else:
            spark_out = spark

        return {
            "extracted_tags": tags,
            "extracted_entities": entities,
            "combined_text": combined,
            "text_spark": spark_out,
        }
    except Exception as e:
        logger.warning(f"Extraction failed: {e}")
        return {
            "extracted_tags": [],
            "extracted_entities": {},
            "combined_text": combined,
            "text_spark": None,
        }


def _parse_json(raw: str) -> dict | None:
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1 :]
        if s.endswith("```"):
            s = s[:-3]
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(s[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _string_list(v) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if isinstance(x, str) and x.strip()]
