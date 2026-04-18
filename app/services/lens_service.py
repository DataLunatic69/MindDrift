"""
Orchestrates the generation of lenses — creative artifacts derived from a
fragment. The aim is NOT to summarize the fragment; it's to transform it into
artifacts the user didn't know they were asking for.

Supported kinds:
  - MOOD_IMAGE: Gemini Nano Banana (OpenAI fallback).
  - ECHO:       short evocative verse / haiku.
  - COUNTER:    shadow-side reframe.
  - SOCRATIC:   three Socratic questions.
  - ROADMAP:    steps toward the fragment's implied intent.
  - LINEAGE:    graph of the user's own nearby fragments via Qdrant k-NN.
  - MINDMAP:    hierarchical expansion (root → branches → leaves) by LLM.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from openai import AsyncOpenAI
from qdrant_client import models as qmodels
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.qdrant import COLLECTION_NAME, qdrant_client
from app.models.fragment import Fragment
from app.models.lens import FragmentLens, LensKind, LensStatus
from app.services.image_service import ImageGenerationError, ImageGenerationService
from app.services.storage_service import StorageService

logger = logging.getLogger(__name__)
settings = get_settings()
_openai_client = AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None


def _fragment_text(fragment: Fragment) -> str:
    parts = [
        fragment.title or "",
        fragment.text_content or "",
        fragment.transcription or "",
        fragment.image_description or "",
    ]
    joined = "\n".join(p for p in parts if p).strip()
    # Keep prompt compact — image models don't benefit from huge inputs.
    return joined[:800]


# ── Prompt crafting ────────────────────────────────────────────────────

MOOD_PROMPT_TEMPLATE = (
    "Create an abstract, painterly, evocative image that captures the "
    "EMOTIONAL atmosphere of the thought below. Do NOT illustrate it literally.\n\n"
    "Style: dreamlike texture, soft edges, layered color. Think mixed-media "
    "collage, watercolor bleeds, grain, warm natural light. Let colors carry "
    "the feeling more than any subject does.\n\n"
    "Important constraints:\n"
    "- No text, no letters, no words anywhere in the image\n"
    "- No people's faces\n"
    "- No logos, no watermarks\n"
    "- Focus on palette, light, composition, negative space\n\n"
    "The thought:\n"
)


def build_mood_prompt(fragment: Fragment) -> str:
    text = _fragment_text(fragment) or "(no content — evoke a sense of quiet beginning)"
    return MOOD_PROMPT_TEMPLATE + text


ECHO_PROMPT = """You are an echo chamber for a single thought.

Return a short piece of verse (4-8 lines, free-form or haiku-adjacent) that
REVERBERATES the feeling of the thought below — do not summarize it, do not
rhyme unless it insists on rhyming. Favor imagery over statement.

Respond with ONLY the verse. No title, no preamble.

The thought:
{text}
"""

COUNTER_PROMPT = """You are the user's gentle counter-voice.

The thought below is one-sided. Write a 2-4 sentence reframe that illuminates
what it LEAVES OUT or ASSUMES — its shadow side. This is not criticism; it's
the other half of the stereo. Stay curious, not adversarial.

Respond with ONLY the reframe. No preamble.

The thought:
{text}
"""

SOCRATIC_PROMPT = """You are Socrates reading the user's note.

Produce THREE questions that this thought raises — questions that would make
the thinker pause, not questions the thinker already asked. Each question
should be a single sentence. Favor depth over breadth.

Respond with ONLY valid JSON (no markdown fences):
{{
  "questions": ["...", "...", "..."]
}}

The thought:
{text}
"""

ROADMAP_PROMPT = """You are a pragmatic ally.

The thought below implies an intent or direction. Extract that intent and
produce 3-6 concrete steps toward it. Each step should be doable in under a
day, action-verb first, no fluff.

Respond with ONLY valid JSON (no markdown fences):
{{
  "intent": "one-sentence restatement of what the user seems to be reaching for",
  "steps": [
    {{"title": "short action title", "detail": "one-sentence elaboration"}},
    ...
  ]
}}

The thought:
{text}
"""

MINDMAP_PROMPT = """You are a structural thinker.

Expand the thought below into a mindmap: a root node, 3-5 branches, each with
2-4 child leaves. Branches should be orthogonal aspects (not sub-topics of
each other). Leaves are concrete instances, examples, or further questions.

