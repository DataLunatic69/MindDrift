"""
Physics-based idea synthesis.

Pipeline (see docs/multi-drift-and-memory-plan.md §8):
  seed fragments ─► fetch embeddings ─► weighted centroid
                                         │
                                         ▼
                    add Gaussian perturbation  (ε = temperature * σ)
                                         │
                                         ▼
             retrieve k-NN from (user fragments ∪ user memory)
                                         │
                                         ▼
             LLM grounded on seeds + neighbors + memory → new idea
"""

from __future__ import annotations

import json
import logging
import math
import random
import uuid

from openai import AsyncOpenAI
from qdrant_client import models as qmodels
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.qdrant import COLLECTION_NAME, MEMORY_COLLECTION_NAME, qdrant_client
from app.models.fragment import Fragment
from app.models.synthesis import Synthesis, SynthesisKind, SynthesisStatus
from app.models.user_memory import UserMemory

logger = logging.getLogger(__name__)
settings = get_settings()
openai_client = AsyncOpenAI(api_key=settings.openai_api_key)


SYNTHESIS_PROMPT = """You are the creative engine of MindDrift.

The user asked the system to generate a new idea from a neighborhood of their
own thinking. The seeds below were combined in embedding space, a small random
perturbation was applied, and the resulting region was probed for nearby ideas
in the user's memory.

Seed fragments (what the user directly chose as starting points):
{seeds}

Neighbors (other ideas of the user's that sit near the resulting vector):
{neighbors}

What the system has learned about how this user thinks:
{memory}

Write ONE new idea that plausibly emerges from the collective gravity of the
above. It should feel discovered, not averaged — surprising but grounded.
Respond with ONLY valid JSON (no markdown fences):
{{
  "title": "A compelling title (max 100 chars)",
  "synthesis": "2-3 sentences describing the new idea",
  "reasoning": "One sentence on why this region of the user's thought-space suggested it"
}}"""


def _fragment_text(f: Fragment) -> str:
    parts = [f.title or "", f.text_content or "", f.transcription or "", f.image_description or ""]
    return "\n".join(p for p in parts if p).strip()[:1200]


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def _centroid(vectors: list[list[float]], weights: list[float] | None = None) -> list[float]:
    if not vectors:
        raise ValueError("cannot take centroid of empty list")
    n = len(vectors[0])
    ws = weights or [1.0] * len(vectors)
    total_w = sum(ws) or 1.0
    out = [0.0] * n
    for v, w in zip(vectors, ws):
        for i in range(n):
            out[i] += v[i] * w
    return [x / total_w for x in out]


def _perturb(vector: list[float], magnitude: float) -> list[float]:
    """Add Gaussian noise, then renormalize — keeps us on the cosine unit sphere."""
    if magnitude <= 0.0:
        return list(vector)
    noisy = [x + random.gauss(0.0, magnitude) for x in vector]
    n = _norm(noisy) or 1.0
    return [x / n for x in noisy]


def _inter_vector_spread(vectors: list[list[float]]) -> float:
    """Mean pairwise cosine distance — a natural scale for the perturbation."""
    if len(vectors) < 2:
        return 0.3  # fall back to a sane default
    total = 0.0
    pairs = 0
    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            a, b = vectors[i], vectors[j]
            dot = sum(x * y for x, y in zip(a, b))
            na = _norm(a) or 1.0
            nb = _norm(b) or 1.0
            cos = dot / (na * nb)
            total += 1.0 - cos
            pairs += 1
    return (total / pairs) if pairs else 0.3


class SynthesisEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _fetch_seed_vectors(
        self, seed_ids: list[uuid.UUID]
    ) -> tuple[list[list[float]], list[Fragment]]:
        points = await qdrant_client.retrieve(
            collection_name=COLLECTION_NAME,
            ids=[str(sid) for sid in seed_ids],
            with_vectors=True,
            with_payload=True,
        )
        vectors: list[list[float]] = []
        vec_by_id: dict[str, list[float]] = {}
        for p in points:
            vec = p.vector
            if isinstance(vec, dict):  # named vectors shape
                vec = next(iter(vec.values()))
            if vec is not None:
                vec_by_id[str(p.id)] = list(vec)

        # Fetch fragments for text content + maintain ordering
        frag_result = await self.db.execute(
            select(Fragment).where(Fragment.id.in_(seed_ids))
        )
        fragments = list(frag_result.scalars().all())

        for f in fragments:
            if str(f.id) in vec_by_id:
                vectors.append(vec_by_id[str(f.id)])
        return vectors, fragments

    async def _retrieve_neighbors(
        self,
        target: list[float],
        owner_id: uuid.UUID,
        exclude_ids: list[uuid.UUID],
        limit: int,
    ) -> list[Fragment]:
        results = await qdrant_client.query_points(
            collection_name=COLLECTION_NAME,
            query=target,
            query_filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="owner_id",
                        match=qmodels.MatchValue(value=str(owner_id)),
                    )
                ],
                must_not=[qmodels.HasIdCondition(has_id=[str(i) for i in exclude_ids])]
                if exclude_ids else None,
            ),
            limit=limit,
            score_threshold=0.2,
        )
        neighbor_ids = [uuid.UUID(p.payload["fragment_id"]) for p in results.points]
        if not neighbor_ids:
            return []
        frags = await self.db.execute(
            select(Fragment).where(Fragment.id.in_(neighbor_ids))
        )
        return list(frags.scalars().all())

    async def _top_profile_memories(
        self, user_id: uuid.UUID, limit: int
    ) -> list[UserMemory]:
        """Direct-mode memory path: pick the user's heaviest PROFILE +
        SYNTHESIS_LEARNING notes, no embedding involved."""
        from app.models.user_memory import MemoryKind
        result = await self.db.execute(
            select(UserMemory)
            .where(
                UserMemory.user_id == user_id,
                UserMemory.kind.in_(
                    [MemoryKind.PROFILE, MemoryKind.SYNTHESIS_LEARNING]
                ),
            )
            .order_by(UserMemory.weight.desc(), UserMemory.updated_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def _retrieve_memory(
        self,
        target: list[float],
        user_id: uuid.UUID,
        limit: int,
    ) -> list[UserMemory]:
        try:
            results = await qdrant_client.query_points(
                collection_name=MEMORY_COLLECTION_NAME,
                query=target,
                query_filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="user_id",
                            match=qmodels.MatchValue(value=str(user_id)),
                        )
                    ]
                ),
                limit=limit,
                score_threshold=0.1,
            )
        except Exception as e:
            logger.debug(f"memory retrieval skipped: {e}")
            return []
        ids_and_scores = [
            (p.payload.get("memory_id"), p.score) for p in results.points
            if p.payload.get("memory_id")
        ]
        if not ids_and_scores:
            return []
        ids = [uuid.UUID(i) for i, _ in ids_and_scores if i]
        mem_result = await self.db.execute(
            select(UserMemory).where(UserMemory.id.in_(ids))
        )
        return list(mem_result.scalars().all())

    async def synthesize(
        self,
        user_id: uuid.UUID,
        seed_fragment_ids: list[uuid.UUID],
        drift_id: uuid.UUID | None = None,
        temperature: float | None = None,
        mode: str = "direct",
    ) -> Synthesis:
        """
        Generate a synthesis proposal.

        `mode="direct"` — skip vector math / k-NN retrieval. Just prompt the
        LLM with seed fragment texts + the user's top profile memories. This
        is the honest default for small fragment clouds where embedding
        neighbors are noisy.

        `mode="physics"` — full pipeline: weighted centroid → Gaussian
        perturbation → retrieve neighbors of the new vector + memory vectors
        → LLM grounded on all of it. Pays off once the user has a dense
        corpus; otherwise barely differs from direct mode.
        """
        if not seed_fragment_ids:
            raise ValueError("seed_fragment_ids must be non-empty")

        temperature = (
            settings.synthesis_default_temperature if temperature is None else temperature
        )

        synthesis = Synthesis(
            user_id=user_id,
            drift_id=drift_id,
            kind=SynthesisKind.PHYSICS,
            status=SynthesisStatus.PENDING,
            seed_fragment_ids=[str(i) for i in seed_fragment_ids],
            temperature=temperature,
        )
        self.db.add(synthesis)
        await self.db.flush()

        try:
            seed_frag_result = await self.db.execute(
                select(Fragment).where(Fragment.id.in_(seed_fragment_ids))
            )
            seed_fragments = list(seed_frag_result.scalars().all())

            neighbors: list[Fragment] = []
            memories: list[UserMemory] = []

            if mode == "physics":
                vectors, physics_seed_fragments = await self._fetch_seed_vectors(
                    seed_fragment_ids
                )
                if vectors:
                    # Prefer the ones we got vectors for
                    seed_fragments = physics_seed_fragments or seed_fragments
                    centroid = _centroid(vectors)
                    spread = _inter_vector_spread(vectors)
                    magnitude = temperature * spread
                    target = _perturb(centroid, magnitude)
                    synthesis.perturbation_magnitude = magnitude

                    neighbors = await self._retrieve_neighbors(
                        target, user_id, seed_fragment_ids,
                        settings.synthesis_neighbor_count,
                    )
                    memories = await self._retrieve_memory(
                        target, user_id, settings.synthesis_memory_count,
                    )
                else:
                    logger.info(
                        f"synthesis {synthesis.id}: no seed embeddings, "
                        f"falling back to direct mode"
                    )
                    memories = await self._top_profile_memories(
                        user_id, settings.synthesis_memory_count
                    )
            else:  # direct mode
                memories = await self._top_profile_memories(
                    user_id, settings.synthesis_memory_count
                )

            synthesis.neighbor_fragment_ids = [str(n.id) for n in neighbors]
            synthesis.memory_ids = [str(m.id) for m in memories]

            # Build prompt
            def _fmt_frag_list(frags: list[Fragment]) -> str:
                if not frags:
                    return "(none)"
                return "\n".join(
                    f"- [{f.title or 'Untitled'}] {_fragment_text(f)[:500]}"
                    for f in frags
                )

            memory_block = (
                "\n".join(f"- {m.content}" for m in memories if m.content)
                or "(no explicit profile yet)"
            )

            prompt = SYNTHESIS_PROMPT.format(
                seeds=_fmt_frag_list(seed_fragments),
                neighbors=_fmt_frag_list(neighbors),
                memory=memory_block,
            )

            response = await openai_client.chat.completions.create(
                model=settings.synthesis_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.7 + (temperature * 0.3),  # slider nudges LLM temp too
            )
            raw = (response.choices[0].message.content or "").strip()
            data = json.loads(raw)

            synthesis.title = (data.get("title") or "")[:300]
            synthesis.synthesis_text = data.get("synthesis") or ""
            synthesis.reasoning = data.get("reasoning") or ""
            synthesis.status = SynthesisStatus.PROPOSED
            await self.db.flush()
            logger.info(f"Synthesis {synthesis.id} proposed: {synthesis.title}")
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            synthesis.status = SynthesisStatus.DISMISSED
            synthesis.reasoning = f"generation error: {e}"[:500]
            await self.db.flush()

        return synthesis
