"""Seed realistic QuickBooks sync log demo data for admin failures UI testing.

Creates append-only ``qb_sync_logs`` rows tagged with ``job_id`` prefix ``qb:demo:`` and
payload ``seed_tag=QB_DEMO_V1``. Safe to re-run: ``seed`` clears prior demo logs first.

Logs are stored under the global QuickBooks namespace (``QB_GLOBAL_NAMESPACE_ID``), matching
production persistence. ``local_entity_id`` values are taken from the target org's invoices /
B2B customers when available, otherwise deterministic demo UUIDs.

Usage:
  poetry run python scripts/seed_qb_sync_logs_demo.py seed
  poetry run python scripts/seed_qb_sync_logs_demo.py seed --organization-id <uuid>
  poetry run python scripts/seed_qb_sync_logs_demo.py seed --dry-run
  poetry run python scripts/seed_qb_sync_logs_demo.py clear
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import app.models  # noqa: F401
from sqlalchemy import delete, func, select

from app.common.enums import UserRole
from app.core.database import get_async_session
from app.integrations.quickbooks.constants import QB_GLOBAL_NAMESPACE_ID
from app.integrations.quickbooks.models import QbSyncLog
from app.modules.invoices.models import CreditNote, Invoice
from app.modules.organizations.models import Organization
from app.modules.user.models import User

from scripts.fe_demo_lib import BILLING_DEMO_ORG_ID

SEED_TAG = "QB_DEMO_V1"
JOB_ID_PREFIX = "qb:demo:"

STATUSES = ("PENDING", "SYNCED", "FAILED", "RETRYING", "SKIPPED")


@dataclass(frozen=True)
class _LogScenario:
    key: str
    entity_type: str
    event_type: str | None
    action: str
    status: str
    attempt_no: int = 1
    error_code: str | None = None
    error_message: str | None = None
    related_qb_id: str | None = None
    payload: dict[str, Any] | None = None
    hours_ago: float = 0.0


def _uid() -> str:
    return str(uuid.uuid4())


def _demo_job_id(key: str) -> str:
    return f"{JOB_ID_PREFIX}{key}"


def _base_payload(**extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"seed_tag": SEED_TAG, "demo": True}
    data.update(extra)
    return data


# Curated scenarios covering every filter dimension on GET /integrations/quickbooks/failures.
SCENARIOS: tuple[_LogScenario, ...] = (
    # ── Customer ─────────────────────────────────────────────────────────────
    _LogScenario("customer-queued", "customer", "CUSTOMER_QUEUED", "Queued", "PENDING", hours_ago=0.5),
    _LogScenario(
        "customer-created",
        "customer",
        "CUSTOMER_CREATED",
        "Created",
        "SYNCED",
        related_qb_id="qb-cust-1001",
        hours_ago=2,
    ),
    _LogScenario(
        "customer-updated",
        "customer",
        "CUSTOMER_UPDATED",
        "Updated",
        "SYNCED",
        related_qb_id="qb-cust-1001",
        hours_ago=6,
    ),
    _LogScenario(
        "customer-auth-failed",
        "customer",
        "CUSTOMER_UPDATED",
        "Updated",
        "FAILED",
        attempt_no=1,
        error_code="TRANSIENT_EXTERNAL_CONNECTION",
        error_message="QuickBooks connection/auth issue: invalid_grant — refresh token revoked",
        hours_ago=8,
    ),
    _LogScenario(
        "customer-retrying",
        "customer",
        "CUSTOMER_UPDATED",
        "Updated",
        "RETRYING",
        attempt_no=2,
        error_code="TRANSIENT_EXTERNAL",
        error_message="Transient upstream failure: upstream timeout after 30s",
        hours_ago=8.5,
    ),
    _LogScenario(
        "customer-terminal-failed",
        "customer",
        "CUSTOMER_CREATED",
        "Created",
        "FAILED",
        attempt_no=3,
        error_code="TERMINAL_VALIDATION",
        error_message="Validation failed for QuickBooks sync: DisplayName is required",
        hours_ago=9,
    ),
    _LogScenario(
        "customer-skipped",
        "customer",
        "CUSTOMER_DELETED",
        "Deleted",
        "SKIPPED",
        payload=_base_payload(reason="customer_not_linked"),
        hours_ago=24,
    ),
    # ── Invoice ──────────────────────────────────────────────────────────────
    _LogScenario("invoice-queued", "invoice", "INVOICE_QUEUED", "Queued", "PENDING", hours_ago=1),
    _LogScenario(
        "invoice-created",
        "invoice",
        "INVOICE_CREATED",
        "Created",
        "SYNCED",
        related_qb_id="qb-inv-2001",
        hours_ago=3,
    ),
    _LogScenario(
        "invoice-updated",
        "invoice",
        "INVOICE_UPDATED",
        "Updated",
        "SYNCED",
        related_qb_id="qb-inv-2001",
        hours_ago=12,
    ),
    _LogScenario(
        "invoice-no-change",
        "invoice",
        "INVOICE_NO_CHANGE",
        "No Change",
        "SYNCED",
        related_qb_id="qb-inv-2001",
        payload=_base_payload(reason="payload_unchanged"),
        hours_ago=18,
    ),
    _LogScenario(
        "invoice-rate-limit",
        "invoice",
        "INVOICE_UPDATED",
        "Updated",
        "FAILED",
        attempt_no=1,
        error_code="TRANSIENT_EXTERNAL",
        error_message="Transient upstream failure: HTTP 429 Too Many Requests",
        hours_ago=20,
    ),
    _LogScenario(
        "invoice-dependency",
        "invoice",
        "INVOICE_CREATED",
        "Created",
        "FAILED",
        attempt_no=2,
        error_code="DEPENDENCY_BLOCKED",
        error_message="Sync dependency missing: QuickBooks customer mapping not found",
        hours_ago=21,
    ),
    _LogScenario(
        "invoice-final-failure",
        "invoice",
        "INVOICE_UPDATED",
        "Updated",
        "FAILED",
        attempt_no=3,
        error_code="TERMINAL_EXTERNAL_HTTPERROR",
        error_message="External integration error: 400 Bad Request — Invalid Reference Id",
        related_qb_id="qb-inv-2001",
        hours_ago=22,
    ),
    _LogScenario(
        "invoice-deleted",
        "invoice",
        "INVOICE_DELETED",
        "Deleted",
        "SYNCED",
        related_qb_id="qb-inv-2099",
        hours_ago=48,
    ),
    # ── Credit note ──────────────────────────────────────────────────────────
    _LogScenario("credit-note-queued", "credit_note", "CREDIT_NOTE_QUEUED", "Queued", "PENDING", hours_ago=4),
    _LogScenario(
        "credit-note-created",
        "credit_note",
        "CREDIT_NOTE_CREATED",
        "Created",
        "SYNCED",
        related_qb_id="qb-cn-3001",
        hours_ago=5,
    ),
    _LogScenario(
        "credit-note-validation-failed",
        "credit_note",
        "CREDIT_NOTE_CREATED",
        "Created",
        "FAILED",
        attempt_no=1,
        error_code="TERMINAL_VALIDATION",
        error_message="Validation failed for QuickBooks sync: credit amount exceeds invoice balance",
        hours_ago=7,
    ),
    _LogScenario(
        "credit-note-no-change",
        "credit_note",
        "CREDIT_NOTE_NO_CHANGE",
        "No Change",
        "SYNCED",
        related_qb_id="qb-cn-3001",
        payload=_base_payload(reason="payload_unchanged"),
        hours_ago=30,
    ),
    # ── Credit application ───────────────────────────────────────────────────
    _LogScenario(
        "credit-app-applied",
        "credit_application",
        "CREDIT_APPLICATION_APPLIED",
        "Credit Applied",
        "SYNCED",
        related_qb_id="qb-inv-2001",
        hours_ago=10,
    ),
    _LogScenario(
        "credit-app-failed",
        "credit_application",
        "CREDIT_APPLICATION_APPLIED",
        "Credit Applied",
        "FAILED",
        attempt_no=2,
        error_code="DEPENDENCY_BLOCKED",
        error_message="Sync dependency missing: credit note not synced to QuickBooks",
        hours_ago=11,
    ),
    # ── Payment ──────────────────────────────────────────────────────────────
    _LogScenario(
        "payment-created",
        "payment",
        None,
        "Created",
        "SYNCED",
        related_qb_id="qb-pmt-4001",
        hours_ago=14,
    ),
    _LogScenario(
        "payment-connection-failed",
        "payment",
        None,
        "Created",
        "FAILED",
        attempt_no=1,
        error_code="TRANSIENT_EXTERNAL_CONNECTION",
        error_message="QuickBooks connection/auth issue: access token expired",
        hours_ago=15,
    ),
    _LogScenario(
        "payment-retrying",
        "payment",
        None,
        "Created",
        "RETRYING",
        attempt_no=2,
        error_code="TRANSIENT_EXTERNAL",
        error_message="Transient upstream failure: connection reset by peer",
        hours_ago=15.5,
    ),
    # ── Search / filter stress cases ─────────────────────────────────────────
    _LogScenario(
        "search-job-alpha",
        "invoice",
        "INVOICE_UPDATED",
        "Updated",
        "FAILED",
        error_code="ValidationError",
        error_message="Missing mapping for revenue account code REV-UK-01",
        hours_ago=36,
    ),
    _LogScenario(
        "search-job-beta",
        "invoice",
        "INVOICE_CREATED",
        "Created",
        "PENDING",
        hours_ago=40,
    ),
    _LogScenario(
        "search-qb-ref",
        "customer",
        "CUSTOMER_UPDATED",
        "Updated",
        "SYNCED",
        related_qb_id="DEMO-QB-REF-7788",
        hours_ago=60,
    ),
    _LogScenario(
        "old-skipped",
        "invoice",
        "INVOICE_QUEUED",
        "Queued",
        "SKIPPED",
        payload=_base_payload(reason="sync_disabled"),
        hours_ago=168,
    ),
)


async def _resolve_org(session, organization_id: str) -> Organization | None:
    return await session.get(Organization, organization_id)


async def _load_entity_pools(session, organization_id: str) -> dict[str, list[str]]:
    invoice_ids = list(
        (
            await session.execute(
                select(Invoice.id)
                .where(Invoice.organization_id == organization_id)
                .order_by(Invoice.created_at.desc())
                .limit(8)
            )
        ).scalars()
    )
    credit_note_ids = list(
        (
            await session.execute(
                select(CreditNote.id)
                .where(CreditNote.organization_id == organization_id)
                .order_by(CreditNote.created_at.desc())
                .limit(4)
            )
        ).scalars()
    )
    customer_ids = list(
        (
            await session.execute(
                select(User.id)
                .where(
                    User.organization_id == organization_id,
                    User.role == UserRole.CUSTOMER_B2B,
                )
                .order_by(User.created_at.desc())
                .limit(4)
            )
        ).scalars()
    )
    return {
        "customer": customer_ids or [_uid()],
        "invoice": invoice_ids or [_uid()],
        "credit_note": credit_note_ids or [_uid()],
        "credit_application": invoice_ids[:2] or [_uid()],
        "payment": invoice_ids[:2] or [_uid()],
    }


def _pick_local_entity_id(entity_type: str, pools: dict[str, list[str]], index: int) -> str:
    pool = pools.get(entity_type) or pools["invoice"]
    return pool[index % len(pool)]


async def _count_demo_logs(session) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(QbSyncLog)
        .where(
            QbSyncLog.organization_id == QB_GLOBAL_NAMESPACE_ID,
            QbSyncLog.job_id.like(f"{JOB_ID_PREFIX}%"),
        )
    )
    return int(result.scalar_one())


async def clear_demo_logs(*, dry_run: bool = False) -> int:
    async with get_async_session() as session:
        existing = await _count_demo_logs(session)
        if existing == 0:
            print("No QB demo sync logs to clear.")
            return 0
        if dry_run:
            print(f"[DRY RUN] Would delete {existing} qb_sync_logs (job_id like {JOB_ID_PREFIX!r}).")
            return existing
        result = await session.execute(
            delete(QbSyncLog).where(
                QbSyncLog.organization_id == QB_GLOBAL_NAMESPACE_ID,
                QbSyncLog.job_id.like(f"{JOB_ID_PREFIX}%"),
            )
        )
        await session.commit()
        deleted = int(result.rowcount or 0)
        print(f"Cleared {deleted} QB demo sync logs.")
        return deleted


async def seed_demo_logs(*, organization_id: str, dry_run: bool = False) -> None:
    now = datetime.now(UTC)
    async with get_async_session() as session:
        org = await _resolve_org(session, organization_id)
        if org is None:
            print(
                f"Warning: organization {organization_id} not found — using synthetic local_entity_id values."
            )
            pools = {
                "customer": [_uid()],
                "invoice": [_uid()],
                "credit_note": [_uid()],
                "credit_application": [_uid()],
                "payment": [_uid()],
            }
        else:
            pools = await _load_entity_pools(session, organization_id)

        await session.execute(
            delete(QbSyncLog).where(
                QbSyncLog.organization_id == QB_GLOBAL_NAMESPACE_ID,
                QbSyncLog.job_id.like(f"{JOB_ID_PREFIX}%"),
            )
        )

        rows: list[QbSyncLog] = []
        for idx, scenario in enumerate(SCENARIOS):
            created_at = now - timedelta(hours=scenario.hours_ago)
            local_entity_id = _pick_local_entity_id(scenario.entity_type, pools, idx)
            payload = dict(scenario.payload or _base_payload())
            payload.setdefault("scenario_key", scenario.key)
            payload.setdefault("reference_org_id", organization_id)

            rows.append(
                QbSyncLog(
                    id=_uid(),
                    organization_id=QB_GLOBAL_NAMESPACE_ID,
                    entity_type=scenario.entity_type,
                    local_entity_id=local_entity_id,
                    event_type=scenario.event_type,
                    action=scenario.action,
                    status=scenario.status,
                    job_id=_demo_job_id(scenario.key),
                    attempt_no=scenario.attempt_no,
                    error_code=scenario.error_code,
                    error_message=scenario.error_message,
                    related_qb_id=scenario.related_qb_id,
                    payload=payload,
                    created_at=created_at,
                )
            )

        session.add_all(rows)

        if dry_run:
            await session.rollback()
            org_label = f"{org.trading_name} ({org.id})" if org else organization_id
            print(f"[DRY RUN] Would insert {len(rows)} qb_sync_logs (reference org: {org_label}).")
        else:
            await session.commit()
            print(f"Seeded {len(rows)} QB demo sync logs.")

        counts = Counter(s.status for s in SCENARIOS)
        entity_counts = Counter(s.entity_type for s in SCENARIOS)
        print("=" * 72)
        print("QuickBooks sync log demo seed")
        print(f"Namespace        : {QB_GLOBAL_NAMESPACE_ID}")
        org_label = f"{org.trading_name} ({org.id})" if org else f"{organization_id} (not found — synthetic entity ids)"
        print(f"Reference org    : {org_label}")
        print(f"Job id prefix    : {JOB_ID_PREFIX!r}")
        print(f"Seed tag         : {SEED_TAG}")
        print(f"Scenarios        : {len(SCENARIOS)}")
        print("Status mix       : " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        print("Entity mix       : " + ", ".join(f"{k}={v}" for k, v in sorted(entity_counts.items())))
        print()
        print("Try the failures API:")
        print("  GET /api/v1/integrations/quickbooks/failures")
        print("  GET /api/v1/integrations/quickbooks/failures?status=FAILED&status=PENDING")
        print("  GET /api/v1/integrations/quickbooks/failures?search=DEMO-QB-REF")
        print("  GET /api/v1/integrations/quickbooks/failures?entity_type=invoice&action=Updated")
        print("=" * 72)


def main() -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--organization-id",
        default=BILLING_DEMO_ORG_ID,
        help=f"Org used to resolve invoice/customer local_entity_id values (default: {BILLING_DEMO_ORG_ID})",
    )
    common.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview actions without committing",
    )

    parser = argparse.ArgumentParser(
        description="Seed or clear QuickBooks sync log demo data for admin UI testing.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed", parents=[common], help="Replace demo qb_sync_logs with curated scenarios")
    sub.add_parser("clear", parents=[common], help="Delete only demo-tagged qb_sync_logs")

    args = parser.parse_args()
    org_id = str(args.organization_id).strip()

    if args.cmd == "clear":
        asyncio.run(clear_demo_logs(dry_run=args.dry_run))
    else:
        asyncio.run(seed_demo_logs(organization_id=org_id, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
