from __future__ import annotations

import json

import structlog
from arq import ArqRedis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from app.common.enums import LogEvent
from app.common.exceptions import IdempotencyConflictError
from app.core.config import settings

logger = structlog.get_logger()

_HEADER = "x-idempotency-key"
_PROCESSING_SENTINEL = "__processing__"
_MUTATING_METHODS = {"POST", "PUT", "PATCH"}
_KEY_PREFIX = "idempotency"

# Cache key is idempotency:{user_id}:{key}. Request body/path are NOT hashed, so the same
# key always replays the first response. For bulk uploads (documents/images), the client
# must send one unique key per logical operation (e.g. one key per "upload this batch");
# retries use the same key and get the cached result.


class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        if request.method not in _MUTATING_METHODS:
            return await call_next(request)

        idem_key = request.headers.get(_HEADER)
        if not idem_key:
            return await call_next(request)

        user_id = self._get_user_id(request)
        redis_key = f"{_KEY_PREFIX}:{user_id}:{idem_key}"
        ttl = settings.IDEMPOTENCY_KEY_TTL

        redis = self._get_redis()
        if redis is None:
            return await call_next(request)

        try:
            claimed = await redis.set(redis_key, _PROCESSING_SENTINEL, ex=ttl, nx=True)
        except Exception:
            logger.warning(LogEvent.IDEMPOTENCY_REDIS_ERROR, action="claim", key=redis_key)
            return await call_next(request)

        if claimed:
            return await self._execute_and_cache(request, call_next, redis, redis_key, ttl)

        return await self._handle_duplicate(redis, redis_key)

    async def _execute_and_cache(
        self,
        request: Request,
        call_next,  # noqa: ANN001
        redis: ArqRedis,  # noqa: ANN001
        redis_key: str,
        ttl: int,
    ) -> Response:
        try:
            response: StreamingResponse = await call_next(request)
        except Exception:
            await self._safe_delete(redis, redis_key)
            raise

        if response.status_code >= 500:
            await self._safe_delete(redis, redis_key)
            return response

        body = b""
        async for chunk in response.body_iterator:
            if isinstance(chunk, bytes):
                body += chunk
            elif isinstance(chunk, str):
                body += chunk.encode()
            else:
                body += bytes(chunk)

        cached = json.dumps(
            {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": body.decode(),
            }
        )

        try:
            await redis.set(redis_key, cached, ex=ttl)
        except Exception:
            logger.warning(LogEvent.IDEMPOTENCY_REDIS_ERROR, action="cache", key=redis_key)

        return Response(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )

    async def _handle_duplicate(self, redis: ArqRedis, redis_key: str) -> Response:  # noqa: ANN001
        try:
            cached_raw = await redis.get(redis_key)
        except Exception:
            logger.warning(LogEvent.IDEMPOTENCY_REDIS_ERROR, action="fetch", key=redis_key)
            raise IdempotencyConflictError("Duplicate request") from None

        if cached_raw is None or cached_raw == _PROCESSING_SENTINEL:
            raise IdempotencyConflictError("A request with this idempotency key is already being processed")

        try:
            cached = json.loads(cached_raw)
        except (json.JSONDecodeError, TypeError):
            raise IdempotencyConflictError("Duplicate request") from None

        headers = cached.get("headers", {})
        headers["X-Idempotent-Replayed"] = "true"

        return Response(
            content=cached["body"],
            status_code=cached["status_code"],
            headers=headers,
            media_type="application/json",
        )

    @staticmethod
    def _get_user_id(request: Request) -> str:
        auth_user = getattr(request.state, "auth_user", None)
        if auth_user and hasattr(auth_user, "id"):
            return str(auth_user.id)
        return request.client.host if request.client else "anonymous"

    @staticmethod
    def _get_redis() -> ArqRedis | None:  # noqa: ANN205
        try:
            from app.core.redis import redis_client

            return redis_client
        except Exception:
            return None

    @staticmethod
    async def _safe_delete(redis: ArqRedis, key: str) -> None:  # noqa: ANN001
        try:
            await redis.delete(key)
        except Exception:
            logger.warning(LogEvent.IDEMPOTENCY_REDIS_ERROR, action="delete", key=key)
