"""Business logic for holidays (admin-only configuration)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.exceptions import ValidationError
from app.common.service import BaseService
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.drivers.models import Driver
from app.modules.holidays.enums import HolidayAudience
from app.modules.holidays.models import Holiday, HolidayAllowedDriver
from app.modules.drivers.schedule.holiday_rules import allowed_driver_ids as get_holiday_allowed_driver_ids
from app.modules.drivers.schedule.notify import notify_drivers_for_holiday_allow_list_change
from app.modules.holidays.repository import HolidayAllowedDriverRepository, HolidayRepository
from app.modules.user.models import User


class HolidayService(BaseService):
    """Service layer for managing holidays and allowed drivers."""

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._holiday_repo = HolidayRepository(session)
        self._allowed_repo = HolidayAllowedDriverRepository(session)
        self._audit = AuditService(session)
        self._ip_address = request.client.host if request and request.client else None
        self._user_agent = request.headers.get("user-agent") if request else None

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _ensure_valid_year_window(start_date: date, end_date: date, year: int) -> None:
        if end_date < start_date:
            raise ValidationError("end_date cannot be before start_date")
        if start_date.year != year:
            raise ValidationError("year must match start_date year")
        if end_date.year not in {year, year + 1}:
            raise ValidationError("end_date must be in the same year as start_date or in the next year")

    @staticmethod
    def _shift_date_by_years(d: date, years_delta: int) -> date:
        try:
            return date(d.year + years_delta, d.month, d.day)
        except ValueError as exc:
            raise ValidationError(f"Date {d.isoformat()} cannot be shifted by {years_delta} year(s): {exc}") from exc

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
        category: AuditCategory = AuditCategory.SYSTEM,
        event_type: AuditEventType | str = AuditEventType.HOLIDAY_CONFIGURED,
    ) -> None:
        if self._audit is None:
            return
        await self._audit.log(
            action=action,
            entity_type="holiday",
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

    # ── CRUD ───────────────────────────────────────────────────────

    async def list_holidays(
        self,
        *,
        year: int | None = None,
        audience: HolidayAudience | None = None,
    ) -> list[Holiday]:
        filters: dict[str, object] = {}
        if year is not None:
            filters["year"] = year
        if audience is not None:
            filters["audience"] = audience.value
        items, _ = await self._holiday_repo.find_all(page=1, size=10_000, **filters)
        return items

    async def list_holiday_years(self) -> list[tuple[int, int]]:
        return await self._holiday_repo.list_year_counts()

    async def get_allowed_driver_name_map(self, driver_ids: Iterable[str]) -> dict[str, str]:
        ids = list(set(driver_ids))
        if not ids:
            return {}

        stmt = (
            select(Driver.id, Driver.driver_code, User.first_name, User.last_name)
            .outerjoin(User, Driver.user_id == User.id)
            .where(Driver.id.in_(ids))
        )
        rows = (await self._session.execute(stmt)).all()

        result: dict[str, str] = {}
        for driver_id, driver_code, first_name, last_name in rows:
            full_name = f"{(first_name or '').strip()} {(last_name or '').strip()}".strip()
            result[str(driver_id)] = full_name or str(driver_code) or "Unknown Driver"
        return result

    async def get_holiday(self, holiday_id: str) -> Holiday:
        return await self._holiday_repo.get_by_id_or_404(holiday_id)

    async def _set_allowed_drivers(self, holiday_id: str, driver_ids: Iterable[str]) -> None:
        # Delete existing rows
        stmt = delete(HolidayAllowedDriver).where(HolidayAllowedDriver.holiday_id == holiday_id)
        await self._session.execute(stmt)
        # Insert new ones
        for driver_id in driver_ids:
            await self._allowed_repo.create({"holiday_id": holiday_id, "driver_id": driver_id})

    async def create_holiday(
        self,
        *,
        name: str,
        start_date: date,
        end_date: date,
        audience: HolidayAudience,
        allow_shifts: bool,
        allowed_driver_ids: list[str] | None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Holiday:
        year = start_date.year
        self._ensure_valid_year_window(start_date, end_date, year)

        holiday = await self._holiday_repo.create(
            {
                "name": name,
                "year": year,
                "start_date": start_date,
                "end_date": end_date,
                "audience": audience.value,
                "allow_shifts": allow_shifts,
            }
        )

        if allow_shifts and allowed_driver_ids:
            await self._set_allowed_drivers(holiday.id, allowed_driver_ids)

        await self._log_audit(
            "holiday.create",
            entity_id=holiday.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={
                "name": holiday.name,
                "year": holiday.year,
                "start_date": holiday.start_date.isoformat(),
                "end_date": holiday.end_date.isoformat(),
                "audience": holiday.audience,
                "allow_shifts": holiday.allow_shifts,
                "allowed_driver_ids": allowed_driver_ids or [],
            },
            severity="NOTICE",
        )

        if allow_shifts and allowed_driver_ids:
            await notify_drivers_for_holiday_allow_list_change(
                self._session,
                driver_ids=set(allowed_driver_ids),
                change_summary=f"Holiday schedule rules were updated ({name})",
                audit_user_id=audit_user_id,
            )

        return holiday

    async def update_holiday(
        self,
        *,
        holiday_id: str,
        name: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        audience: HolidayAudience | None = None,
        allow_shifts: bool | None = None,
        allowed_driver_ids: list[str] | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Holiday:
        await self._holiday_repo.get_by_id_or_404(holiday_id)
        stmt = (
            select(Holiday)
            .where(Holiday.id == holiday_id)
            .options(selectinload(Holiday.allowed_drivers))
        )
        holiday = (await self._session.execute(stmt)).scalar_one()
        old_allow_shifts = holiday.allow_shifts
        old_allowed_ids = get_holiday_allowed_driver_ids(holiday) if old_allow_shifts else set()

        new_name = name if name is not None else holiday.name
        new_start = start_date if start_date is not None else holiday.start_date
        new_end = end_date if end_date is not None else holiday.end_date
        new_year = new_start.year
        new_audience = audience.value if audience is not None else holiday.audience
        new_allow_shifts = allow_shifts if allow_shifts is not None else holiday.allow_shifts

        self._ensure_valid_year_window(new_start, new_end, new_year)

        data: dict[str, object] = {
            "name": new_name,
            "year": new_year,
            "start_date": new_start,
            "end_date": new_end,
            "audience": new_audience,
            "allow_shifts": new_allow_shifts,
        }

        await self._holiday_repo.update_by_id(holiday_id, data)

        # Replace allowed drivers only if explicitly provided
        if allowed_driver_ids is not None:
            if new_allow_shifts and allowed_driver_ids:
                await self._set_allowed_drivers(holiday_id, allowed_driver_ids)
            else:
                await self._set_allowed_drivers(holiday_id, [])

        await self._log_audit(
            "holiday.update",
            entity_id=holiday_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={
                "name": holiday.name,
                "year": holiday.year,
                "start_date": holiday.start_date.isoformat(),
                "end_date": holiday.end_date.isoformat(),
                "audience": holiday.audience,
                "allow_shifts": holiday.allow_shifts,
            },
            new_value={
                "name": new_name,
                "year": new_year,
                "start_date": new_start.isoformat(),
                "end_date": new_end.isoformat(),
                "audience": new_audience,
                "allow_shifts": new_allow_shifts,
            },
            severity="NOTICE",
        )

        # Ensure relationship collections are reloaded after explicit delete/insert.
        self._session.expire_all()
        updated = await self._holiday_repo.get_by_id_or_404(holiday_id)
        reload_stmt = (
            select(Holiday)
            .where(Holiday.id == holiday_id)
            .options(selectinload(Holiday.allowed_drivers))
        )
        reloaded = (await self._session.execute(reload_stmt)).scalar_one()
        new_allowed_ids = get_holiday_allowed_driver_ids(reloaded) if reloaded.allow_shifts else set()

        schedule_rules_changed = (allow_shifts is not None and allow_shifts != old_allow_shifts) or (
            allowed_driver_ids is not None
        )
        if schedule_rules_changed:
            await notify_drivers_for_holiday_allow_list_change(
                self._session,
                driver_ids=old_allowed_ids | new_allowed_ids,
                change_summary=f"Holiday work rules were updated ({reloaded.name})",
                audit_user_id=audit_user_id,
            )

        return updated

    async def delete_holiday(
        self,
        *,
        holiday_id: str,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> None:
        existing = await self._holiday_repo.get_by_id_or_404(holiday_id)
        await self._holiday_repo.hard_delete(holiday_id)
        await self._log_audit(
            "holiday.delete",
            entity_id=holiday_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={
                "name": existing.name,
                "year": existing.year,
                "start_date": existing.start_date.isoformat(),
                "end_date": existing.end_date.isoformat(),
                "audience": existing.audience,
                "allow_shifts": existing.allow_shifts,
            },
            severity="CRITICAL",
        )

    # ── Copy between years ─────────────────────────────────────────

    async def copy_holidays(
        self,
        *,
        source_year: int,
        target_year: int,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> int:
        if source_year == target_year:
            raise ValidationError("source_year and target_year must be different")

        # Load all holidays for source year
        src_holidays, _ = await self._holiday_repo.find_all(page=1, size=10_000, order_by="start_date", order_desc=False, year=source_year)

        # Delete existing target-year holidays (and their allowed drivers via cascade)
        stmt = delete(Holiday).where(Holiday.year == target_year)
        await self._session.execute(stmt)

        copied = 0
        created_ids: list[str] = []
        years_delta = target_year - source_year
        for src in src_holidays:
            new_start = self._shift_date_by_years(src.start_date, years_delta)
            new_end = self._shift_date_by_years(src.end_date, years_delta)
            self._ensure_valid_year_window(new_start, new_end, target_year)

            new_holiday = await self._holiday_repo.create(
                {
                    "name": src.name,
                    "year": target_year,
                    "start_date": new_start,
                    "end_date": new_end,
                    "audience": src.audience,
                    "allow_shifts": src.allow_shifts,
                }
            )

            # Copy allowed drivers
            if src.allow_shifts and src.allowed_drivers:
                driver_ids = [row.driver_id for row in src.allowed_drivers]
                await self._set_allowed_drivers(new_holiday.id, driver_ids)

            copied += 1
            created_ids.append(new_holiday.id)

        await self._log_audit(
            "holiday.copy_year",
            entity_id=None,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={
                "source_year": source_year,
                "target_year": target_year,
                "copied_count": copied,
                "holiday_ids": created_ids,
            },
            severity="NOTICE",
        )

        return copied
