"""Permission repository — data-access layer for UserPermission model.

Handles CRUD for per-user permission overrides and Redis cache
for resolved permission sets. No business logic — that lives in
PermissionService.
"""

import json
from datetime import UTC, datetime
from uuid import uuid4

import structlog
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.permission import PermissionLevel, Resource
from app.modules.permission.models import UserPermission

logger = structlog.get_logger()

_CACHE_PREFIX = "perms"
_CACHE_TTL_SECONDS = 300


class PermissionRepository:
    """Repository for user permission overrides."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Read ─────────────────────────────────────

    async def get_overrides_for_user(self, user_id: str) -> dict[Resource, PermissionLevel]:
        """Load all permission overrides for a user from DB.

        Returns a dict of Resource -> PermissionLevel for rows that exist.
        Resources without an override are NOT included (caller merges with defaults).
        """
        stmt = select(UserPermission).where(UserPermission.user_id == user_id)
        result = await self.session.execute(stmt)
        rows = result.scalars().all()
        overrides: dict[Resource, PermissionLevel] = {}
        for row in rows:
            try:
                resource = Resource(row.resource)
                level = PermissionLevel(row.level)
                overrides[resource] = level
            except ValueError:
                logger.warning(
                    "permission_unknown_resource",
                    user_id=user_id,
                    resource=row.resource,
                    level=row.level,
                )
        return overrides

    async def get_all_for_user(self, user_id: str) -> list[UserPermission]:
        """Load all raw UserPermission rows for a user (for admin display)."""
        stmt = select(UserPermission).where(UserPermission.user_id == user_id).order_by(UserPermission.resource)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ── Write ────────────────────────────────────

    async def upsert(
        self,
        user_id: str,
        resource: Resource,
        level: PermissionLevel,
        granted_by: str | None = None,
    ) -> UserPermission:
        """Insert or update a single permission override (upsert).

        Uses PostgreSQL ON CONFLICT for atomicity.
        """
        now = datetime.now(UTC)
        stmt = (
            pg_insert(UserPermission)
            .values(
                id=str(uuid4()),
                user_id=user_id,
                resource=resource.value,
                level=level.value,
                granted_by=granted_by,
                created_at=now,
                updated_at=now,
                version=1,
            )
            .on_conflict_do_update(
                constraint="uq_user_permissions_user_resource",
                set_={
                    "level": level.value,
                    "granted_by": granted_by,
                    "updated_at": now,
                    "version": UserPermission.version + 1,
                },
            )
            .returning(UserPermission)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one()

    async def bulk_set(
        self,
        user_id: str,
        permissions: dict[Resource, PermissionLevel],
        granted_by: str | None = None,
    ) -> None:
        """Replace ALL overrides for a user in one transaction.

        Deletes existing overrides and inserts the new set. Caller (service)
        is responsible for filtering — this method stores exactly what it receives.
        """
        await self.delete_all_for_user(user_id)

        if not permissions:
            return

        now = datetime.now(UTC)
        for resource, level in permissions.items():
            perm = UserPermission(
                user_id=user_id,
                resource=resource.value,
                level=level.value,
                granted_by=granted_by,
                created_at=now,
                updated_at=now,
            )
            self.session.add(perm)
        await self.session.flush()

    async def delete_override(self, user_id: str, resource: Resource) -> bool:
        """Remove a single override (revert to role default). Returns True if a row was deleted."""
        stmt = delete(UserPermission).where(
            UserPermission.user_id == user_id,
            UserPermission.resource == resource.value,
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0  # type: ignore[union-attr]

    async def delete_all_for_user(self, user_id: str) -> int:
        """Remove all overrides for a user (reset to pure role defaults)."""
        stmt = delete(UserPermission).where(UserPermission.user_id == user_id)
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount  # type: ignore[return-value]

    # ── Cache ────────────────────────────────────

    @staticmethod
    async def get_cached_permissions(
        user_id: str,
    ) -> dict[Resource, PermissionLevel] | None:
        """Load resolved permissions from Redis. Returns None on cache miss."""
        try:
            from app.core.redis import get_redis

            redis = get_redis()
            raw = await redis.get(_cache_key_for(user_id))
            if raw is None:
                return None
            data = json.loads(raw)
            return {Resource(k): PermissionLevel(v) for k, v in data.items()}
        except Exception:
            logger.debug("permission_cache_read_failed", user_id=user_id)
            return None

    @staticmethod
    async def set_cached_permissions(
        user_id: str,
        permissions: dict[Resource, PermissionLevel],
    ) -> None:
        """Store resolved permissions in Redis with TTL."""
        try:
            from app.core.redis import get_redis

            redis = get_redis()
            data = {r.value: level.value for r, level in permissions.items()}
            await redis.set(
                _cache_key_for(user_id),
                json.dumps(data),
                ex=_CACHE_TTL_SECONDS,
            )
        except Exception:
            logger.debug("permission_cache_write_failed", user_id=user_id)

    @staticmethod
    async def invalidate_cache(user_id: str) -> None:
        """Remove cached permissions for a user (call after any permission change)."""
        try:
            from app.core.redis import get_redis

            redis = get_redis()
            await redis.delete(_cache_key_for(user_id))
        except Exception:
            logger.debug("permission_cache_invalidate_failed", user_id=user_id)


def _cache_key_for(user_id: str) -> str:
    return f"{_CACHE_PREFIX}:{user_id}"
