"""Business logic for scoped status automation rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import structlog
from fastapi import Request
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.common.service import BaseService
from app.common.enums import UserRole
from app.core.config import settings
from app.core.redis import get_redis
from app.modules.orders.enums import DeliveryStopStatus, OrderStatus, PackageStatus
from app.modules.orders.models import DeliveryStopEvent, Order, OrderEvent, Package, PackageEvent
from app.modules.orders.repository import OrderRepository, PackageExecutionRepository
from app.modules.orders.service import OrderStatusEventService
from app.modules.organizations.models import Organization
from app.modules.status_automation_rules.enums import (
    EntityType,
    StatusAutomationRuleStatus,
    StatusAutomationScopeType,
    TimingValue,
)
from app.modules.status_automation_rules.models import StatusAutomationAction, StatusAutomationCondition, StatusAutomationRuleSet
from app.modules.status_automation_rules.repository import (
    StatusAutomationActionRepository,
    StatusAutomationConditionRepository,
    StatusAutomationExecutionLogRepository,
    StatusAutomationRuleSetRepository,
    StatusAutomationTriggerRepository,
)

logger = structlog.get_logger()


@dataclass
class StatusEventContext:
    event_id: str
    organization_id: str
    entity_type: str
    entity_id: str
    from_status: str | None
    to_status: str
    order_id: str | None = None
    delivery_stop_id: str | None = None
    actor_user_id: str | None = None
    occurred_at: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "StatusEventContext":
        missing = [k for k in ("event_id", "organization_id", "entity_type", "entity_id", "to_status") if not payload.get(k)]
        if missing:
            raise ValidationError(
                "Status automation event payload is missing required fields.",
                details=[{"field": f, "message": "This field is required.", "type": "missing_required_field"} for f in missing],
                code="RULE_EVENT_PAYLOAD_INVALID",
            )
        return cls(
            event_id=str(payload["event_id"]),
            organization_id=str(payload["organization_id"]),
            entity_type=str(payload["entity_type"]),
            entity_id=str(payload["entity_id"]),
            from_status=str(payload["from_status"]) if payload.get("from_status") is not None else None,
            to_status=str(payload["to_status"]),
            order_id=str(payload["order_id"]) if payload.get("order_id") is not None else None,
            delivery_stop_id=str(payload["delivery_stop_id"]) if payload.get("delivery_stop_id") is not None else None,
            actor_user_id=str(payload["actor_user_id"]) if payload.get("actor_user_id") is not None else None,
            occurred_at=str(payload["occurred_at"]) if payload.get("occurred_at") is not None else None,
        )


class StatusAutomationRulesService(BaseService):
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._rule_repo = StatusAutomationRuleSetRepository(session)
        self._trigger_repo = StatusAutomationTriggerRepository(session)
        self._condition_repo = StatusAutomationConditionRepository(session)
        self._action_repo = StatusAutomationActionRepository(session)
        self._exec_repo = StatusAutomationExecutionLogRepository(session)
        self._order_repo = OrderRepository(session)
        self._package_exec_repo = PackageExecutionRepository(session)

    @staticmethod
    def _validate_v2_graph(*, trigger: dict[str, Any], conditions: list[dict[str, Any]], actions: list[dict[str, Any]]) -> None:
        if not actions or len(actions) != 1:
            raise ValidationError("Exactly one THEN status action is required.", code="RULE_GRAPH_INVALID")
        if len(conditions) > 1:
            raise ValidationError("At most one timing condition is allowed.", code="RULE_GRAPH_INVALID")
        trigger_status = str(trigger["status_value"])
        if trigger_status == "CANCELLED" and not conditions:
            raise ValidationError("Timing is required when IF status is CANCELLED.", code="RULE_GRAPH_INVALID")
        if trigger_status != "CANCELLED" and conditions:
            raise ValidationError("Timing is only allowed when IF status is CANCELLED.", code="RULE_GRAPH_INVALID")
        if conditions and str(conditions[0]["value"]) != TimingValue.AFTER_PICKUP.value:
            raise ValidationError("Timing must be AFTER_PICKUP.", code="RULE_GRAPH_INVALID")

    async def _validate_org(self, org_id: str) -> None:
        row = (await self._session.execute(select(Organization.id).where(Organization.id == org_id))).scalar_one_or_none()
        if row is None:
            raise NotFoundError("organization", org_id)

    @staticmethod
    def _assert_org_access(user_role: str, user_org_id: str | None, requested_org_id: str) -> None:
        if user_role == UserRole.CUSTOMER_B2B.value and str(user_org_id or "") != str(requested_org_id):
            raise ForbiddenError("You do not have access to this organisation.")

    async def list_rule_sets(
        self,
        *,
        scope_type: StatusAutomationScopeType | None,
        scope_org_id: str | None,
        status: StatusAutomationRuleStatus | None,
        q: str | None,
        applies_to: str | None,
        rule_kind: str | None,
        page: int,
        size: int,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[StatusAutomationRuleSet], int]:
        if scope_org_id:
            await self._validate_org(scope_org_id)
        return await self._rule_repo.list_for_scope(
            scope_type=scope_type.value if scope_type else None,
            scope_org_id=scope_org_id,
            status=status.value if status else None,
            q=q,
            applies_to=applies_to,
            rule_kind=rule_kind,
            page=page,
            size=size,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    async def get_rule_set(self, rule_set_id: str) -> StatusAutomationRuleSet:
        return await self._rule_repo.get_by_id_with_children_or_404(rule_set_id)

    async def create_rule_set(
        self,
        *,
        payload: dict[str, Any],
        trigger: dict[str, Any],
        conditions: list[dict[str, Any]],
        actions: list[dict[str, Any]],
    ) -> StatusAutomationRuleSet:
        self._validate_v2_graph(trigger=trigger, conditions=conditions, actions=actions)
        if payload["scope_type"] == StatusAutomationScopeType.ORG.value:
            await self._validate_org(payload["scope_org_id"])
        parent_id = payload.get("parent_global_rule_set_id")
        if parent_id:
            parent = await self._rule_repo.get_by_id_or_404(parent_id)
            if parent.scope_type != StatusAutomationScopeType.GLOBAL.value:
                raise ValidationError("parent_global_rule_set_id must reference a GLOBAL rule.")
        created = await self._rule_repo.create(payload)
        await self._trigger_repo.replace_for_rule_set(created.id, trigger)
        await self._condition_repo.replace_for_rule_set(created.id, conditions)
        await self._action_repo.replace_for_rule_set(created.id, actions)
        await self._session.commit()
        return await self._rule_repo.get_by_id_with_children_or_404(created.id)

    async def update_rule_set(
        self,
        *,
        rule_set_id: str,
        payload: dict[str, Any],
        trigger: dict[str, Any] | None,
        conditions: list[dict[str, Any]] | None,
        actions: list[dict[str, Any]] | None,
        expected_version: int | None,
    ) -> StatusAutomationRuleSet:
        existing = await self._rule_repo.get_by_id_or_404(rule_set_id)
        if "scope_type" in payload or "scope_org_id" in payload or "parent_global_rule_set_id" in payload:
            raise ValidationError("Scope and parent linkage cannot be changed once created.")
        merged_trigger = trigger or {
            "entity_type": existing.trigger.entity_type,
            "status_value": existing.trigger.status_value,
        }
        merged_conditions = conditions if conditions is not None else [{"value": c.value} for c in existing.conditions]
        merged_actions = actions if actions is not None else [{"new_status": a.new_status} for a in existing.actions]
        self._validate_v2_graph(trigger=merged_trigger, conditions=merged_conditions, actions=merged_actions)
        await self._rule_repo.update_by_id(rule_set_id, payload, expected_version=expected_version)
        if trigger is not None:
            await self._trigger_repo.replace_for_rule_set(rule_set_id, trigger)
        if conditions is not None:
            await self._condition_repo.replace_for_rule_set(rule_set_id, conditions)
        if actions is not None:
            await self._action_repo.replace_for_rule_set(rule_set_id, actions)
        await self._session.commit()
        return await self._rule_repo.get_by_id_with_children_or_404(rule_set_id)

    async def delete_rule_set(self, *, rule_set_id: str) -> None:
        await self._rule_repo.hard_delete(rule_set_id)
        await self._session.commit()

    async def set_rule_status(self, *, rule_set_id: str, status: StatusAutomationRuleStatus, expected_version: int | None) -> StatusAutomationRuleSet:
        await self._rule_repo.update_by_id(rule_set_id, {"status": status.value}, expected_version=expected_version)
        await self._session.commit()
        return await self._rule_repo.get_by_id_with_children_or_404(rule_set_id)

    async def create_customised_from_global(
        self,
        *,
        org_id: str,
        global_rule_set_id: str,
        payload: dict[str, Any],
        trigger: dict[str, Any] | None,
        conditions: list[dict[str, Any]] | None,
        actions: list[dict[str, Any]] | None,
    ) -> StatusAutomationRuleSet:
        await self._validate_org(org_id)
        global_rule = await self._rule_repo.get_by_id_with_children_or_404(global_rule_set_id)
        if global_rule.scope_type != StatusAutomationScopeType.GLOBAL.value:
            raise ValidationError("Only GLOBAL rules can be customised.")
        if await self._rule_repo.find_active_customised_by_parent(
            organization_id=org_id, parent_global_rule_set_id=global_rule_set_id
        ):
            raise ConflictError("An active customised rule already exists for this default.")

        create_payload = {
            "name": payload.get("name") or f"{global_rule.name}-org-{org_id[:8]}",
            "scope_type": StatusAutomationScopeType.ORG.value,
            "scope_org_id": org_id,
            "parent_global_rule_set_id": global_rule_set_id,
            "status": payload.get("status", global_rule.status),
            "priority": payload.get("priority", global_rule.priority),
            "notes": payload.get("notes", global_rule.notes),
        }
        source_trigger = global_rule.trigger
        source_conditions = list(global_rule.conditions)
        source_actions = list(global_rule.actions)
        return await self.create_rule_set(
            payload=create_payload,
            trigger=trigger
            or {
                "entity_type": source_trigger.entity_type,
                "status_value": source_trigger.status_value,
            },
            conditions=conditions
            if conditions is not None
            else [{"value": c.value} for c in source_conditions],
            actions=actions
            if actions is not None
            else [{"new_status": a.new_status} for a in source_actions],
        )

    async def restore_default_for_customised(self, *, org_id: str, rule_set_id: str, expected_version: int | None) -> StatusAutomationRuleSet:
        row = await self._rule_repo.get_by_id_or_404(rule_set_id)
        if row.scope_type != StatusAutomationScopeType.ORG.value or row.scope_org_id != org_id:
            raise ValidationError("restore-default only applies to org scoped rules in this organisation.")
        if not row.parent_global_rule_set_id:
            raise ValidationError("Only CUSTOMISED rules can be restored to default.")
        if expected_version is not None and row.version != expected_version:
            raise ConflictError("status_automation_rule_sets was modified by another request.")
        parent_id = str(row.parent_global_rule_set_id)
        parent = await self._rule_repo.get_by_id_with_children_or_404(parent_id)
        if parent.scope_type != StatusAutomationScopeType.GLOBAL.value:
            raise ValidationError("Linked parent default rule is invalid.")
        await self._rule_repo.hard_delete(rule_set_id)
        await self._session.commit()
        return parent

    async def get_applicable_for_org(self, *, org_id: str, include_inactive: bool = True) -> list[dict[str, Any]]:
        await self._validate_org(org_id)
        stmt = (
            select(StatusAutomationRuleSet)
            .where(
                or_(
                    StatusAutomationRuleSet.scope_type == StatusAutomationScopeType.GLOBAL.value,
                    and_(
                        StatusAutomationRuleSet.scope_type == StatusAutomationScopeType.ORG.value,
                        StatusAutomationRuleSet.scope_org_id == org_id,
                    ),
                )
            )
            .options(
                selectinload(StatusAutomationRuleSet.trigger),
                selectinload(StatusAutomationRuleSet.conditions),
                selectinload(StatusAutomationRuleSet.actions),
            )
        )
        if not include_inactive:
            stmt = stmt.where(StatusAutomationRuleSet.status == StatusAutomationRuleStatus.ACTIVE.value)
        all_rules = list((await self._session.execute(stmt)).scalars().all())
        effective = await self.get_effective_for_org(org_id=org_id)
        effective_ids = {row["rule_set"].id for row in effective}

        by_id: dict[str, dict[str, Any]] = {}
        for rule in all_rules:
            kind = "DEFAULT" if rule.scope_type == StatusAutomationScopeType.GLOBAL.value else ("CUSTOMISED" if rule.parent_global_rule_set_id else "NEW")
            can_delete = kind in {"NEW", "CUSTOMISED"}
            by_id[rule.id] = {
                "rule_set": rule,
                "rule_kind": kind,
                "global_rule_set_id": rule.parent_global_rule_set_id if kind == "CUSTOMISED" else (rule.id if kind == "DEFAULT" else None),
                "is_effective_for_org": rule.id in effective_ids,
                "can_restore_default": bool(kind == "CUSTOMISED"),
                "can_delete": can_delete,
            }

        for row in list(by_id.values()):
            if row["rule_kind"] == "DEFAULT":
                global_id = row["rule_set"].id
                has_active_custom = any(
                    r["rule_kind"] == "CUSTOMISED"
                    and r["rule_set"].parent_global_rule_set_id == global_id
                    and r["rule_set"].status == StatusAutomationRuleStatus.ACTIVE.value
                    for r in by_id.values()
                )
                if has_active_custom:
                    by_id.pop(global_id, None)

        return sorted(by_id.values(), key=lambda r: (r["rule_set"].priority, r["rule_set"].updated_at), reverse=True)

    async def get_effective_for_org(self, *, org_id: str) -> list[dict[str, Any]]:
        await self._validate_org(org_id)
        stmt = (
            select(StatusAutomationRuleSet)
            .where(StatusAutomationRuleSet.status == StatusAutomationRuleStatus.ACTIVE.value)
            .options(
                selectinload(StatusAutomationRuleSet.trigger),
                selectinload(StatusAutomationRuleSet.conditions),
                selectinload(StatusAutomationRuleSet.actions),
            )
        )
        all_rows = list((await self._session.execute(stmt)).scalars().all())
        globals_ = [r for r in all_rows if r.scope_type == StatusAutomationScopeType.GLOBAL.value]
        org_rows = [r for r in all_rows if r.scope_type == StatusAutomationScopeType.ORG.value and r.scope_org_id == org_id]
        hidden_global_ids = {str(r.parent_global_rule_set_id) for r in org_rows if r.parent_global_rule_set_id}
        visible_globals = [r for r in globals_ if str(r.id) not in hidden_global_ids]
        merged = visible_globals + org_rows
        merged = sorted(merged, key=lambda r: (r.priority, r.updated_at, r.id), reverse=True)
        out: list[dict[str, Any]] = []
        for row in merged:
            kind = "DEFAULT" if row.scope_type == StatusAutomationScopeType.GLOBAL.value else ("CUSTOMISED" if row.parent_global_rule_set_id else "NEW")
            out.append(
                {
                    "rule_set": row,
                    "rule_kind": kind,
                    "global_rule_set_id": row.parent_global_rule_set_id if kind == "CUSTOMISED" else (row.id if kind == "DEFAULT" else None),
                    "is_effective_for_org": True,
                    "can_restore_default": bool(kind == "CUSTOMISED"),
                    "can_delete": kind in {"NEW", "CUSTOMISED"},
                }
            )
        return out

    async def _acquire_entity_lock(self, ctx: StatusEventContext) -> tuple[bool, str]:
        key = f"status-auto-lock:{ctx.organization_id}:{ctx.entity_type}:{ctx.entity_id}"
        try:
            redis = get_redis()
        except RuntimeError:
            return False, key
        acquired = await redis.set(key, ctx.event_id, ex=30, nx=True)
        return bool(acquired), key

    async def _release_entity_lock(self, acquired: bool, key: str) -> None:
        if not acquired:
            return
        try:
            redis = get_redis()
            await redis.delete(key)
        except RuntimeError:
            return

    @staticmethod
    def _is_before_pickup(order_status: str | None) -> bool:
        if not order_status:
            return True
        return order_status in {
            OrderStatus.PENDING_PICKUP.value,
            OrderStatus.PICKUP_SCHEDULED.value,
            OrderStatus.ENROUTE_PICKUP.value,
        }

    async def _resolve_order_status(self, order_id: str | None) -> str | None:
        if not order_id:
            return None
        row = (
            await self._session.execute(
                select(self._order_repo.model.status).where(self._order_repo.model.id == order_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return row.value if hasattr(row, "value") else str(row)

    async def _trigger_matches(self, rule: StatusAutomationRuleSet, ctx: StatusEventContext) -> bool:
        trig = rule.trigger
        if trig is None:
            return False
        if trig.entity_type != ctx.entity_type:
            return False
        return ctx.to_status == trig.status_value

    async def _conditions_match(self, conditions: list[StatusAutomationCondition], ctx: StatusEventContext) -> bool:
        if not conditions:
            return True
        order_id = ctx.order_id if ctx.order_id else (ctx.entity_id if ctx.entity_type == EntityType.BOOKING_ORDER.value else None)
        order_status = await self._resolve_order_status(order_id)
        before_pickup = self._is_before_pickup(order_status)
        return bool(conditions[0].value == TimingValue.AFTER_PICKUP.value and not before_pickup)

    async def _execute_change_status(self, action: StatusAutomationAction, ctx: StatusEventContext) -> int:
        new_status_value = action.new_status
        if ctx.entity_type == EntityType.PACKAGE.value:
            package = await self._package_exec_repo.get_by_id_or_404(ctx.entity_id)
            new_status = PackageStatus(new_status_value)
            if package.status == new_status:
                return 0
            await self._package_exec_repo.update_package_status(
                package=package,
                status=new_status,
                actor_user_id=ctx.actor_user_id,
                suppress_automation=True,
            )
            return 1

        if ctx.entity_type == EntityType.DELIVERY_STOP.value:
            stop_id = ctx.delivery_stop_id or ctx.entity_id
            stop = await self._order_repo.get_stop_by_id(stop_id)
            if stop is None:
                return 0
            new_status = DeliveryStopStatus(new_status_value)
            old_status = stop.status
            if old_status == new_status:
                return 0
            stop.status = new_status
            OrderStatusEventService(self._session).record_delivery_stop_transition(
                delivery_stop_id=stop.id,
                from_status=old_status,
                to_status=new_status,
                actor_user_id=ctx.actor_user_id,
            )
            return 1

        if ctx.entity_type == EntityType.BOOKING_ORDER.value:
            order_id = ctx.order_id or ctx.entity_id
            order = await self._order_repo.get_by_id_or_404(order_id)
            new_status = OrderStatus(new_status_value)
            old_status = order.status
            if old_status == new_status:
                return 0
            order.status = new_status
            OrderStatusEventService(self._session).record_order_transition(
                order_id=order.id,
                from_status=old_status,
                to_status=new_status,
                actor_user_id=ctx.actor_user_id,
            )
            return 1

        raise ValidationError("Unsupported event entity type.", code="RULE_ACTION_ENTITY_UNSUPPORTED")

    async def evaluate_for_event(self, payload: dict[str, Any], *, commit: bool = False) -> dict[str, int]:
        """Evaluate effective rules for one transition event."""
        ctx = StatusEventContext.from_payload(payload)
        if not settings.STATUS_AUTOMATION_RUNTIME_ENABLED:
            logger.info(
                "STATUS_AUTOMATION_EVENT_SKIPPED",
                event_id=ctx.event_id,
                organization_id=ctx.organization_id,
                reason="runtime_disabled",
            )
            return {"evaluated": 0, "matched": 0, "executed": 0}
        allowlist = settings.status_automation_enabled_org_ids
        if allowlist and ctx.organization_id not in allowlist:
            logger.info(
                "STATUS_AUTOMATION_EVENT_SKIPPED",
                event_id=ctx.event_id,
                organization_id=ctx.organization_id,
                reason="org_not_enabled",
            )
            return {"evaluated": 0, "matched": 0, "executed": 0}

        lock_acquired, lock_key = await self._acquire_entity_lock(ctx)
        if not lock_acquired:
            logger.info(
                "STATUS_AUTOMATION_EVENT_SKIPPED",
                event_id=ctx.event_id,
                organization_id=ctx.organization_id,
                reason="entity_lock_not_acquired",
            )
            return {"evaluated": 0, "matched": 0, "executed": 0}
        evaluated = 0
        matched = 0
        executed = 0
        try:
            # One transition event must not execute multiple rule sets; replays must no-op once any log exists.
            if await self._exec_repo.exists(event_id=ctx.event_id):
                logger.info(
                    "STATUS_AUTOMATION_EVENT_SKIPPED",
                    event_id=ctx.event_id,
                    organization_id=ctx.organization_id,
                    reason="event_already_processed",
                )
                return {"evaluated": 0, "matched": 0, "executed": 0}

            rows = await self.get_effective_for_org(org_id=ctx.organization_id)
            for row in rows:
                rule = row["rule_set"]
                if not await self._trigger_matches(rule, ctx):
                    continue
                evaluated += 1
                if not await self._conditions_match(rule.conditions, ctx):
                    continue
                matched += 1
                try:
                    if settings.STATUS_AUTOMATION_SHADOW_MODE:
                        await self._exec_repo.create(
                            {
                                "event_id": ctx.event_id,
                                "organization_id": ctx.organization_id,
                                "entity_type": ctx.entity_type,
                                "entity_id": ctx.entity_id,
                                "rule_set_id": rule.id,
                                "status": "SHADOW_MATCHED",
                                "message": "Shadow mode: actions not executed.",
                            }
                        )
                        break
                    action = rule.actions[0] if rule.actions else None
                    if action is None:
                        raise ValidationError("Rule is missing THEN status action.", code="RULE_ACTION_INVALID")
                    executed += await self._execute_change_status(action, ctx)
                    await self._exec_repo.create(
                        {
                            "event_id": ctx.event_id,
                            "organization_id": ctx.organization_id,
                            "entity_type": ctx.entity_type,
                            "entity_id": ctx.entity_id,
                            "rule_set_id": rule.id,
                            "status": "SUCCESS",
                            "message": f"Executed {executed} action(s).",
                        }
                    )
                    break
                except ValidationError as exc:
                    logger.warning(
                        "STATUS_AUTOMATION_RULE_EXECUTION_FAILED",
                        event_id=ctx.event_id,
                        rule_set_id=rule.id,
                        code=str(exc.code),
                        message=exc.message,
                    )
                    raise
                except Exception as exc:
                    logger.exception(
                        "STATUS_AUTOMATION_RULE_EXECUTION_FAILED",
                        event_id=ctx.event_id,
                        rule_set_id=rule.id,
                        error=str(exc),
                    )
                    raise
            if commit:
                await self._session.commit()
        finally:
            await self._release_entity_lock(lock_acquired, lock_key)

        logger.info(
            "STATUS_AUTOMATION_EVENT_EVALUATED",
            event_id=ctx.event_id,
            organization_id=ctx.organization_id,
            evaluated=evaluated,
            matched=matched,
            executed=executed,
        )
        return {"evaluated": evaluated, "matched": matched, "executed": executed}

    async def run_daily_reconciliation(self, *, run_date: date, commit: bool = True) -> dict[str, int]:
        """Replay one day's package/stop transition events as safety-net reconciliation."""
        start_dt = datetime.combine(run_date, time.min, tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(days=1)

        total_events = 0
        total_evaluated = 0
        total_matched = 0
        total_executed = 0

        pkg_rows = (
            await self._session.execute(
                select(
                    PackageEvent.id.label("event_id"),
                    PackageEvent.created_at.label("occurred_at"),
                    PackageEvent.actor_user_id.label("actor_user_id"),
                    PackageEvent.from_status.label("from_status"),
                    PackageEvent.to_status.label("to_status"),
                    Package.id.label("package_id"),
                    Package.order_id.label("order_id"),
                    Package.delivery_stop_id.label("delivery_stop_id"),
                    Order.organization_id.label("organization_id"),
                )
                .join(Package, Package.id == PackageEvent.package_id)
                .join(Order, Order.id == Package.order_id)
                .where(PackageEvent.created_at >= start_dt, PackageEvent.created_at < end_dt)
                .order_by(PackageEvent.created_at.asc())
            )
        ).mappings().all()

        for row in pkg_rows:
            total_events += 1
            metrics = await self.evaluate_for_event(
                {
                    "event_id": str(row["event_id"]),
                    "occurred_at": row["occurred_at"].isoformat() if row["occurred_at"] else None,
                    "organization_id": str(row["organization_id"]),
                    "entity_type": "PACKAGE",
                    "entity_id": str(row["package_id"]),
                    "order_id": str(row["order_id"]),
                    "delivery_stop_id": str(row["delivery_stop_id"]) if row["delivery_stop_id"] else None,
                    "from_status": row["from_status"],
                    "to_status": row["to_status"],
                    "actor_user_id": str(row["actor_user_id"]) if row["actor_user_id"] else None,
                },
                commit=False,
            )
            total_evaluated += metrics["evaluated"]
            total_matched += metrics["matched"]
            total_executed += metrics["executed"]

        stop_rows = (
            await self._session.execute(
                select(
                    DeliveryStopEvent.id.label("event_id"),
                    DeliveryStopEvent.created_at.label("occurred_at"),
                    DeliveryStopEvent.actor_user_id.label("actor_user_id"),
                    DeliveryStopEvent.from_status.label("from_status"),
                    DeliveryStopEvent.to_status.label("to_status"),
                    DeliveryStop.id.label("delivery_stop_id"),
                    DeliveryStop.order_id.label("order_id"),
                    Order.organization_id.label("organization_id"),
                )
                .join(DeliveryStop, DeliveryStop.id == DeliveryStopEvent.delivery_stop_id)
                .join(Order, Order.id == DeliveryStop.order_id)
                .where(DeliveryStopEvent.created_at >= start_dt, DeliveryStopEvent.created_at < end_dt)
                .order_by(DeliveryStopEvent.created_at.asc())
            )
        ).mappings().all()

        for row in stop_rows:
            total_events += 1
            metrics = await self.evaluate_for_event(
                {
                    "event_id": str(row["event_id"]),
                    "occurred_at": row["occurred_at"].isoformat() if row["occurred_at"] else None,
                    "organization_id": str(row["organization_id"]),
                    "entity_type": "DELIVERY_STOP",
                    "entity_id": str(row["delivery_stop_id"]),
                    "order_id": str(row["order_id"]),
                    "delivery_stop_id": str(row["delivery_stop_id"]),
                    "from_status": row["from_status"],
                    "to_status": row["to_status"],
                    "actor_user_id": str(row["actor_user_id"]) if row["actor_user_id"] else None,
                },
                commit=False,
            )
            total_evaluated += metrics["evaluated"]
            total_matched += metrics["matched"]
            total_executed += metrics["executed"]

        order_rows = (
            await self._session.execute(
                select(
                    OrderEvent.id.label("event_id"),
                    OrderEvent.created_at.label("occurred_at"),
                    OrderEvent.actor_user_id.label("actor_user_id"),
                    OrderEvent.from_status.label("from_status"),
                    OrderEvent.to_status.label("to_status"),
                    Order.id.label("order_id"),
                    Order.organization_id.label("organization_id"),
                )
                .join(Order, Order.id == OrderEvent.order_id)
                .where(OrderEvent.created_at >= start_dt, OrderEvent.created_at < end_dt)
                .order_by(OrderEvent.created_at.asc())
            )
        ).mappings().all()

        for row in order_rows:
            total_events += 1
            metrics = await self.evaluate_for_event(
                {
                    "event_id": str(row["event_id"]),
                    "occurred_at": row["occurred_at"].isoformat() if row["occurred_at"] else None,
                    "organization_id": str(row["organization_id"]),
                    "entity_type": "BOOKING_ORDER",
                    "entity_id": str(row["order_id"]),
                    "order_id": str(row["order_id"]),
                    "delivery_stop_id": None,
                    "from_status": row["from_status"],
                    "to_status": row["to_status"],
                    "actor_user_id": str(row["actor_user_id"]) if row["actor_user_id"] else None,
                },
                commit=False,
            )
            total_evaluated += metrics["evaluated"]
            total_matched += metrics["matched"]
            total_executed += metrics["executed"]

        if commit:
            await self._session.commit()
        logger.info(
            "STATUS_AUTOMATION_RECONCILIATION_SUMMARY",
            run_date=run_date.isoformat(),
            events=total_events,
            evaluated=total_evaluated,
            matched=total_matched,
            executed=total_executed,
        )
        return {
            "events": total_events,
            "evaluated": total_evaluated,
            "matched": total_matched,
            "executed": total_executed,
        }

