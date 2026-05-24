"""Unit tests for v2 suspension rules service and expression evaluator."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.common.enums import UserRole, UserStatus
from app.core.config import settings
from app.modules.billing.models import BillingPayment, BillingPaymentAllocation
from app.modules.invoices.models import Invoice, InvoiceEvent
from app.modules.organizations.enums import CompanySize, IndustryType
from app.modules.organizations.models import OrgContact, Organization
from app.modules.suspension_rules.enums import (
    RuleScopeType,
    SuspensionConditionType,
    SuspensionConnector,
    SuspensionRuleStatus,
    SuspensionRuleType,
)
from app.modules.suspension_rules.models import (
    OrgSuspensionGlobalSuppression,
    SuspensionActivity,
    SuspensionRuleCondition,
    SuspensionRuleSet,
)
from app.modules.suspension_rules.service import RuleDecision, SuspensionRulesService
from app.modules.user.models import User


def _org_payload(tag: str) -> dict:
    return {
        "trading_name": f"Acme {tag}",
        "legal_entity_name": f"Acme Legal {tag}",
        "industry": IndustryType.LOGISTICS_TRANSPORT,
        "company_size": CompanySize.EMPLOYEES_11_50,
        "date_of_incorporation": date(2020, 1, 1),
        "companies_house_number": f"CH-{tag}",
        "reg_address_line_1": "1 Test Street",
        "reg_city": "London",
        "reg_postcode": "E1 1AA",
    }


@pytest.mark.asyncio
async def test_legacy_to_conditions_enforces_unique_condition_types(db_session) -> None:  # type: ignore[no-untyped-def]
    service = SuspensionRulesService(db_session, request=None)
    with pytest.raises(Exception):
        service._ensure_unique_condition_types(
            [
                {"condition_type": "INVOICE_OVERDUE_DAYS"},
                {"condition_type": "INVOICE_OVERDUE_DAYS"},
            ]
        )


@pytest.mark.asyncio
async def test_evaluate_rule_sets_uses_and_precedence_over_or(db_session) -> None:  # type: ignore[no-untyped-def]
    service = SuspensionRulesService(db_session, request=None)
    org_id = str(uuid4())
    rule = SuspensionRuleSet(
        name="Rule precedence",
        scope_type=RuleScopeType.GLOBAL,
        rule_type=SuspensionRuleType.CREDIT_LIMIT,
        status=SuspensionRuleStatus.ACTIVE,
        auto_suspension_enabled=False,
        pause_new_bookings=False,
        restrict_portal_login=False,
        notify_finance_team=False,
        notify_account_manager=False,
    )
    rule.conditions = [
        SuspensionRuleCondition(
            position=1,
            connector=SuspensionConnector.NONE,
            condition_type=SuspensionConditionType.INVOICE_OVERDUE_DAYS,
            threshold_value=10,
            unit="Days",
        ),
        SuspensionRuleCondition(
            position=2,
            connector=SuspensionConnector.OR,
            condition_type=SuspensionConditionType.TOTAL_OVERDUE_AMOUNT,
            threshold_value=5000,
            unit="GBP",
        ),
        SuspensionRuleCondition(
            position=3,
            connector=SuspensionConnector.AND,
            condition_type=SuspensionConditionType.CREDIT_UTILIZATION,
            threshold_value=90,
            unit="%",
        ),
    ]
    # A OR (B AND C): A false, B true, C true -> true
    metrics = {
        "invoice_overdue_days": 5.0,
        "total_overdue_amount": 6000.0,
        "credit_utilization": 91.0,
    }
    decisions = service._evaluate_rule_sets(org_id, [rule], metrics)
    assert len(decisions) == 1
    assert decisions[0].final_result is True
    assert decisions[0].action == "WARN_ONLY"


@pytest.mark.asyncio
async def test_effective_rule_sets_prefers_org_override(db_session) -> None:  # type: ignore[no-untyped-def]
    service = SuspensionRulesService(db_session, request=None)
    org = Organization(**_org_payload("A1"))
    db_session.add(org)
    await db_session.flush()

    global_credit = SuspensionRuleSet(
        name="global-credit",
        scope_type=RuleScopeType.GLOBAL,
        rule_type=SuspensionRuleType.CREDIT_LIMIT,
        status=SuspensionRuleStatus.ACTIVE,
    )
    global_cash = SuspensionRuleSet(
        name="global-cash",
        scope_type=RuleScopeType.GLOBAL,
        rule_type=SuspensionRuleType.CASH,
        status=SuspensionRuleStatus.ACTIVE,
    )
    org_credit = SuspensionRuleSet(
        name="org-credit",
        scope_type=RuleScopeType.ORG,
        scope_org_id=org.id,
        rule_type=SuspensionRuleType.CREDIT_LIMIT,
        status=SuspensionRuleStatus.ACTIVE,
    )
    db_session.add_all([global_credit, global_cash, org_credit])
    await db_session.flush()

    resolved = await service.get_effective_rule_sets_for_org(org.id)
    names = {r.name for r in resolved}
    assert "org-credit" in names
    assert "global-credit" in names
    assert "global-cash" in names


@pytest.mark.asyncio
async def test_effective_rule_sets_returns_all_org_rules_for_same_type(db_session) -> None:  # type: ignore[no-untyped-def]
    service = SuspensionRulesService(db_session, request=None)
    org = Organization(**_org_payload("A2"))
    db_session.add(org)
    await db_session.flush()

    global_credit_a = SuspensionRuleSet(
        name="global-credit-a",
        scope_type=RuleScopeType.GLOBAL,
        rule_type=SuspensionRuleType.CREDIT_LIMIT,
        status=SuspensionRuleStatus.ACTIVE,
    )
    global_credit_b = SuspensionRuleSet(
        name="global-credit-b",
        scope_type=RuleScopeType.GLOBAL,
        rule_type=SuspensionRuleType.CREDIT_LIMIT,
        status=SuspensionRuleStatus.ACTIVE,
    )
    org_credit_a = SuspensionRuleSet(
        name="org-credit-a",
        scope_type=RuleScopeType.ORG,
        scope_org_id=org.id,
        rule_type=SuspensionRuleType.CREDIT_LIMIT,
        status=SuspensionRuleStatus.ACTIVE,
    )
    org_credit_b = SuspensionRuleSet(
        name="org-credit-b",
        scope_type=RuleScopeType.ORG,
        scope_org_id=org.id,
        rule_type=SuspensionRuleType.CREDIT_LIMIT,
        status=SuspensionRuleStatus.ACTIVE,
    )
    db_session.add_all([global_credit_a, global_credit_b, org_credit_a, org_credit_b])
    await db_session.flush()

    resolved = await service.get_effective_rule_sets_for_org(org.id, rule_type=SuspensionRuleType.CREDIT_LIMIT)
    assert {r.name for r in resolved} == {"global-credit-a", "global-credit-b", "org-credit-a", "org-credit-b"}


@pytest.mark.asyncio
async def test_effective_rule_sets_customised_hides_only_linked_default(db_session) -> None:  # type: ignore[no-untyped-def]
    service = SuspensionRulesService(db_session, request=None)
    org = Organization(**_org_payload("A3"))
    db_session.add(org)
    await db_session.flush()

    global_a = SuspensionRuleSet(
        name="global-a",
        scope_type=RuleScopeType.GLOBAL,
        rule_type=SuspensionRuleType.CREDIT_LIMIT,
        status=SuspensionRuleStatus.ACTIVE,
    )
    global_b = SuspensionRuleSet(
        name="global-b",
        scope_type=RuleScopeType.GLOBAL,
        rule_type=SuspensionRuleType.CREDIT_LIMIT,
        status=SuspensionRuleStatus.ACTIVE,
    )
    db_session.add_all([global_a, global_b])
    await db_session.flush()
    customised = SuspensionRuleSet(
        name="custom-a",
        scope_type=RuleScopeType.ORG,
        scope_org_id=org.id,
        parent_global_rule_set_id=global_a.id,
        rule_type=SuspensionRuleType.CREDIT_LIMIT,
        status=SuspensionRuleStatus.ACTIVE,
    )
    db_session.add(customised)
    await db_session.flush()

    resolved = await service.get_effective_rule_sets_for_org(org.id, rule_type=SuspensionRuleType.CREDIT_LIMIT)
    assert {r.name for r in resolved} == {"global-b", "custom-a"}


@pytest.mark.asyncio
async def test_effective_rule_sets_junction_suppression_hides_global(db_session) -> None:  # type: ignore[no-untyped-def]
    service = SuspensionRulesService(db_session, request=None)
    org = Organization(**_org_payload("JUNC"))
    db_session.add(org)
    await db_session.flush()

    global_cr = SuspensionRuleSet(
        name="junction-global",
        scope_type=RuleScopeType.GLOBAL,
        rule_type=SuspensionRuleType.CREDIT_LIMIT,
        status=SuspensionRuleStatus.ACTIVE,
    )
    db_session.add(global_cr)
    await db_session.flush()

    db_session.add(
        OrgSuspensionGlobalSuppression(
            organization_id=org.id,
            global_rule_set_id=global_cr.id,
        )
    )
    await db_session.flush()

    resolved = await service.get_effective_rule_sets_for_org(org.id, rule_type=SuspensionRuleType.CREDIT_LIMIT)
    assert resolved == []


@pytest.mark.asyncio
async def test_junction_suppression_is_org_scoped(db_session) -> None:  # type: ignore[no-untyped-def]
    service = SuspensionRulesService(db_session, request=None)
    org_a = Organization(**_org_payload("JA"))
    org_b = Organization(**_org_payload("JB"))
    db_session.add_all([org_a, org_b])
    await db_session.flush()

    global_cr = SuspensionRuleSet(
        name="scoped-global",
        scope_type=RuleScopeType.GLOBAL,
        rule_type=SuspensionRuleType.CREDIT_LIMIT,
        status=SuspensionRuleStatus.ACTIVE,
    )
    db_session.add(global_cr)
    await db_session.flush()

    db_session.add(
        OrgSuspensionGlobalSuppression(
            organization_id=org_a.id,
            global_rule_set_id=global_cr.id,
        )
    )
    await db_session.flush()

    resolved_a = await service.get_effective_rule_sets_for_org(org_a.id, rule_type=SuspensionRuleType.CREDIT_LIMIT)
    resolved_b = await service.get_effective_rule_sets_for_org(org_b.id, rule_type=SuspensionRuleType.CREDIT_LIMIT)
    assert resolved_a == []
    assert {r.name for r in resolved_b} == {"scoped-global"}


@pytest.mark.asyncio
async def test_evaluate_rule_sets_any_matching_ruleset_triggers_decision(db_session) -> None:  # type: ignore[no-untyped-def]
    service = SuspensionRulesService(db_session, request=None)
    org_id = str(uuid4())
    no_match = SuspensionRuleSet(
        name="no-match",
        scope_type=RuleScopeType.GLOBAL,
        rule_type=SuspensionRuleType.CREDIT_LIMIT,
        status=SuspensionRuleStatus.ACTIVE,
    )
    no_match.conditions = [
        SuspensionRuleCondition(
            position=1,
            connector=SuspensionConnector.NONE,
            condition_type=SuspensionConditionType.INVOICE_OVERDUE_DAYS,
            threshold_value=45,
            unit="Days",
        )
    ]
    match = SuspensionRuleSet(
        name="match",
        scope_type=RuleScopeType.GLOBAL,
        rule_type=SuspensionRuleType.CREDIT_LIMIT,
        status=SuspensionRuleStatus.ACTIVE,
        pause_new_bookings=True,
    )
    match.conditions = [
        SuspensionRuleCondition(
            position=1,
            connector=SuspensionConnector.NONE,
            condition_type=SuspensionConditionType.INVOICE_OVERDUE_DAYS,
            threshold_value=10,
            unit="Days",
        )
    ]
    decisions = service._evaluate_rule_sets(org_id, [no_match, match], {"invoice_overdue_days": 20.0})
    assert len(decisions) == 1
    assert decisions[0].rule_name == "match"
    assert decisions[0].action == "SUSPEND"


@pytest.mark.asyncio
async def test_build_metrics_uses_derived_invoice_payment_state(db_session) -> None:  # type: ignore[no-untyped-def]
    service = SuspensionRulesService(db_session, request=None)
    org = Organization(**_org_payload("METRICS"))
    db_session.add(org)
    await db_session.flush()

    overdue_unpaid = Invoice(
        invoice_number=f"INV-{uuid4().hex[:6]}",
        organization_id=org.id,
        issue_date=date.today() - timedelta(days=20),
        due_date=date.today() - timedelta(days=10),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("20.00"),
        total=Decimal("120.00"),
        status="SENT",
    )
    fully_paid = Invoice(
        invoice_number=f"INV-{uuid4().hex[6:12]}",
        organization_id=org.id,
        issue_date=date.today() - timedelta(days=5),
        due_date=date.today() + timedelta(days=5),
        subtotal=Decimal("50.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("10.00"),
        total=Decimal("60.00"),
        status="SENT",
    )
    voided = Invoice(
        invoice_number=f"INV-{uuid4().hex[12:18]}",
        organization_id=org.id,
        issue_date=date.today() - timedelta(days=7),
        due_date=date.today() - timedelta(days=1),
        subtotal=Decimal("70.00"),
        vat_rate=Decimal("20.00"),
        vat_amount=Decimal("14.00"),
        total=Decimal("84.00"),
        status="SENT",
    )
    db_session.add_all([overdue_unpaid, fully_paid, voided])
    await db_session.flush()

    payment = BillingPayment(
        organization_id=org.id,
        amount=Decimal("60.00"),
        currency="GBP",
        payment_date=date.today(),
        provider="MANUAL",
    )
    db_session.add(payment)
    await db_session.flush()
    db_session.add(
        BillingPaymentAllocation(
            payment_id=payment.id,
            invoice_id=fully_paid.id,
            revision_no=1,
            allocated_amount=Decimal("60.00"),
        )
    )
    db_session.add(
        InvoiceEvent(
            invoice_id=voided.id,
            event_type="VOIDED",
        )
    )
    await db_session.flush()

    metrics = await service._build_metrics([org.id], today=date.today())
    org_metrics = metrics[org.id]

    assert org_metrics["number_of_unpaid_invoices"] == 1.0
    assert org_metrics["total_outstanding_amount"] == 120.0
    assert org_metrics["total_overdue_amount"] == 120.0


@pytest.mark.asyncio
async def test_payment_model_mapping_for_rule_types(db_session) -> None:  # type: ignore[no-untyped-def]
    service = SuspensionRulesService(db_session, request=None)
    assert service._payment_model_for_rule_type(SuspensionRuleType.CREDIT_CARD.value) == "CARD"
    assert service._payment_model_for_rule_type(SuspensionRuleType.BANK_TRANSFER.value) == "BANK_TRANSFER"
    assert service._payment_model_for_rule_type(SuspensionRuleType.CASH.value) == "CASH"
    assert service._payment_model_for_rule_type(SuspensionRuleType.CREDIT_LIMIT.value) == "CREDIT_ACCOUNT"


@pytest.mark.asyncio
async def test_apply_decisions_pause_bookings_does_not_suspend_users(db_session) -> None:  # type: ignore[no-untyped-def]
    service = SuspensionRulesService(db_session, request=None)
    org = Organization(**_org_payload("PB"))
    db_session.add(org)
    await db_session.flush()

    user = User(
        email=f"pb-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        first_name="B2B",
        last_name="User",
        role=UserRole.CUSTOMER_B2B,
        status=UserStatus.ACTIVE,
        email_verified=True,
        organization_id=org.id,
    )
    db_session.add(user)
    await db_session.flush()
    rule_set = SuspensionRuleSet(
        name=f"pause-rule-{uuid4().hex[:6]}",
        scope_type=RuleScopeType.ORG,
        scope_org_id=org.id,
        rule_type=SuspensionRuleType.CREDIT_LIMIT,
        status=SuspensionRuleStatus.ACTIVE,
    )
    db_session.add(rule_set)
    await db_session.flush()

    decision = RuleDecision(
        rule_set_id=rule_set.id,
        organization_id=org.id,
        action="SUSPEND",
        apply_user_suspension=False,
        block_new_bookings=True,
        block_portal_login=False,
        conditions_met={"invoice_overdue_days": 40},
        evaluated_expression="(INVOICE_OVERDUE_DAYS(40>=30))",
        group_results=[True],
        final_result=True,
        rule_type=SuspensionRuleType.CREDIT_LIMIT.value,
        payment_model="CREDIT_ACCOUNT",
        rule_name="pause-only",
        notify_finance_team=False,
        notify_account_manager=False,
    )
    run = await service._run_repo.create({"run_date": "2026-04-20"})
    await service._apply_decisions_for_org(org.id, decisions=[decision], run_id=run.id, commit=False)

    assert user.status == UserStatus.ACTIVE
    assert org.status.value == "ON_HOLD"


@pytest.mark.asyncio
async def test_apply_decisions_restrict_portal_sets_org_suspended(db_session) -> None:  # type: ignore[no-untyped-def]
    service = SuspensionRulesService(db_session, request=None)
    org = Organization(**_org_payload("RL"))
    db_session.add(org)
    await db_session.flush()
    rule_set = SuspensionRuleSet(
        name=f"restrict-rule-{uuid4().hex[:6]}",
        scope_type=RuleScopeType.ORG,
        scope_org_id=org.id,
        rule_type=SuspensionRuleType.CREDIT_CARD,
        status=SuspensionRuleStatus.ACTIVE,
    )
    db_session.add(rule_set)
    await db_session.flush()

    decision = RuleDecision(
        rule_set_id=rule_set.id,
        organization_id=org.id,
        action="SUSPEND",
        apply_user_suspension=False,
        block_new_bookings=False,
        block_portal_login=True,
        conditions_met={"payment_failure_count": 5},
        evaluated_expression="(PAYMENT_FAILURE_COUNT(5>=3))",
        group_results=[True],
        final_result=True,
        rule_type=SuspensionRuleType.CREDIT_CARD.value,
        payment_model="CARD",
        rule_name="restrict-login",
        notify_finance_team=False,
        notify_account_manager=False,
    )
    run = await service._run_repo.create({"run_date": "2026-04-20"})
    await service._apply_decisions_for_org(org.id, decisions=[decision], run_id=run.id, commit=False)

    assert org.status.value == "SUSPENDED"


async def _seed_rule_activity_and_user(db_session, tag: str) -> tuple[Organization, SuspensionRuleSet, User]:  # type: ignore[no-untyped-def]
    org = Organization(**_org_payload(tag))
    db_session.add(org)
    await db_session.flush()

    rule_set = SuspensionRuleSet(
        name=f"notify-rule-{tag.lower()}",
        scope_type=RuleScopeType.ORG,
        scope_org_id=org.id,
        rule_type=SuspensionRuleType.CREDIT_LIMIT,
        status=SuspensionRuleStatus.ACTIVE,
    )
    db_session.add(rule_set)

    user = User(
        email=f"notify-{tag.lower()}@example.com",
        password_hash="x",
        first_name="Notify",
        last_name="User",
        role=UserRole.CUSTOMER_B2B,
        status=UserStatus.ACTIVE,
        email_verified=True,
        organization_id=org.id,
    )
    db_session.add(user)
    await db_session.flush()
    return org, rule_set, user


def _activity_for_user(*, rule_set: SuspensionRuleSet, user: User) -> SuspensionActivity:
    return SuspensionActivity(
        rule_set_id=rule_set.id,
        rule_name_snapshot=rule_set.name,
        account_id=user.id,
        conditions_met={"invoice_overdue_days": 45},
        action_taken="SUSPENDED",
    )


@pytest.mark.asyncio
async def test_queue_notifications_notify_account_manager_prefers_manager(db_session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    service = SuspensionRulesService(db_session, request=None)
    org, rule_set, user = await _seed_rule_activity_and_user(db_session, "MANAGER")

    manager = User(
        email="manager@example.com",
        password_hash="x",
        first_name="Account",
        last_name="Manager",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        email_verified=True,
    )
    db_session.add(manager)
    await db_session.flush()
    org.account_manager_user_id = manager.id

    decision = RuleDecision(
        rule_set_id=rule_set.id,
        organization_id=org.id,
        action="SUSPEND",
        apply_user_suspension=False,
        block_new_bookings=False,
        block_portal_login=False,
        conditions_met={"invoice_overdue_days": 45},
        evaluated_expression="(INVOICE_OVERDUE_DAYS(45>=30))",
        group_results=[True],
        final_result=True,
        rule_type=SuspensionRuleType.CREDIT_LIMIT.value,
        payment_model="CREDIT_ACCOUNT",
        rule_name="notify-manager",
        notify_finance_team=False,
        notify_account_manager=True,
    )
    monkeypatch.setattr(settings, "FINANCE_TEAM_EMAIL", "finance@example.com", raising=False)
    activity = _activity_for_user(rule_set=rule_set, user=user)
    db_session.add(activity)
    await db_session.flush()

    with patch("app.modules.suspension_rules.service.enqueue", new_callable=AsyncMock) as enqueue_mock:
        await service._queue_notifications(activity=activity, users=[user], decision=decision)
        assert enqueue_mock.await_count == 1
        queued_email = enqueue_mock.await_args_list[0].args[1]
        assert queued_email == "manager@example.com"


@pytest.mark.asyncio
async def test_queue_notifications_notify_account_manager_falls_back_to_account_owner(db_session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    service = SuspensionRulesService(db_session, request=None)
    org, rule_set, user = await _seed_rule_activity_and_user(db_session, "OWNER")

    owner_user = User(
        email="owner@example.com",
        password_hash="x",
        first_name="Account",
        last_name="Owner",
        role=UserRole.CUSTOMER_B2B,
        status=UserStatus.ACTIVE,
        email_verified=True,
        organization_id=org.id,
    )
    db_session.add(owner_user)
    await db_session.flush()
    db_session.add(
        OrgContact(
            organization_id=org.id,
            contact_number="+4400000000",
            contact_role="ACCOUNT_OWNER",
            user_id=owner_user.id,
        )
    )
    await db_session.flush()

    decision = RuleDecision(
        rule_set_id=rule_set.id,
        organization_id=org.id,
        action="SUSPEND",
        apply_user_suspension=False,
        block_new_bookings=False,
        block_portal_login=False,
        conditions_met={"invoice_overdue_days": 45},
        evaluated_expression="(INVOICE_OVERDUE_DAYS(45>=30))",
        group_results=[True],
        final_result=True,
        rule_type=SuspensionRuleType.CREDIT_LIMIT.value,
        payment_model="CREDIT_ACCOUNT",
        rule_name="notify-owner",
        notify_finance_team=False,
        notify_account_manager=True,
    )
    monkeypatch.setattr(settings, "FINANCE_TEAM_EMAIL", "finance@example.com", raising=False)
    activity = _activity_for_user(rule_set=rule_set, user=user)
    db_session.add(activity)
    await db_session.flush()

    with patch("app.modules.suspension_rules.service.enqueue", new_callable=AsyncMock) as enqueue_mock:
        await service._queue_notifications(activity=activity, users=[user], decision=decision)
        assert enqueue_mock.await_count == 1
        queued_email = enqueue_mock.await_args_list[0].args[1]
        assert queued_email == "owner@example.com"


@pytest.mark.asyncio
async def test_queue_notifications_notify_account_manager_falls_back_to_finance(db_session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    service = SuspensionRulesService(db_session, request=None)
    org, rule_set, user = await _seed_rule_activity_and_user(db_session, "FALLBACK")

    decision = RuleDecision(
        rule_set_id=rule_set.id,
        organization_id=org.id,
        action="SUSPEND",
        apply_user_suspension=False,
        block_new_bookings=False,
        block_portal_login=False,
        conditions_met={"invoice_overdue_days": 45},
        evaluated_expression="(INVOICE_OVERDUE_DAYS(45>=30))",
        group_results=[True],
        final_result=True,
        rule_type=SuspensionRuleType.CREDIT_LIMIT.value,
        payment_model="CREDIT_ACCOUNT",
        rule_name="notify-fallback",
        notify_finance_team=False,
        notify_account_manager=True,
    )
    monkeypatch.setattr(settings, "FINANCE_TEAM_EMAIL", "finance@example.com", raising=False)
    activity = _activity_for_user(rule_set=rule_set, user=user)
    db_session.add(activity)
    await db_session.flush()

    with patch("app.modules.suspension_rules.service.enqueue", new_callable=AsyncMock) as enqueue_mock:
        await service._queue_notifications(activity=activity, users=[user], decision=decision)
        assert enqueue_mock.await_count == 1
        assert enqueue_mock.await_args_list[0].args[1] == "finance@example.com"


@pytest.mark.asyncio
async def test_queue_notifications_finance_and_manager_fallback_dedupes(db_session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    service = SuspensionRulesService(db_session, request=None)
    org, rule_set, user = await _seed_rule_activity_and_user(db_session, "FINANCE")

    decision = RuleDecision(
        rule_set_id=rule_set.id,
        organization_id=org.id,
        action="SUSPEND",
        apply_user_suspension=False,
        block_new_bookings=False,
        block_portal_login=False,
        conditions_met={"invoice_overdue_days": 45},
        evaluated_expression="(INVOICE_OVERDUE_DAYS(45>=30))",
        group_results=[True],
        final_result=True,
        rule_type=SuspensionRuleType.CREDIT_LIMIT.value,
        payment_model="CREDIT_ACCOUNT",
        rule_name="notify-finance",
        notify_finance_team=True,
        notify_account_manager=True,
    )
    monkeypatch.setattr(settings, "FINANCE_TEAM_EMAIL", "finance@example.com", raising=False)
    activity = _activity_for_user(rule_set=rule_set, user=user)
    db_session.add(activity)
    await db_session.flush()

    with patch("app.modules.suspension_rules.service.enqueue", new_callable=AsyncMock) as enqueue_mock:
        await service._queue_notifications(activity=activity, users=[user], decision=decision)
        assert enqueue_mock.await_count == 1
        assert enqueue_mock.await_args_list[0].args[1] == "finance@example.com"
