import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.redis import get_redis
from app.security import decode_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])

# In-memory connection registry (per-process; use Redis pubsub for multi-process)
active_connections: dict[str, list[WebSocket]] = {}


async def authenticate_ws(websocket: WebSocket) -> str | None:
    """Extract and verify JWT from WebSocket query params."""
    token = websocket.query_params.get("token")
    if not token:
        return None
    try:
        payload = await decode_token(token)
        return payload.sub
    except Exception:
        return None


@router.websocket("/ws/canvas")
async def canvas_websocket(websocket: WebSocket):
    """
    Real-time WebSocket for canvas events.

    Events pushed to client:
    - drift: fragment position updates from drift engine
    - collision: new collision detected
    - fragment_ready: ingestion pipeline completed

    Client connect: ws://host/api/v1/ws/canvas?token=<jwt>
    """
    user_id = await authenticate_ws(websocket)
    if not user_id:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()

    # Register connection
    if user_id not in active_connections:
        active_connections[user_id] = []
    active_connections[user_id].append(websocket)

    logger.info(f"WebSocket connected: user={user_id}")

    # Subscribe to Redis pubsub for this user's events
    redis = get_redis()
    pubsub = redis.pubsub()
    channel = f"user:{user_id}:events"
    await pubsub.subscribe(channel)

    try:
        # Two concurrent tasks: listen to Redis + listen to client
        async def redis_listener():
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    await websocket.send_text(data)

        async def client_listener():
            while True:
                # Client can send position updates or pings
                data = await websocket.receive_text()
                msg = json.loads(data)

                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                elif msg.get("type") == "position_update":
                    # Broadcast to other connections of same user
                    for conn in active_connections.get(user_id, []):
                        if conn != websocket:
                            await conn.send_text(data)

        await asyncio.gather(redis_listener(), client_listener())

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: user={user_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # Cleanup
        await pubsub.unsubscribe(channel)
        if user_id in active_connections:
            active_connections[user_id] = [
                c for c in active_connections[user_id] if c != websocket
            ]
            if not active_connections[user_id]:
                del active_connections[user_id]


async def push_event_to_user(user_id: str, event: dict) -> None:
    """Push an event to a user via Redis pubsub (works across processes)."""
    redis = get_redis()
    channel = f"user:{user_id}:events"
    await redis.publish(channel, json.dumps(event))
