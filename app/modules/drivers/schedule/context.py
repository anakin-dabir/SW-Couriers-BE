"""Shared repositories/session for schedule operations."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.drivers.repository import (
    DriverRepository,
    DriverShiftRepository,
    DriverWeeklyScheduleRepository,
)
from app.modules.user.repository import UserRepository


@dataclass
class ScheduleContext:
    session: AsyncSession
    driver_repo: DriverRepository
    shift_repo: DriverShiftRepository
    weekly_repo: DriverWeeklyScheduleRepository
    user_repo: UserRepository
