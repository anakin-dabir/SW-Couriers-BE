"""Driver repository — data-access layer for Driver model.

Extends BaseRepository for CRUD, pagination, and optimistic locking.
Business logic lives in DriverService. Data-level scoping can be applied
via scope_filters when drivers are tied to org/region in the future.
"""

from sqlalchemy import Select, String, and_, cast, func, or_, select
from sqlalchemy import not_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.common.repository import BaseRepository
from app.modules.drivers.models import (
    Driver,
    DriverDraft,
    DriverDocument,
    DriverShift,
    DriverTermsClause,
    DriverTermsAndConditions,
    DriverTimeOff,
    DriverTrafficViolation,
    DriverTrafficViolationProof,
    DriverWeeklySchedule,
)
from app.modules.user.models import User
from app.modules.drivers.enums import DriverAccountStatus


class DriverRepository(BaseRepository):
    """Repository for the Driver model."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Driver)

    async def find_by_user_id(self, user_id: str) -> Driver | None:
        """Find a driver by linked user id (one driver per user)."""
        return await self.find_one(user_id=user_id)

    async def get_by_id_with_user(self, driver_id: str, **scope_filters: object) -> Driver | None:
        """Get driver by id with related user and draft loaded (single query-ish).

        Draft is included so draft JSONB fields can be rendered in responses without
        additional round trips.
        """
        stmt = (
            select(Driver)
            .where(Driver.id == driver_id)
            .options(joinedload(Driver.user), selectinload(Driver.draft))
        )
        stmt = self._apply_where(stmt, **scope_filters)
        result = await self.session.execute(stmt)
        return result.unique().scalar_one_or_none()

    # ── List & KPI helpers ────────────────────────────

    def _apply_admin_list_filters_to_pair(
        self,
        stmt: Select | None,
        count_stmt: Select,
        *,
        search: str | None = None,
        account_status: list[str] | None = None,
        exclude_drafts: bool = True,
        live_status: list[str] | None = None,
        depot_id: str | None = None,
    ) -> tuple[Select | None, Select]:
        filters = []
        if exclude_drafts and not account_status:
            filters.append(Driver.account_status != DriverAccountStatus.DRAFT)
            filters.append(Driver.user_id.is_not(None))
        if account_status:
            filters.append(Driver.account_status.in_(account_status))
        if live_status:
            filters.append(Driver.live_status.in_(live_status))
        if depot_id is not None:
            filters.append(Driver.depot_id == depot_id)
        if filters:
            cond = and_(*filters)
            if stmt is not None:
                stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)
        if search:
            like = f"%{search.strip()}%"
            if stmt is not None:
                stmt = stmt.join(User, Driver.user_id == User.id)
            count_stmt = count_stmt.join(User, Driver.user_id == User.id)
            search_cond = or_(
                Driver.driver_code.ilike(like),
                (User.first_name + " " + User.last_name).ilike(like),
                User.phone.ilike(like),
            )
            if stmt is not None:
                stmt = stmt.where(search_cond)
            count_stmt = count_stmt.where(search_cond)
        return stmt, count_stmt

    async def search_and_filter(
        self,
        *,
        page: int,
        size: int,
        search: str | None = None,
        account_status: list[str] | None = None,
        exclude_drafts: bool = True,
        live_status: list[str] | None = None,
        depot_id: str | None = None,
        order_by: str | None = "created_at",
        order_desc: bool = True,
    ) -> tuple[list[Driver], int]:
        """Search and filter drivers with pagination.

        Search covers driver_code, full name, and phone.
        """
        stmt: Select = select(Driver).options(joinedload(Driver.user))
        # Draft list endpoints often need draft_id/created_by from pivot table.
        if account_status and any(s in {DriverAccountStatus.DRAFT, DriverAccountStatus.DRAFT.value} for s in account_status):
            stmt = stmt.options(selectinload(Driver.draft))
        count_stmt: Select = select(func.count()).select_from(Driver)

        stmt, count_stmt = self._apply_admin_list_filters_to_pair(
            stmt,
            count_stmt,
            search=search,
            account_status=account_status,
            exclude_drafts=exclude_drafts,
            live_status=live_status,
            depot_id=depot_id,
        )

        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()

        # ordering
        order_column = getattr(Driver, order_by or "created_at", None)
        if order_column is None:
            order_column = Driver.created_at
        stmt = stmt.order_by(order_column.desc() if order_desc else order_column.asc())

        offset = (page - 1) * size
        stmt = stmt.offset(offset).limit(size)
        result = await self.session.execute(stmt)
        items = list(result.scalars().all())
        return items, total

    async def search_and_filter_drafts(
        self,
        *,
        page: int,
        size: int,
        search: str | None = None,
        live_status: list[str] | None = None,
        depot_id: str | None = None,
        order_by: str | None = "created_at",
        order_desc: bool = True,
    ) -> tuple[list[Driver], int]:
        """Search and filter DRAFT drivers with pagination.

        Draft identity fields are stored in driver_drafts.draft_data (JSONB) until submit.
        Search covers:
        - drivers.driver_code
        - driver_drafts.draft_id
        - driver_drafts.draft_data: email, phone, first_name, last_name, and full name.
        """
        stmt: Select = (
            select(Driver)
            .where(Driver.account_status == DriverAccountStatus.DRAFT.value)
            .outerjoin(DriverDraft, DriverDraft.driver_id == Driver.id)
            .options(joinedload(Driver.user), selectinload(Driver.draft))
        )
        count_stmt: Select = (
            select(func.count())
            .select_from(Driver)
            .where(Driver.account_status == DriverAccountStatus.DRAFT.value)
            .outerjoin(DriverDraft, DriverDraft.driver_id == Driver.id)
        )

        filters = []
        if live_status:
            filters.append(Driver.live_status.in_(live_status))
        if depot_id is not None:
            filters.append(Driver.depot_id == depot_id)
        if filters:
            cond = and_(*filters)
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)

        if search:
            like = f"%{search.strip()}%"
            email = cast(DriverDraft.draft_data["email"].astext, String)
            phone = cast(DriverDraft.draft_data["phone"].astext, String)
            first_name = cast(DriverDraft.draft_data["first_name"].astext, String)
            last_name = cast(DriverDraft.draft_data["last_name"].astext, String)
            full_name = func.trim(func.coalesce(first_name, "") + " " + func.coalesce(last_name, ""))

            search_cond = or_(
                Driver.driver_code.ilike(like),
                DriverDraft.draft_id.ilike(like),
                email.ilike(like),
                phone.ilike(like),
                first_name.ilike(like),
                last_name.ilike(like),
                full_name.ilike(like),
            )
            stmt = stmt.where(search_cond)
            count_stmt = count_stmt.where(search_cond)

        total = (await self.session.execute(count_stmt)).scalar_one()

        order_column = getattr(Driver, order_by or "created_at", None) or Driver.created_at
        stmt = stmt.order_by(order_column.desc() if order_desc else order_column.asc())

        offset = (page - 1) * size
        stmt = stmt.offset(offset).limit(size)
        result = await self.session.execute(stmt)
        items = list(result.unique().scalars().all())
        return items, total

    async def count_drivers_default_admin_list_total(self) -> int:
        """Drivers returned by GET /v1/drivers when no filters are applied (exclude DRAFT; linked user required)."""
        count_stmt = select(func.count()).select_from(Driver)
        _, count_stmt = self._apply_admin_list_filters_to_pair(
            None,
            count_stmt,
            search=None,
            account_status=None,
            exclude_drafts=True,
            live_status=None,
            depot_id=None,
        )
        result = await self.session.execute(count_stmt)
        return result.scalar_one()

    async def count_by_account_status(self, status: str | DriverAccountStatus) -> int:
        status_val = status.value if isinstance(status, DriverAccountStatus) else status
        stmt = select(func.count()).select_from(Driver).where(
            Driver.account_status == status_val,
            Driver.user_id.is_not(None),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()


class DriverDocumentRepository(BaseRepository):
    """Repository for driver documents."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, DriverDocument)