Respond with ONLY valid JSON (no markdown fences):
{{
  "root": {{
    "label": "5-8 word root phrase distilling the thought",
    "children": [
      {{
        "label": "branch name",
        "children": [
          {{"label": "leaf"}},
          ...
        ]
      }},
      ...
    ]
  }}
}}

The thought:
{text}
"""


# ── LLM helper ────────────────────────────────────────────────────────

async def _llm_complete(
    prompt: str, *, max_tokens: int = 500, temperature: float = 0.8
) -> str:
    if _openai_client is None:
        raise RuntimeError("OPENAI_API_KEY not configured")
    response = await _openai_client.chat.completions.create(
        model=settings.synthesis_model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return (response.choices[0].message.content or "").strip()


def _parse_json(raw: str) -> dict[str, Any]:
    """Tolerate stray markdown fences."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        # drop optional language tag on first line
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1 :]
        if s.endswith("```"):
            s = s[:-3]
    return json.loads(s)


# ── Service ────────────────────────────────────────────────────────────

class LensService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.storage = StorageService()
        self.image_service = ImageGenerationService()

    async def list_for_fragment(
        self, fragment_id: uuid.UUID, owner_id: uuid.UUID
    ) -> list[FragmentLens]:
        """Lenses for a fragment the caller owns. Media paths resolved to
        signed URLs in place (the rows are detached from the session first so
        this isn't persisted)."""
        # Ownership guard: one query joining fragment for owner check.
        owned = await self.db.execute(
            select(Fragment.id).where(
                Fragment.id == fragment_id, Fragment.owner_id == owner_id
            )
        )
        if owned.scalar_one_or_none() is None:
            return []

        result = await self.db.execute(
            select(FragmentLens)
            .where(FragmentLens.fragment_id == fragment_id)
            .order_by(FragmentLens.created_at.asc())
        )
        lenses = list(result.scalars().all())
        for lens in lenses:
            self._resolve_media(lens)
        return lenses

    def _resolve_media(self, lens: FragmentLens) -> None:
        try:
            self.db.expunge(lens)
        except Exception:
            pass
        if lens.media_path and not (
            lens.media_path.startswith("http://")
            or lens.media_path.startswith("https://")
        ):
            try:
                lens.media_path = self.storage.get_signed_url(
                    lens.media_path, expires_in=3600
                )
            except Exception as e:
                logger.warning(f"lens signed URL failed: {e}")

    async def create_pending(
        self, fragment_id: uuid.UUID, kind: LensKind
    ) -> FragmentLens:
        lens = FragmentLens(
            fragment_id=fragment_id,
            kind=kind,
            status=LensStatus.PENDING,
        )
        self.db.add(lens)
        await self.db.flush()
        return lens

    async def mark_failed(self, lens_id: uuid.UUID, error: str) -> None:
        result = await self.db.execute(
            select(FragmentLens).where(FragmentLens.id == lens_id)
        )
        lens = result.scalar_one_or_none()
        if not lens:
            return
        lens.status = LensStatus.FAILED
        lens.error = error[:2000]
        lens.updated_at = datetime.now(timezone.utc)
        await self.db.flush()

    # ── Mood image generation ─────────────────────────────────────────

    async def generate_mood_image(
        self, lens_id: uuid.UUID, fragment_id: uuid.UUID, owner_id: uuid.UUID
    ) -> FragmentLens | None:
        """Run the full mood-image pipeline for an already-pending lens row."""
        lens_result = await self.db.execute(
            select(FragmentLens).where(FragmentLens.id == lens_id)
        )
        lens = lens_result.scalar_one_or_none()
        if lens is None:
            logger.warning(f"generate_mood_image: lens {lens_id} not found")
            return None

        frag_result = await self.db.execute(
            select(Fragment).where(Fragment.id == fragment_id)
        )
        fragment = frag_result.scalar_one_or_none()
        if fragment is None:
            lens.status = LensStatus.FAILED
            lens.error = "fragment missing"
            await self.db.flush()
            return lens

        prompt = build_mood_prompt(fragment)
        try:
            result = await self.image_service.generate(prompt)
        except ImageGenerationError as e:
            lens.status = LensStatus.FAILED
            lens.error = str(e)[:2000]
            await self.db.flush()
            return lens

        try:
            path = self.storage.upload_bytes(
                owner_id=owner_id,
                data=result.data,
                subdir="lenses/mood",
                ext="png",
                content_type="image/png",
            )
        except Exception as e:
            logger.error(f"mood image upload failed: {e}")
            lens.status = LensStatus.FAILED
            lens.error = f"storage upload failed: {e}"[:2000]
            await self.db.flush()
            return lens

        lens.media_path = path
        lens.provider = result.provider
        lens.status = LensStatus.READY
        lens.error = None
        await self.db.flush()
        logger.info(
            f"mood lens {lens.id} ready via {result.provider} for fragment {fragment_id}"
        )
        return lens

    # ── Text / JSON lens helpers ──────────────────────────────────────

    async def _load_for_generation(
        self, lens_id: uuid.UUID, fragment_id: uuid.UUID
    ) -> tuple[FragmentLens, Fragment] | None:
        lens = (
            await self.db.execute(
                select(FragmentLens).where(FragmentLens.id == lens_id)
            )
        ).scalar_one_or_none()
        if lens is None:
            logger.warning(f"lens {lens_id} not found")
            return None
        fragment = (
            await self.db.execute(
                select(Fragment).where(Fragment.id == fragment_id)
            )
        ).scalar_one_or_none()
        if fragment is None:
            lens.status = LensStatus.FAILED
            lens.error = "fragment missing"
            await self.db.flush()
            return None
        return lens, fragment

    async def _finish_ok(
        self,
        lens: FragmentLens,
        *,
        text: str | None = None,
        data: dict | None = None,
    ) -> FragmentLens:
        lens.text_content = text
        lens.data = data
        lens.status = LensStatus.READY
        lens.provider = settings.synthesis_model
        lens.error = None
        await self.db.flush()
        return lens

    async def _finish_failed(self, lens: FragmentLens, err: str) -> FragmentLens:
        lens.status = LensStatus.FAILED
        lens.error = err[:2000]
        await self.db.flush()
        return lens

    # ── Individual generators ─────────────────────────────────────────

    async def generate_echo(
        self, lens_id: uuid.UUID, fragment_id: uuid.UUID
    ) -> FragmentLens | None:
        loaded = await self._load_for_generation(lens_id, fragment_id)
        if loaded is None:
            return None
        lens, fragment = loaded
        try:
            verse = await _llm_complete(
                ECHO_PROMPT.format(text=_fragment_text(fragment) or "(silence)"),
                max_tokens=180,
                temperature=0.9,
            )
            return await self._finish_ok(lens, text=verse)
        except Exception as e:
            logger.error(f"echo lens {lens_id} failed: {e}")
            return await self._finish_failed(lens, str(e))

    async def generate_counter(
        self, lens_id: uuid.UUID, fragment_id: uuid.UUID
    ) -> FragmentLens | None:
        loaded = await self._load_for_generation(lens_id, fragment_id)
        if loaded is None:
            return None
        lens, fragment = loaded
        try:
            counter = await _llm_complete(
                COUNTER_PROMPT.format(text=_fragment_text(fragment) or "(no content)"),
                max_tokens=220,
                temperature=0.7,
            )
            return await self._finish_ok(lens, text=counter)
        except Exception as e:
            logger.error(f"counter lens {lens_id} failed: {e}")
            return await self._finish_failed(lens, str(e))

    async def generate_socratic(
        self, lens_id: uuid.UUID, fragment_id: uuid.UUID
    ) -> FragmentLens | None:
        loaded = await self._load_for_generation(lens_id, fragment_id)
        if loaded is None:
            return None
        lens, fragment = loaded
        try:
            raw = await _llm_complete(
                SOCRATIC_PROMPT.format(text=_fragment_text(fragment) or "(no content)"),
                max_tokens=260,
                temperature=0.8,
            )
            parsed = _parse_json(raw)
            questions = [q for q in (parsed.get("questions") or []) if isinstance(q, str)]
            if not questions:
                return await self._finish_failed(lens, "no questions returned")
            return await self._finish_ok(
                lens,
                text="\n".join(f"— {q}" for q in questions),
                data={"questions": questions},
            )
        except Exception as e:
            logger.error(f"socratic lens {lens_id} failed: {e}")
            return await self._finish_failed(lens, str(e))

    async def generate_roadmap(
        self, lens_id: uuid.UUID, fragment_id: uuid.UUID
    ) -> FragmentLens | None:
        loaded = await self._load_for_generation(lens_id, fragment_id)
        if loaded is None:
            return None
        lens, fragment = loaded
        try:
            raw = await _llm_complete(
                ROADMAP_PROMPT.format(text=_fragment_text(fragment) or "(no content)"),
                max_tokens=500,
                temperature=0.6,
            )
            parsed = _parse_json(raw)
            intent = parsed.get("intent") or ""
            steps = parsed.get("steps") or []
            if not steps:
                return await self._finish_failed(lens, "no steps returned")
            pretty = "\n".join(
                f"{i + 1}. {s.get('title', '')}: {s.get('detail', '')}"
                for i, s in enumerate(steps)
                if isinstance(s, dict)
            )
            return await self._finish_ok(
                lens,
                text=pretty,
                data={"intent": intent, "steps": steps},
            )
        except Exception as e:
            logger.error(f"roadmap lens {lens_id} failed: {e}")
            return await self._finish_failed(lens, str(e))

    async def generate_mindmap(
        self, lens_id: uuid.UUID, fragment_id: uuid.UUID
    ) -> FragmentLens | None:
        loaded = await self._load_for_generation(lens_id, fragment_id)
        if loaded is None:
            return None
        lens, fragment = loaded
        try:
            raw = await _llm_complete(
                MINDMAP_PROMPT.format(text=_fragment_text(fragment) or "(no content)"),
                max_tokens=900,
                temperature=0.7,
            )
            parsed = _parse_json(raw)
            root = parsed.get("root")
            if not isinstance(root, dict) or not root.get("label"):
                return await self._finish_failed(lens, "mindmap missing root node")
            return await self._finish_ok(lens, data={"root": root})
        except Exception as e:
            logger.error(f"mindmap lens {lens_id} failed: {e}")
            return await self._finish_failed(lens, str(e))

    async def generate_lineage(
        self, lens_id: uuid.UUID, fragment_id: uuid.UUID, owner_id: uuid.UUID
    ) -> FragmentLens | None:
        """
        Build a graph of the user's OWN fragments nearest this one in embedding
        space. Root = the seed fragment. Edges connect root → top-k neighbors,
        weighted by cosine similarity. No LLM call — the graph *is* the artifact.
        """
        loaded = await self._load_for_generation(lens_id, fragment_id)
        if loaded is None:
            return None
        lens, fragment = loaded

        if fragment.qdrant_point_id is None:
            return await self._finish_failed(lens, "fragment has no embedding yet")

        try:
            results = await qdrant_client.query_points(
                collection_name=COLLECTION_NAME,
                query=str(fragment.qdrant_point_id),
                query_filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="owner_id",
                            match=qmodels.MatchValue(value=str(owner_id)),
                        )
                    ],
                    must_not=[qmodels.HasIdCondition(has_id=[str(fragment.id)])],
                ),
                limit=12,
                score_threshold=0.3,
            )
        except Exception as e:
            logger.error(f"lineage qdrant query failed: {e}")
            return await self._finish_failed(lens, f"qdrant: {e}")

        neighbor_ids: list[uuid.UUID] = []
        scores: dict[str, float] = {}
        for p in results.points:
            fid = p.payload.get("fragment_id") if p.payload else None
            if not fid:
                continue
            try:
                neighbor_ids.append(uuid.UUID(fid))
                scores[fid] = float(p.score)
            except ValueError:
                continue

        neighbors: list[Fragment] = []
        if neighbor_ids:
            nres = await self.db.execute(
                select(Fragment).where(Fragment.id.in_(neighbor_ids))
            )
            neighbors = list(nres.scalars().all())

        root_id = str(fragment.id)
        nodes = [
            {
                "id": root_id,
                "label": fragment.title or (fragment.text_content or "(untitled)")[:60],
                "is_root": True,
            }
        ]
        edges = []
        for n in neighbors:
            nid = str(n.id)
            nodes.append({
                "id": nid,
                "label": n.title or (n.text_content or "(untitled)")[:60],
                "fragment_type": n.fragment_type.value if n.fragment_type else None,
            })
            edges.append({
                "source": root_id,
                "target": nid,
                "similarity": scores.get(nid, 0.0),
            })

        if not edges:
            return await self._finish_failed(lens, "no neighbors in embedding space yet")

        return await self._finish_ok(
            lens,
            data={"nodes": nodes, "edges": edges, "root_id": root_id},
        )
