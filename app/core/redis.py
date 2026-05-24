from __future__ import annotations

from urllib.parse import urlparse

import structlog
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.common.enums import LogEvent
from app.core.config import settings

logger = structlog.get_logger()


def get_redis_settings() -> RedisSettings:
    parsed = urlparse(settings.REDIS_URL)
    path = (parsed.path or "").lstrip("/")
    try:
        database = int(path) if path else 0
    except ValueError:
        database = 0
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=database,
        password=parsed.password,
    )


# Single pool for the app: used by get_redis() (health, cache) and by queue.add_queue().
redis_client: ArqRedis | None = None


async def init_redis() -> None:
    global redis_client
    redis_client = await create_pool(get_redis_settings())
    await redis_client.ping()
    logger.info(LogEvent.REDIS_CONNECTED)


async def close_redis() -> None:
    global redis_client
    if redis_client is not None:
        await redis_client.close()
        redis_client = None


def get_redis() -> ArqRedis:
    if redis_client is None:
        raise RuntimeError("Redis not initialized. Call init_redis() first.")
    return redis_client
