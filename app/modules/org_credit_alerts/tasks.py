"""Arq tasks for the Credit Alerts module.

**Periodic (low worker, Arq cron)** — ``evaluate_org_credit_alerts_task`` runs
every 15 minutes and evaluates only **date-driven** alert types: scheduled
review reminder and review overdue. There is no per-write event for "a day passed", so cron is the only
correct mechanism for these.

**Event-driven (inline)** — every other alert type fires from the service
that mutates the underlying state, with no scheduling latency:

- utilisation warning/critical → ``OrgCreditLedgerService.consume_credit`` and
  ``manual_adjust_used`` (when ``delta_used > 0``) call
  :meth:`OrgCreditAlertService.evaluate_after_used_credit_change`.
- account on hold / suspended → ``OrgCreditService.place_hold`` and
  ``suspend_account`` call
  :meth:`OrgCreditAlertService.evaluate_after_status_change`.
- internal score drop → ``OrgCreditMonitoringService.recalculate_internal_score``
  calls :meth:`OrgCreditAlertService.fire_credit_score_drop_alert`.
- bureau rating downgrade → ``OrgCreditMonitoringService.recalculate_creditsafe``
  calls :meth:`OrgCreditAlertService.fire_rating_downgrade_alert`.
- late payment behaviour → wire from invoice ageing whenever overdue counts
  change; helper is :meth:`OrgCreditAlertService.fire_late_payment_alert`.

The open-alert + ``cooldown_until`` rules in :meth:`create_alert` keep
duplicates safe even if both an inline call and the cron evaluator happen to
overlap during a transition.

- ``auto_unsnooze_credit_alerts_task``: periodic cron; unsnoozes due alerts.
- ``send_credit_alert_email_task``: enqueued when a fired alert's config
  includes email. Recipients: mostly the org account manager; scheduled
  review reminder prefers the assigned reviewer; review overdue goes to both
  (deduped); if the account manager is missing the assigned reviewer is used
  so mail is not dropped.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import select

from app.common.enums import LogEvent
from app.common.utils import mask_email
from app.core.database import get_async_session
from app.core.queue import retry_backoff
from app.mailer import EmailTemplateName, send_email
from app.modules.org_credit.models import OrgCreditAccount
from app.modules.org_credit.repository import OrgCreditAccountRepository
from app.modules.org_credit_alerts.enums import CreditAlertType
from app.modules.org_credit_alerts.repository import OrgCreditAlertRepository
from app.modules.org_credit_alerts.service import OrgCreditAlertService
from app.modules.organizations.models import Organization
from app.modules.user.repository import UserRepository

logger = structlog.get_logger()


def _ordered_unique_user_ids(*user_ids: str | None) -> list[str]:
    out: list[str] = []
    for uid in user_ids:
        if uid and uid not in out:
            out.append(uid)
    return out


def _credit_alert_email_recipient_user_ids(
    alert_type: CreditAlertType,
    *,
    account_manager_user_id: str | None,
    assigned_reviewer_user_id: str | None,
) -> list[str]:
    am = account_manager_user_id
    ar = assigned_reviewer_user_id
    if alert_type == CreditAlertType.SCHEDULED_CREDIT_REVIEW_REMINDER:
        if ar:
            return [ar]
        if am:
            return [am]
        return []
    if alert_type == CreditAlertType.REVIEW_OVERDUE:
        return _ordered_unique_user_ids(am, ar)
    if am:
        return [am]
    if ar:
        return [ar]
    return []


async def evaluate_org_credit_alerts_task(ctx: dict, today: str | None = None) -> dict:
    """Run the **date-driven** credit-alert evaluators across every org credit account.

    Covers review reminder and review overdue. All other alert types fire inline from their owning services
    (see module docstring).
    """
    from datetime import date as date_cls

    async with get_async_session() as session:
        try:
            run_date = date_cls.fromisoformat(today) if today else datetime.now(UTC).date()
        except ValueError:
            run_date = datetime.now(UTC).date()

        stmt = select(OrgCreditAccount.organization_id).distinct()
        result = await session.execute(stmt)
        org_ids = [row[0] for row in result.all()]

        svc = OrgCreditAlertService(session, request=None)
        await svc.auto_unsnooze_due()

        total_created = 0
        for org_id in org_ids:
            try:
                created = await svc.evaluate_for_organization(org_id, today=run_date)
                total_created += created
            except Exception:
                logger.exception("org_credit.alerts_evaluation_failed", organization_id=org_id)

        await session.commit()

    logger.info("org_credit.alerts_evaluation_complete", orgs=len(org_ids), alerts_created=total_created)
    return {"orgs": len(org_ids), "alerts_created": total_created}


async def auto_unsnooze_credit_alerts_task(ctx: dict) -> int:
    """Move alerts whose snooze window has elapsed back to ACTIVE."""
    async with get_async_session() as session:
        svc = OrgCreditAlertService(session, request=None)
        count = await svc.auto_unsnooze_due()
        await session.commit()
    return count


async def send_credit_alert_email_task(ctx: dict, alert_id: str) -> None:
    """Send ``credit_alert.html`` to resolved recipients; sets ``email_sent_at`` on full success.

    Retries the whole job on a send failure; no routable user is not an error.
    """
    async with get_async_session() as session:
        alert_repo = OrgCreditAlertRepository(session)
        account_repo = OrgCreditAccountRepository(session)
        user_repo = UserRepository(session)

        alert = await alert_repo.get_with_user(alert_id)
        if alert is None:
            logger.warning("org_credit.alert_email_skipped_missing_alert", alert_id=alert_id)
            return

        org_stmt = select(Organization).where(Organization.id == alert.organization_id)
        org_result = await session.execute(org_stmt)
        org = org_result.scalar_one_or_none()

        account = await account_repo.get_by_org_id(alert.organization_id)
        recipient_user_ids = _credit_alert_email_recipient_user_ids(
            alert.alert_type,
            account_manager_user_id=org.account_manager_user_id if org else None,
            assigned_reviewer_user_id=account.assigned_reviewer_user_id if account else None,
        )
        if not recipient_user_ids:
            logger.info(
                "org_credit.alert_email_skipped_no_recipient",
                alert_id=alert_id,
                organization_id=alert.organization_id,
                alert_type=alert.alert_type.value,
            )
            return

        org_name = org.trading_name if org else "your organisation"
        subject_prefix = "[CRITICAL] " if alert.severity.value == "CRITICAL" else "[WARNING] "
        subject = f"{subject_prefix}{alert.title} — {org_name}"

        last_email_for_log = ""
        send_count = 0
        try:
            for user_id in recipient_user_ids:
                user = await user_repo.get_by_id(user_id)
                if user is None or not user.email:
                    logger.info(
                        "org_credit.alert_email_skipped_user_no_email",
                        alert_id=alert_id,
                        user_id=user_id,
                    )
                    continue
                last_email_for_log = user.email
                await send_email(
                    user.email,
                    subject,
                    template_name=EmailTemplateName.CREDIT_ALERT,
                    context={
                        "title": alert.title,
                        "summary": alert.summary,
                        "severity": alert.severity.value,
                        "org_name": org_name,
                        "recipient_name": (user.first_name or "").strip() or "there",
                        "triggered_at_display": alert.triggered_at.strftime("%d %b %Y, %H:%M UTC"),
                        "action_url": None,
                    },
                )
                send_count += 1
                logger.info("org_credit.alert_email_sent", alert_id=alert_id, to=mask_email(user.email))
            if send_count == 0:
                logger.info(
                    "org_credit.alert_email_skipped_no_recipient",
                    alert_id=alert_id,
                    organization_id=alert.organization_id,
                    alert_type=alert.alert_type.value,
                )
                return
            alert.email_sent_at = datetime.now(UTC)
            await session.commit()
        except Exception as exc:
            logger.warning(
                LogEvent.MAIL_SEND_FAILED,
                alert_id=alert_id,
                to=mask_email(last_email_for_log) if last_email_for_log else None,
            )
            raise retry_backoff(ctx.get("job_try", 1), base=30) from exc


tasks = [
    evaluate_org_credit_alerts_task,
    auto_unsnooze_credit_alerts_task,
    send_credit_alert_email_task,
]
