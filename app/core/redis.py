from redis.asyncio import ConnectionPool, Redis

from app.config import get_settings

settings = get_settings()

pool = ConnectionPool.from_url(settings.redis_url, max_connections=20, decode_responses=True)


def get_redis() -> Redis:
    return Redis(connection_pool=pool)


async def close_redis() -> None:
    await pool.aclose()
