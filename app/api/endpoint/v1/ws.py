import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.redis import get_redis
from app.models.drift import Drift
from app.security import decode_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])

# Per-process registries. Cross-process broadcast is via Redis pubsub.
active_user_connections: dict[str, list[WebSocket]] = {}
active_drift_connections: dict[str, list[WebSocket]] = {}


async def authenticate_ws(websocket: WebSocket) -> str | None:
    token = websocket.query_params.get("token")
    if not token:
        return None
    try:
        payload = await decode_token(token)
        sub = payload.get("sub")
        return str(sub) if sub else None
    except Exception:
        return None


async def _user_owns_drift(user_id: str, drift_id: str) -> bool:
    try:
        uid = uuid.UUID(user_id)
        did = uuid.UUID(drift_id)
    except ValueError:
        return False
    async with async_session_factory() as session:
        result = await session.execute(
            select(Drift.id).where(Drift.id == did, Drift.owner_id == uid)
        )
        return result.scalar_one_or_none() is not None


@router.websocket("/ws/canvas")
async def canvas_websocket(websocket: WebSocket):
    """
    Legacy user-scoped canvas socket. Pushes events on channel `user:{id}:events`.
    Kept for the existing frontend; new UI should use /ws/drifts/{drift_id}.
    """
    user_id = await authenticate_ws(websocket)
    if not user_id:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()
    active_user_connections.setdefault(user_id, []).append(websocket)
    logger.info(f"WS(user) connected: {user_id}")

    redis = get_redis()
    pubsub = redis.pubsub()
    channel = f"user:{user_id}:events"
    await pubsub.subscribe(channel)

    try:
        async def redis_listener():
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    await websocket.send_text(data)

        async def client_listener():
            while True:
                data = await websocket.receive_text()
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                elif msg.get("type") == "position_update":
                    for conn in active_user_connections.get(user_id, []):
                        if conn != websocket:
                            await conn.send_text(data)

        await asyncio.gather(redis_listener(), client_listener())
    except WebSocketDisconnect:
        logger.info(f"WS(user) disconnected: {user_id}")
    except Exception as e:
        logger.error(f"WS(user) error: {e}")
    finally:
        await pubsub.unsubscribe(channel)
        if user_id in active_user_connections:
            active_user_connections[user_id] = [
                c for c in active_user_connections[user_id] if c != websocket
            ]
            if not active_user_connections[user_id]:
                del active_user_connections[user_id]


@router.websocket("/ws/drifts/{drift_id}")
async def drift_websocket(websocket: WebSocket, drift_id: str):
    """
    Real-time drift socket. Each open drift tab subscribes to its own channel
    so a user can have multiple drifts open simultaneously without crosstalk.

    Events pushed:
    - drift: member position update
    - collision: collision detected within this drift
    - synthesis: physics-based synthesis proposal ready

    Client: ws://host/api/v1/ws/drifts/{drift_id}?token=<jwt>
    """
    user_id = await authenticate_ws(websocket)
    if not user_id:
        await websocket.close(code=4001, reason="Unauthorized")
        return
    if not await _user_owns_drift(user_id, drift_id):
        await websocket.close(code=4003, reason="Forbidden")
        return

    await websocket.accept()
    active_drift_connections.setdefault(drift_id, []).append(websocket)
    logger.info(f"WS(drift) connected: user={user_id} drift={drift_id}")

    redis = get_redis()
    pubsub = redis.pubsub()
    channel = f"drift:{drift_id}:events"
    await pubsub.subscribe(channel)

    try:
        async def redis_listener():
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    await websocket.send_text(data)

        async def client_listener():
            while True:
                data = await websocket.receive_text()
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                elif msg.get("type") == "position_update":
                    # Fan out to other tabs of the same drift (local process only).
                    for conn in active_drift_connections.get(drift_id, []):
                        if conn != websocket:
                            await conn.send_text(data)

        await asyncio.gather(redis_listener(), client_listener())
    except WebSocketDisconnect:
        logger.info(f"WS(drift) disconnected: user={user_id} drift={drift_id}")
    except Exception as e:
        logger.error(f"WS(drift) error: {e}")
    finally:
        await pubsub.unsubscribe(channel)
        if drift_id in active_drift_connections:
            active_drift_connections[drift_id] = [
                c for c in active_drift_connections[drift_id] if c != websocket
            ]
            if not active_drift_connections[drift_id]:
                del active_drift_connections[drift_id]


async def push_event_to_user(user_id: str, event: dict) -> None:
    """Push an event to all of a user's legacy sockets via Redis pubsub."""
    redis = get_redis()
    await redis.publish(f"user:{user_id}:events", json.dumps(event))


async def push_drift_event(drift_id: str, event: dict) -> None:
    """Push an event to every socket subscribed to a drift via Redis pubsub."""
    redis = get_redis()
    await redis.publish(f"drift:{drift_id}:events", json.dumps(event))
