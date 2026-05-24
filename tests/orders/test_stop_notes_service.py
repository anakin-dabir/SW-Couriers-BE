"""OrderService stop note create/update validation (package_ids, aliases)."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ValidationError
from app.modules.orders.enums import DeliveryStopStatus, OrderStatus, PackageStatus, StopNoteType
from app.modules.orders.models import DeliveryStop, Order, Package
from app.modules.orders.service import OrderService


async def _seed_order_stop_packages(
    db_session: AsyncSession,
    org_factory,
    user_factory,
) -> tuple[str, str, list[Package], Package]:
    suffix = uuid.uuid4().hex[:8].upper()
    org = await org_factory()
    user = await user_factory(status="ACTIVE", email_verified=True, role="CUSTOMER_B2B")

    order = Order(
        order_id=f"SWC-ORD-N{suffix}",
        master_label_id=f"ML-N{suffix}",
        organization_id=org.id,
        customer_id=user.id,
        status=OrderStatus.DELIVERY_IN_PROGRESS,
    )
    db_session.add(order)
    await db_session.flush()

    other_order = Order(
        order_id=f"SWC-ORD-O{suffix}",
        master_label_id=f"ML-O{suffix}",
        organization_id=org.id,
        customer_id=user.id,
        status=OrderStatus.DELIVERY_IN_PROGRESS,
    )
    db_session.add(other_order)
    await db_session.flush()

    dstop = DeliveryStop(
        order_id=order.id,
        tracking_id=f"TRK-N-{suffix}",
        recipient_first_name="A",
        recipient_last_name="B",
        recipient_phone="07123456789",
        recipient_email="a@example.com",
        line_1="1 St",
        city="London",
        postcode="E1 1AA",
        status=DeliveryStopStatus.OUT_FOR_DELIVERY,
    )
    db_session.add(dstop)
    await db_session.flush()

    other_stop = DeliveryStop(
        order_id=other_order.id,
        tracking_id=f"TRK-O-{suffix}",
        recipient_first_name="C",
        recipient_last_name="D",
        recipient_phone="07987654321",
        recipient_email="c@example.com",
        line_1="2 St",
        city="London",
        postcode="E2 2AA",
        status=DeliveryStopStatus.OUT_FOR_DELIVERY,
    )
    db_session.add(other_stop)
    await db_session.flush()

    packages: list[Package] = []
    for _ in range(2):
        packages.append(
            Package(
                order_id=order.id,
                delivery_stop_id=dstop.id,
                status=PackageStatus.OUT_FOR_DELIVERY,
            )
        )
    db_session.add_all(packages)
    wrong = Package(
        order_id=other_order.id,
        delivery_stop_id=other_stop.id,
        status=PackageStatus.OUT_FOR_DELIVERY,
    )
    db_session.add(wrong)
    await db_session.commit()
    for p in packages:
        await db_session.refresh(p)
    await db_session.refresh(wrong)
    await db_session.refresh(order)
    await db_session.refresh(dstop)
    return order.id, dstop.id, packages, wrong


@pytest.mark.asyncio
async def test_create_stop_note_rejects_foreign_package_id(
    db_session: AsyncSession,
    org_factory,
    user_factory,
) -> None:
    order_id, stop_id, _pkgs, wrong = await _seed_order_stop_packages(db_session, org_factory, user_factory)
    svc = OrderService(db_session)
    with pytest.raises(ValidationError) as exc:
        await svc.create_stop_note(
            order_id=order_id,
            stop_id=stop_id,
            note_type=StopNoteType.PACKAGE_ISSUE_NOTE.value,
            message="issue",
            is_blocking=False,
            sort_order=0,
            images=None,
            package_ids=[wrong.id],
        )
    assert exc.value.code == "INVALID_PACKAGE_IDS_FOR_STOP"


@pytest.mark.asyncio
async def test_create_stop_note_client_note_alias_persists_customer(
    db_session: AsyncSession,
    org_factory,
    user_factory,
) -> None:
    order_id, stop_id, _pkgs, _wrong = await _seed_order_stop_packages(db_session, org_factory, user_factory)
    svc = OrderService(db_session)
    entry = await svc.create_stop_note(
        order_id=order_id,
        stop_id=stop_id,
        note_type="CLIENT_NOTE",
        message="from alias",
        is_blocking=False,
        sort_order=0,
        images=None,
        package_ids=None,
    )
    assert entry.note_type == StopNoteType.CUSTOMER.value


@pytest.mark.asyncio
async def test_strict_rejects_unknown_note_type(
    db_session: AsyncSession,
    org_factory,
    user_factory,
) -> None:
    order_id, stop_id, _pkgs, _wrong = await _seed_order_stop_packages(db_session, org_factory, user_factory)
    svc = OrderService(db_session)
    with patch("app.modules.orders.service.is_strict_stop_note_types", return_value=True):
        with pytest.raises(ValidationError) as exc:
            await svc.create_stop_note(
                order_id=order_id,
                stop_id=stop_id,
                note_type="HANDOVER",
                message="legacy",
                is_blocking=False,
                sort_order=0,
                images=None,
            )
        assert exc.value.code == "INVALID_STOP_NOTE_TYPE"


@pytest.mark.asyncio
async def test_create_package_issue_with_valid_package_ids(
    db_session: AsyncSession,
    org_factory,
    user_factory,
) -> None:
    order_id, stop_id, pkgs, _w = await _seed_order_stop_packages(db_session, org_factory, user_factory)
    svc = OrderService(db_session)
    entry = await svc.create_stop_note(
        order_id=order_id,
        stop_id=stop_id,
        note_type=StopNoteType.PACKAGE_ISSUE_NOTE.value,
        message="Damaged outer box",
        is_blocking=False,
        sort_order=0,
        images=None,
        package_ids=[pkgs[1].id, pkgs[0].id],
    )
    assert set(entry.package_ids) == {pkgs[0].id, pkgs[1].id}
    listed = await svc.list_stop_notes(order_id=order_id, stop_id=stop_id)
    match = next((x for x in listed if x.id == entry.id), None)
    assert match is not None
    assert match.package_ids == sorted([pkgs[0].id, pkgs[1].id])


@pytest.mark.asyncio
async def test_create_package_issue_rejects_one_bad_of_two_ids(
    db_session: AsyncSession,
    org_factory,
    user_factory,
) -> None:
    order_id, stop_id, pkgs, _w = await _seed_order_stop_packages(db_session, org_factory, user_factory)
    svc = OrderService(db_session)
    bad = str(uuid.uuid4())
    with pytest.raises(ValidationError) as exc:
        await svc.create_stop_note(
            order_id=order_id,
            stop_id=stop_id,
            note_type=StopNoteType.PACKAGE_ISSUE_NOTE.value,
            message="mixed",
            is_blocking=False,
            sort_order=0,
            images=None,
            package_ids=[pkgs[0].id, bad],
        )
    assert exc.value.code == "INVALID_PACKAGE_IDS_FOR_STOP"


@pytest.mark.asyncio
async def test_admin_note_alias_normalizes_to_admin(
    db_session: AsyncSession,
    org_factory,
    user_factory,
) -> None:
    order_id, stop_id, _pkgs, _w = await _seed_order_stop_packages(db_session, org_factory, user_factory)
    svc = OrderService(db_session)
    entry = await svc.create_stop_note(
        order_id=order_id,
        stop_id=stop_id,
        note_type="ADMIN_NOTE",
        message="Gate code",
        is_blocking=False,
        sort_order=0,
        images=None,
    )
    assert entry.note_type == StopNoteType.ADMIN.value


@pytest.mark.asyncio
async def test_update_note_type_to_admin_clears_package_ids(
    db_session: AsyncSession,
    org_factory,
    user_factory,
) -> None:
    order_id, stop_id, pkgs, _w = await _seed_order_stop_packages(db_session, org_factory, user_factory)
    svc = OrderService(db_session)
    created = await svc.create_stop_note(
        order_id=order_id,
        stop_id=stop_id,
        note_type=StopNoteType.PACKAGE_ISSUE_NOTE.value,
        message="issue",
        is_blocking=False,
        sort_order=0,
        images=None,
        package_ids=[pkgs[0].id],
    )
    assert created.package_ids == [pkgs[0].id]
    updated = await svc.update_stop_note(
        order_id=order_id,
        stop_id=stop_id,
        note_id=created.id,
        note_type=StopNoteType.ADMIN.value,
        message="ops instruction",
        is_blocking=None,
        sort_order=None,
        images=None,
        deleted_image_ids=None,
        package_ids=None,
        update_package_ids=False,
    )
    assert updated.note_type == StopNoteType.ADMIN.value
    assert updated.package_ids == []


@pytest.mark.asyncio
async def test_list_stop_notes_omits_stale_linked_package_id(
    db_session: AsyncSession,
    org_factory,
    user_factory,
) -> None:
    order_id, stop_id, pkgs, _w = await _seed_order_stop_packages(db_session, org_factory, user_factory)
    stale = str(uuid.uuid4())
    svc = OrderService(db_session)
    note = await svc.create_stop_note(
        order_id=order_id,
        stop_id=stop_id,
        note_type=StopNoteType.PACKAGE_ISSUE_NOTE.value,
        message="stale row",
        is_blocking=False,
        sort_order=0,
        images=None,
        package_ids=[pkgs[0].id],
    )
    from app.modules.orders.models import StopNote

    row = await db_session.get(StopNote, note.id)
    assert row is not None
    row.package_ids = [pkgs[0].id, stale]
    await db_session.commit()

    listed = await svc.list_stop_notes(order_id=order_id, stop_id=stop_id)
    match = next(x for x in listed if x.id == note.id)
    assert match.package_ids == [pkgs[0].id]


@pytest.mark.asyncio
async def test_update_package_ids_requires_ack_semantics_via_replace(
    db_session: AsyncSession,
    org_factory,
    user_factory,
) -> None:
    order_id, stop_id, pkgs, _w = await _seed_order_stop_packages(db_session, org_factory, user_factory)
    svc = OrderService(db_session)
    created = await svc.create_stop_note(
        order_id=order_id,
        stop_id=stop_id,
        note_type=StopNoteType.PACKAGE_ISSUE_NOTE.value,
        message="a",
        is_blocking=False,
        sort_order=0,
        images=None,
        package_ids=[pkgs[0].id],
    )
    updated = await svc.update_stop_note(
        order_id=order_id,
        stop_id=stop_id,
        note_id=created.id,
        note_type=None,
        message=None,
        is_blocking=None,
        sort_order=None,
        images=None,
        deleted_image_ids=None,
        package_ids=[pkgs[1].id],
        update_package_ids=True,
    )
    assert updated.package_ids == [pkgs[1].id]
