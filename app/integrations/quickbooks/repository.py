"""Data access helpers for QuickBooks integration tables."""

from __future__ import annotations

from datetime import UTC, datetime
from collections.abc import Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.repository import BaseRepository
from app.integrations.quickbooks.constants import QB_GLOBAL_NAMESPACE_ID
from app.integrations.quickbooks.models import (
    QbConnection,
    QbLink,
    QbReferenceMapping,
    QbSyncLog,
    QbSyncSettings,
)


class QbConnectionRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, QbConnection)

    @staticmethod
    def _scope(_organization_id: str | None = None) -> str:
        return QB_GLOBAL_NAMESPACE_ID

    async def get_active_by_org(self, organization_id: str) -> QbConnection | None:
        return await self.find_one(organization_id=self._scope(organization_id), is_active=True)

    async def upsert_for_org(self, organization_id: str, data: dict) -> QbConnection:
        scope = self._scope(organization_id)
        existing = await self.find_one(organization_id=scope)
        if existing is None:
            return await self.create({"organization_id": scope, **data})
        return await self.update_by_id(existing.id, data, expected_version=existing.version)

    async def list_active(self, *, limit: int = 500) -> list[QbConnection]:
        stmt = (
            select(QbConnection)
            .where(QbConnection.is_active.is_(True))
            .order_by(QbConnection.updated_at.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class QbLinkRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, QbLink)

    @staticmethod
    def _scope(_organization_id: str | None = None) -> str:
        return QB_GLOBAL_NAMESPACE_ID

    async def get_by_local(self, organization_id: str, entity_type: str, local_entity_id: str) -> QbLink | None:
        return await self.find_one(
            organization_id=self._scope(organization_id),
            entity_type=entity_type,
            local_entity_id=local_entity_id,
        )

    async def upsert_mapping(
        self,
        *,
        organization_id: str,
        entity_type: str,
        local_entity_id: str,
        qb_entity_id: str,
        sync_token: str | None = None,
        sync_status: str = "SYNCED",
        last_error: str | None = None,
    ) -> QbLink:
        link = await self.get_by_local(organization_id, entity_type, local_entity_id)
        scope = self._scope(organization_id)
        payload = {
            "qb_entity_id": qb_entity_id,
            "sync_token": sync_token,
            "sync_status": sync_status,
            "last_synced_at": datetime.now(UTC),
            "last_error": last_error,
        }
        if link is None:
            return await self.create(
                {
                    "organization_id": scope,
                    "entity_type": entity_type,
                    "local_entity_id": local_entity_id,
                    **payload,
                }
            )
        return await self.update_by_id(link.id, payload, expected_version=link.version)

    async def mark_failed(self, *, organization_id: str, entity_type: str, local_entity_id: str, error_message: str) -> None:
        link = await self.get_by_local(organization_id, entity_type, local_entity_id)
        scope = self._scope(organization_id)
        if link is None:
            await self.create(
                {
                    "organization_id": scope,
                    "entity_type": entity_type,
                    "local_entity_id": local_entity_id,
                    "qb_entity_id": f"pending:{local_entity_id}",
                    "sync_status": "FAILED",
                    "last_error": error_message[:500],
                }
            )
            return
        await self.update_by_id(
            link.id,
            {"sync_status": "FAILED", "last_error": error_message[:500]},
            expected_version=link.version,
        )


class QbSyncLogRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, QbSyncLog)

    @staticmethod
    def _scope(_organization_id: str | None = None) -> str:
        return QB_GLOBAL_NAMESPACE_ID

    async def log(
        self,
        *,
        organization_id: str,
        entity_type: str,
        local_entity_id: str | None,
        event_type: str | None = None,
        action: str,
        status: str,
        job_id: str | None = None,
        attempt_no: int = 1,
        error_code: str | None = None,
        error_message: str | None = None,
        related_qb_id: str | None = None,
        payload: dict | None = None,
    ) -> QbSyncLog:
        scope = self._scope(organization_id)
        return await self.create(
            {
                "organization_id": scope,
                "entity_type": entity_type,
                "local_entity_id": local_entity_id,
                "event_type": event_type,
                "action": action,
                "status": status,
                "job_id": job_id,
                "attempt_no": attempt_no,
                "error_code": error_code,
                "error_message": error_message,
                "related_qb_id": related_qb_id,
                "payload": payload,
            }
        )

    async def list_recent_failures(self, organization_id: str, limit: int = 100) -> list[QbSyncLog]:
        scope = self._scope(organization_id)
        stmt = (
            select(QbSyncLog)
            .where(QbSyncLog.organization_id == scope, QbSyncLog.status == "FAILED")
            .order_by(QbSyncLog.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_logs(
        self,
        *,
        organization_id: str,
        statuses: Sequence[str] | None = None,
        status: str | None = None,
        entity_type: str | None = None,
        event_type: str | None = None,
        action: str | None = None,
        error_code: str | None = None,
        job_id: str | None = None,
        local_entity_id: str | None = None,
        search: str | None = None,
        created_from: datetime | None = None,
        created_to_exclusive: datetime | None = None,
        limit: int = 100,
    ) -> list[QbSyncLog]:
        scope = self._scope(organization_id)
        stmt = select(QbSyncLog).where(QbSyncLog.organization_id == scope)
        if statuses:
            stmt = stmt.where(QbSyncLog.status.in_(statuses))
        if status is not None:
            stmt = stmt.where(QbSyncLog.status == status)
        if entity_type is not None:
            stmt = stmt.where(QbSyncLog.entity_type == entity_type)
        if event_type is not None:
            stmt = stmt.where(QbSyncLog.event_type == event_type)
        if action is not None:
            stmt = stmt.where(QbSyncLog.action == action)
        if error_code is not None:
            stmt = stmt.where(QbSyncLog.error_code == error_code)
        if job_id is not None:
            stmt = stmt.where(QbSyncLog.job_id == job_id)
        if local_entity_id is not None:
            stmt = stmt.where(QbSyncLog.local_entity_id == local_entity_id)
        if created_from is not None:
            stmt = stmt.where(QbSyncLog.created_at >= created_from)
        if created_to_exclusive is not None:
            stmt = stmt.where(QbSyncLog.created_at < created_to_exclusive)
        if search is not None and search.strip():
            term = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    QbSyncLog.job_id.ilike(term),
                    QbSyncLog.entity_type.ilike(term),
                    QbSyncLog.related_qb_id.ilike(term),
                    QbSyncLog.error_code.ilike(term),
                )
            )
        stmt = stmt.order_by(QbSyncLog.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_log(self, *, organization_id: str, log_id: str) -> QbSyncLog | None:
        scope = self._scope(organization_id)
        stmt = select(QbSyncLog).where(
            QbSyncLog.organization_id == scope,
            QbSyncLog.id == log_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


class QbReferenceMappingRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, QbReferenceMapping)

    @staticmethod
    def _scope(_organization_id: str | None = None) -> str:
        return QB_GLOBAL_NAMESPACE_ID

    async def list_for_org(
        self,
        organization_id: str,
        *,
        mapping_type: str | None = None,
        is_active: bool | None = None,
        limit: int = 200,
    ) -> list[QbReferenceMapping]:
        scope = self._scope(organization_id)
        stmt = select(QbReferenceMapping).where(QbReferenceMapping.organization_id == scope)
        if mapping_type is not None:
            stmt = stmt.where(QbReferenceMapping.mapping_type == mapping_type)
        if is_active is not None:
            stmt = stmt.where(QbReferenceMapping.is_active == is_active)
        stmt = stmt.order_by(QbReferenceMapping.mapping_type.asc(), QbReferenceMapping.local_key.asc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_mapping(self, organization_id: str, mapping_type: str, local_key: str) -> QbReferenceMapping | None:
        return await self.find_one(
            organization_id=self._scope(organization_id),
            mapping_type=mapping_type,
            local_key=local_key,
        )

    async def upsert_mapping(
        self,
        *,
        organization_id: str,
        mapping_type: str,
        local_key: str,
        qb_ref_id: str,
        qb_ref_name: str | None = None,
        is_active: bool = True,
        metadata: dict | None = None,
    ) -> QbReferenceMapping:
        scope = self._scope(organization_id)
        existing = await self.get_mapping(organization_id, mapping_type, local_key)
        payload = {
            "qb_ref_id": qb_ref_id,
            "qb_ref_name": qb_ref_name,
            "is_active": is_active,
            "metadata_json": metadata,
        }
        if existing is None:
            return await self.create(
                {
                    "organization_id": scope,
                    "mapping_type": mapping_type,
                    "local_key": local_key,
                    **payload,
                }
            )
        return await self.update_by_id(existing.id, payload, expected_version=existing.version)

    async def deactivate_mapping(self, *, organization_id: str, mapping_type: str, local_key: str) -> QbReferenceMapping | None:
        existing = await self.get_mapping(organization_id, mapping_type, local_key)
        if existing is None:
            return None
        return await self.update_by_id(existing.id, {"is_active": False}, expected_version=existing.version)


class QbSyncSettingsRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, QbSyncSettings)

    @staticmethod
    def _scope(_organization_id: str | None = None) -> str:
        return QB_GLOBAL_NAMESPACE_ID

    async def get_for_org(self, organization_id: str) -> QbSyncSettings | None:
        return await self.find_one(organization_id=self._scope(organization_id))

    async def get_or_create_default(self, organization_id: str) -> QbSyncSettings:
        existing = await self.get_for_org(organization_id)
        if existing is not None:
            return existing
        return await self.create({"organization_id": self._scope(organization_id)})

    async def upsert_for_org(self, organization_id: str, data: dict) -> QbSyncSettings:
        scope = self._scope(organization_id)
        existing = await self.get_for_org(organization_id)
        if existing is None:
            return await self.create({"organization_id": scope, **data})
        return await self.update_by_id(existing.id, data, expected_version=existing.version)
