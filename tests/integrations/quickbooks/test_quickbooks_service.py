"""Unit tests for QuickBooks service OAuth and org-resolution behavior."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr, ValidationError as PydanticValidationError

from app.common.exceptions import ValidationError
from app.integrations.quickbooks.constants import QB_GLOBAL_NAMESPACE_ID
from app.integrations.quickbooks.schemas import QuickBooksFailuresListQuery
from app.integrations.quickbooks.service import QuickBooksService
from app.modules.notifications.enums import NotificationEvent, NotificationType


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def setex(self, key: str, ttl: int, value: str) -> None:  # noqa: ARG002
        self.store[key] = value

    async def get(self, key: str):
        return self.store.get(key)

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:  # noqa: ARG002
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True


class _FakeSession:
    def __init__(self, records: dict[tuple[type, str], object]) -> None:
        self._records = records

    async def get(self, model: type, id_: str):
        return self._records.get((model, id_))

    async def flush(self) -> None:
        return None

    async def execute(self, stmt):  # noqa: ANN001, ARG002
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: []),
            scalar_one=lambda: 0,
            scalar_one_or_none=lambda: None,
        )


def test_resolve_swc_scope_id_uses_global_namespace() -> None:
    org_id = QuickBooksService.resolve_swc_scope_id()
    assert org_id == QB_GLOBAL_NAMESPACE_ID


@pytest.mark.asyncio
async def test_get_connect_url_stores_state_in_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = _FakeRedis()
    monkeypatch.setattr("app.integrations.quickbooks.service.get_redis", lambda: fake_redis)
    monkeypatch.setattr("app.integrations.quickbooks.auth.settings.QUICKBOOKS_CLIENT_ID", "cid")
    monkeypatch.setattr("app.integrations.quickbooks.auth.settings.QUICKBOOKS_CLIENT_SECRET", SecretStr("csecret"))
    monkeypatch.setattr("app.integrations.quickbooks.auth.settings.QUICKBOOKS_REDIRECT_URI", "https://api.example.com/callback")
    monkeypatch.setattr("app.integrations.quickbooks.auth.settings.QUICKBOOKS_SCOPES", "com.intuit.quickbooks.accounting")

    service = QuickBooksService(session=None)  # type: ignore[arg-type]
    data = await service.get_connect_url(organization_id="org-1", actor_user_id="user-1")

    assert data["authorization_url"].startswith("https://appcenter.intuit.com/connect/oauth2?")
    assert data["state"]
    assert f"qb:oauth_state:{data['state']}" in fake_redis.store

    saved = json.loads(fake_redis.store[f"qb:oauth_state:{data['state']}"])
    assert saved["scope_id"] == QB_GLOBAL_NAMESPACE_ID
    assert saved["user_id"] == "user-1"
    assert isinstance(saved["exp"], int)


@pytest.mark.asyncio
async def test_handle_callback_exchanges_token_and_upserts_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = _FakeRedis()
    state = "state-123"
    fake_redis.store[f"qb:oauth_state:{state}"] = json.dumps(
        {"organization_id": "org-99", "user_id": "user-55", "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())}
    )

    token_payload = {
        "realm_id": "realm-abc",
        "access_token": "access-token-plain",
        "refresh_token": "refresh-token-plain",
        "access_token_expires_at": datetime.now(UTC) + timedelta(hours=1),
        "refresh_token_expires_at": datetime.now(UTC) + timedelta(days=90),
    }

    monkeypatch.setattr("app.integrations.quickbooks.service.get_redis", lambda: fake_redis)
    monkeypatch.setattr("app.integrations.quickbooks.service.exchange_code_for_tokens", AsyncMock(return_value=token_payload))
    monkeypatch.setattr("app.integrations.quickbooks.auth.settings.QUICKBOOKS_ENCRYPTION_KEY", SecretStr("qb-encryption-key-for-tests"))

    service = QuickBooksService(session=None)  # type: ignore[arg-type]
    service._conn_repo.upsert_for_org = AsyncMock()  # type: ignore[method-assign]

    data = await service.handle_callback(state=state, code="code-1", realm_id="realm-abc")

    assert data["connected"] is True
    assert data["realm_id"] == "realm-abc"

    service._conn_repo.upsert_for_org.assert_awaited_once()  # type: ignore[attr-defined]
    _, kwargs = service._conn_repo.upsert_for_org.call_args  # type: ignore[attr-defined]
    assert kwargs == {}
    args = service._conn_repo.upsert_for_org.call_args.args  # type: ignore[attr-defined]
    assert args[0] == QB_GLOBAL_NAMESPACE_ID
    payload = args[1]
    assert payload["realm_id"] == "realm-abc"
    assert payload["access_token_enc"] != "access-token-plain"
    assert payload["refresh_token_enc"] != "refresh-token-plain"
    assert f"qb:oauth_state:{state}" not in fake_redis.store


@pytest.mark.asyncio
async def test_handle_callback_raises_when_state_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.integrations.quickbooks.service.get_redis", lambda: _FakeRedis())
    service = QuickBooksService(session=None)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="state is missing or expired"):
        await service.handle_callback(state="missing", code="code", realm_id="realm")


@pytest.mark.asyncio
async def test_handle_callback_rejects_malformed_state_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = _FakeRedis()
    state = "state-bad-json"
    fake_redis.store[f"qb:oauth_state:{state}"] = "{not-json"
    monkeypatch.setattr("app.integrations.quickbooks.service.get_redis", lambda: fake_redis)
    service = QuickBooksService(session=None)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="payload is invalid"):
        await service.handle_callback(state=state, code="code", realm_id="realm")


@pytest.mark.asyncio
async def test_handle_callback_rejects_expired_state_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = _FakeRedis()
    state = "state-expired"
    fake_redis.store[f"qb:oauth_state:{state}"] = json.dumps(
        {"organization_id": "org-1", "user_id": "user-55", "exp": int((datetime.now(UTC) - timedelta(minutes=5)).timestamp())}
    )
    monkeypatch.setattr("app.integrations.quickbooks.service.get_redis", lambda: fake_redis)
    service = QuickBooksService(session=None)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="state is missing or expired"):
        await service.handle_callback(state=state, code="code", realm_id="realm")


@pytest.mark.asyncio
async def test_handle_callback_keeps_state_when_token_exchange_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = _FakeRedis()
    state = "state-token-fail"
    fake_redis.store[f"qb:oauth_state:{state}"] = json.dumps(
        {"organization_id": "org-99", "user_id": "user-55", "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())}
    )
    monkeypatch.setattr("app.integrations.quickbooks.service.get_redis", lambda: fake_redis)
    monkeypatch.setattr(
        "app.integrations.quickbooks.service.exchange_code_for_tokens",
        AsyncMock(side_effect=ValidationError("token exchange failed")),
    )
    service = QuickBooksService(session=None)  # type: ignore[arg-type]
    service._conn_repo.upsert_for_org = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(ValidationError, match="token exchange failed"):
        await service.handle_callback(state=state, code="code-1", realm_id="realm-abc")

    assert f"qb:oauth_state:{state}" in fake_redis.store
    assert f"qb:oauth_state_lock:{state}" not in fake_redis.store
    service._conn_repo.upsert_for_org.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_handle_callback_rejects_when_state_is_already_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = _FakeRedis()
    state = "state-locked"
    fake_redis.store[f"qb:oauth_state:{state}"] = json.dumps(
        {"organization_id": "org-99", "user_id": "user-55", "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())}
    )
    fake_redis.store[f"qb:oauth_state_lock:{state}"] = "1"
    monkeypatch.setattr("app.integrations.quickbooks.service.get_redis", lambda: fake_redis)
    service = QuickBooksService(session=None)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="already being processed"):
        await service.handle_callback(state=state, code="code-1", realm_id="realm-abc")


@pytest.mark.asyncio
async def test_enqueue_credit_note_sync_marks_queued_and_enqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.invoices.models import CreditNote

    credit_note = SimpleNamespace(id="cn-1", organization_id="org-1", version=3, qb_sync_status="NOT_SYNCED")
    session = _FakeSession({(CreditNote, "cn-1"): credit_note})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service._sync_log_repo.log = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            status="FAILED",
            error_code="AuthenticationError",
            error_message="QuickBooks token refresh failed: invalid_grant",
            entity_type="credit_application",
            local_entity_id="app-1",
            event_type="CREDIT_APPLICATION_APPLIED",
            action="Credit Applied",
            job_id="job-1",
            attempt_no=1,
            related_qb_id=None,
            created_at=datetime.now(UTC),
        )
    )
    monkeypatch.setattr(
        "app.integrations.quickbooks.service.enqueue",
        AsyncMock(return_value=SimpleNamespace(job_id="job-123")),
    )

    data = await service.enqueue_credit_note_sync(organization_id="org-1", credit_note_id="cn-1")

    assert data["queued"] is True
    assert data["job_id"] == "job-123"
    assert data["entity_type"] == "credit_note"
    assert data["local_entity_id"] == "cn-1"
    assert data["sync_status"] == "pending"
    assert credit_note.qb_sync_status == "QUEUED"


@pytest.mark.asyncio
async def test_enqueue_invoice_sync_dedupes_when_already_queued() -> None:
    from app.modules.invoices.models import Invoice

    invoice = SimpleNamespace(id="inv-1", organization_id="org-1", version=2, qb_sync_status="QUEUED")
    session = _FakeSession({(Invoice, "inv-1"): invoice})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]

    data = await service.enqueue_invoice_sync(organization_id="org-1", invoice_id="inv-1", force=False)

    assert data["queued"] is False
    assert data["sync_status"] == "pending"


@pytest.mark.asyncio
async def test_enqueue_invoice_sync_force_bypasses_dedupe(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.invoices.models import Invoice

    invoice = SimpleNamespace(id="inv-1", organization_id="org-1", version=2, qb_sync_status="QUEUED")
    session = _FakeSession({(Invoice, "inv-1"): invoice})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service._sync_log_repo.log = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            status="FAILED",
            error_code="AuthenticationError",
            error_message="QuickBooks token refresh failed: invalid_grant",
            entity_type="credit_application",
            local_entity_id="app-1",
            event_type="CREDIT_APPLICATION_APPLIED",
            action="Credit Applied",
            job_id="job-1",
            attempt_no=1,
            related_qb_id=None,
            created_at=datetime.now(UTC),
        )
    )
    enqueue_mock = AsyncMock(return_value=SimpleNamespace(job_id="job-123"))
    monkeypatch.setattr("app.integrations.quickbooks.service.enqueue", enqueue_mock)

    data = await service.enqueue_invoice_sync(organization_id="org-1", invoice_id="inv-1", force=True)

    assert data["queued"] is True
    assert data["sync_status"] == "pending"
    enqueue_aa = enqueue_mock.await_args
    assert enqueue_aa is not None
    assert ":force:" in enqueue_aa.kwargs["_job_id"]


@pytest.mark.asyncio
async def test_enqueue_payment_sync_marks_queued_and_enqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.billing.models import BillingPayment

    payment = SimpleNamespace(id="pay-1", organization_id="org-1", version=2, qb_sync_status="NOT_SYNCED")
    session = _FakeSession({(BillingPayment, "pay-1"): payment})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service._sync_log_repo.log = AsyncMock()  # type: ignore[method-assign]
    enqueue_mock = AsyncMock(return_value=SimpleNamespace(job_id="job-123"))
    monkeypatch.setattr("app.integrations.quickbooks.service.enqueue", enqueue_mock)

    data = await service.enqueue_payment_sync(organization_id="org-1", payment_id="pay-1")

    assert data["queued"] is True
    assert data["job_id"] == "job-123"
    assert data["entity_type"] == "payment"
    assert data["sync_status"] == "pending"
    assert payment.qb_sync_status == "QUEUED"


@pytest.mark.asyncio
async def test_get_status_returns_extended_payload_for_connected_org() -> None:
    session = _FakeSession({})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    now = datetime.now(UTC)
    service._conn_repo.find_one = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            is_active=True,
            realm_id="realm-1",
            access_token_expires_at=now + timedelta(hours=1),
            created_at=now - timedelta(days=2),
            last_refreshed_at=now - timedelta(minutes=30),
            last_error_at=None,
            last_error=None,
        )
    )
    service._status_sync_metrics = AsyncMock(return_value=(now - timedelta(minutes=5), 3))  # type: ignore[method-assign]

    data = await service.get_status(organization_id="org-1")

    assert data["connected"] is True
    assert data["connection_status"] == "active"
    assert data["expires_at"] is not None
    assert data["status_created_at"] is not None
    assert data["last_refreshed_at"] is not None
    assert data["failed_syncs"] == 3


@pytest.mark.asyncio
async def test_sync_credit_note_now_rejects_non_issued_credit_note() -> None:
    from app.modules.invoices.models import CreditNote

    credit_note = SimpleNamespace(
        id="cn-1",
        organization_id="org-1",
        status="DRAFT",
        customer_id="user-1",
        total_credit_amount=100,
        credit_note_number="CN-000001",
        issue_date=datetime.now(UTC).date(),
        reason="test",
    )
    session = _FakeSession({(CreditNote, "cn-1"): credit_note})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="Only ISSUED credit notes can be synced"):
        await service.sync_credit_note_now(organization_id="org-1", credit_note_id="cn-1")


@pytest.mark.asyncio
async def test_sync_invoice_now_applies_credit_to_invoice() -> None:
    from app.modules.invoices.models import Invoice

    invoice = SimpleNamespace(
        id="inv-1",
        organization_id="org-1",
        status="SENT",
        customer_id="cust-1",
        order_id=None,
        vat_rate=Decimal("20.00"),
        currency="GBP",
        total=Decimal("100.00"),
        invoice_number="INV-000001",
        issue_date=datetime.now(UTC).date(),
        due_date=datetime.now(UTC).date(),
        notes=None,
        qb_sync_status="NOT_SYNCED",
        qb_last_sync_at=None,
    )
    session = _FakeSession({(Invoice, "inv-1"): invoice})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service.sync_customer_now = AsyncMock()  # type: ignore[method-assign]
    service._settings_repo.get_or_create_default = AsyncMock(return_value=SimpleNamespace(strict_mapping_mode=False))  # type: ignore[method-assign]
    service._resolve_mapping_ref = AsyncMock(return_value=None)  # type: ignore[method-assign]
    service._sync_log_repo.log = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            status="FAILED",
            error_code="AuthenticationError",
            error_message="QuickBooks token refresh failed: invalid_grant",
            entity_type="credit_application",
            local_entity_id="app-1",
            event_type="CREDIT_APPLICATION_APPLIED",
            action="Credit Applied",
            job_id="job-1",
            attempt_no=1,
            related_qb_id=None,
            created_at=datetime.now(UTC),
        )
    )
    service._link_repo.upsert_mapping = AsyncMock()  # type: ignore[method-assign]

    customer_link = SimpleNamespace(qb_entity_id="qb-customer-1")
    credit_note_link = SimpleNamespace(qb_entity_id="qb-credit-1")
    application = SimpleNamespace(id="app-1", credit_note_id="cn-1", applied_amount=Decimal("10.00"))
    service._credit_app_repo.list_for_invoice = AsyncMock(return_value=[application])  # type: ignore[method-assign]

    async def _get_by_local(_org_id: str, entity_type: str, local_id: str):
        if entity_type == "customer" and local_id == "cust-1":
            return customer_link
        if entity_type == "invoice" and local_id == "inv-1":
            return None
        if entity_type == "credit_note" and local_id == "cn-1":
            return credit_note_link
        if entity_type == "credit_application" and local_id == "app-1":
            return None
        return None

    service._link_repo.get_by_local = AsyncMock(side_effect=_get_by_local)  # type: ignore[method-assign]

    with patch("app.integrations.quickbooks.service.QuickBooksClient") as qb_client_cls:
        client_instance = qb_client_cls.return_value
        client_instance.create_invoice = AsyncMock(return_value={"Invoice": {"Id": "qb-invoice-1", "SyncToken": "2"}})
        client_instance.create_payment = AsyncMock(return_value={"Payment": {"Id": "qb-payment-1", "SyncToken": "1"}})

        await service.sync_invoice_now(organization_id="org-1", invoice_id="inv-1")

    client_instance.create_payment.assert_awaited_once()
    cp_aa = client_instance.create_payment.await_args
    assert cp_aa is not None
    payment_payload = cp_aa.args[0]
    assert payment_payload["TotalAmt"] == 10.0
    assert len(payment_payload["Line"]) == 2
    assert payment_payload["Line"][0]["LinkedTxn"][0]["TxnType"] == "Invoice"
    assert payment_payload["Line"][1]["LinkedTxn"][0]["TxnType"] == "CreditMemo"
    assert "CreditMemo" not in payment_payload
    upsert_calls = service._link_repo.upsert_mapping.await_args_list  # type: ignore[attr-defined]
    assert any(call.kwargs.get("entity_type") == "invoice" for call in upsert_calls)
    assert any(call.kwargs.get("entity_type") == "credit_application" for call in upsert_calls)
    log_calls = service._sync_log_repo.log.await_args_list  # type: ignore[attr-defined]
    assert any(
        call.kwargs.get("entity_type") == "invoice"
        and call.kwargs.get("related_qb_id") == "qb-invoice-1"
        and call.kwargs.get("action") == "Created"
        and call.kwargs.get("event_type") == "INVOICE_CREATED"
        for call in log_calls
    )
    assert any(
        call.kwargs.get("entity_type") == "credit_application"
        and call.kwargs.get("related_qb_id") == "qb-payment-1"
        and call.kwargs.get("action") == "Credit Applied"
        and call.kwargs.get("event_type") == "CREDIT_APPLICATION_APPLIED"
        for call in log_calls
    )


@pytest.mark.asyncio
async def test_sync_invoice_credit_applications_skips_existing_link_when_not_forced() -> None:
    invoice = SimpleNamespace(id="inv-1")
    session = _FakeSession({})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service._sync_log_repo.log = AsyncMock()  # type: ignore[method-assign]
    service._link_repo.upsert_mapping = AsyncMock()  # type: ignore[method-assign]

    application = SimpleNamespace(id="app-1", credit_note_id="cn-1", applied_amount=Decimal("10.00"))
    existing_link = SimpleNamespace(qb_entity_id="qb-payment-existing")
    service._credit_app_repo.list_for_invoice = AsyncMock(return_value=[application])  # type: ignore[method-assign]

    async def _get_by_local(_org_id: str, entity_type: str, local_id: str):
        if entity_type == "credit_application" and local_id == "app-1":
            return existing_link
        return None

    service._link_repo.get_by_local = AsyncMock(side_effect=_get_by_local)  # type: ignore[method-assign]

    with patch("app.integrations.quickbooks.service.QuickBooksClient") as qb_client_cls:
        await service._sync_invoice_credit_applications(
            organization_id="org-1",
            invoice=cast(Any, invoice),
            qb_invoice_id="qb-invoice-1",
            qb_customer_id="qb-customer-1",
            force=False,
            job_id="job-1",
            attempt_no=1,
        )
        qb_client_cls.return_value.create_payment.assert_not_called()

    service._link_repo.upsert_mapping.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_sync_invoice_credit_applications_reapplies_when_forced() -> None:
    invoice = SimpleNamespace(id="inv-1")
    session = _FakeSession({})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service._sync_log_repo.log = AsyncMock()  # type: ignore[method-assign]
    service._link_repo.upsert_mapping = AsyncMock()  # type: ignore[method-assign]

    application = SimpleNamespace(id="app-1", credit_note_id="cn-1", applied_amount=Decimal("10.00"))
    existing_link = SimpleNamespace(qb_entity_id="qb-payment-existing")
    credit_note_link = SimpleNamespace(qb_entity_id="qb-credit-1")
    service._credit_app_repo.list_for_invoice = AsyncMock(return_value=[application])  # type: ignore[method-assign]

    async def _get_by_local(_org_id: str, entity_type: str, local_id: str):
        if entity_type == "credit_application" and local_id == "app-1":
            return existing_link
        if entity_type == "credit_note" and local_id == "cn-1":
            return credit_note_link
        return None

    service._link_repo.get_by_local = AsyncMock(side_effect=_get_by_local)  # type: ignore[method-assign]

    with patch("app.integrations.quickbooks.service.QuickBooksClient") as qb_client_cls:
        qb_client_cls.return_value.create_payment = AsyncMock(return_value={"Payment": {"Id": "qb-payment-1", "SyncToken": "1"}})
        await service._sync_invoice_credit_applications(
            organization_id="org-1",
            invoice=cast(Any, invoice),
            qb_invoice_id="qb-invoice-1",
            qb_customer_id="qb-customer-1",
            force=True,
            job_id="job-1",
            attempt_no=1,
        )
        qb_client_cls.return_value.create_payment.assert_awaited_once()

    service._link_repo.upsert_mapping.assert_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_sync_invoice_credit_applications_syncs_credit_note_when_missing_mapping() -> None:
    invoice = SimpleNamespace(id="inv-1")
    session = _FakeSession({})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service._sync_log_repo.log = AsyncMock()  # type: ignore[method-assign]
    service._link_repo.upsert_mapping = AsyncMock()  # type: ignore[method-assign]
    service.sync_credit_note_now = AsyncMock()  # type: ignore[method-assign]

    application = SimpleNamespace(id="app-1", credit_note_id="cn-1", applied_amount=Decimal("10.00"))
    service._credit_app_repo.list_for_invoice = AsyncMock(return_value=[application])  # type: ignore[method-assign]
    credit_note_lookups = {"count": 0}

    async def _get_by_local(_org_id: str, entity_type: str, local_id: str):
        if entity_type == "credit_application" and local_id == "app-1":
            return None
        if entity_type == "credit_note" and local_id == "cn-1":
            credit_note_lookups["count"] += 1
            if credit_note_lookups["count"] == 1:
                return None
            return SimpleNamespace(qb_entity_id="qb-credit-1")
        return None

    service._link_repo.get_by_local = AsyncMock(side_effect=_get_by_local)  # type: ignore[method-assign]

    with patch("app.integrations.quickbooks.service.QuickBooksClient") as qb_client_cls:
        qb_client_cls.return_value.create_payment = AsyncMock(return_value={"Payment": {"Id": "qb-payment-1", "SyncToken": "1"}})
        await service._sync_invoice_credit_applications(
            organization_id="org-1",
            invoice=cast(Any, invoice),
            qb_invoice_id="qb-invoice-1",
            qb_customer_id="qb-customer-1",
            force=False,
            job_id="job-1",
            attempt_no=1,
        )

    service.sync_credit_note_now.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_sync_invoice_now_marks_failed_when_credit_apply_payment_has_no_id() -> None:
    from app.modules.invoices.models import Invoice

    invoice = SimpleNamespace(
        id="inv-1",
        organization_id="org-1",
        status="SENT",
        customer_id="cust-1",
        order_id=None,
        vat_rate=Decimal("20.00"),
        currency="GBP",
        total=Decimal("100.00"),
        invoice_number="INV-000001",
        issue_date=datetime.now(UTC).date(),
        due_date=datetime.now(UTC).date(),
        notes=None,
        qb_sync_status="NOT_SYNCED",
        qb_last_sync_at=None,
    )
    session = _FakeSession({(Invoice, "inv-1"): invoice})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service.sync_customer_now = AsyncMock()  # type: ignore[method-assign]
    service._settings_repo.get_or_create_default = AsyncMock(return_value=SimpleNamespace(strict_mapping_mode=False))  # type: ignore[method-assign]
    service._resolve_mapping_ref = AsyncMock(return_value=None)  # type: ignore[method-assign]
    service._sync_log_repo.log = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            status="FAILED",
            error_code="AuthenticationError",
            error_message="QuickBooks token refresh failed: invalid_grant",
            entity_type="credit_application",
            local_entity_id="app-1",
            event_type="CREDIT_APPLICATION_APPLIED",
            action="Credit Applied",
            job_id="job-1",
            attempt_no=1,
            related_qb_id=None,
            created_at=datetime.now(UTC),
        )
    )
    service._link_repo.upsert_mapping = AsyncMock()  # type: ignore[method-assign]
    service._link_repo.mark_failed = AsyncMock()  # type: ignore[method-assign]

    application = SimpleNamespace(id="app-1", credit_note_id="cn-1", applied_amount=Decimal("10.00"))
    credit_note_link = SimpleNamespace(qb_entity_id="qb-credit-1")
    customer_link = SimpleNamespace(qb_entity_id="qb-customer-1")
    service._credit_app_repo.list_for_invoice = AsyncMock(return_value=[application])  # type: ignore[method-assign]

    async def _get_by_local(_org_id: str, entity_type: str, local_id: str):
        if entity_type == "customer" and local_id == "cust-1":
            return customer_link
        if entity_type == "invoice" and local_id == "inv-1":
            return None
        if entity_type == "credit_note" and local_id == "cn-1":
            return credit_note_link
        if entity_type == "credit_application" and local_id == "app-1":
            return None
        return None

    service._link_repo.get_by_local = AsyncMock(side_effect=_get_by_local)  # type: ignore[method-assign]

    with patch("app.integrations.quickbooks.service.QuickBooksClient") as qb_client_cls, patch(
        "app.integrations.quickbooks.service.notify"
    ) as notify_mock, patch(
        "app.integrations.quickbooks.service.get_redis",
    ) as redis_mock:
        client_instance = qb_client_cls.return_value
        client_instance.create_invoice = AsyncMock(return_value={"Invoice": {"Id": "qb-invoice-1", "SyncToken": "2"}})
        client_instance.create_payment = AsyncMock(
            side_effect=Exception("QuickBooks token refresh failed: invalid_grant")
        )
        redis_mock.return_value = _FakeRedis()
        service._conn_repo.find_one = AsyncMock(  # type: ignore[method-assign]
            return_value=SimpleNamespace(
                organization_id="org-1",
                is_active=False,
                realm_id="realm-1",
                access_token_expires_at=datetime.now(UTC),
                created_at=datetime.now(UTC),
                last_refreshed_at=None,
                last_error_at=datetime.now(UTC),
                last_error="token refresh failed",
                connected_by_id="admin-1",
            )
        )

        with pytest.raises(Exception, match="QuickBooks token refresh failed"):
            await service.sync_invoice_now(organization_id="org-1", invoice_id="inv-1")

    assert invoice.qb_sync_status == "FAILED"
    assert service._link_repo.mark_failed.await_count == 2  # type: ignore[attr-defined]
    notify_mock.assert_awaited_once()
    kwargs = notify_mock.await_args.kwargs  # type: ignore[attr-defined]
    assert kwargs["event"] == NotificationEvent.ADMIN_QUICKBOOKS_CONNECTION_FAILURE
    assert kwargs["notification_type"] == NotificationType.ADMIN_INTERNAL
    assert kwargs["organization_id"] == QB_GLOBAL_NAMESPACE_ID
    assert kwargs["user_id"] == "admin-1"


@pytest.mark.asyncio
async def test_list_failed_syncs_includes_related_qb_id() -> None:
    session = _FakeSession({})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service._sync_log_repo.list_recent_failures = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            SimpleNamespace(
                id="log-1",
                entity_type="invoice",
                event_type="INVOICE_UPDATED",
                local_entity_id="inv-1",
                action="Updated",
                status="FAILED",
                attempt_no=1,
                job_id="job-1",
                error_code="ValidationError",
                error_message="Missing mapping",
                related_qb_id="12345",
                created_at=datetime.now(UTC),
            )
        ]
    )

    rows = await service.list_failed_syncs(organization_id="org-1")

    assert len(rows) == 1
    assert rows[0]["related_qb_id"] == "12345"


@pytest.mark.asyncio
async def test_list_logs_passes_filters_and_returns_expected_shape() -> None:
    session = _FakeSession({})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    now = datetime.now(UTC)
    service._sync_log_repo.list_logs = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            SimpleNamespace(
                id="log-1",
                entity_type="invoice",
                event_type="INVOICE_UPDATED",
                local_entity_id="inv-1",
                action="Updated",
                status="FAILED",
                attempt_no=2,
                job_id="job-1",
                error_code="ValidationError",
                error_message="Missing mapping",
                related_qb_id="qb-1",
                created_at=now,
            )
        ]
    )

    rows = await service.list_logs(
        organization_id="org-1",
        statuses=["FAILED"],
        entity_type="invoice",
        action="Updated",
        error_code="ValidationError",
        job_id="job-1",
        local_entity_id="inv-1",
        search="qb-1",
        limit=10,
    )

    assert len(rows) == 1
    assert rows[0]["attempt_no"] == 2
    assert rows[0]["job_id"] == "job-1"
    assert rows[0]["related_qb_id"] == "qb-1"
    assert rows[0]["event_type"] == "INVOICE_UPDATED"
    repo_kwargs = service._sync_log_repo.list_logs.await_args.kwargs  # type: ignore[attr-defined]
    assert repo_kwargs["search"] == "qb-1"


def test_failures_list_query_rejects_empty_status() -> None:
    with pytest.raises(PydanticValidationError, match="at least one value"):
        QuickBooksFailuresListQuery(status=[])


@pytest.mark.asyncio
async def test_list_logs_passes_multi_status_filter() -> None:
    session = _FakeSession({})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service._sync_log_repo.list_logs = AsyncMock(return_value=[])  # type: ignore[method-assign]

    await service.list_logs(organization_id="org-1", statuses=["FAILED", "PENDING"], limit=10)

    repo_kwargs = service._sync_log_repo.list_logs.await_args.kwargs  # type: ignore[attr-defined]
    assert repo_kwargs["statuses"] == ["FAILED", "PENDING"]


@pytest.mark.asyncio
async def test_list_logs_passes_created_at_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import date as date_cls

    session = _FakeSession({})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service._sync_log_repo.list_logs = AsyncMock(return_value=[])  # type: ignore[method-assign]

    class _FixedDate(date_cls):
        @classmethod
        def today(cls) -> date_cls:
            return date_cls(2026, 2, 25)

    monkeypatch.setattr("app.integrations.quickbooks.service.date", _FixedDate)

    await service.list_logs(
        organization_id="org-1",
        period="LAST_7_DAYS",
        limit=25,
    )

    repo_kwargs = service._sync_log_repo.list_logs.await_args.kwargs  # type: ignore[attr-defined]
    assert repo_kwargs["created_from"] == datetime(2026, 2, 19, 0, 0, tzinfo=UTC)
    assert repo_kwargs["created_to_exclusive"] == datetime(2026, 2, 26, 0, 0, tzinfo=UTC)
    assert repo_kwargs["created_from"] is not None


@pytest.mark.asyncio
async def test_get_log_detail_raises_when_not_found() -> None:
    session = _FakeSession({})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service._sync_log_repo.get_log = AsyncMock(return_value=None)  # type: ignore[method-assign]

    with pytest.raises(Exception, match="qb_sync_log"):
        await service.get_log_detail(organization_id="org-1", log_id="missing-log")


@pytest.mark.asyncio
async def test_bulk_resync_queues_supported_logs_and_skips_invalid() -> None:
    session = _FakeSession({})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service._sync_log_repo.list_logs = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            SimpleNamespace(status="FAILED", error_code="ValidationError", error_message="Missing mapping", entity_type="invoice", local_entity_id="inv-1"),
            SimpleNamespace(status="FAILED", error_code="ValidationError", error_message="Missing mapping", entity_type="unknown", local_entity_id="x-1"),
            SimpleNamespace(status="FAILED", error_code="ValidationError", error_message="Missing mapping", entity_type="credit_note", local_entity_id=None),
        ]
    )
    service.enqueue_resync = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {"queued": True, "job_id": "job-1", "entity_type": "invoice", "local_entity_id": "inv-1", "sync_status": "pending"},
            ValidationError("Unsupported entity_type for resync"),
        ]
    )

    data = await service.bulk_resync(organization_id="org-1", include_non_connection_failures=True, limit=50)

    assert data["requested"] == 3
    assert data["queued"] == 1
    assert data["skipped"] == 2
    assert len(data["items"]) == 1


@pytest.mark.asyncio
async def test_bulk_resync_defaults_to_failed_and_pending_statuses() -> None:
    session = _FakeSession({})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service._sync_log_repo.list_logs = AsyncMock(return_value=[])  # type: ignore[method-assign]

    data = await service.bulk_resync(organization_id="org-1")

    assert data["requested"] == 0
    kwargs = service._sync_log_repo.list_logs.await_args.kwargs  # type: ignore[attr-defined]
    assert kwargs["statuses"] == ["FAILED", "PENDING"]


@pytest.mark.asyncio
async def test_bulk_resync_defaults_to_connection_failures_for_failed_rows() -> None:
    session = _FakeSession({})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service._sync_log_repo.list_logs = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            SimpleNamespace(status="FAILED", error_code="TERMINAL_VALIDATION", error_message="Missing mapping", entity_type="invoice", local_entity_id="inv-1"),
            SimpleNamespace(status="FAILED", error_code="TRANSIENT_EXTERNAL_CONNECTION", error_message="QuickBooks token refresh failed", entity_type="invoice", local_entity_id="inv-2"),
            SimpleNamespace(status="FAILED", error_code="TRANSIENT_EXTERNAL", error_message="Rate limit from qbo", entity_type="invoice", local_entity_id="inv-4"),
            SimpleNamespace(status="PENDING", error_code=None, error_message=None, entity_type="invoice", local_entity_id="inv-3"),
        ]
    )
    service.enqueue_resync = AsyncMock(  # type: ignore[method-assign]
        return_value={"queued": True, "job_id": "job-1", "entity_type": "invoice", "local_entity_id": "inv-x", "sync_status": "pending"}
    )

    data = await service.bulk_resync(organization_id="org-1", limit=50)

    assert data["requested"] == 3
    assert service.enqueue_resync.await_count == 3  # type: ignore[attr-defined]


def test_classify_sync_error_transient_and_terminal() -> None:
    transient_code, _, transient_retry = QuickBooksService.classify_sync_error(RuntimeError("timeout from qbo"))
    assert transient_code == "TRANSIENT_EXTERNAL"
    assert transient_retry is True

    validation_code, _, validation_retry = QuickBooksService.classify_sync_error(ValidationError("bad mapping"))
    assert validation_code == "TERMINAL_VALIDATION"
    assert validation_retry is False


@pytest.mark.asyncio
async def test_bulk_resync_can_include_non_connection_failures_when_overridden() -> None:
    session = _FakeSession({})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service._sync_log_repo.list_logs = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            SimpleNamespace(status="FAILED", error_code="ValidationError", error_message="Missing mapping", entity_type="invoice", local_entity_id="inv-1"),
        ]
    )
    service.enqueue_resync = AsyncMock(  # type: ignore[method-assign]
        return_value={"queued": True, "job_id": "job-1", "entity_type": "invoice", "local_entity_id": "inv-1", "sync_status": "pending"}
    )

    data = await service.bulk_resync(
        organization_id="org-1",
        include_non_connection_failures=True,
        limit=50,
    )

    assert data["requested"] == 1
    service.enqueue_resync.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_bulk_resync_final_failures_replays_only_retry_exhausted_failed_logs() -> None:
    session = _FakeSession({})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service._sync_log_repo.list_logs = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            SimpleNamespace(status="FAILED", attempt_no=1, entity_type="invoice", local_entity_id="inv-1"),
            SimpleNamespace(status="FAILED", attempt_no=3, entity_type="invoice", local_entity_id="inv-2"),
            SimpleNamespace(status="FAILED", attempt_no=4, entity_type="credit_note", local_entity_id=None),
        ]
    )
    service.enqueue_resync = AsyncMock(  # type: ignore[method-assign]
        return_value={"queued": True, "job_id": "job-final", "entity_type": "invoice", "local_entity_id": "inv-2", "sync_status": "pending"}
    )

    data = await service.bulk_resync_final_failures(
        organization_id="org-1",
        entity_type="invoice",
        event_type="INVOICE_UPDATED",
        action="Updated",
        error_code="ValidationError",
        limit=50,
    )

    assert data["requested"] == 2
    assert data["queued"] == 1
    assert data["skipped"] == 1
    service.enqueue_resync.assert_awaited_once()  # type: ignore[attr-defined]
    kwargs = service._sync_log_repo.list_logs.await_args.kwargs  # type: ignore[attr-defined]
    assert kwargs["statuses"] == ["FAILED"]
    assert kwargs["entity_type"] == "invoice"
    assert kwargs["event_type"] == "INVOICE_UPDATED"
    assert kwargs["action"] == "Updated"
    assert kwargs["error_code"] == "ValidationError"


@pytest.mark.asyncio
async def test_bulk_resync_final_failures_supports_payment_entity() -> None:
    session = _FakeSession({})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service._sync_log_repo.list_logs = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            SimpleNamespace(status="FAILED", attempt_no=3, entity_type="payment", local_entity_id="pay-1"),
        ]
    )
    service.enqueue_resync = AsyncMock(  # type: ignore[method-assign]
        return_value={"queued": True, "job_id": "job-pay", "entity_type": "payment", "local_entity_id": "pay-1", "sync_status": "pending"}
    )

    data = await service.bulk_resync_final_failures(
        organization_id="org-1",
        entity_type="payment",
        event_type="PAYMENT_CREATED",
        action="Created",
        limit=50,
    )

    assert data["requested"] == 1
    assert data["queued"] == 1
    service.enqueue_resync.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_refresh_connections_due_refreshes_expiring_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    session = _FakeSession({})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    conn_due = SimpleNamespace(
        organization_id="org-1",
        access_token_expires_at=now + timedelta(minutes=5),
        last_refreshed_at=now - timedelta(minutes=20),
        created_at=now - timedelta(days=2),
    )
    conn_stale = SimpleNamespace(
        organization_id="org-2",
        access_token_expires_at=now + timedelta(hours=2),
        last_refreshed_at=now - timedelta(days=2),
        created_at=now - timedelta(days=10),
    )
    conn_fresh = SimpleNamespace(
        organization_id="org-3",
        access_token_expires_at=now + timedelta(hours=2),
        last_refreshed_at=now - timedelta(minutes=30),
        created_at=now - timedelta(days=5),
    )
    service._conn_repo.list_active = AsyncMock(return_value=[conn_due, conn_stale, conn_fresh])  # type: ignore[method-assign]
    ensure_mock = AsyncMock(
        side_effect=[
            SimpleNamespace(access_token_expires_at=now + timedelta(hours=4)),
            SimpleNamespace(access_token_expires_at=now + timedelta(hours=4)),
        ]
    )
    monkeypatch.setattr("app.integrations.quickbooks.service.QuickBooksClient.ensure_connection", ensure_mock)

    data = await service.refresh_connections_due(limit=50)

    assert data == {"checked": 3, "refreshed": 2, "skipped": 1, "failed": 0}
    assert ensure_mock.await_count == 2


@pytest.mark.asyncio
async def test_sync_customer_now_ignores_pending_link_and_creates_customer() -> None:
    from app.modules.organizations.models import Organization
    from app.modules.user.models import User

    user = SimpleNamespace(
        id="cust-1",
        organization_id="org-1",
        email="customer.qb@example.com",
        first_name="QB",
        last_name="Customer",
        title="MR",
        position_role="Accounts Payable",
        phone="+44 7700 900123",
        notes="Preferred invoice recipient",
    )
    organization = SimpleNamespace(
        id="org-1",
        trading_name="ShiftOpus Trading",
        legal_entity_name="ShiftOpus Ltd",
        reg_address_line_1="1 Test Street",
        reg_address_line_2="Floor 2",
        reg_city="London",
        reg_state="LND",
        reg_postcode="SW1A 1AA",
        reg_country="United Kingdom",
    )
    session = _FakeSession({(User, "cust-1"): user, (Organization, "org-1"): organization})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service._sync_log_repo.log = AsyncMock()  # type: ignore[method-assign]
    service._link_repo.upsert_mapping = AsyncMock()  # type: ignore[method-assign]
    service._link_repo.mark_failed = AsyncMock()  # type: ignore[method-assign]
    service._link_repo.get_by_local = AsyncMock(return_value=SimpleNamespace(qb_entity_id="pending:cust-1"))  # type: ignore[method-assign]

    with patch("app.integrations.quickbooks.service.QuickBooksClient") as qb_client_cls:
        client_instance = qb_client_cls.return_value
        client_instance.create_customer = AsyncMock(return_value={"Customer": {"Id": "qb-customer-1", "SyncToken": "1"}})
        client_instance.update_customer = AsyncMock()

        await service.sync_customer_now(organization_id="org-1", customer_id="cust-1")

    client_instance.create_customer.assert_awaited_once()
    client_instance.update_customer.assert_not_awaited()
    cc_aa = client_instance.create_customer.await_args
    assert cc_aa is not None
    payload = cc_aa.args[0]
    assert payload["DisplayName"] == "customer.qb@example.com"
    assert payload["PrimaryEmailAddr"] == {"Address": "customer.qb@example.com"}
    assert payload["GivenName"] == "QB"
    assert payload["FamilyName"] == "Customer"
    assert payload["PrimaryPhone"] == {"FreeFormNumber": "+44 7700 900123"}
    assert payload["Title"] == "MR"
    assert payload["Job"] == "Accounts Payable"
    assert payload["CompanyName"] == "ShiftOpus Trading"
    assert payload["BillAddr"]["Line1"] == "1 Test Street"
    assert payload["Notes"] == "Preferred invoice recipient"


@pytest.mark.asyncio
async def test_sync_invoice_now_includes_rich_payload_fields() -> None:
    from app.modules.invoices.models import Invoice
    from app.modules.user.models import User

    invoice = SimpleNamespace(
        id="inv-1",
        organization_id="org-1",
        status="SENT",
        customer_id="cust-1",
        order_id="ord-1",
        vat_rate=Decimal("20.00"),
        currency="GBP",
        total=Decimal("120.00"),
        invoice_number="INV-000001",
        issue_date=datetime.now(UTC).date(),
        due_date=datetime.now(UTC).date(),
        notes="Fragile delivery",
        qb_sync_status="NOT_SYNCED",
        qb_last_sync_at=None,
    )
    customer = SimpleNamespace(id="cust-1", email="billing@example.com")
    session = _FakeSession({(Invoice, "inv-1"): invoice, (User, "cust-1"): customer})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service.sync_customer_now = AsyncMock()  # type: ignore[method-assign]
    service._settings_repo.get_or_create_default = AsyncMock(return_value=SimpleNamespace(strict_mapping_mode=False))  # type: ignore[method-assign]
    service._resolve_mapping_ref = AsyncMock(return_value=None)  # type: ignore[method-assign]
    service._sync_log_repo.log = AsyncMock()  # type: ignore[method-assign]
    service._link_repo.upsert_mapping = AsyncMock()  # type: ignore[method-assign]
    service._credit_app_repo.list_for_invoice = AsyncMock(return_value=[])  # type: ignore[method-assign]
    service._get_invoice_line_items = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            SimpleNamespace(description="Base Fare", quantity=2, unit_price=Decimal("40.00"), total_price=Decimal("80.00")),
            SimpleNamespace(description="Fuel Surcharge", quantity=1, unit_price=Decimal("40.00"), total_price=Decimal("40.00")),
        ]
    )
    service._get_order = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            order_id="SWC-ORD-009001",
            pickup_address_id="addr-1",
        )
    )
    service._get_organization = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            reg_address_line_1="1 Test Street",
            legal_entity_name="Shiftopus Ltd",
            reg_address_line_2="Floor 2",
            reg_city="London",
            reg_state="LND",
            reg_postcode="SW1A 1AA",
            reg_country="United Kingdom",
        )
    )
    service._get_address = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            line_1="Warehouse 7",
            line_2="Dock A",
            city="Birmingham",
            state="West Midlands",
            postcode="B1 1AA",
            country="GB",
        )
    )
    service._link_repo.get_by_local = AsyncMock(side_effect=[SimpleNamespace(qb_entity_id="qb-customer-1"), None])  # type: ignore[method-assign]

    with patch("app.integrations.quickbooks.service.QuickBooksClient") as qb_client_cls:
        client_instance = qb_client_cls.return_value
        client_instance.create_invoice = AsyncMock(return_value={"Invoice": {"Id": "qb-invoice-1", "SyncToken": "2"}})

        await service.sync_invoice_now(organization_id="org-1", invoice_id="inv-1")

    inv_aa = client_instance.create_invoice.await_args
    assert inv_aa is not None
    payload = inv_aa.args[0]
    assert payload["CustomerRef"] == {"value": "qb-customer-1"}
    assert payload["DocNumber"] == "INV-000001"
    assert payload["TxnDate"] == invoice.issue_date.isoformat()
    assert payload["DueDate"] == invoice.due_date.isoformat()
    assert payload["PrivateNote"] == "Order ID: #SWC-ORD-009001 | Fragile delivery"
    assert payload["BillEmail"] == {"Address": "billing@example.com"}
    assert payload["BillAddr"]["Line1"] == "1 Test Street"
    assert payload["BillAddr"]["City"] == "London"
    assert payload["ShipAddr"]["Line1"] == "Warehouse 7"
    assert payload["ShipAddr"]["City"] == "Birmingham"
    assert payload["CustomerMemo"] == {"value": "Order ID: #SWC-ORD-009001"}
    service._get_address.assert_awaited_once_with("addr-1")  # type: ignore[attr-defined]
    assert len(payload["Line"]) == 2
    assert payload["Line"][0]["DetailType"] == "SalesItemLineDetail"
    assert payload["Line"][0]["Description"] == "Base Fare"
    assert payload["Line"][0]["SalesItemLineDetail"]["Qty"] == 2.0


@pytest.mark.asyncio
async def test_sync_credit_note_now_includes_rich_payload_fields() -> None:
    from app.modules.invoices.models import CreditNote
    from app.modules.organizations.models import Organization
    from app.modules.user.models import User

    credit_note = SimpleNamespace(
        id="cn-1",
        organization_id="org-1",
        status="ISSUED",
        customer_id="cust-1",
        total_credit_amount=Decimal("25.00"),
        credit_note_number="CN-000001",
        issue_date=datetime.now(UTC).date(),
        reason="Damaged parcel compensation",
        currency="GBP",
        qb_sync_status="NOT_SYNCED",
        qb_last_sync_at=None,
    )
    customer = SimpleNamespace(
        id="cust-1",
        email="customer.qb@example.com",
    )
    organization = SimpleNamespace(
        id="org-1",
        reg_address_line_1="1 Test Street",
        legal_entity_name="Shiftopus Ltd",
        reg_address_line_2="Floor 2",
        reg_city="London",
        reg_state="LND",
        reg_postcode="SW1A 1AA",
        reg_country="United Kingdom",
    )
    session = _FakeSession(
        {
            (CreditNote, "cn-1"): credit_note,
            (User, "cust-1"): customer,
            (Organization, "org-1"): organization,
        }
    )
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service.sync_customer_now = AsyncMock()  # type: ignore[method-assign]
    service._settings_repo.get_or_create_default = AsyncMock(return_value=SimpleNamespace(strict_mapping_mode=False))  # type: ignore[method-assign]
    service._resolve_mapping_ref = AsyncMock(return_value=None)  # type: ignore[method-assign]
    service._sync_log_repo.log = AsyncMock()  # type: ignore[method-assign]
    service._link_repo.upsert_mapping = AsyncMock()  # type: ignore[method-assign]
    service._link_repo.get_by_local = AsyncMock(side_effect=[SimpleNamespace(qb_entity_id="qb-customer-1"), None])  # type: ignore[method-assign]

    with patch("app.integrations.quickbooks.service.QuickBooksClient") as qb_client_cls:
        client_instance = qb_client_cls.return_value
        client_instance.create_credit_memo = AsyncMock(return_value={"CreditMemo": {"Id": "qb-credit-1", "SyncToken": "2"}})

        await service.sync_credit_note_now(organization_id="org-1", credit_note_id="cn-1")

    cm_aa = client_instance.create_credit_memo.await_args
    assert cm_aa is not None
    payload = cm_aa.args[0]
    assert payload["CustomerRef"] == {"value": "qb-customer-1"}
    assert payload["DocNumber"] == "CN-000001"
    assert payload["TxnDate"] == credit_note.issue_date.isoformat()
    assert payload["Line"][0]["DetailType"] == "SalesItemLineDetail"
    assert payload["BillEmail"] == {"Address": "customer.qb@example.com"}
    assert payload["BillAddr"]["Line1"] == "1 Test Street"
    assert payload["CustomerMemo"] == {"value": "Damaged parcel compensation"}
    assert payload["Line"][0]["Description"] == "Damaged parcel compensation"


@pytest.mark.asyncio
async def test_sync_payment_now_updates_existing_qbo_payment() -> None:
    from app.modules.billing.models import BillingPayment

    payment = SimpleNamespace(
        id="pay-1",
        organization_id="org-1",
        customer_id="cust-1",
        allocated_amount=Decimal("25.00"),
        payment_date=datetime.now(UTC).date(),
        payment_number="PAY-000123",
        notes="allocation changed",
        qb_payload_fingerprint=None,
        qb_sync_status="QUEUED",
        qb_last_sync_at=None,
    )
    session = _FakeSession({(BillingPayment, "pay-1"): payment})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service.sync_customer_now = AsyncMock()  # type: ignore[method-assign]
    service._sync_log_repo.log = AsyncMock()  # type: ignore[method-assign]
    service._link_repo.upsert_mapping = AsyncMock()  # type: ignore[method-assign]
    service._link_repo.mark_failed = AsyncMock()  # type: ignore[method-assign]
    service._billing_alloc_repo.latest_for_payment = AsyncMock(  # type: ignore[method-assign]
        return_value=[SimpleNamespace(invoice_id="inv-1", allocated_amount=Decimal("25.00"))]
    )

    async def _get_by_local(_org_id: str, entity_type: str, _local_id: str):
        if entity_type == "customer":
            return SimpleNamespace(qb_entity_id="qb-customer-1")
        if entity_type == "invoice":
            return SimpleNamespace(qb_entity_id="qb-invoice-1")
        if entity_type == "payment":
            return SimpleNamespace(qb_entity_id="qb-payment-1", sync_token="2")
        return None

    service._link_repo.get_by_local = AsyncMock(side_effect=_get_by_local)  # type: ignore[method-assign]

    with patch("app.integrations.quickbooks.service.QuickBooksClient") as qb_client_cls:
        client = qb_client_cls.return_value
        client.update_payment = AsyncMock(return_value={"Payment": {"Id": "qb-payment-1", "SyncToken": "3"}})
        client.create_payment = AsyncMock()

        await service.sync_payment_now(organization_id="org-1", payment_id="pay-1")

    client.update_payment.assert_awaited_once()
    client.create_payment.assert_not_awaited()
    up_aa = client.update_payment.await_args
    assert up_aa is not None
    payload = up_aa.args[0]
    assert payload["Id"] == "qb-payment-1"
    assert payload["SyncToken"] == "2"
    assert payload["sparse"] is True
    assert payload["Line"][0]["LinkedTxn"][0]["TxnId"] == "qb-invoice-1"
    log_calls = service._sync_log_repo.log.await_args_list  # type: ignore[attr-defined]
    assert any(call.kwargs.get("event_type") == "PAYMENT_UPDATED" for call in log_calls)


@pytest.mark.asyncio
async def test_sync_payment_now_updates_existing_qbo_payment_to_unapplied_when_no_allocations() -> None:
    from app.modules.billing.models import BillingPayment

    payment = SimpleNamespace(
        id="pay-2",
        organization_id="org-1",
        customer_id="cust-1",
        allocated_amount=Decimal("0.00"),
        payment_date=datetime.now(UTC).date(),
        payment_number="PAY-000124",
        notes=None,
        qb_payload_fingerprint=None,
        qb_sync_status="QUEUED",
        qb_last_sync_at=None,
    )
    session = _FakeSession({(BillingPayment, "pay-2"): payment})
    service = QuickBooksService(session=session)  # type: ignore[arg-type]
    service.sync_customer_now = AsyncMock()  # type: ignore[method-assign]
    service._sync_log_repo.log = AsyncMock()  # type: ignore[method-assign]
    service._link_repo.upsert_mapping = AsyncMock()  # type: ignore[method-assign]
    service._link_repo.mark_failed = AsyncMock()  # type: ignore[method-assign]
    service._billing_alloc_repo.latest_for_payment = AsyncMock(return_value=[])  # type: ignore[method-assign]

    async def _get_by_local(_org_id: str, entity_type: str, _local_id: str):
        if entity_type == "customer":
            return SimpleNamespace(qb_entity_id="qb-customer-1")
        if entity_type == "payment":
            return SimpleNamespace(qb_entity_id="qb-payment-2", sync_token="4")
        return None

    service._link_repo.get_by_local = AsyncMock(side_effect=_get_by_local)  # type: ignore[method-assign]

    with patch("app.integrations.quickbooks.service.QuickBooksClient") as qb_client_cls:
        client = qb_client_cls.return_value
        client.update_payment = AsyncMock(return_value={"Payment": {"Id": "qb-payment-2", "SyncToken": "5"}})
        client.create_payment = AsyncMock()

        await service.sync_payment_now(organization_id="org-1", payment_id="pay-2")

    client.update_payment.assert_awaited_once()
    update_payload = client.update_payment.await_args.args[0]
    assert update_payload["Line"] == []
    assert update_payload["TotalAmt"] == 0.0
    client.create_payment.assert_not_awaited()
