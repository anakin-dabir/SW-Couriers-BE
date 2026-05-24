"""Business logic for suspension rules (legacy v1 + canonical scoped engine)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import Request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums import UserRole, UserStatus
from app.common.exceptions import ConflictError, NotFoundError, ValidationError
from app.common.service import BaseService
from app.common.utils import mark_user_suspended
from app.core.config import settings
from app.core.queue import QueuePriority, enqueue
from app.mailer import EmailTemplateName
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService

from app.modules.orders.models import Order
from app.modules.billing.models import BillingPaymentAllocation

from app.modules.orders.models import Order

from app.modules.invoices.enums import PaymentStatus
from app.modules.invoices.models import Invoice, InvoiceCreditApplication, InvoiceEvent
from app.modules.invoices.service import compute_payment_status
from app.modules.org_credit_suspension.models import OrgCreditConfig
from app.modules.organizations.enums import ContactRole, OrganizationStatus, PaymentModel
from app.modules.organizations.models import OrgContact, OrgPaymentMethod, Organization
from app.modules.suspension_rules.enums import (
    RuleScopeType,
    SuspensionActionTaken,
    SuspensionConditionType,
    SuspensionConnector,
    SuspensionRuleStatus,
    SuspensionRuleType,
)
from app.modules.suspension_rules.models import (
    PaymentRiskEvent,
    SuspensionActivity,
    SuspensionNotificationAudit,
    SuspensionRuleSet,
)
from app.modules.suspension_rules.repository import (
    OrgSuspensionGlobalSuppressionRepository,
    PaymentRiskEventRepository,
    SuspensionActivityRepository,
    SuspensionEvaluationRunRepository,
    SuspensionNotificationAuditRepository,
    SuspensionRuleConditionRepository,
    SuspensionRuleSetRepository,
)
from app.modules.user.models import User
from app.modules.user.repository import UserRepository

UNPAID_STATES = {PaymentStatus.UNPAID.value, PaymentStatus.PARTIALLY_PAID.value, PaymentStatus.OVERDUE.value}


@dataclass
class RuleDecision:
    rule_set_id: str
    organization_id: str
    action: Literal["SUSPEND", "WARN_ONLY", "NO_ACTION"]
    apply_user_suspension: bool
    block_new_bookings: bool
    block_portal_login: bool
    conditions_met: dict[str, Any]
    evaluated_expression: str
    group_results: list[bool]
    final_result: bool
    rule_type: str
    payment_model: str
    rule_name: str
    notify_finance_team: bool
    notify_account_manager: bool


def _metric_key(condition_type: str) -> str:
    return condition_type.lower()


def infer_org_override_response_meta(item: SuspensionRuleSet) -> tuple[str | None, Literal["CUSTOMISED", "NEW"]]:
    """Derive global template id and rule_kind for org upsert responses without relying on effective ordering."""
    pid = getattr(item, "parent_global_rule_set_id", None)
    if pid:
        return str(pid), "CUSTOMISED"
    return None, "NEW"


class SuspensionRulesService(BaseService):
    """Service layer for suspension rule CRUD + scheduled evaluation."""

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._rule_set_repo = SuspensionRuleSetRepository(session)
        self._rule_condition_repo = SuspensionRuleConditionRepository(session)
        self._activity_repo = SuspensionActivityRepository(session)
        self._run_repo = SuspensionEvaluationRunRepository(session)
        self._notification_audit_repo = SuspensionNotificationAuditRepository(session)
        self._risk_repo = PaymentRiskEventRepository(session)
        self._suppression_repo = OrgSuspensionGlobalSuppressionRepository(session)
        self._user_repo = UserRepository(session)
        self._audit = AuditService(session)
        self._ip_address = request.client.host if request and request.client else None
        self._user_agent = request.headers.get("user-agent") if request else None

    async def _log_audit(
        self,
        action: str,
        *,
        entity_id: str | None = None,
        user_id: str | None = None,
        user_role: str | None = None,
        old_value: dict | None = None,
        new_value: dict | None = None,
        severity: str = "INFO",
        category: AuditCategory = AuditCategory.SECURITY,
        event_type: AuditEventType | str = AuditEventType.CREDIT_TERMS_MODIFIED,
    ) -> None:
        await self._audit.log(
            action=action,
            entity_type="suspension_rule_set",
            entity_id=entity_id,
            user_id=user_id,
            user_role=user_role,
            old_value=old_value,
            new_value=new_value,
            ip_address=self._ip_address,
            user_agent=self._user_agent,
            severity=severity,
            category=category,
            event_type=event_type,
        )

    # ---------------------------------------------------------------------
    # Canonical scoped ruleset CRUD
    # ---------------------------------------------------------------------
    async def list_rule_sets(
        self,
        *,
        scope_type: RuleScopeType | None = None,
        scope_org_id: str | None = None,
        rule_type: SuspensionRuleType | None = None,
        status: SuspensionRuleStatus | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[SuspensionRuleSet], int]:
        stmt = select(SuspensionRuleSet)
        if scope_type is not None:
            stmt = stmt.where(SuspensionRuleSet.scope_type == scope_type.value)
        if scope_org_id is not None:
            stmt = stmt.where(SuspensionRuleSet.scope_org_id == scope_org_id)
        if rule_type is not None:
            stmt = stmt.where(SuspensionRuleSet.rule_type == rule_type.value)
        if status is not None:
            stmt = stmt.where(SuspensionRuleSet.status == status.value)
        rows = await self._session.execute(stmt.order_by(SuspensionRuleSet.created_at.desc()))
        all_items = list(rows.scalars().all())
        total = len(all_items)
        start = (page - 1) * size
        return all_items[start : start + size], total

    async def get_rule_set(self, rule_set_id: str) -> SuspensionRuleSet:
        return await self._rule_set_repo.get_by_id_with_conditions_or_404(rule_set_id)

    async def get_effective_rule_sets_for_org(
        self,
        organization_id: str,
        *,
        rule_type: SuspensionRuleType | None = None,
    ) -> list[SuspensionRuleSet]:
        """Return suspension rule rows **included in evaluation** for ``organization_id``.

        Loads **ACTIVE-only** overlay semantics via :meth:`_effective_rule_sets_with_source_for_org`.
        Same logical rows drive REST ``GET …/effective-rule-sets/{org_id}``, organisations suspension GET,
        and :meth:`run_daily_suspension_job` (no duplicated resolver elsewhere).
        """
        items = [row["rule_set"] for row in await self.get_effective_rule_sets_with_source_for_org(organization_id)]
        if rule_type is not None:
            return [r for r in items if r.rule_type == rule_type.value]
        return items

    async def get_effective_rule_sets_with_source_for_org(
        self,
        organization_id: str,
        *,
        rule_type: SuspensionRuleType | None = None,
    ) -> list[dict[str, Any]]:
        """Resolve **effective** rules per org: ACTIVE overlay only (DEFAULT / CUSTOMISED / NEW metadata).

        Single choke-point backing evaluator-backed callers—same resolver path as the scheduled job's
        :meth:`_effective_rule_sets_for_org`.
        """
        rows = await self._effective_rule_sets_with_source_for_org(organization_id)
        if rule_type is not None:
            return [r for r in rows if r["rule_set"].rule_type == rule_type.value]
        return rows

    async def get_org_applicable_rule_sets_with_source_for_org(
        self,
        organization_id: str,
        *,
        rule_type: SuspensionRuleType | None = None,
    ) -> list[dict[str, Any]]:
        """Admin/inventory view for one organisation—not used by runtime evaluation.

        Loads GLOBAL templates plus ORG rows for this org (ACTIVE and INACTIVE). Compared to listing raw
        globals, **DEFAULT rows are omitted** when an **ACTIVE** CUSTOMISED org rule exists for the same
        ``parent_global_rule_set_id``—the customised row represents that template for Screen B inventory.

        ``is_effective_for_org`` mirrors membership in :meth:`get_effective_rule_sets_with_source_for_org`:
        True iff this physical ``SuspensionRuleSet`` row appears in the **evaluation overlay**.

        Evaluation (:meth:`run_daily_suspension_job`, enforcement endpoints) continues to use
        **effective only**, never this inventory helper.
        """
        await self._validate_scope(scope_type=RuleScopeType.ORG.value, scope_org_id=organization_id)
        effective_rows = await self.get_effective_rule_sets_with_source_for_org(
            organization_id, rule_type=rule_type
        )
        effective_ids = {str(row["rule_set"].id) for row in effective_rows}

        stmt = (
            select(SuspensionRuleSet)
            .where(
                or_(
                    SuspensionRuleSet.scope_type == RuleScopeType.GLOBAL.value,
                    and_(
                        SuspensionRuleSet.scope_type == RuleScopeType.ORG.value,
                        SuspensionRuleSet.scope_org_id == organization_id,
                    ),
                )
            )
            .options(selectinload(SuspensionRuleSet.conditions))
        )
        if rule_type is not None:
            stmt = stmt.where(SuspensionRuleSet.rule_type == rule_type.value)
        result = await self._session.execute(stmt)
        all_rules = list(result.unique().scalars().all())
        grouped: dict[str, list[SuspensionRuleSet]] = {}
        for rule in all_rules:
            grouped.setdefault(rule.rule_type, []).append(rule)

        resolved: list[dict[str, Any]] = []
        for rt in sorted(grouped.keys()):
            rules = grouped[rt]
            org_rules = [r for r in rules if r.scope_type == RuleScopeType.ORG.value and r.scope_org_id == organization_id]
            global_rules = sorted(
                [r for r in rules if r.scope_type == RuleScopeType.GLOBAL.value],
                key=lambda r: (r.updated_at, r.created_at),
                reverse=True,
            )
            customised_rules = sorted(
                [r for r in org_rules if r.parent_global_rule_set_id],
                key=lambda r: (r.updated_at, r.created_at),
                reverse=True,
            )
            new_rules = sorted(
                [r for r in org_rules if not r.parent_global_rule_set_id],
                key=lambda r: (r.updated_at, r.created_at),
                reverse=True,
            )
            active_custom_parent_global_ids = {
                str(r.parent_global_rule_set_id)
                for r in org_rules
                if r.parent_global_rule_set_id and r.status == SuspensionRuleStatus.ACTIVE.value
            }
            for global_rule in global_rules:
                rid = str(global_rule.id)
                if rid in active_custom_parent_global_ids:
                    continue
                resolved.append(
                    {
                        "rule_set": global_rule,
                        "is_override": False,
                        "source_scope_type": RuleScopeType.GLOBAL.value,
                        "source_rule_set_id": global_rule.id,
                        "global_rule_set_id": global_rule.id,
                        "rule_kind": "DEFAULT",
                        "is_effective_for_org": rid in effective_ids,
                    }
                )
            for org_rule in customised_rules:
                rid = str(org_rule.id)
                resolved.append(
                    {
                        "rule_set": org_rule,
                        "is_override": True,
                        "source_scope_type": RuleScopeType.ORG.value,
                        "source_rule_set_id": org_rule.id,
                        "global_rule_set_id": org_rule.parent_global_rule_set_id,
                        "rule_kind": "CUSTOMISED",
                        "is_effective_for_org": rid in effective_ids,
                    }
                )
            for org_rule in new_rules:
                rid = str(org_rule.id)
                resolved.append(
                    {
                        "rule_set": org_rule,
                        "is_override": True,
                        "source_scope_type": RuleScopeType.ORG.value,
                        "source_rule_set_id": org_rule.id,
                        "global_rule_set_id": None,
                        "rule_kind": "NEW",
                        "is_effective_for_org": rid in effective_ids,
                    }
                )
        return resolved

    async def create_rule_set(
        self,
        *,
        payload: dict[str, Any],
        conditions: list[dict[str, Any]],
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> SuspensionRuleSet:
        await self._validate_scope(
            scope_type=payload.get("scope_type"),
            scope_org_id=payload.get("scope_org_id"),
        )
        await self._validate_parent_global_link(payload)
        self._ensure_unique_condition_types(conditions)
        rule = await self._rule_set_repo.create(payload)
        await self._rule_condition_repo.replace_for_ruleset(rule.id, conditions)
        await self._log_audit(
            action="suspension_rule_set.create",
            entity_id=rule.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={"name": rule.name, "scope_type": rule.scope_type, "rule_type": rule.rule_type},
            severity="NOTICE",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.CREDIT_TERMS_MODIFIED,
        )
        await self._session.commit()
        return await self._rule_set_repo.get_by_id_with_conditions_or_404(str(rule.id))

    async def update_rule_set(
        self,
        *,
        rule_set_id: str,
        payload: dict[str, Any],
        conditions: list[dict[str, Any]] | None = None,
        expected_version: int | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> SuspensionRuleSet:
        existing = await self._rule_set_repo.get_by_id_or_404(rule_set_id)
        if "scope_type" in payload or "scope_org_id" in payload:
            raise ValidationError("scope_type and scope_org_id cannot be updated once a rule set is created.")
        if "parent_global_rule_set_id" in payload:
            raise ValidationError("parent_global_rule_set_id cannot be updated once a rule set is created.")
        await self._validate_scope(scope_type=existing.scope_type, scope_org_id=existing.scope_org_id)
        if conditions is not None:
            self._ensure_unique_condition_types(conditions)
        updated = await self._rule_set_repo.update_by_id(rule_set_id, payload, expected_version=expected_version)
        if conditions is not None:
            await self._rule_condition_repo.replace_for_ruleset(rule_set_id, conditions)
        await self._log_audit(
            action="suspension_rule_set.update",
            entity_id=rule_set_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={"name": existing.name, "status": existing.status},
            new_value={"name": updated.name, "status": updated.status},
            severity="NOTICE",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.CREDIT_TERMS_MODIFIED,
        )
        await self._session.commit()
        return await self._rule_set_repo.get_by_id_with_conditions_or_404(rule_set_id)

    async def create_customised_rule_from_global(
        self,
        *,
        organization_id: str,
        global_rule_set_id: str,
        payload: dict[str, Any],
        conditions: list[dict[str, Any]] | None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> SuspensionRuleSet:
        await self._validate_scope(scope_type=RuleScopeType.ORG.value, scope_org_id=organization_id)
        global_rule = await self._rule_set_repo.get_by_id_with_conditions_or_404(global_rule_set_id)
        if global_rule.scope_type != RuleScopeType.GLOBAL.value:
            raise ValidationError("global_rule_set_id must reference a GLOBAL rule set.")

        existing_active = await self._rule_set_repo.find_active_customised_by_parent(
            organization_id=organization_id,
            parent_global_rule_set_id=global_rule_set_id,
        )
        if existing_active is not None:
            raise ConflictError("An active customised rule already exists for this global rule and organization.")

        requested_name = payload.get("name")
        if requested_name:
            resolved_name = str(requested_name)
        else:
            base_name = f"{global_rule.name.strip()}-org-{organization_id[:8]}"
            resolved_name = base_name[:255]
            counter = 2
            while await self._rule_set_repo.find_one(name=resolved_name):
                suffix = f"-{counter}"
                resolved_name = f"{base_name[: max(1, 255 - len(suffix))]}{suffix}"
                counter += 1

        create_payload = {
            "name": resolved_name,
            "condition_summary": payload.get("condition_summary", global_rule.condition_summary),
            "scope_type": RuleScopeType.ORG.value,
            "scope_org_id": organization_id,
            "parent_global_rule_set_id": global_rule.id,
            "rule_type": global_rule.rule_type,
            "status": payload.get("status", global_rule.status),
            "notes": payload.get("notes", global_rule.notes),
            "auto_suspension_enabled": payload.get("auto_suspension_enabled", global_rule.auto_suspension_enabled),
            "pause_new_bookings": payload.get("pause_new_bookings", global_rule.pause_new_bookings),
            "restrict_portal_login": payload.get("restrict_portal_login", global_rule.restrict_portal_login),
            "notify_finance_team": payload.get("notify_finance_team", global_rule.notify_finance_team),
            "notify_account_manager": payload.get("notify_account_manager", global_rule.notify_account_manager),
        }
        cloned_conditions = [
            {
                "position": cond.position,
                "connector": cond.connector,
                "condition_type": cond.condition_type,
                "threshold_value": cond.threshold_value,
                "unit": cond.unit,
            }
            for cond in sorted(global_rule.conditions, key=lambda c: c.position)
        ]
        final_conditions = conditions if conditions is not None else cloned_conditions
        if not final_conditions:
            raise ValidationError("conditions are required to create a customised rule.")
        return await self.create_rule_set(
            payload=create_payload,
            conditions=final_conditions,
            audit_user_id=audit_user_id,
            audit_user_role=audit_user_role,
        )

    async def set_org_rule_status(
        self,
        *,
        organization_id: str,
        rule_set_id: str,
        status: SuspensionRuleStatus,
        expected_version: int | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> SuspensionRuleSet:
        row = await self._rule_set_repo.get_by_id_or_404(rule_set_id)
        if row.scope_type != RuleScopeType.ORG.value or row.scope_org_id != organization_id:
            raise ValidationError("Only ORG-scoped rules for this organization can be toggled.")
        return await self.update_rule_set(
            rule_set_id=rule_set_id,
            payload={"status": status.value},
            expected_version=expected_version,
            audit_user_id=audit_user_id,
            audit_user_role=audit_user_role,
        )

    async def restore_default_for_customised_rule(
        self,
        *,
        organization_id: str,
        rule_set_id: str,
        expected_version: int | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> SuspensionRuleSet:
        row = await self._rule_set_repo.get_by_id_or_404(rule_set_id)
        if row.scope_type != RuleScopeType.ORG.value or row.scope_org_id != organization_id:
            raise ValidationError("restore-default is only allowed for ORG-scoped rules in this organization.")
        if not row.parent_global_rule_set_id:
            raise ValidationError("Only customised rules can be restored to default.")
        parent_id = str(row.parent_global_rule_set_id)
        parent = await self._rule_set_repo.get_by_id_with_conditions_or_404(parent_id)
        if parent.scope_type != RuleScopeType.GLOBAL.value:
            raise ValidationError("Linked parent default rule is invalid.")
        if expected_version is not None and row.version != expected_version:
            raise ConflictError("suspension_rule_sets was modified by another request.")
        await self._log_audit(
            action="suspension_rule_set.restore_default",
            entity_id=rule_set_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={
                "custom_rule_set_id": rule_set_id,
                "custom_rule_name": row.name,
                "parent_global_rule_set_id": parent_id,
            },
            new_value={"restored_global_rule_set_id": parent_id},
            severity="NOTICE",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.CREDIT_TERMS_MODIFIED,
        )
        await self._rule_set_repo.hard_delete(rule_set_id)
        await self._session.commit()
        return parent

    async def list_org_global_suppressions(self, organization_id: str) -> list[str]:
        """GLOBAL rule-set ids opted out for this organisation (effective resolver hides them as DEFAULT)."""
        await self._validate_scope(scope_type=RuleScopeType.ORG.value, scope_org_id=organization_id)
        ids = await self._suppression_repo.global_ids_suppressed_for_org(organization_id)
        return sorted(ids)

    async def set_org_global_suppression(
        self,
        *,
        organization_id: str,
        global_rule_set_id: str,
        suppressed: bool,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> None:
        """Persist or remove per-org opt-out for a GLOBAL template row."""
        await self._validate_scope(scope_type=RuleScopeType.ORG.value, scope_org_id=organization_id)
        global_rule = await self._rule_set_repo.get_by_id_or_404(global_rule_set_id)
        if global_rule.scope_type != RuleScopeType.GLOBAL.value:
            raise ValidationError("global_rule_set_id must reference a GLOBAL rule set.")
        if suppressed:
            await self._suppression_repo.upsert_row(organization_id, global_rule_set_id)
        else:
            await self._suppression_repo.delete_row(organization_id, global_rule_set_id)
        await self._log_audit(
            action="suspension_global_suppression.set",
            entity_id=global_rule_set_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={
                "organization_id": organization_id,
                "global_rule_set_id": global_rule_set_id,
                "suppressed": suppressed,
            },
            severity="NOTICE",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.CREDIT_TERMS_MODIFIED,
        )
        await self._session.commit()

    async def upsert_org_rule_override(
        self,
        *,
        organization_id: str,
        rule_type: SuspensionRuleType,
        payload: dict[str, Any],
        conditions: list[dict[str, Any]] | None,
        expected_version: int | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> SuspensionRuleSet:
        await self._validate_scope(scope_type=RuleScopeType.ORG.value, scope_org_id=organization_id)
        # Multi-ruleset mode: "upsert" updates the most recently updated ORG ruleset
        # for this org+rule_type to preserve backward compatibility.
        existing = await self._rule_set_repo.find_latest_by_scope_and_type(
            scope_type=RuleScopeType.ORG.value,
            scope_org_id=organization_id,
            rule_type=rule_type.value,
        )
        if existing is not None:
            try:
                return await self.update_rule_set(
                    rule_set_id=existing.id,
                    payload=payload,
                    conditions=conditions,
                    expected_version=expected_version,
                    audit_user_id=audit_user_id,
                    audit_user_role=audit_user_role,
                )
            except ConflictError:
                # Upsert semantics: if caller sent a stale version, retry once using
                # latest row state for this exact org+rule_type override.
                return await self.update_rule_set(
                    rule_set_id=existing.id,
                    payload=payload,
                    conditions=conditions,
                    expected_version=None,
                    audit_user_id=audit_user_id,
                    audit_user_role=audit_user_role,
                )

        source_rows = await self._effective_rule_sets_with_source_for_org(organization_id)
        matching_rows = [r for r in source_rows if r["rule_set"].rule_type == rule_type.value]
        source = (
            sorted(
                matching_rows,
                key=lambda r: (r["rule_set"].updated_at, r["rule_set"].created_at),
                reverse=True,
            )[0]
            if matching_rows
            else None
        )
        source_rule: SuspensionRuleSet | None = source["rule_set"] if source else None
        requested_name = payload.get("name")
        if requested_name:
            resolved_name = str(requested_name)
        else:
            base_name = (source_rule.name if source_rule else f"{rule_type.value}-org-rule").strip()
            base_name = f"{base_name}-org-{organization_id[:8]}"
            resolved_name = base_name[:255]
            counter = 2
            while await self._rule_set_repo.find_one(name=resolved_name):
                suffix = f"-{counter}"
                resolved_name = f"{base_name[: max(1, 255 - len(suffix))]}{suffix}"
                counter += 1

        create_payload = {
            "name": resolved_name,
            "condition_summary": payload.get("condition_summary", source_rule.condition_summary if source_rule else None),
            "scope_type": RuleScopeType.ORG.value,
            "scope_org_id": organization_id,
            "parent_global_rule_set_id": (
                source_rule.id
                if source_rule is not None and source_rule.scope_type == RuleScopeType.GLOBAL.value
                else None
            ),
            "rule_type": rule_type.value,
            "status": payload.get("status", source_rule.status if source_rule else SuspensionRuleStatus.ACTIVE.value),
            "notes": payload.get("notes", source_rule.notes if source_rule else None),
            "auto_suspension_enabled": payload.get(
                "auto_suspension_enabled",
                source_rule.auto_suspension_enabled if source_rule is not None else False,
            ),
            "pause_new_bookings": payload.get(
                "pause_new_bookings",
                source_rule.pause_new_bookings if source_rule is not None else False,
            ),
            "restrict_portal_login": payload.get(
                "restrict_portal_login",
                source_rule.restrict_portal_login if source_rule is not None else False,
            ),
            "notify_finance_team": payload.get(
                "notify_finance_team",
                source_rule.notify_finance_team if source_rule is not None else False,
            ),
            "notify_account_manager": payload.get(
                "notify_account_manager",
                source_rule.notify_account_manager if source_rule is not None else False,
            ),
        }
        source_conditions: list[dict[str, Any]] = []
        if source_rule is not None:
            source_conditions = [
                {
                    "position": cond.position,
                    "connector": cond.connector,
                    "condition_type": cond.condition_type,
                    "threshold_value": cond.threshold_value,
                    "unit": cond.unit,
                }
                for cond in sorted(source_rule.conditions, key=lambda c: c.position)
            ]
        final_conditions = conditions if conditions is not None else source_conditions
        if not final_conditions:
            raise ValidationError("conditions are required when no source rule exists for this rule_type")
        return await self.create_rule_set(
            payload=create_payload,
            conditions=final_conditions,
            audit_user_id=audit_user_id,
            audit_user_role=audit_user_role,
        )

    async def _validate_scope(self, *, scope_type: str | None, scope_org_id: str | None) -> None:
        if scope_type not in {RuleScopeType.GLOBAL.value, RuleScopeType.ORG.value}:
            raise ValidationError("scope_type must be GLOBAL or ORG")
        if scope_type == RuleScopeType.GLOBAL.value:
            if scope_org_id is not None:
                raise ValidationError("scope_org_id must be null for GLOBAL rules")
            return
        # ORG scope
        if not scope_org_id:
            raise ValidationError("scope_org_id is required when scope_type=ORG")
        org = await self._session.get(Organization, scope_org_id)
        if org is None:
            raise ValidationError("scope_org_id references a non-existent organization")

    async def _validate_parent_global_link(self, payload: dict[str, Any]) -> None:
        parent_id_raw = payload.get("parent_global_rule_set_id")
        if parent_id_raw is None:
            return
        parent_id = str(parent_id_raw)
        if payload.get("scope_type") != RuleScopeType.ORG.value:
            raise ValidationError("parent_global_rule_set_id is only allowed for ORG-scoped rules.")
        parent = await self._rule_set_repo.get_by_id_or_404(parent_id)
        if parent.scope_type != RuleScopeType.GLOBAL.value:
            raise ValidationError("parent_global_rule_set_id must reference a GLOBAL rule set.")
        if payload.get("rule_type") != parent.rule_type:
            raise ValidationError("Customised rule must keep the same rule_type as its parent global rule.")

    async def delete_rule_set(
        self,
        *,
        rule_set_id: str,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> None:
        existing = await self._rule_set_repo.get_by_id_or_404(rule_set_id)
        await self._log_audit(
            action="suspension_rule_set.delete",
            entity_id=rule_set_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={"name": existing.name, "status": existing.status},
            severity="WARNING",
            category=AuditCategory.SECURITY,
            event_type=AuditEventType.CREDIT_TERMS_MODIFIED,
        )
        await self._rule_set_repo.hard_delete(rule_set_id)
        await self._session.commit()

    async def delete_org_rule_override(
        self,
        *,
        organization_id: str,
        rule_type: SuspensionRuleType,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> None:
        """Delete the ORG-scoped override for one rule type.

        Raises NotFoundError if no override exists (org is already using the global default).
        """
        existing = await self._rule_set_repo.find_one(
            scope_type=RuleScopeType.ORG.value,
            scope_org_id=organization_id,
            rule_type=rule_type.value,
        )
        if existing is None:
            raise NotFoundError(f"No override found for rule_type={rule_type.value} on organisation {organization_id}")
        await self.delete_rule_set(
            rule_set_id=existing.id,
            audit_user_id=audit_user_id,
            audit_user_role=audit_user_role,
        )

    async def list_activity(
        self,
        *,
        account_id: str | None = None,
        rule_set_id: str | None = None,
        rule_id: str | None = None,
        organization_id: str | None = None,
        rule_type: str | None = None,
        payment_model: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[SuspensionActivity], int]:
        stmt = select(SuspensionActivity)
        count_stmt = select(func.count()).select_from(SuspensionActivity)
        where_clauses = []
        if account_id:
            where_clauses.append(SuspensionActivity.account_id == account_id)
        if rule_set_id:
            where_clauses.append(SuspensionActivity.rule_set_id == rule_set_id)
        if rule_id:
            where_clauses.append(SuspensionActivity.rule_set_id == rule_id)
        if organization_id:
            where_clauses.append(SuspensionActivity.organization_id == organization_id)
        if rule_type:
            where_clauses.append(SuspensionActivity.rule_type == rule_type)
        if payment_model:
            where_clauses.append(SuspensionActivity.payment_model == payment_model)

        for clause in where_clauses:
            stmt = stmt.where(clause)
            count_stmt = count_stmt.where(clause)

        stmt = stmt.order_by(SuspensionActivity.created_at.desc()).offset((page - 1) * size).limit(size)
        rows = await self._session.execute(stmt)
        items = list(rows.scalars().all())
        total_rows = await self._session.execute(count_stmt)
        total = int(total_rows.scalar_one() or 0)
        return items, total

    async def list_activity_v2(
        self,
        *,
        account_id: str | None = None,
        rule_set_id: str | None = None,
        rule_id: str | None = None,
        organization_id: str | None = None,
        rule_type: str | None = None,
        payment_model: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        items, total = await self.list_activity(
            account_id=account_id,
            rule_set_id=rule_set_id,
            rule_id=rule_id,
            organization_id=organization_id,
            rule_type=rule_type,
            payment_model=payment_model,
            page=page,
            size=size,
        )
        account_ids = {it.account_id for it in items if it.account_id}
        org_ids = {it.organization_id for it in items if it.organization_id}

        user_map: dict[str, User] = {}
        if account_ids:
            user_rows = await self._session.execute(select(User).where(User.id.in_(account_ids)))
            for user in user_rows.scalars().all():
                user_map[user.id] = user

        org_map: dict[str, Organization] = {}
        if org_ids:
            org_rows = await self._session.execute(select(Organization).where(Organization.id.in_(org_ids)))
            for org in org_rows.scalars().all():
                org_map[org.id] = org

        rows: list[dict[str, Any]] = []
        for it in items:
            user = user_map.get(it.account_id)
            org = org_map.get(it.organization_id) if it.organization_id else None
            client_name = user.full_name if user else (getattr(org, "trading_name", None) if org else None)
            client_email = user.email if user else None
            rows.append(
                {
                    "id": it.id,
                    "timestamp": it.created_at,
                    "rule_set_id": it.rule_set_id,
                    "rule_id": it.rule_set_id,
                    "rule_name": it.rule_name_snapshot,
                    "rule_type": it.rule_type,
                    "payment_model": it.payment_model,
                    "organization_id": it.organization_id,
                    "account_id": it.account_id,
                    "client_name": client_name,
                    "client_email": client_email,
                    "conditions_met": it.conditions_met,
                    "action_taken": it.action_taken,
                    "notification_status": it.notification_status,
                    "notes": it.notes,
                }
            )
        return rows, total

    # ---------------------------------------------------------------------
    # Evaluation runtime
    # ---------------------------------------------------------------------
    async def run_daily_suspension_job(
        self,
        *,
        batch_size: int = 1000,
        today: date | None = None,
        commit: bool = True,
    ) -> None:
        """Run suspension evaluation using **effective (ACTIVE-only)** rows—never inventory/applicable APIs."""
        run_date = today or date.today()
        run = await self._run_repo.create({"run_date": run_date.isoformat()})
        try:
            org_ids = await self._active_org_ids()
            metrics_by_org = await self._build_metrics(org_ids, today=run_date)
            matched = warned = suspended = failed = 0

            for org_id in org_ids:
                rules = await self._effective_rule_sets_for_org(org_id)
                if not rules:
                    continue
                decisions = self._evaluate_rule_sets(org_id, rules, metrics_by_org.get(org_id, {}))
                matched += len(decisions)
                outcome = await self._apply_decisions_for_org(org_id, decisions=decisions, run_id=run.id, commit=False)
                warned += outcome["warned"]
                suspended += outcome["suspended"]
                failed += outcome["failed"]

            run.status = "COMPLETED"
            run.completed_at = datetime.utcnow()
            run.evaluated_count = len(org_ids)
            run.matched_count = matched
            run.warned_count = warned
            run.suspended_count = suspended
            run.failed_count = failed
            if commit:
                await self._session.commit()
            else:
                await self._session.flush()
        except Exception:
            run.status = "FAILED"
            run.completed_at = datetime.utcnow()
            run.failed_count = (run.failed_count or 0) + 1
            await self._session.flush()
            if commit:
                await self._session.commit()
            raise

    async def create_payment_risk_event(
        self,
        *,
        organization_id: str,
        customer_id: str | None,
        order_id: str | None,
        payment_model: PaymentModel,
        event_type: str,
        occurred_on: date | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PaymentRiskEvent:
        event = await self._risk_repo.create(
            {
                "organization_id": organization_id,
                "customer_id": customer_id,
                "order_id": order_id,
                "payment_model": payment_model.value,
                "event_type": event_type,
                "occurred_on": occurred_on or date.today(),
                "rule_metadata": metadata or {},
            }
        )
        await self._session.commit()
        return event

    async def _active_org_ids(self) -> list[str]:
        stmt = select(Organization.id).where(Organization.status == OrganizationStatus.ACTIVE)
        rows = await self._session.execute(stmt)
        return [r[0] for r in rows.all()]

    async def _effective_rule_sets_for_org(self, organization_id: str) -> list[SuspensionRuleSet]:
        """Rule instances fed into suspension evaluation for one org (ACTIVE overlay only)."""
        rows = await self._effective_rule_sets_with_source_for_org(organization_id)
        return [row["rule_set"] for row in rows]

    async def _effective_rule_sets_with_source_for_org(self, organization_id: str) -> list[dict[str, Any]]:
        """Single resolver for ACTIVE overlay semantics—scheduled job + REST effective endpoints."""
        junction_hidden = await self._suppression_repo.global_ids_suppressed_for_org(organization_id)
        stmt = select(SuspensionRuleSet).where(SuspensionRuleSet.status == SuspensionRuleStatus.ACTIVE.value)
        rows = await self._session.execute(stmt)
        all_rules = list(rows.scalars().all())
        grouped: dict[str, list[SuspensionRuleSet]] = {}
        for rule in all_rules:
            grouped.setdefault(rule.rule_type, []).append(rule)
        resolved: list[dict[str, Any]] = []
        for _, rules in grouped.items():
            org_rules = [r for r in rules if r.scope_type == RuleScopeType.ORG.value and r.scope_org_id == organization_id]
            global_rules = sorted(
                [r for r in rules if r.scope_type == RuleScopeType.GLOBAL.value],
                key=lambda r: (r.updated_at, r.created_at),
                reverse=True,
            )
            customised_rules = sorted(
                [r for r in org_rules if r.parent_global_rule_set_id],
                key=lambda r: (r.updated_at, r.created_at),
                reverse=True,
            )
            new_rules = sorted(
                [r for r in org_rules if not r.parent_global_rule_set_id],
                key=lambda r: (r.updated_at, r.created_at),
                reverse=True,
            )
            suppressed_global_ids = {str(r.parent_global_rule_set_id) for r in customised_rules if r.parent_global_rule_set_id}
            visible_globals = [r for r in global_rules if r.id not in suppressed_global_ids]
            visible_globals = [r for r in visible_globals if r.id not in junction_hidden]

            for global_rule in visible_globals:
                resolved.append(
                    {
                        "rule_set": global_rule,
                        "is_override": False,
                        "source_scope_type": RuleScopeType.GLOBAL.value,
                        "source_rule_set_id": global_rule.id,
                        "global_rule_set_id": global_rule.id,
                        "rule_kind": "DEFAULT",
                    }
                )
            for org_rule in customised_rules:
                resolved.append(
                    {
                        "rule_set": org_rule,
                        "is_override": True,
                        "source_scope_type": RuleScopeType.ORG.value,
                        "source_rule_set_id": org_rule.id,
                        "global_rule_set_id": org_rule.parent_global_rule_set_id,
                        "rule_kind": "CUSTOMISED",
                    }
                )
            for org_rule in new_rules:
                resolved.append(
                    {
                        "rule_set": org_rule,
                        "is_override": True,
                        "source_scope_type": RuleScopeType.ORG.value,
                        "source_rule_set_id": org_rule.id,
                        "global_rule_set_id": None,
                        "rule_kind": "NEW",
                    }
                )
        return resolved

    async def _build_metrics(self, organization_ids: list[str], *, today: date) -> dict[str, dict[str, float]]:
        metrics: dict[str, dict[str, float]] = {org_id: {} for org_id in organization_ids}
        if not organization_ids:
            return metrics

        latest_alloc_subq = (
            select(
                BillingPaymentAllocation.payment_id,
                BillingPaymentAllocation.invoice_id,
                func.max(BillingPaymentAllocation.revision_no).label("max_revision_no"),
            )
            .group_by(BillingPaymentAllocation.payment_id, BillingPaymentAllocation.invoice_id)
            .subquery()
        )
        paid_subq = (
            select(
                BillingPaymentAllocation.invoice_id,
                func.coalesce(func.sum(BillingPaymentAllocation.allocated_amount), 0).label("paid_total"),
            )
            .select_from(BillingPaymentAllocation)
            .join(
                latest_alloc_subq,
                and_(
                    BillingPaymentAllocation.payment_id == latest_alloc_subq.c.payment_id,
                    BillingPaymentAllocation.invoice_id == latest_alloc_subq.c.invoice_id,
                    BillingPaymentAllocation.revision_no == latest_alloc_subq.c.max_revision_no,
                ),
            )
            .group_by(BillingPaymentAllocation.invoice_id)
            .subquery()
        )
        credit_subq = (
            select(
                InvoiceCreditApplication.invoice_id,
                func.coalesce(func.sum(InvoiceCreditApplication.applied_amount), 0).label("credit_total"),
            )
            .group_by(InvoiceCreditApplication.invoice_id)
            .subquery()
        )
        outcome_ts_subq = (
            select(
                InvoiceEvent.invoice_id,
                func.max(InvoiceEvent.created_at).label("max_created_at"),
            )
            .where(InvoiceEvent.event_type.in_(["VOIDED", "WRITTEN_OFF"]))
            .group_by(InvoiceEvent.invoice_id)
            .subquery()
        )
        outcome_subq = (
            select(
                InvoiceEvent.invoice_id,
                InvoiceEvent.event_type.label("outcome_event_type"),
            )
            .select_from(InvoiceEvent)
            .join(
                outcome_ts_subq,
                and_(
                    InvoiceEvent.invoice_id == outcome_ts_subq.c.invoice_id,
                    InvoiceEvent.created_at == outcome_ts_subq.c.max_created_at,
                ),
            )
            .subquery()
        )

        invoice_stmt = (
            select(
                Invoice.organization_id,
                Invoice.due_date,
                Invoice.total,
                func.coalesce(paid_subq.c.paid_total, 0).label("paid_total"),
                func.coalesce(credit_subq.c.credit_total, 0).label("credit_total"),
                outcome_subq.c.outcome_event_type,
                Invoice,
            )
            .outerjoin(paid_subq, paid_subq.c.invoice_id == Invoice.id)
            .outerjoin(credit_subq, credit_subq.c.invoice_id == Invoice.id)
            .outerjoin(outcome_subq, outcome_subq.c.invoice_id == Invoice.id)
            .where(Invoice.organization_id.in_(organization_ids), Invoice.status == "SENT")
        )
        invoice_rows = await self._session.execute(invoice_stmt)
        for org_id, due_date, total, paid_total, credit_total, outcome_event_type, invoice in invoice_rows.all():
            if org_id is None:
                continue
            status = compute_payment_status(
                invoice,
                paid_amount=paid_total or Decimal("0"),
                credit_total=credit_total or Decimal("0"),
                outcome_event_type=outcome_event_type,
            )
            outstanding = max(float((total or Decimal("0")) - (credit_total or Decimal("0")) - (paid_total or Decimal("0"))), 0.0)
            m = metrics.setdefault(org_id, {})
            m.setdefault(_metric_key(SuspensionConditionType.TOTAL_OUTSTANDING_AMOUNT.value), 0.0)
            m.setdefault(_metric_key(SuspensionConditionType.TOTAL_OVERDUE_AMOUNT.value), 0.0)
            m.setdefault(_metric_key(SuspensionConditionType.NUMBER_OF_UNPAID_INVOICES.value), 0.0)
            m.setdefault(_metric_key(SuspensionConditionType.INVOICE_OVERDUE_DAYS.value), 0.0)
            m.setdefault(_metric_key(SuspensionConditionType.CREDIT_NOT_CLEARED_AFTER_DUE_DATE.value), 0.0)
            m.setdefault(_metric_key(SuspensionConditionType.OUTSTANDING_CASH_BALANCE.value), 0.0)
            m.setdefault(_metric_key(SuspensionConditionType.CASH_INVOICE_OVERDUE_DAYS.value), 0.0)
            if status in UNPAID_STATES:
                m[_metric_key(SuspensionConditionType.TOTAL_OUTSTANDING_AMOUNT.value)] += outstanding
                m[_metric_key(SuspensionConditionType.NUMBER_OF_UNPAID_INVOICES.value)] += 1
                m[_metric_key(SuspensionConditionType.OUTSTANDING_CASH_BALANCE.value)] += outstanding
            if due_date and due_date < today and status in UNPAID_STATES:
                overdue_days = float((today - due_date).days)
                m[_metric_key(SuspensionConditionType.TOTAL_OVERDUE_AMOUNT.value)] += outstanding
                m[_metric_key(SuspensionConditionType.INVOICE_OVERDUE_DAYS.value)] = max(
                    m[_metric_key(SuspensionConditionType.INVOICE_OVERDUE_DAYS.value)], overdue_days
                )
                m[_metric_key(SuspensionConditionType.CREDIT_NOT_CLEARED_AFTER_DUE_DATE.value)] = max(
                    m[_metric_key(SuspensionConditionType.CREDIT_NOT_CLEARED_AFTER_DUE_DATE.value)], overdue_days
                )
                m[_metric_key(SuspensionConditionType.CASH_INVOICE_OVERDUE_DAYS.value)] = max(
                    m[_metric_key(SuspensionConditionType.CASH_INVOICE_OVERDUE_DAYS.value)], overdue_days
                )

        credit_limits: dict[str, float] = {}
        payment_limit_stmt = select(OrgPaymentMethod.organization_id, OrgPaymentMethod.credit_limit).where(
            OrgPaymentMethod.organization_id.in_(organization_ids),
            OrgPaymentMethod.payment_model == PaymentModel.CREDIT_ACCOUNT,
        )
        payment_limit_rows = await self._session.execute(payment_limit_stmt)
        for org_id, limit in payment_limit_rows.all():
            if org_id and limit is not None:
                credit_limits[org_id] = float(limit)
        config_limit_stmt = select(OrgCreditConfig.organization_id, OrgCreditConfig.approved_credit_limit).where(
            OrgCreditConfig.organization_id.in_(organization_ids)
        )
        config_limit_rows = await self._session.execute(config_limit_stmt)
        for org_id, limit in config_limit_rows.all():
            if org_id and org_id not in credit_limits and limit is not None:
                credit_limits[org_id] = float(limit)

        for org_id in organization_ids:
            outstanding = metrics[org_id].get(_metric_key(SuspensionConditionType.TOTAL_OUTSTANDING_AMOUNT.value), 0.0)
            limit = credit_limits.get(org_id, 0.0)
            utilization = (outstanding / limit * 100.0) if limit > 0 else 0.0
            metrics[org_id][_metric_key(SuspensionConditionType.CREDIT_UTILIZATION.value)] = round(utilization, 2)

        since_30 = today - timedelta(days=30)
        since_90 = today - timedelta(days=90)
        for org_id in organization_ids:
            fail_count = await self._risk_repo.count_events(
                organization_id=org_id,
                payment_model=PaymentModel.CARD.value,
                event_type="PAYMENT_FAILED",
                since_on=since_30,
            )
            retry_fail_count = await self._risk_repo.count_events(
                organization_id=org_id,
                payment_model=PaymentModel.CARD.value,
                event_type="RETRY_FAILED",
                since_on=since_30,
            )
            chargeback_count = await self._risk_repo.count_events(
                organization_id=org_id,
                payment_model=PaymentModel.CARD.value,
                event_type="CHARGEBACK",
                since_on=since_90,
            )
            recent = await self._risk_repo.recent_events(organization_id=org_id, payment_model=PaymentModel.CARD.value, limit=30)
            consecutive = 0
            for event in recent:
                if event.event_type in {"PAYMENT_FAILED", "RETRY_FAILED"}:
                    consecutive += 1
                elif event.event_type == "PAYMENT_SUCCESS":
                    break

            metrics[org_id][_metric_key(SuspensionConditionType.PAYMENT_FAILURE_COUNT.value)] = float(fail_count)
            metrics[org_id][_metric_key(SuspensionConditionType.PAYMENT_RETRY_FAILURE_COUNT.value)] = float(retry_fail_count)
            metrics[org_id][_metric_key(SuspensionConditionType.CHARGEBACK_TRIGGERED.value)] = 1.0 if chargeback_count > 0 else 0.0
            metrics[org_id][_metric_key(SuspensionConditionType.CONSECUTIVE_PAYMENT_FAILURE.value)] = float(consecutive)

        order_stmt = select(Order.organization_id).where(Order.organization_id.in_(organization_ids))
        order_rows = await self._session.execute(order_stmt)
        for org_id, payment_status in order_rows.all():
            if org_id is None:
                continue
            metrics[org_id].setdefault(_metric_key(SuspensionConditionType.MAX_UNPAID_ORDERS.value), 0.0)
            if str(payment_status).lower() != "paid":
                metrics[org_id][_metric_key(SuspensionConditionType.MAX_UNPAID_ORDERS.value)] += 1
        return metrics

    def _evaluate_rule_sets(self, organization_id: str, rules: list[SuspensionRuleSet], org_metrics: dict[str, float]) -> list[RuleDecision]:
        decisions: list[RuleDecision] = []
        for rule in rules:
            ordered = sorted(rule.conditions, key=lambda c: c.position)
            if not ordered:
                continue
            group_results: list[bool] = []
            expression_parts: list[str] = []
            current_group_result = True
            current_group_expr = ""
            conditions_met: dict[str, Any] = {}
            for idx, cond in enumerate(ordered):
                metric_name = _metric_key(cond.condition_type)
                metric_value = float(org_metrics.get(metric_name, 0.0))
                condition_ok = metric_value >= float(cond.threshold_value)
                conditions_met[metric_name] = metric_value
                token = f"{cond.condition_type}({metric_value}>={float(cond.threshold_value)})"
                connector = cond.connector or SuspensionConnector.NONE.value
                if idx == 0 or connector == SuspensionConnector.NONE.value:
                    current_group_result = condition_ok
                    current_group_expr = token
                elif connector == SuspensionConnector.AND.value:
                    current_group_result = current_group_result and condition_ok
                    current_group_expr = f"{current_group_expr} AND {token}"
                elif connector == SuspensionConnector.OR.value:
                    group_results.append(current_group_result)
                    expression_parts.append(f"({current_group_expr})")
                    current_group_result = condition_ok
                    current_group_expr = token
                else:
                    raise ValidationError(f"Unsupported connector: {connector}")
            group_results.append(current_group_result)
            expression_parts.append(f"({current_group_expr})")
            final_result = any(group_results)
            if not final_result:
                continue
            apply_user_suspension = bool(rule.auto_suspension_enabled)
            block_new_bookings = bool(rule.pause_new_bookings)
            block_portal_login = bool(rule.restrict_portal_login)
            action = "SUSPEND" if (apply_user_suspension or block_new_bookings or block_portal_login) else "WARN_ONLY"
            decisions.append(
                RuleDecision(
                    rule_set_id=rule.id,
                    organization_id=organization_id,
                    action=action,
                    apply_user_suspension=apply_user_suspension,
                    block_new_bookings=block_new_bookings,
                    block_portal_login=block_portal_login,
                    conditions_met=conditions_met,
                    evaluated_expression=" OR ".join(expression_parts),
                    group_results=group_results,
                    final_result=final_result,
                    rule_type=rule.rule_type,
                    payment_model=self._payment_model_for_rule_type(rule.rule_type),
                    rule_name=rule.name,
                    notify_finance_team=rule.notify_finance_team,
                    notify_account_manager=rule.notify_account_manager,
                )
            )
        return decisions

    async def _apply_decisions_for_org(self, organization_id: str, *, decisions: list[RuleDecision], run_id: str, commit: bool) -> dict[str, int]:
        outcome = {"warned": 0, "suspended": 0, "failed": 0}
        if not decisions:
            return outcome
        users_stmt = select(User).where(
            User.organization_id == organization_id,
            User.role == UserRole.CUSTOMER_B2B,
            User.status.in_([UserStatus.ACTIVE, UserStatus.SUSPENDED]),
        )
        users_rows = await self._session.execute(users_stmt)
        users = list(users_rows.scalars().all())
        org = await self._session.get(Organization, organization_id)

        should_suspend_users = any(d.apply_user_suspension for d in decisions)
        should_pause_bookings = any(d.block_new_bookings for d in decisions)
        should_restrict_portal = any(d.block_portal_login for d in decisions)
        should_warn_only = not (should_suspend_users or should_pause_bookings or should_restrict_portal)

        if should_suspend_users:
            outcome["suspended"] += 1
            for user in users:
                if user.status != UserStatus.SUSPENDED:
                    try:
                        await self._user_repo.update_by_id(user.id, {"status": UserStatus.SUSPENDED}, expected_version=user.version)
                        await mark_user_suspended(
                            user.id,
                            ttl_seconds=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
                        )
                    except ConflictError:
                        outcome["failed"] += 1

        if org is not None:
            # Org-level controls are separate from user-level suspension.
            if should_restrict_portal:
                org.status = OrganizationStatus.SUSPENDED
            elif should_pause_bookings or should_suspend_users:
                org.status = OrganizationStatus.ON_HOLD

        if should_warn_only:
            outcome["warned"] += 1

        for decision in decisions:
            activity = SuspensionActivity(
                rule_set_id=decision.rule_set_id,
                rule_name_snapshot=decision.rule_name,
                account_id=users[0].id if users else organization_id,
                organization_id=organization_id,
                rule_type=decision.rule_type,
                payment_model=decision.payment_model,
                run_id=run_id,
                conditions_met=decision.conditions_met,
                action_taken=SuspensionActionTaken.SUSPENDED if decision.action == "SUSPEND" else SuspensionActionTaken.WARNING_SENT,
                notes="Daily suspension job",
                evaluated_expression=decision.evaluated_expression,
                group_results=decision.group_results,
                final_result=decision.final_result,
                notification_status="QUEUED",
            )
            self._session.add(activity)
            await self._session.flush()
            await self._queue_notifications(activity=activity, users=users, decision=decision)

        if commit:
            await self._session.commit()
        else:
            await self._session.flush()
        return outcome

    async def _queue_notifications(self, *, activity: SuspensionActivity, users: list[User], decision: RuleDecision) -> None:
        recipients: list[tuple[str, str]] = []
        if users and decision.action == "WARN_ONLY":
            recipients.append(("customer", users[0].email))
        if decision.notify_finance_team:
            finance_email = (getattr(settings, "FINANCE_TEAM_EMAIL", "") or "").strip()
            if finance_email:
                recipients.append(("finance", finance_email))
        if decision.notify_account_manager:
            manager_email = await self._resolve_account_manager_email(decision.organization_id)
            if manager_email:
                recipients.append(("account_manager", manager_email))
            else:
                owner_emails = await self._resolve_account_owner_emails(decision.organization_id)
                if owner_emails:
                    recipients.extend(("account_owner", email) for email in owner_emails)
                else:
                    finance_fallback = (getattr(settings, "FINANCE_TEAM_EMAIL", "") or "").strip()
                    if finance_fallback:
                        recipients.append(("finance_fallback", finance_fallback))

        seen: set[str] = set()
        deduped_recipients: list[tuple[str, str]] = []
        for recipient_type, email in recipients:
            key = email.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped_recipients.append((recipient_type, email))

        for recipient_type, email in deduped_recipients:
            await enqueue(
                "send_email_task",
                email,
                "Suspension rule triggered",
                template_name=(
                    EmailTemplateName.SUSPENSION_WARNING_B2B.value
                    if recipient_type == "customer"
                    else EmailTemplateName.SUSPENSION_RULE_FIRED_FINANCE.value
                ),
                context={
                    "name": users[0].full_name if users else email,
                    "rule_name": decision.rule_name,
                    "condition_summary": decision.evaluated_expression,
                    "conditions_met_human": ", ".join(f"{k}={v}" for k, v in decision.conditions_met.items()),
                    "action_taken_human": decision.action,
                    "support_email": settings.EMAIL_FROM_ADDRESS,
                },
                _queue=QueuePriority.LOW,
            )
            self._session.add(
                SuspensionNotificationAudit(
                    activity_id=activity.id,
                    channel="EMAIL",
                    recipient=email,
                    status="QUEUED",
                    rule_metadata={"recipient_type": recipient_type},
                )
            )

    async def _resolve_account_manager_email(self, organization_id: str) -> str | None:
        org = await self._session.get(Organization, organization_id)
        if org is None or not org.account_manager_user_id:
            return None
        manager = await self._session.get(User, org.account_manager_user_id)
        if manager is None or not manager.email:
            return None
        return str(manager.email)

    async def _resolve_account_owner_emails(self, organization_id: str) -> list[str]:
        stmt = (
            select(User.email)
            .join(OrgContact, OrgContact.user_id == User.id)
            .where(
                OrgContact.organization_id == organization_id,
                OrgContact.contact_role == ContactRole.ACCOUNT_OWNER,
                User.email.isnot(None),
            )
        )
        rows = await self._session.execute(stmt)
        return [str(email) for email in rows.scalars().all() if email]

    def _payment_model_for_rule_type(self, rule_type: str) -> str:
        if rule_type == SuspensionRuleType.CREDIT_CARD.value:
            return PaymentModel.CARD.value
        if rule_type == SuspensionRuleType.BANK_TRANSFER.value:
            return PaymentModel.BANK_TRANSFER.value
        if rule_type == SuspensionRuleType.CASH.value:
            return PaymentModel.CASH.value
        return PaymentModel.CREDIT_ACCOUNT.value

    # ---------------------------------------------------------------------
    # Mapping helpers
    # ---------------------------------------------------------------------
    def _ensure_unique_condition_types(self, rows: list[dict[str, Any]]) -> None:
        seen: set[str] = set()
        for row in rows:
            ctype = str(row.get("condition_type"))
            if ctype in seen:
                raise ValidationError(f"Condition `{ctype}` can only appear once per rule.")
            seen.add(ctype)
