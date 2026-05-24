from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import AuthUser
from app.common.enums import Job
from app.common.exceptions import ConflictError, NotFoundError, ValidationError
from app.common.service import BaseService
from app.core.queue import QueuePriority, enqueue
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.org_credit.enums import OrgCreditAccountStatus
from app.modules.org_credit.models import OrgCreditAccount
from app.modules.org_credit.repository import OrgCreditAccountRepository
from app.modules.org_credit_alerts.constants import (
    ALLOWED_COOLDOWN_PERIODS,
    DEFAULT_CONFIG,
    REVIEW_REMINDER_MAX_DAYS,
    REVIEW_REMINDER_MIN_DAYS,
)
from app.modules.org_credit_alerts.enums import (
    COOLDOWN_MINUTES,
    SNOOZE_DURATION_HOURS,
    CreditAlertCooldownPeriod,
    CreditAlertDeliveryChannel,
    CreditAlertSeverity,
    CreditAlertSnoozeDuration,
    CreditAlertStatus,
    CreditAlertType,
)
from app.modules.org_credit_alerts.models import OrgCreditAlert, OrgCreditAlertConfig
from app.modules.org_credit_alerts.repository import (
    GlobalCreditAlertThresholdRepository,
    OrgCreditAlertConfigRepository,
    OrgCreditAlertRepository,
)
from app.modules.org_credit_reviews.repository import OrgCreditReviewRepository
from app.modules.organizations.models import Organization
from app.modules.organizations.repository import OrganizationRepository
from app.modules.user.repository import UserRepository

logger = structlog.get_logger()


def _caller_role_str(caller: AuthUser) -> str:
    return caller.role if isinstance(caller.role, str) else caller.role.value


@dataclass(frozen=True, slots=True)
class _EffectiveConfig:
    """Resolved config — either a stored row or the in-memory defaults for a given type."""

    enabled: bool
    threshold_pct: Decimal | None
    score_drop_points: int | None
    reminder_days: int | None
    late_payment_count: int | None
    cooldown_period: CreditAlertCooldownPeriod
    delivery_channel: CreditAlertDeliveryChannel
    auto_acknowledge: bool