class DriverTermsAndConditionsRepository(BaseRepository):
    """Repository for driver terms and conditions."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, DriverTermsAndConditions)

    async def find_current_active(self) -> DriverTermsAndConditions | None:
        stmt = (
            select(DriverTermsAndConditions)
            .where(DriverTermsAndConditions.is_active.is_(True))
            .order_by(DriverTermsAndConditions.effective_from.desc().nullslast(), DriverTermsAndConditions.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self) -> list[DriverTermsAndConditions]:
        stmt = select(DriverTermsAndConditions).order_by(DriverTermsAndConditions.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def deactivate_all(self) -> None:
        stmt = (
            DriverTermsAndConditions.__table__.update()
            .values(is_active=False)
            .where(DriverTermsAndConditions.is_active.is_(True))
        )
        await self.session.execute(stmt)

    async def update_terms_by_id(self, terms_id: str, data: dict[str, object]) -> DriverTermsAndConditions:
        """Update terms row without generic integer-version bump logic."""
        row = await self.get_by_id_or_404(terms_id)
        for key, value in data.items():
            if hasattr(row, key):
                setattr(row, key, value)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def list_clauses(self, terms_id: str) -> list[DriverTermsClause]:
        stmt = (
            select(DriverTermsClause)
            .where(DriverTermsClause.terms_id == terms_id)
            .order_by(DriverTermsClause.clause_order.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def replace_clauses(
        self,
        *,
        terms_id: str,
        clauses: list[dict[str, object]],
    ) -> list[DriverTermsClause]:
        await self.session.execute(DriverTermsClause.__table__.delete().where(DriverTermsClause.terms_id == terms_id))
        created: list[DriverTermsClause] = []
        for c in clauses:
            row = DriverTermsClause(
                terms_id=terms_id,
                clause_order=int(c["clause_order"]),
                heading=str(c["heading"]),
                body=str(c["body"]),
            )
            self.session.add(row)
            created.append(row)
        await self.session.flush()
        for row in created:
            await self.session.refresh(row)
        created.sort(key=lambda r: r.clause_order)
        return created


class DriverDraftRepository(BaseRepository):
    """Repository for driver draft pivot rows."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, DriverDraft)


