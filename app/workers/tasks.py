import asyncio
import logging
import uuid

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


_worker_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    """
    Run an async coroutine from a synchronous Celery task.

    Uses a single persistent event loop per worker process. Creating a fresh
    loop per task breaks async resources pinned to their creator loop
    (SQLAlchemy/asyncpg pool, httpx clients inside Qdrant / OpenAI, aioredis),
    producing errors like 'Event loop is closed' or 'got Future attached to a
    different loop'. A persistent loop keeps all connections valid across
    tasks; Celery's prefork gives each worker its own process, so there's no
    loop sharing across workers.
    """
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)
    return _worker_loop.run_until_complete(coro)


@celery_app.task(name="app.workers.tasks.ingest_fragment", bind=True, max_retries=3)
def ingest_fragment(self, fragment_id: str, owner_id: str):
    """Run the LangGraph ingestion pipeline for a fragment."""
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.graph.ingest import run_ingest
    from app.models.fragment import Fragment, FragmentStatus

    async def _ingest():
        fid = uuid.UUID(fragment_id)
        oid = uuid.UUID(owner_id)

        async with async_session_factory() as session:
            result = await session.execute(
                select(Fragment).where(Fragment.id == fid)
            )
            fragment = result.scalar_one_or_none()
            if not fragment:
                logger.error(f"Fragment {fid} not found")
                return

            # Mark as processing
            fragment.status = FragmentStatus.PROCESSING
            await session.commit()

        # Build the graph state from the fragment
        state = {
            "fragment_id": fid,
            "owner_id": oid,
            "fragment_type": fragment.fragment_type,
            "text_content": fragment.text_content,
            "media_urls": fragment.media_urls or [],
        }

        await run_ingest(state)

    try:
        _run_async(_ingest())
        logger.info(f"Ingestion complete for fragment {fragment_id}")
    except Exception as exc:
        logger.error(f"Ingestion failed for fragment {fragment_id}: {exc}")
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="app.workers.tasks.run_drift_for_user")
def run_drift_for_user(user_id: str):
    """Run drift simulation + collision detection for a single user."""
    from app.core.database import async_session_factory
    from app.drift.detector import CollisionDetector
    from app.drift.scheduler import DriftScheduler
    from app.drift.synthesizer import Synthesizer

    async def _drift():
        uid = uuid.UUID(user_id)

        async with async_session_factory() as session:
            # 1. Run drift physics
            scheduler = DriftScheduler(session)
            moved = await scheduler.run_drift_tick(uid)

            # 2. Detect new collisions
            detector = CollisionDetector(session)
            new_collisions = await detector.detect_collisions_for_user(uid)

            # 3. Synthesize proposed collisions
            if new_collisions:
                synthesizer = Synthesizer(session)
                count = await synthesizer.synthesize_pending(uid)
                logger.info(f"Synthesized {count} collisions for user {uid}")

            await session.commit()

        logger.info(f"Drift tick done for user {uid}: {moved} moved, {len(new_collisions)} collisions")

    _run_async(_drift())


@celery_app.task(name="app.workers.tasks.run_drift_for_drift")
def run_drift_for_drift(drift_id: str):
    """Run physics + collision detection scoped to a single drift."""
    import json
    from sqlalchemy import select

    from app.api.endpoint.v1.ws import push_drift_event
    from app.core.database import async_session_factory
    from app.core.redis import get_redis
    from app.drift.detector import CollisionDetector
    from app.drift.scheduler import DriftScheduler
    from app.drift.synthesizer import Synthesizer
    from app.models.drift import Drift, DriftMember

    async def _run():
        did = uuid.UUID(drift_id)

        async with async_session_factory() as session:
            drift = (
                await session.execute(select(Drift).where(Drift.id == did))
            ).scalar_one_or_none()
            if not drift:
                logger.warning(f"run_drift_for_drift: drift {did} not found")
                return

            scheduler = DriftScheduler(session)
            moved = await scheduler.run_drift_tick_for_drift(did)

            # Gather member positions to broadcast
            members_result = await session.execute(
                select(DriftMember).where(DriftMember.drift_id == did)
            )
            members = list(members_result.scalars().all())

            # Collision detection & synthesis stay per-user for now;
            # collisions will attach to the drift that triggered them in a
            # follow-up change, once CollisionDetector is drift-aware.
            detector = CollisionDetector(session)
            new_collisions = await detector.detect_collisions_for_user(drift.owner_id)
            if new_collisions:
                synthesizer = Synthesizer(session)
                await synthesizer.synthesize_pending(drift.owner_id)

            await session.commit()

        # Broadcast drift positions on the drift channel
        redis = get_redis()
        channel = f"drift:{did}:events"
        for m in members:
            payload = json.dumps({
                "event": "drift",
                "drift_id": str(did),
                "fragment_id": str(m.fragment_id),
                "canvas_x": m.canvas_x,
                "canvas_y": m.canvas_y,
                "drift_vx": m.drift_vx,
                "drift_vy": m.drift_vy,
            })
            await redis.publish(channel, payload)

        for col in new_collisions:
            await push_drift_event(str(did), {
                "event": "collision",
                "collision_id": str(col.id),
                "fragment_a_id": str(col.fragment_a_id),
                "fragment_b_id": str(col.fragment_b_id),
                "similarity_score": col.similarity_score,
            })

        logger.info(f"Drift tick done for drift {did}: {moved} moved")

    _run_async(_run())


