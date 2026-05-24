"""Unit tests for Redis token blacklist.

When Redis is available (e.g. local or CI with Redis), these tests validate
blacklist_token and is_token_blacklisted against a real Redis instance.
If Redis is not reachable, tests are skipped so the suite still passes.
"""

import os

import pytest
import pytest_asyncio

from app.common.utils import (
    _BLACKLIST_PREFIX,
    blacklist_token,
    is_token_blacklisted,
)
from app.core.redis import close_redis, get_redis, init_redis


@pytest_asyncio.fixture
async def redis_available():
    """Init Redis if possible; skip tests in this module if Redis is unreachable."""
    try:
        await init_redis()
        yield
    except Exception:
        pytest.skip("Redis not available — blacklist unit tests skipped")
    finally:
        await close_redis()


@pytest.mark.asyncio
async def test_blacklist_and_check_when_redis_available(redis_available) -> None:
    """With Redis up, blacklisting a JTI makes is_token_blacklisted return True."""
    jti = "test-jti-" + os.urandom(8).hex()
    ttl = 60
    await blacklist_token(jti, ttl)
    try:
        assert await is_token_blacklisted(jti) is True
        other_jti = "other-jti-" + os.urandom(8).hex()
        assert await is_token_blacklisted(other_jti) is False
    finally:
        r = get_redis()
        await r.delete(f"{_BLACKLIST_PREFIX}{jti}")