class DriverTimeOffRepository(BaseRepository):
    """Repository for driver planned time off."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, DriverTimeOff)


class DriverWeeklyScheduleRepository(BaseRepository):
    """Repository for driver weekly schedules."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, DriverWeeklySchedule)


class DriverShiftRepository(BaseRepository):
    """Repository for driver shifts."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, DriverShift)


class DriverTrafficViolationRepository(BaseRepository):
    """Repository for driver traffic violations."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, DriverTrafficViolation)

    async def get_by_id_with_proofs(self, violation_id: str) -> DriverTrafficViolation | None:
        stmt: Select = (
            select(DriverTrafficViolation)
            .where(DriverTrafficViolation.id == violation_id)
            .options(selectinload(DriverTrafficViolation.proofs))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_all_with_proofs(
        self,
        *,
        page: int,
        size: int,
        order_by: str | None = "occurred_at",
        order_desc: bool = True,
        driver_id: str,
    ) -> tuple[list[DriverTrafficViolation], int]:
        """Paginated violations for a driver with proofs loaded (no N+1)."""
        stmt: Select = select(DriverTrafficViolation).where(DriverTrafficViolation.driver_id == driver_id).options(
            selectinload(DriverTrafficViolation.proofs)
        )
        count_stmt: Select = select(func.count()).select_from(DriverTrafficViolation).where(DriverTrafficViolation.driver_id == driver_id)

        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()

        order_column = getattr(DriverTrafficViolation, order_by or "occurred_at", None) or DriverTrafficViolation.occurred_at
        stmt = stmt.order_by(order_column.desc() if order_desc else order_column.asc())
        stmt = stmt.offset((page - 1) * size).limit(size)
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total


class DriverTrafficViolationProofRepository(BaseRepository):
    """Repository for driver traffic violation proofs."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, DriverTrafficViolationProof)