class OrgCreditAlertService(BaseService):
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._alert_repo = OrgCreditAlertRepository(session)
        self._config_repo = OrgCreditAlertConfigRepository(session)
        self._global_threshold_repo = GlobalCreditAlertThresholdRepository(session)
        self._account_repo = OrgCreditAccountRepository(session)
        self._review_repo = OrgCreditReviewRepository(session)
        self._org_repo = OrganizationRepository(session)
        self._user_repo = UserRepository(session)
        self._audit = AuditService(session)
        self._ip = request.client.host if request and request.client else None
        self._ua = request.headers.get("user-agent") if request else None

    async def _ensure_org(self, organization_id: str) -> Organization:
        return await self._org_repo.get_by_id_or_404(organization_id)

    async def list_configs(self, organization_id: str) -> list[dict[str, Any]]:
        await self._ensure_org(organization_id)
        existing = {c.alert_type: c for c in await self._config_repo.list_for_org(organization_id)}
        global_thresholds = {g.alert_type: g.threshold_pct for g in await self._global_threshold_repo.list_all()}
        return [self._config_payload(alert_type, existing.get(alert_type), global_thresholds) for alert_type in CreditAlertType]

    def _config_payload(
        self,
        alert_type: CreditAlertType,
        cfg: OrgCreditAlertConfig | None,
        global_thresholds: dict[CreditAlertType, Decimal] | None = None,
    ) -> dict[str, Any]:
        defaults = DEFAULT_CONFIG[alert_type]
        hardcoded_threshold = defaults.get("threshold_pct")

        if cfg is not None:
            # org → global → hardcoded
            threshold_pct = cfg.threshold_pct
            if threshold_pct is None:
                threshold_pct = (global_thresholds or {}).get(alert_type)
            if threshold_pct is None:
                threshold_pct = hardcoded_threshold
            return {
                "alert_type": cfg.alert_type.value,
                "enabled": cfg.enabled,
                "threshold_pct": _decimal_or_none(threshold_pct),
                "score_drop_points": cfg.score_drop_points,
                "reminder_days": cfg.reminder_days,
                "late_payment_count": cfg.late_payment_count,
                "cooldown_period": cfg.cooldown_period.value,
                "delivery_channel": cfg.delivery_channel.value,
                "auto_acknowledge": cfg.auto_acknowledge,
            }

        # No org config: global → hardcoded
        threshold_pct = (global_thresholds or {}).get(alert_type)
        if threshold_pct is None:
            threshold_pct = hardcoded_threshold
        return {
            "alert_type": alert_type.value,
            "enabled": bool(defaults.get("enabled", True)),
            "threshold_pct": _decimal_or_none(threshold_pct),
            "score_drop_points": defaults.get("score_drop_points"),
            "reminder_days": defaults.get("reminder_days"),
            "late_payment_count": defaults.get("late_payment_count"),
            "cooldown_period": defaults["cooldown_period"].value,
            "delivery_channel": defaults["delivery_channel"].value,
            "auto_acknowledge": bool(defaults.get("auto_acknowledge", False)),
        }

    async def upsert_configs(
        self,
        organization_id: str,
        *,
        caller: AuthUser,
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        await self._ensure_org(organization_id)
        current = {c.alert_type: c for c in await self._config_repo.list_for_org(organization_id)}

        updated: list[OrgCreditAlertConfig] = []
        for item in items:
            alert_type: CreditAlertType = item["alert_type"]
            self._validate_config_item(alert_type, item)
            cfg = current.get(alert_type)
            if cfg is None:
                cfg = await self._config_repo.create({
                    "organization_id": organization_id,
                    "alert_type": alert_type,
                    "enabled": item["enabled"],
                    "threshold_pct": item.get("threshold_pct"),
                    "score_drop_points": item.get("score_drop_points"),
                    "reminder_days": item.get("reminder_days"),
                    "late_payment_count": item.get("late_payment_count"),
                    "cooldown_period": item["cooldown_period"],
                    "delivery_channel": item["delivery_channel"],
                    "auto_acknowledge": item["auto_acknowledge"],
                })
            else:
                cfg.enabled = item["enabled"]
                cfg.threshold_pct = item.get("threshold_pct")
                cfg.score_drop_points = item.get("score_drop_points")
                cfg.reminder_days = item.get("reminder_days")
                cfg.late_payment_count = item.get("late_payment_count")
                cfg.cooldown_period = item["cooldown_period"]
                cfg.delivery_channel = item["delivery_channel"]
                cfg.auto_acknowledge = item["auto_acknowledge"]
            updated.append(cfg)

        await self._session.flush()

        await self._audit.log(
            action="org_credit.alert_configuration_updated",
            entity_type="org_credit_alert_config",
            entity_id=organization_id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value={"count": len(updated), "types": [c.alert_type.value for c in updated]},
            ip_address=self._ip,
            user_agent=self._ua,
            reason=f"Updated {len(updated)} credit alert configuration(s)",
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_SETTINGS_UPDATED,
            organization_id=organization_id,
            severity="INFO",
        )
        logger.info("org_credit.alert_configuration_updated", organization_id=organization_id, count=len(updated))
        return [self._config_payload(c.alert_type, c) for c in updated]

    def _validate_config_item(self, alert_type: CreditAlertType, item: dict[str, Any]) -> None:
        allowed_cooldowns = ALLOWED_COOLDOWN_PERIODS.get(alert_type)
        if allowed_cooldowns is not None and item["cooldown_period"] not in allowed_cooldowns:
            raise ValidationError(f"Unsupported cooldown period for {alert_type.value}.")
        if alert_type in {
            CreditAlertType.CREDIT_UTILISATION_MONITORING_WARNING,
            CreditAlertType.CREDIT_UTILISATION_MONITORING_CRITICAL,
        }:
            threshold = item.get("threshold_pct")
            if threshold is None or Decimal(threshold) <= 0 or Decimal(threshold) > 100:
                raise ValidationError("Utilisation threshold must be between 0 and 100.")
        elif alert_type == CreditAlertType.CREDIT_SCORE_DECREASE:
            points = item.get("score_drop_points")
            if points is None or points <= 0:
                raise ValidationError("Credit score decrease requires positive score drop points.")
        elif alert_type == CreditAlertType.SCHEDULED_CREDIT_REVIEW_REMINDER:
            days = item.get("reminder_days")
            if days is None or days < REVIEW_REMINDER_MIN_DAYS or days > REVIEW_REMINDER_MAX_DAYS:
                raise ValidationError(
                    f"Review reminder days must be between {REVIEW_REMINDER_MIN_DAYS} and {REVIEW_REMINDER_MAX_DAYS}.",
                )
        elif alert_type == CreditAlertType.LATE_PAYMENT_BEHAVIOUR:
            count = item.get("late_payment_count")
            if count is None or count <= 0:
                raise ValidationError("Late payment behaviour requires a positive invoice count threshold.")

    async def get_summary(self, organization_id: str) -> dict[str, Any]:
        await self._ensure_org(organization_id)
        active = await self._alert_repo.count_active(organization_id)
        unack = await self._alert_repo.count_unacknowledged(organization_id)
        last = await self._alert_repo.last_triggered_at(organization_id)
        return {
            "active_alerts_count": active,
            "unacknowledged_alerts_count": unack,
            "last_alert_triggered_at": last.isoformat() if last else None,
        }

    async def list_active(self, organization_id: str) -> list[dict[str, Any]]:
        await self._ensure_org(organization_id)
        rows = await self._alert_repo.list_active(organization_id)
        return [self.alert_to_dict(r) for r in rows]

    async def list_active_preview(self, organization_id: str) -> list[dict[str, Any]]:
        await self._ensure_org(organization_id)
        rows = await self._alert_repo.list_active(organization_id, limit=3)
        return [self.alert_to_dict(r) for r in rows]

    async def list_history(
        self,
        organization_id: str,
        *,
        page: int,
        size: int,
        statuses: list[CreditAlertStatus] | None = None,
        alert_types: list[CreditAlertType] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        await self._ensure_org(organization_id)
        rows, total = await self._alert_repo.list_history(
            organization_id,
            page=page,
            size=size,
            statuses=statuses,
            alert_types=alert_types,
        )
        return [self.alert_to_dict(r) for r in rows], total

    async def get_detail(self, organization_id: str, alert_id: str) -> dict[str, Any]:
        await self._ensure_org(organization_id)
        alert = await self._alert_repo.get_with_user(alert_id)
        if alert is None or alert.organization_id != organization_id:
            raise NotFoundError(resource="org_credit_alert", id=alert_id)
        return self.alert_to_dict(alert)

    def alert_to_dict(self, alert: OrgCreditAlert) -> dict[str, Any]:
        ack_user = None
        try:
            u = alert.acknowledged_by
        except Exception:
            u = None
        if u is not None:
            ack_user = {"id": u.id, "first_name": u.first_name, "last_name": u.last_name}
        return {
            "id": alert.id,
            "organization_id": alert.organization_id,
            "alert_type": alert.alert_type.value,
            "severity": alert.severity.value,
            "status": alert.status.value,
            "title": alert.title,
            "summary": alert.summary,
            "context": alert.context,
            "triggered_at": alert.triggered_at.isoformat(),
            "snoozed_until": alert.snoozed_until.isoformat() if alert.snoozed_until else None,
            "acknowledged_at": alert.acknowledged_at.isoformat() if alert.acknowledged_at else None,
            "acknowledged_by": ack_user,
            "resolution_notes": alert.resolution_notes,
            "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
        }

    async def acknowledge(
        self,
        organization_id: str,
        alert_id: str,
        *,
        caller: AuthUser,
        resolution_notes: str | None,
    ) -> dict[str, Any]:
        await self._ensure_org(organization_id)
        alert = await self._alert_repo.get_with_user(alert_id)
        if alert is None or alert.organization_id != organization_id:
            raise NotFoundError(resource="org_credit_alert", id=alert_id)
        if alert.status in (CreditAlertStatus.ACKNOWLEDGED, CreditAlertStatus.AUTO_ACKNOWLEDGED, CreditAlertStatus.RESOLVED):
            raise ConflictError("Alert has already been handled.")

        now = datetime.now(UTC)
        alert.status = CreditAlertStatus.ACKNOWLEDGED
        alert.acknowledged_at = now
        alert.acknowledged_by_user_id = caller.id
        alert.resolution_notes = resolution_notes
        alert.snoozed_until = None
        await self._session.flush()
        await self._session.refresh(alert, attribute_names=["acknowledged_by"])

        await self._audit.log(
            action="org_credit.alert_acknowledged",
            entity_type="org_credit_alert",
            entity_id=alert.id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value={
                "alert_type": alert.alert_type.value,
                "resolution_notes": resolution_notes,
            },
            ip_address=self._ip,
            user_agent=self._ua,
            reason=resolution_notes or f"Acknowledged {alert.alert_type.value.replace('_', ' ').lower()} alert",
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_ALERT_ACKNOWLEDGED,
            organization_id=organization_id,
            severity="NOTICE",
        )
        logger.info("org_credit.alert_acknowledged", organization_id=organization_id, alert_id=alert.id)
        return self.alert_to_dict(alert)

    async def snooze(
        self,
        organization_id: str,
        alert_id: str,
        *,
        caller: AuthUser,
        duration: CreditAlertSnoozeDuration,
    ) -> dict[str, Any]:
        await self._ensure_org(organization_id)
        alert = await self._alert_repo.get_with_user(alert_id)
        if alert is None or alert.organization_id != organization_id:
            raise NotFoundError(resource="org_credit_alert", id=alert_id)
        if alert.status != CreditAlertStatus.ACTIVE and alert.status != CreditAlertStatus.SNOOZED:
            raise ConflictError("Only active alerts can be snoozed.")

        now = datetime.now(UTC)
        hours = SNOOZE_DURATION_HOURS[duration]
        alert.status = CreditAlertStatus.SNOOZED
        snoozed_until = now + timedelta(hours=hours)
        alert.snoozed_until = snoozed_until
        await self._session.flush()

        await self._audit.log(
            action="org_credit.alert_snoozed",
            entity_type="org_credit_alert",
            entity_id=alert.id,
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value={
                "alert_type": alert.alert_type.value,
                "duration": duration.value,
                "snoozed_until": snoozed_until.isoformat(),
            },
            ip_address=self._ip,
            user_agent=self._ua,
            reason=f"Snoozed {alert.alert_type.value.replace('_', ' ').lower()} alert for {duration.value.replace('_', ' ').lower()}",
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_ALERT_SNOOZED,
            organization_id=organization_id,
            severity="INFO",
        )
        logger.info("org_credit.alert_snoozed", organization_id=organization_id, alert_id=alert.id, until=snoozed_until.isoformat())
        return self.alert_to_dict(alert)

    async def auto_unsnooze_due(self) -> int:
        rows = await self._alert_repo.find_snoozed_expired()
        for row in rows:
            row.status = CreditAlertStatus.ACTIVE
            row.snoozed_until = None
        if rows:
            await self._session.flush()
            logger.info("org_credit.alerts_auto_unsnoozed", count=len(rows))
        return len(rows)

    async def _resolve_config(
        self,
        organization_id: str,
        alert_type: CreditAlertType,
    ) -> _EffectiveConfig | None:
        cfg = await self._config_repo.get_for_org_and_type(organization_id, alert_type)
        defaults = DEFAULT_CONFIG.get(alert_type)

        if cfg is not None:
            # org → global → hardcoded
            threshold_pct = cfg.threshold_pct
            if threshold_pct is None:
                global_row = await self._global_threshold_repo.get_by_type(alert_type)
                threshold_pct = global_row.threshold_pct if global_row else None
            if threshold_pct is None and defaults:
                threshold_pct = defaults.get("threshold_pct")
            return _EffectiveConfig(
                enabled=cfg.enabled,
                threshold_pct=threshold_pct,
                score_drop_points=cfg.score_drop_points,
                reminder_days=cfg.reminder_days,
                late_payment_count=cfg.late_payment_count,
                cooldown_period=cfg.cooldown_period,
                delivery_channel=cfg.delivery_channel,
                auto_acknowledge=cfg.auto_acknowledge,
            )

        # No org config
        if defaults is None or not defaults.get("enabled", False):
            return None
        # global → hardcoded
        global_row = await self._global_threshold_repo.get_by_type(alert_type)
        threshold_pct = global_row.threshold_pct if global_row else None
        if threshold_pct is None:
            threshold_pct = defaults.get("threshold_pct")
        return _EffectiveConfig(
            enabled=bool(defaults.get("enabled", True)),
            threshold_pct=threshold_pct,
            score_drop_points=defaults.get("score_drop_points"),
            reminder_days=defaults.get("reminder_days"),
            late_payment_count=defaults.get("late_payment_count"),
            cooldown_period=defaults["cooldown_period"],
            delivery_channel=defaults["delivery_channel"],
            auto_acknowledge=bool(defaults.get("auto_acknowledge", False)),
        )

    async def create_alert(
        self,
        *,
        organization_id: str,
        alert_type: CreditAlertType,
        severity: CreditAlertSeverity,
        title: str,
        summary: str,
        context: dict[str, Any] | None = None,
    ) -> OrgCreditAlert | None:
        """Fire an alert for the given org/type. Returns None if dedupped, cooldown, or disabled."""
        cfg = await self._resolve_config(organization_id, alert_type)
        if cfg is None or not cfg.enabled:
            return None

        open_alert = await self._alert_repo.find_open_for_type(organization_id, alert_type)
        if open_alert is not None:
            return None

        last = await self._alert_repo.find_last_for_type(organization_id, alert_type)
        now = datetime.now(UTC)
        if last is not None and last.cooldown_until is not None and last.cooldown_until > now:
            return None

        cooldown_minutes = COOLDOWN_MINUTES[cfg.cooldown_period]
        auto_ack = cfg.auto_acknowledge
        status = CreditAlertStatus.AUTO_ACKNOWLEDGED if auto_ack else CreditAlertStatus.ACTIVE

        alert = await self._alert_repo.create({
            "organization_id": organization_id,
            "alert_type": alert_type,
            "severity": severity,
            "status": status,
            "title": title,
            "summary": summary,
            "context": context,
            "triggered_at": now,
            "cooldown_until": now + timedelta(minutes=cooldown_minutes),
            "acknowledged_at": now if auto_ack else None,
        })

        await self._audit.log(
            action="org_credit.alert_triggered",
            entity_type="org_credit_alert",
            entity_id=alert.id,
            user_id=None,
            user_role="SYSTEM",
            new_value={
                "alert_type": alert_type.value,
                "severity": severity.value,
                "title": title,
                "auto_acknowledged": auto_ack,
            },
            reason=summary or title,
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_ALERT_TRIGGERED,
            organization_id=organization_id,
            severity="WARNING" if severity == CreditAlertSeverity.WARNING else "CRITICAL" if severity == CreditAlertSeverity.CRITICAL else "INFO",
        )
        logger.info(
            "org_credit.alert_triggered",
            organization_id=organization_id,
            alert_type=alert_type.value,
            severity=severity.value,
            alert_id=alert.id,
        )

        if cfg.delivery_channel in (CreditAlertDeliveryChannel.BOTH, CreditAlertDeliveryChannel.EMAIL_ONLY):
            await enqueue(
                Job.SEND_CREDIT_ALERT_EMAIL,
                alert.id,
                priority=QueuePriority.NOTIFICATIONS,
            )

        return alert

    async def evaluate_for_organization(self, organization_id: str, *, today: date | None = None) -> int:
        """Cron entry point — runs only the **date-driven** alert types.

        Utilisation, limit breach, on-hold, suspended, score drop, rating
        downgrade, and late payment behaviour are now fired inline from the
        services that mutate the underlying state (see
        ``evaluate_after_used_credit_change``, ``evaluate_after_status_change``,
        ``fire_credit_score_drop_alert``, ``fire_rating_downgrade_alert``,
        ``fire_late_payment_alert``).
        """
        today = today or datetime.now(UTC).date()
        account = await self._account_repo.get_by_org_id(organization_id)
        if account is None:
            return 0

        created = 0
        org = await self._org_repo.get_by_id(organization_id)
        org_name = org.trading_name if org else "Organisation"

        if await self._eval_review_reminder(organization_id, account, today, org_name):
            created += 1
        if await self._eval_review_overdue(organization_id, account, today, org_name):
            created += 1
        return created

    async def evaluate_after_used_credit_change(
        self,
        organization_id: str,
        account: OrgCreditAccount,
    ) -> int:
        """Inline entry point after ``used_credit`` / ``credit_limit`` writes.

        Runs the utilisation warn/critical and limit breach evaluators.
        Dedupe + cooldown rules in :meth:`create_alert` make repeat calls safe.
        """
        org = await self._org_repo.get_by_id(organization_id)
        org_name = org.trading_name if org else "Organisation"
        created = 0
        if await self._eval_utilisation(organization_id, account, org_name):
            created += 1
        return created

    async def evaluate_after_status_change(
        self,
        organization_id: str,
        account: OrgCreditAccount,
    ) -> int:
        """Inline entry point after ``OrgCreditAccount.status`` is mutated."""
        org = await self._org_repo.get_by_id(organization_id)
        org_name = org.trading_name if org else "Organisation"
        created = 0
        if await self._eval_account_on_hold(organization_id, account, org_name):
            created += 1
        if await self._eval_account_suspended(organization_id, account, org_name):
            created += 1
        return created

    async def _eval_utilisation(
        self,
        organization_id: str,
        account: OrgCreditAccount,
        org_name: str,
    ) -> bool:
        if not account.credit_limit or account.credit_limit <= 0:
            return False
        util_pct = account.used_credit / account.credit_limit * 100
        critical_cfg = await self._resolve_config(organization_id, CreditAlertType.CREDIT_UTILISATION_MONITORING_CRITICAL)
        warning_cfg = await self._resolve_config(organization_id, CreditAlertType.CREDIT_UTILISATION_MONITORING_WARNING)
        candidates = (
            (critical_cfg, CreditAlertType.CREDIT_UTILISATION_MONITORING_CRITICAL, CreditAlertSeverity.CRITICAL, "Utilisation Critical", Decimal("90")),
            (warning_cfg, CreditAlertType.CREDIT_UTILISATION_MONITORING_WARNING, CreditAlertSeverity.WARNING, "Utilisation Warning", Decimal("75")),
        )
        for cfg, alert_type, severity, title, fallback in candidates:
            if cfg is None or not cfg.enabled:
                continue
            threshold = cfg.threshold_pct or fallback
            if util_pct < threshold:
                continue
            return bool(await self.create_alert(
                organization_id=organization_id,
                alert_type=alert_type,
                severity=severity,
                title=title,
                summary=f"Utilisation reached {util_pct:.1f}%, exceeding {threshold}% threshold.",
                context={"utilisation_percent": float(util_pct), "org_name": org_name},
            ))
        return False

    async def _eval_review_reminder(
        self,
        organization_id: str,
        account: OrgCreditAccount,
        today: date,
        org_name: str,
    ) -> bool:
        cfg = await self._resolve_config(organization_id, CreditAlertType.SCHEDULED_CREDIT_REVIEW_REMINDER)
        if cfg is None or not cfg.enabled or account.next_review_date is None:
            return False
        days_until = (account.next_review_date - today).days
        window = cfg.reminder_days or 14
        if days_until < 0 or days_until > window:
            return False
        summary = f"Credit review due in {days_until} day{'s' if days_until != 1 else ''}."
        return bool(await self.create_alert(
            organization_id=organization_id,
            alert_type=CreditAlertType.SCHEDULED_CREDIT_REVIEW_REMINDER,
            severity=CreditAlertSeverity.WARNING,
            title="Review Due Soon",
            summary=summary,
            context={
                "days_until": days_until,
                "next_review_date": account.next_review_date.isoformat(),
                "org_name": org_name,
            },
        ))

    async def _eval_review_overdue(
        self,
        organization_id: str,
        account: OrgCreditAccount,
        today: date,
        org_name: str,
    ) -> bool:
        cfg = await self._resolve_config(organization_id, CreditAlertType.REVIEW_OVERDUE)
        if cfg is None or not cfg.enabled or account.next_review_date is None:
            return False
        if account.next_review_date >= today:
            return False
        overdue_days = (today - account.next_review_date).days
        summary = f"Credit review not completed by due date (overdue by {overdue_days} day{'s' if overdue_days != 1 else ''})."
        return bool(await self.create_alert(
            organization_id=organization_id,
            alert_type=CreditAlertType.REVIEW_OVERDUE,
            severity=CreditAlertSeverity.CRITICAL,
            title="Review Overdue",
            summary=summary,
            context={
                "overdue_days": overdue_days,
                "due_date": account.next_review_date.isoformat(),
                "org_name": org_name,
            },
        ))

    async def _eval_account_on_hold(
        self,
        organization_id: str,
        account: OrgCreditAccount,
        org_name: str,
    ) -> bool:
        if account.status != OrgCreditAccountStatus.ON_HOLD:
            return False
        summary = "Account placed ON_HOLD due to credit policy."
        return bool(await self.create_alert(
            organization_id=organization_id,
            alert_type=CreditAlertType.ACCOUNT_ON_HOLD,
            severity=CreditAlertSeverity.CRITICAL,
            title="Account On Hold",
            summary=summary,
            context={"org_name": org_name},
        ))

    async def _eval_account_suspended(
        self,
        organization_id: str,
        account: OrgCreditAccount,
        org_name: str,
    ) -> bool:
        if account.status != OrgCreditAccountStatus.SUSPENDED:
            return False
        summary = "Account has been suspended; credit access is fully restricted."
        return bool(await self.create_alert(
            organization_id=organization_id,
            alert_type=CreditAlertType.ACCOUNT_SUSPENDED,
            severity=CreditAlertSeverity.CRITICAL,
            title="Account Suspended",
            summary=summary,
            context={"org_name": org_name},
        ))

    async def fire_credit_score_drop_alert(
        self,
        organization_id: str,
        *,
        previous_score: int | None,
        new_score: int,
    ) -> OrgCreditAlert | None:
        """Event-driven: call from the credit report ingestion flow."""
        cfg = await self._resolve_config(organization_id, CreditAlertType.CREDIT_SCORE_DECREASE)
        if cfg is None or not cfg.enabled or previous_score is None:
            return None
        drop = previous_score - new_score
        threshold = cfg.score_drop_points or 10
        if drop < threshold:
            return None
        return await self.create_alert(
            organization_id=organization_id,
            alert_type=CreditAlertType.CREDIT_SCORE_DECREASE,
            severity=CreditAlertSeverity.WARNING,
            title="Credit Score Drop",
            summary=f"Credit score dropped by {drop} points in the latest recalculation.",
            context={"previous_score": previous_score, "new_score": new_score, "drop": drop},
        )

    async def fire_rating_downgrade_alert(
        self,
        organization_id: str,
        *,
        previous_band: str | None,
        new_band: str,
    ) -> OrgCreditAlert | None:
        cfg = await self._resolve_config(organization_id, CreditAlertType.CREDIT_RATING_DOWNGRADE)
        if cfg is None or not cfg.enabled or previous_band is None or previous_band == new_band:
            return None
        return await self.create_alert(
            organization_id=organization_id,
            alert_type=CreditAlertType.CREDIT_RATING_DOWNGRADE,
            severity=CreditAlertSeverity.WARNING,
            title="Credit Rating Downgrade",
            summary=f"Credit score dropped from {previous_band} to {new_band}.",
            context={"previous_band": previous_band, "new_band": new_band},
        )

    async def fire_late_payment_alert(
        self,
        organization_id: str,
        *,
        overdue_invoice_count: int,
    ) -> OrgCreditAlert | None:
        cfg = await self._resolve_config(organization_id, CreditAlertType.LATE_PAYMENT_BEHAVIOUR)
        if cfg is None or not cfg.enabled:
            return None
        threshold = cfg.late_payment_count or 3
        if overdue_invoice_count < threshold:
            return None
        return await self.create_alert(
            organization_id=organization_id,
            alert_type=CreditAlertType.LATE_PAYMENT_BEHAVIOUR,
            severity=CreditAlertSeverity.WARNING,
            title="Payment Overdue Pattern",
            summary=f"{overdue_invoice_count} invoices overdue simultaneously.",
            context={"overdue_invoice_count": overdue_invoice_count},
        )


    async def list_global_thresholds(self) -> list[dict[str, Any]]:
        rows = await self._global_threshold_repo.list_all()
        return [{"alert_type": r.alert_type.value, "threshold_pct": _decimal_or_none(r.threshold_pct)} for r in rows]

    async def update_global_thresholds(
        self,
        *,
        caller: AuthUser,
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        updated = []
        for item in items:
            alert_type: CreditAlertType = item["alert_type"]
            threshold_pct = Decimal(str(item["threshold_pct"]))
            row = await self._global_threshold_repo.get_by_type(alert_type)
            if row is None:
                row = await self._global_threshold_repo.create({
                    "alert_type": alert_type,
                    "threshold_pct": threshold_pct,
                })
            else:
                row.threshold_pct = threshold_pct
            updated.append(row)
        await self._session.flush()

        await self._audit.log(
            action="org_credit.global_alert_thresholds_updated",
            entity_type="global_credit_alert_threshold",
            entity_id="system",
            user_id=caller.id,
            user_role=_caller_role_str(caller),
            new_value={"types": [r.alert_type.value for r in updated]},
            ip_address=self._ip,
            user_agent=self._ua,
            reason=f"Updated {len(updated)} global credit alert threshold(s)",
            category=AuditCategory.CREDIT,
            event_type=AuditEventType.CREDIT_SETTINGS_UPDATED,
            severity="INFO",
        )
        logger.info("org_credit.global_alert_thresholds_updated", count=len(updated))
        return [{"alert_type": r.alert_type.value, "threshold_pct": _decimal_or_none(r.threshold_pct)} for r in updated]


def _decimal_or_none(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return str(v)
    return str(Decimal(v))
