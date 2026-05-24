"""Service for per-organisation credit & suspension configuration."""

from __future__ import annotations

import structlog
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ValidationError
from app.common.service import BaseService
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.org_credit_suspension.repository import OrgCreditConfigRepository, OrgSuspensionConfigRepository
from app.modules.org_credit_suspension.v1.schemas import (
    OrgCreditConfigResponse,
    OrgCreditConfigUpsert,
    OrgCreditSuspensionFullResponse,
    OrgSuspensionConfigResponse,
    OrgSuspensionConfigUpsert,
)
from app.modules.organizations.repository import OrganizationRepository

logger = structlog.get_logger()


class OrgCreditSuspensionService(BaseService):
    """Business logic for per-org credit & suspension configuration."""

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._org_repo = OrganizationRepository(session)
        self._credit_repo = OrgCreditConfigRepository(session)
        self._suspension_repo = OrgSuspensionConfigRepository(session)
        self._audit = AuditService(session)

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_full_config(self, org_id: str) -> OrgCreditSuspensionFullResponse:
        """Return the full credit & suspension config for an org.

        Both sections are null until first configured via their respective PUT endpoints.
        """
        await self._org_repo.get_by_id_or_404(org_id)

        credit = await self._credit_repo.get_by_org(org_id)
        suspension = await self._suspension_repo.get_by_org(org_id)

        return OrgCreditSuspensionFullResponse(
            credit_config=OrgCreditConfigResponse.model_validate(credit) if credit else None,
            suspension_config=OrgSuspensionConfigResponse.model_validate(suspension) if suspension else None,
        )

    # ── Credit config ─────────────────────────────────────────────────────────

    async def upsert_credit_config(
        self,
        org_id: str,
        data: OrgCreditConfigUpsert,
        admin_user_id: str,
    ) -> OrgCreditConfigResponse:
        """Create or replace the credit config for an org. Admin only."""
        await self._org_repo.get_by_id_or_404(org_id)

        payload = data.model_dump(exclude={"reason"})
        payload["organization_id"] = org_id

        existing = await self._credit_repo.get_by_org(org_id)

        if existing is None:
            config = await self._credit_repo.create(payload)
            action = "org_credit_config.created"
            old_value = None
        else:
            config = await self._credit_repo.update_by_id(existing.id, payload)
            action = "org_credit_config.updated"
            old_value = {
                "approved_credit_limit": str(existing.approved_credit_limit),
                "credit_clearance_period_days": existing.credit_clearance_period_days,
                "credit_utilization_warning_pct": existing.credit_utilization_warning_pct,
                "allow_bookings_beyond_limit": existing.allow_bookings_beyond_limit,
            }

        await self._audit.log(
            action=action,
            entity_type="org_credit_config",
            entity_id=config.id,
            user_id=admin_user_id,
            old_value=old_value,
            new_value={k: str(v) if v is not None else None for k, v in payload.items() if k != "organization_id"},
            reason=data.reason,
            severity="NOTICE",
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_LIMIT_ADJUSTED,
        )
        logger.info(action, org_id=org_id, config_id=config.id, admin_id=admin_user_id)

        return OrgCreditConfigResponse.model_validate(config)

    # ── Suspension config ─────────────────────────────────────────────────────

    async def upsert_suspension_config(
        self,
        org_id: str,
        data: OrgSuspensionConfigUpsert,
        admin_user_id: str,
    ) -> OrgSuspensionConfigResponse:
        """Deprecated: org-level suspension configs are now managed in suspension-rules module."""
        raise ValidationError(
            "Legacy org suspension config API is deprecated. Use /v1/suspension-rules/orgs/{org_id}/rule-types/{rule_type}/override"
        )
