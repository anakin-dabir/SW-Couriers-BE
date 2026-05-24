"""Repository helpers for status automation rules."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.repository import BaseRepository
from app.modules.status_automation_rules.enums import StatusAutomationScopeType
from app.modules.status_automation_rules.models import (
    StatusAutomationAction,
    StatusAutomationCondition,
    StatusAutomationExecutionLog,
    StatusAutomationRuleSet,
    StatusAutomationTrigger,
)


class StatusAutomationRuleSetRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, StatusAutomationRuleSet)

    async def get_by_id_with_children_or_404(self, rule_set_id: str) -> StatusAutomationRuleSet:
        stmt = (
            select(StatusAutomationRuleSet)
            .where(StatusAutomationRuleSet.id == rule_set_id)
            .options(
                selectinload(StatusAutomationRuleSet.trigger),
                selectinload(StatusAutomationRuleSet.conditions),
                selectinload(StatusAutomationRuleSet.actions),
            )
            # Freshly created/updated instances may already live in the session identity map
            # with stale relationship state (e.g. trigger=None before child rows are inserted).
            .execution_options(populate_existing=True)
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return await self.get_by_id_or_404(rule_set_id)
        return row

    async def list_for_scope(
        self,
        *,
        scope_type: str | None,
        scope_org_id: str | None,
        status: str | None,
        q: str | None,
        applies_to: str | None,
        rule_kind: str | None,
        page: int,
        size: int,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[StatusAutomationRuleSet], int]:
        stmt = select(StatusAutomationRuleSet).options(
            selectinload(StatusAutomationRuleSet.trigger),
            selectinload(StatusAutomationRuleSet.conditions),
            selectinload(StatusAutomationRuleSet.actions),
        )
        count_stmt = select(func.count()).select_from(StatusAutomationRuleSet)

        if scope_type is not None:
            stmt = stmt.where(StatusAutomationRuleSet.scope_type == scope_type)
            count_stmt = count_stmt.where(StatusAutomationRuleSet.scope_type == scope_type)
        if scope_org_id is not None:
            stmt = stmt.where(StatusAutomationRuleSet.scope_org_id == scope_org_id)
            count_stmt = count_stmt.where(StatusAutomationRuleSet.scope_org_id == scope_org_id)
        if status is not None:
            stmt = stmt.where(StatusAutomationRuleSet.status == status)
            count_stmt = count_stmt.where(StatusAutomationRuleSet.status == status)
        if q:
            q_clause = StatusAutomationRuleSet.name.ilike(f"%{q.strip()}%")
            stmt = stmt.where(q_clause)
            count_stmt = count_stmt.where(q_clause)
        if rule_kind:
            if rule_kind == "DEFAULT":
                c = and_(
                    StatusAutomationRuleSet.scope_type == StatusAutomationScopeType.GLOBAL.value,
                    StatusAutomationRuleSet.parent_global_rule_set_id.is_(None),
                )
            elif rule_kind == "CUSTOMISED":
                c = and_(
                    StatusAutomationRuleSet.scope_type == StatusAutomationScopeType.ORG.value,
                    StatusAutomationRuleSet.parent_global_rule_set_id.is_not(None),
                )
            else:
                c = and_(
                    StatusAutomationRuleSet.scope_type == StatusAutomationScopeType.ORG.value,
                    StatusAutomationRuleSet.parent_global_rule_set_id.is_(None),
                )
            stmt = stmt.where(c)
            count_stmt = count_stmt.where(c)
        if applies_to:
            applies_clause = exists_trigger_entity(applies_to)
            stmt = stmt.where(applies_clause)
            count_stmt = count_stmt.where(applies_clause)

        sort_column = getattr(StatusAutomationRuleSet, sort_by, StatusAutomationRuleSet.updated_at)
        stmt = stmt.order_by(sort_column.desc() if sort_order == "desc" else sort_column.asc())
        stmt = stmt.offset((page - 1) * size).limit(size)
        rows = (await self.session.execute(stmt)).scalars().all()
        total = (await self.session.execute(count_stmt)).scalar_one()
        return list(rows), total

    async def find_active_customised_by_parent(self, *, organization_id: str, parent_global_rule_set_id: str) -> StatusAutomationRuleSet | None:
        stmt = select(StatusAutomationRuleSet).where(
            StatusAutomationRuleSet.scope_type == StatusAutomationScopeType.ORG.value,
            StatusAutomationRuleSet.scope_org_id == organization_id,
            StatusAutomationRuleSet.parent_global_rule_set_id == parent_global_rule_set_id,
            StatusAutomationRuleSet.status == "ACTIVE",
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


def exists_trigger_entity(entity_type: str):
    return StatusAutomationRuleSet.id.in_(
        select(StatusAutomationTrigger.rule_set_id).where(StatusAutomationTrigger.entity_type == entity_type)
    )


class StatusAutomationTriggerRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, StatusAutomationTrigger)

    async def replace_for_rule_set(self, rule_set_id: str, payload: dict[str, Any]) -> StatusAutomationTrigger:
        existing = await self.find_one(rule_set_id=rule_set_id)
        if existing is not None:
            await self.hard_delete(existing.id)
        return await self.create({"rule_set_id": rule_set_id, **payload})


class StatusAutomationConditionRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, StatusAutomationCondition)

    async def replace_for_rule_set(self, rule_set_id: str, conditions: list[dict[str, Any]]) -> None:
        rows = (await self.session.execute(select(StatusAutomationCondition).where(StatusAutomationCondition.rule_set_id == rule_set_id))).scalars().all()
        for row in rows:
            await self.session.delete(row)
        # Ensure old row(s) are physically deleted before re-inserting with same unique key.
        await self.session.flush()
        for condition in conditions:
            await self.create({"rule_set_id": rule_set_id, **condition})


class StatusAutomationActionRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, StatusAutomationAction)

    async def replace_for_rule_set(self, rule_set_id: str, actions: list[dict[str, Any]]) -> None:
        rows = (await self.session.execute(select(StatusAutomationAction).where(StatusAutomationAction.rule_set_id == rule_set_id))).scalars().all()
        for row in rows:
            await self.session.delete(row)
        # Ensure old row(s) are physically deleted before re-inserting with same unique key.
        await self.session.flush()
        for action in actions:
            await self.create({"rule_set_id": rule_set_id, **action})


class StatusAutomationExecutionLogRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, StatusAutomationExecutionLog)

