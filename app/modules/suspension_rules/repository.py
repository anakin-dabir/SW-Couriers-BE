"""Repositories for suspension rules and activity."""

from datetime import date

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.exceptions import NotFoundError
from app.common.repository import BaseRepository
from app.modules.suspension_rules.models import (
    OrgSuspensionGlobalSuppression,
    PaymentRiskEvent,
    SuspensionActivity,
    SuspensionEvaluationRun,
    SuspensionNotificationAudit,
    SuspensionRuleCondition,
    SuspensionRuleSet,
)


class SuspensionActivityRepository(BaseRepository):
    """Data access for SuspensionActivity records (read-mostly)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, SuspensionActivity)


class SuspensionRuleSetRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, SuspensionRuleSet)

    async def find_latest_by_scope_and_type(
        self,
        *,
        scope_type: str,
        scope_org_id: str | None,
        rule_type: str,
    ) -> SuspensionRuleSet | None:
        stmt = (
            select(SuspensionRuleSet)
            .where(
                SuspensionRuleSet.scope_type == scope_type,
                SuspensionRuleSet.scope_org_id == scope_org_id,
                SuspensionRuleSet.rule_type == rule_type,
            )
            .order_by(SuspensionRuleSet.updated_at.desc(), SuspensionRuleSet.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_with_conditions_or_404(self, id: str) -> SuspensionRuleSet:
        """Load a ruleset for API responses with `conditions` eagerly loaded (async-safe)."""
        stmt = (
            select(SuspensionRuleSet)
            .where(SuspensionRuleSet.id == id)
            .options(selectinload(SuspensionRuleSet.conditions))
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundError(resource=self.model.__tablename__, id=id)
        return row

    async def find_active_customised_by_parent(
        self,
        *,
        organization_id: str,
        parent_global_rule_set_id: str,
    ) -> SuspensionRuleSet | None:
        stmt = (
            select(SuspensionRuleSet)
            .where(
                SuspensionRuleSet.scope_type == "ORG",
                SuspensionRuleSet.scope_org_id == organization_id,
                SuspensionRuleSet.parent_global_rule_set_id == parent_global_rule_set_id,
                SuspensionRuleSet.status == "ACTIVE",
            )
            .order_by(SuspensionRuleSet.updated_at.desc(), SuspensionRuleSet.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


class OrgSuspensionGlobalSuppressionRepository(BaseRepository):
    """Per-org opt-outs hiding specific GLOBAL suspension rule rows from effective resolution."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgSuspensionGlobalSuppression)

    async def global_ids_suppressed_for_org(self, organization_id: str) -> set[str]:
        stmt = select(OrgSuspensionGlobalSuppression.global_rule_set_id).where(
            OrgSuspensionGlobalSuppression.organization_id == organization_id
        )
        result = await self.session.execute(stmt)
        return {str(x[0]) for x in result.all()}

    async def upsert_row(self, organization_id: str, global_rule_set_id: str) -> OrgSuspensionGlobalSuppression:
        existing = await self.find_one(organization_id=organization_id, global_rule_set_id=global_rule_set_id)
        if existing is not None:
            return existing
        row = OrgSuspensionGlobalSuppression(
            organization_id=organization_id,
            global_rule_set_id=global_rule_set_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def delete_row(self, organization_id: str, global_rule_set_id: str) -> bool:
        row = await self.find_one(organization_id=organization_id, global_rule_set_id=global_rule_set_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True


class SuspensionRuleConditionRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, SuspensionRuleCondition)

    async def replace_for_ruleset(self, rule_set_id: str, conditions: list[dict]) -> list[SuspensionRuleCondition]:
        delete_stmt = select(SuspensionRuleCondition).where(SuspensionRuleCondition.rule_set_id == rule_set_id)
        result = await self.session.execute(delete_stmt)
        for row in result.scalars().all():
            await self.session.delete(row)
        await self.session.flush()
        created: list[SuspensionRuleCondition] = []
        for payload in conditions:
            instance = SuspensionRuleCondition(rule_set_id=rule_set_id, **payload)
            self.session.add(instance)
            created.append(instance)
        await self.session.flush()
        return created


class SuspensionEvaluationRunRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, SuspensionEvaluationRun)


class SuspensionNotificationAuditRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, SuspensionNotificationAudit)


class PaymentRiskEventRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PaymentRiskEvent)

    async def count_events(
        self,
        *,
        organization_id: str,
        payment_model: str,
        event_type: str,
        since_on: date | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(PaymentRiskEvent).where(
            PaymentRiskEvent.organization_id == organization_id,
            PaymentRiskEvent.payment_model == payment_model,
            PaymentRiskEvent.event_type == event_type,
        )
        if since_on is not None:
            stmt = stmt.where(PaymentRiskEvent.occurred_on >= since_on)
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def recent_events(
        self,
        *,
        organization_id: str,
        payment_model: str,
        limit: int = 50,
    ) -> list[PaymentRiskEvent]:
        stmt = (
            select(PaymentRiskEvent)
            .where(
                and_(
                    PaymentRiskEvent.organization_id == organization_id,
                    PaymentRiskEvent.payment_model == payment_model,
                )
            )
            .order_by(PaymentRiskEvent.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
