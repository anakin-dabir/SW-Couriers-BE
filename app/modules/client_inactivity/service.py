"""Client inactivity policy service."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import structlog
from fastapi.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import UserInactiveReason
from app.common.enums.logger import LogEvent
from app.common.exceptions import NotFoundError, ValidationError
from app.common.service import BaseService
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.client_inactivity.constants import (
    DEFAULT_INACTIVE_AFTER_DAYS,
    MAX_INACTIVE_AFTER_DAYS,
    MIN_INACTIVE_AFTER_DAYS,
)
from app.modules.client_inactivity.repository import ClientInactivityConfigRepository, ClientInactivityUserRepository
from app.modules.client_inactivity.v1.schemas import ClientInactivityConfigPatch, ClientInactivityConfigResponse

logger = structlog.get_logger()


class ClientInactivityService(BaseService):
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._config_repo = ClientInactivityConfigRepository(session)
        self._user_repo = ClientInactivityUserRepository(session)
        self._audit = AuditService(session)

    @staticmethod
    def is_inactivity_reactivatable(*, status: str, inactive_reason: str | None) -> bool:
        return status == "INACTIVE" and inactive_reason == UserInactiveReason.INACTIVITY.value

    def _validate_days(self, inactive_after_days: int) -> None:
        if inactive_after_days < MIN_INACTIVE_AFTER_DAYS or inactive_after_days > MAX_INACTIVE_AFTER_DAYS:
            raise ValidationError(
                f"inactive_after_days must be between {MIN_INACTIVE_AFTER_DAYS} and {MAX_INACTIVE_AFTER_DAYS}"
            )

    async def get_config(self) -> ClientInactivityConfigResponse:
        config = await self._config_repo.get_singleton()
        if config is None:
            config = await self._config_repo.create(
                {
                    "enabled": True,
                    "inactive_after_days": DEFAULT_INACTIVE_AFTER_DAYS,
                }
            )
            logger.info("client_inactivity_config.seeded", config_id=config.id)
        return ClientInactivityConfigResponse.model_validate(config)

    async def patch_config(
        self,
        data: ClientInactivityConfigPatch,
        *,
        admin_user_id: str,
    ) -> ClientInactivityConfigResponse:
        config = await self._config_repo.get_singleton()
        if config is None:
            raise NotFoundError(resource="client_inactivity_config", id="singleton")

        incoming = data.model_dump(exclude_none=True, exclude={"version"})
        if not incoming:
            raise ValidationError("At least one mutable field is required.")

        if "inactive_after_days" in incoming:
            self._validate_days(incoming["inactive_after_days"])

        old_value = {
            "enabled": config.enabled,
            "inactive_after_days": config.inactive_after_days,
        }
        updated = await self._config_repo.update_by_id(config.id, incoming, expected_version=data.version)
        await self._audit.log(
            action="client_inactivity.config.patched",
            entity_type="client_inactivity_config",
            entity_id=updated.id,
            user_id=admin_user_id,
            old_value=old_value,
            new_value=incoming,
            severity="NOTICE",
            category=AuditCategory.SYSTEM,
            event_type=AuditEventType.SYSTEM_CONFIG_CHANGED,
        )
        logger.info("client_inactivity_config.patched", config_id=updated.id, admin_user_id=admin_user_id)
        return ClientInactivityConfigResponse.model_validate(updated)

    async def reactivate_user_on_login(self, *, user_id: str) -> bool:
        reactivated = await self._user_repo.reactivate_on_login(
            user_id,
            reason=UserInactiveReason.INACTIVITY.value,
        )
        if not reactivated:
            return False
        await self._audit.log(
            action="client_inactivity.user.reactivated_on_login",
            entity_type="user",
            entity_id=user_id,
            user_id=user_id,
            new_value={"status": "ACTIVE", "inactive_reason": None},
            severity="INFO",
            category=AuditCategory.ACCESS,
            event_type=AuditEventType.ACCOUNT_REACTIVATED,
        )
        logger.info(LogEvent.CLIENT_INACTIVITY_USER_REACTIVATED, user_id=user_id)
        return True

    async def run_daily_inactivity_job(self, *, today: date | None = None, commit: bool = False) -> dict[str, int]:
        run_day = today or datetime.now(UTC).date()
        config = await self._config_repo.get_singleton()
        if config is None or not config.enabled:
            logger.info(LogEvent.CLIENT_INACTIVITY_CRON_SKIPPED, today=str(run_day), enabled=config.enabled if config else False)
            return {"marked_inactive": 0}

        cutoff = datetime.combine(run_day, datetime.min.time(), tzinfo=UTC) - timedelta(days=config.inactive_after_days)
        candidates = await self._user_repo.list_b2b_inactivity_candidates(cutoff=cutoff)
        if not candidates:
            logger.info(LogEvent.CLIENT_INACTIVITY_CRON_COMPLETED, today=str(run_day), marked_inactive=0)
            return {"marked_inactive": 0}

        now = datetime.now(UTC)
        user_ids = [user.id for user in candidates]
        marked = await self._user_repo.mark_inactive_for_inactivity(
            user_ids,
            inactivated_at=now,
            reason=UserInactiveReason.INACTIVITY.value,
        )
        for user in candidates:
            await self._audit.log(
                action="client_inactivity.user.marked_inactive",
                entity_type="user",
                entity_id=user.id,
                user_id=None,
                old_value={"status": "ACTIVE"},
                new_value={
                    "status": "INACTIVE",
                    "inactive_reason": UserInactiveReason.INACTIVITY.value,
                    "inactive_after_days": config.inactive_after_days,
                },
                severity="NOTICE",
                category=AuditCategory.ACCOUNT,
                event_type=AuditEventType.ACCOUNT_DEACTIVATED,
            )

        if commit:
            await self._session.flush()

        logger.info(
            LogEvent.CLIENT_INACTIVITY_CRON_COMPLETED,
            today=str(run_day),
            marked_inactive=marked,
            inactive_after_days=config.inactive_after_days,
        )
        return {"marked_inactive": marked}
