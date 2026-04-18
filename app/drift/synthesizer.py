import json
import logging

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.collision import Collision, CollisionStatus
from app.models.fragment import Fragment

logger = logging.getLogger(__name__)
settings = get_settings()
openai_client = AsyncOpenAI(api_key=settings.openai_api_key)

SYNTHESIS_PROMPT = """You are the creative engine of Thought Drift, a system where half-finished
ideas float like constellations until they collide and form something new.

Two fragments have drifted close together based on deep semantic similarity.
Your job: imagine what new idea, project, or insight could emerge from their collision.

Fragment A:
- Title: {title_a}
- Content: {content_a}
- Tags: {tags_a}

Fragment B:
- Title: {title_b}
- Content: {content_b}
- Tags: {tags_b}

Generate a synthesis — a new idea born from the collision of these two fragments.
Respond ONLY with valid JSON (no markdown):
{{
  "title": "A compelling title for the new combined idea (max 100 chars)",
  "synthesis": "A 2-3 sentence description of the new idea that emerges",
  "reasoning": "One sentence explaining the connection you saw between the fragments"
}}"""


def _fragment_text(fragment: Fragment) -> str:
    """Combine all text fields into one string."""
    parts = [
        fragment.text_content or "",
        fragment.transcription or "",
        fragment.image_description or "",
    ]
    return "\n".join(p for p in parts if p).strip()[:1500]


class Synthesizer:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def synthesize_collision(self, collision: Collision) -> Collision:
        """Generate an LLM synthesis for a proposed collision."""
        # Load both fragments
        result_a = await self.db.execute(
            select(Fragment).where(Fragment.id == collision.fragment_a_id)
        )
        result_b = await self.db.execute(
            select(Fragment).where(Fragment.id == collision.fragment_b_id)
        )
        frag_a = result_a.scalar_one_or_none()
        frag_b = result_b.scalar_one_or_none()

        if not frag_a or not frag_b:
            logger.warning(f"Fragments not found for collision {collision.id}")
            return collision

        try:
            response = await openai_client.chat.completions.create(
                model=settings.synthesis_model,
                messages=[
                    {
                        "role": "user",
                        "content": SYNTHESIS_PROMPT.format(
                            title_a=frag_a.title or "Untitled",
                            content_a=_fragment_text(frag_a),
                            tags_a=", ".join(frag_a.tags or []),
                            title_b=frag_b.title or "Untitled",
                            content_b=_fragment_text(frag_b),
                            tags_b=", ".join(frag_b.tags or []),
                        ),
                    }
                ],
                max_tokens=400,
                temperature=0.8,
            )

            raw = response.choices[0].message.content.strip()
            data = json.loads(raw)

            collision.synthesis_title = data.get("title", "")[:300]
            collision.synthesis_text = data.get("synthesis", "")
            collision.synthesis_reasoning = data.get("reasoning", "")
            collision.status = CollisionStatus.SYNTHESIZED

            await self.db.flush()
            logger.info(f"Synthesized collision {collision.id}: {collision.synthesis_title}")

            # Notify the user's socket(s) so an open collisions tab re-fetches
            # without a manual refresh. Failure here is non-fatal.
            try:
                from app.api.endpoint.v1.ws import push_event_to_user
                await push_event_to_user(
                    str(collision.user_id),
                    {
                        "event": "synthesis_complete",
                        "collision_id": str(collision.id),
                        "synthesis_title": collision.synthesis_title,
                    },
                )
            except Exception as push_exc:
                logger.debug(f"push synthesis_complete failed: {push_exc}")

        except Exception as e:
            logger.error(f"Synthesis failed for collision {collision.id}: {e}")

        return collision

    async def synthesize_pending(self, user_id=None) -> int:
        """Synthesize all proposed collisions (optionally for a specific user)."""
        query = select(Collision).where(Collision.status == CollisionStatus.PROPOSED)
        if user_id:
            query = query.where(Collision.user_id == user_id)

        result = await self.db.execute(query)
        collisions = list(result.scalars().all())

        count = 0
        for collision in collisions:
            await self.synthesize_collision(collision)
            count += 1

        return count