@celery_app.task(name="app.workers.tasks.run_drift_all_users")
def run_drift_all_users():
    """
    Periodic task: fan out ticks to every SCHEDULED drift. LIVE drifts are
    ticked on demand via POST /drifts/{id}/tick and don't need the beat.
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.drift import Drift, DriftMode

    async def _all():
        async with async_session_factory() as session:
            result = await session.execute(
                select(Drift.id).where(Drift.mode == DriftMode.SCHEDULED)
            )
            drift_ids = [row[0] for row in result.all()]

        logger.info(f"Running drift for {len(drift_ids)} scheduled drifts")
        for did in drift_ids:
            run_drift_for_drift.delay(str(did))

    _run_async(_all())


@celery_app.task(name="app.workers.tasks.update_user_memory_from_synthesis")
def update_user_memory_from_synthesis(synthesis_id: str, user_id: str, resolution: str):
    """
    Close the feedback loop: when a user ACCEPTS a synthesis, distill the
    outcome into a SYNTHESIS_LEARNING memory row (+ vector). On DISMISS,
    decay weights on nearby memories — a gentle negative signal.
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.synthesis import Synthesis, SynthesisStatus
    from app.models.user_memory import MemoryKind, UserMemory
    from app.services.memory_service import MemoryService

    async def _run():
        sid = uuid.UUID(synthesis_id)
        uid = uuid.UUID(user_id)

        async with async_session_factory() as session:
            result = await session.execute(
                select(Synthesis).where(Synthesis.id == sid, Synthesis.user_id == uid)
            )
            synthesis = result.scalar_one_or_none()
            if not synthesis:
                return

            memory_service = MemoryService(session)

            if resolution == SynthesisStatus.ACCEPTED.value:
                summary_parts = [
                    synthesis.title or "",
                    synthesis.synthesis_text or "",
                    synthesis.reasoning or "",
                ]
                content = " — ".join(p for p in summary_parts if p)
                if content:
                    await memory_service.create_memory(
                        user_id=uid,
                        kind=MemoryKind.SYNTHESIS_LEARNING,
                        content=content,
                        metadata={
                            "source_synthesis_id": str(sid),
                            "temperature": synthesis.temperature,
                        },
                        weight=1.0,
                    )

            elif resolution == SynthesisStatus.DISMISSED.value:
                # Gentle negative signal: decay recent SYNTHESIS_LEARNING memories
                # that are close to the dismissed synthesis. Cheap and approximate.
                recent = await session.execute(
                    select(UserMemory).where(
                        UserMemory.user_id == uid,
                        UserMemory.kind == MemoryKind.SYNTHESIS_LEARNING,
                    ).order_by(UserMemory.updated_at.desc()).limit(10)
                )
                for m in recent.scalars().all():
                    m.weight = max(0.1, m.weight * 0.95)
                await session.flush()

            await session.commit()

    _run_async(_run())


@celery_app.task(
    name="app.workers.tasks.generate_lens_task",
    bind=True,
    max_retries=2,
    default_retry_delay=20,
)
def generate_lens_task(self, lens_id: str, fragment_id: str, owner_id: str, kind: str):
    """
    Generate a single lens artifact asynchronously. Image generation can take
    10–20s — keep it off the request path.
    """
    from app.api.endpoint.v1.ws import push_event_to_user
    from app.core.database import async_session_factory
    from app.models.lens import LensKind
    from app.services.lens_service import LensService

    async def _run():
        fid = uuid.UUID(fragment_id)
        oid = uuid.UUID(owner_id)
        lid = uuid.UUID(lens_id)

        async with async_session_factory() as session:
            service = LensService(session)
            try:
                parsed_kind = LensKind(kind)
            except ValueError:
                await service.mark_failed(lid, f"unknown kind: {kind}")
                await session.commit()
                return

            if parsed_kind == LensKind.MOOD_IMAGE:
                lens = await service.generate_mood_image(lid, fid, oid)
            elif parsed_kind == LensKind.ECHO:
                lens = await service.generate_echo(lid, fid)
            elif parsed_kind == LensKind.COUNTER:
                lens = await service.generate_counter(lid, fid)
            elif parsed_kind == LensKind.SOCRATIC:
                lens = await service.generate_socratic(lid, fid)
            elif parsed_kind == LensKind.ROADMAP:
                lens = await service.generate_roadmap(lid, fid)
            elif parsed_kind == LensKind.MINDMAP:
                lens = await service.generate_mindmap(lid, fid)
            elif parsed_kind == LensKind.LINEAGE:
                lens = await service.generate_lineage(lid, fid, oid)
            else:
                await service.mark_failed(lid, f"no generator for {kind}")
                await session.commit()
                return

            await session.commit()

        # Tell the user's open tabs the lens changed state so they re-fetch.
        try:
            await push_event_to_user(
                owner_id,
                {
                    "event": "lens_update",
                    "fragment_id": fragment_id,
                    "lens_id": lens_id,
                    "kind": kind,
                    "status": lens.status.value if lens else "failed",
                },
            )
        except Exception as e:
            logger.debug(f"lens push event failed: {e}")

    try:
        _run_async(_run())
    except Exception as exc:
        logger.error(f"generate_lens_task error: {exc}")
        raise self.retry(exc=exc)


@celery_app.task(name="app.workers.tasks.health_ping")
def health_ping():
    logger.debug("Celery worker alive")
    return "pong"
