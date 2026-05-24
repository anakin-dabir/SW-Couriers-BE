from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict, cast

from sqlalchemy import ColumnElement, and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, joinedload

from app.common.repository import BaseRepository
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.helpers import audit_actor_filter_clause, audit_actor_label
from app.modules.audit.models import AuditLog, AuditSavedView
from app.modules.user.models import User


class DataAccessSummaryRow(TypedDict):
    admin: str
    email: str
    events: int
    last_access: datetime | None


class DataAccessHeatmapRow(TypedDict):
    day: int
    hour: int
    count: int


class ChangeFieldRow(TypedDict):
    field: str
    before: Any
    after: Any


class ChangeHistoryRow(TypedDict):
    id: str
    created_at: datetime
    category: str
    entity_type: str
    entity_ref: str | None
    action: str
    email: str | None
    actor: str | None
    fields_changed: int
    summary: str | None
    changes: list[ChangeFieldRow]


class FieldHistoryRow(TypedDict):
    timestamp: datetime
    before: Any
    after: Any
    actor: str | None
    reason: str | None
    event_type: str | None
    email: str | None


class FieldHistoryTrendPoint(TypedDict):
    date: str
    value: float | None


class ComparisonResultRow(TypedDict):
    field: str
    value_a: Any
    value_b: Any
    changes: int


class AuditRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AuditLog)

    async def get_log_by_id(
        self, organization_id: str, audit_log_id: str
    ) -> AuditLog | None:
        """Return a single audit row scoped to the organization (direct or via its users).

        Joins the user so callers can format actor/email without an extra round trip.
        """
        stmt = (
            select(AuditLog)
            .outerjoin(User, AuditLog.user_id == User.id)
            .where(AuditLog.id == audit_log_id)
            .where(
                or_(
                    AuditLog.organization_id == organization_id,
                    User.organization_id == organization_id,
                )
            )
            .options(contains_eager(AuditLog.user))
        )
        result = await self.session.execute(stmt)
        return result.unique().scalar_one_or_none()

    async def get_related_events(
        self,
        organization_id: str,
        correlation_id: str,
        exclude_id: str | None = None,
        limit: int = 50,
    ) -> list[AuditLog]:
        """Audit rows sharing the same correlation_id (same HTTP request), sorted chronologically."""
        stmt = (
            select(AuditLog)
            .outerjoin(User, AuditLog.user_id == User.id)
            .where(
                AuditLog.correlation_id == correlation_id,
                or_(
                    AuditLog.organization_id == organization_id,
                    User.organization_id == organization_id,
                ),
            )
            .options(contains_eager(AuditLog.user))
            .order_by(AuditLog.created_at.asc())
            .limit(limit)
        )
        if exclude_id:
            stmt = stmt.where(AuditLog.id != exclude_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().unique().all())

    async def iter_logs_for_export(
        self,
        organization_id: str,
        *,
        category: list[str] | None = None,
        event_type: list[str] | None = None,
        severity: list[str] | None = None,
        actor: str | None = None,
        search: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        chunk_size: int = 500,
    ) -> AsyncIterator[AuditLog]:
        """Stream audit rows for an organization, applying the standard filter set.

        Uses ``stream()`` so we don't materialize the full export in memory for large ranges.
        """
        stmt = (
            select(AuditLog)
            .outerjoin(User, AuditLog.user_id == User.id)
            .where(
                or_(
                    AuditLog.organization_id == organization_id,
                    User.organization_id == organization_id,
                )
            )
            .options(contains_eager(AuditLog.user))
            .order_by(AuditLog.created_at.desc())
            .execution_options(yield_per=chunk_size)
        )

        if category and "all" not in category:
            stmt = stmt.where(AuditLog.category.in_(category))
        if event_type and "all" not in event_type:
            stmt = stmt.where(AuditLog.event_type.in_(event_type))
        if severity and "all" not in severity:
            stmt = stmt.where(AuditLog.severity.in_(severity))
        actor_clause = audit_actor_filter_clause(actor)
        if actor_clause is not None:
            stmt = stmt.where(actor_clause)
        if from_date:
            stmt = stmt.where(AuditLog.created_at >= from_date)
        if to_date:
            stmt = stmt.where(AuditLog.created_at <= to_date)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    AuditLog.ip_address.ilike(pattern),
                    AuditLog.action.ilike(pattern),
                    AuditLog.reason.ilike(pattern),
                    AuditLog.entity_ref.ilike(pattern),
                    User.email.ilike(pattern),
                )
            )

        result = await self.session.stream(stmt)
        async for row in result.scalars():
            yield row

    async def get_latest_hash_for_org(self, organization_id: str) -> str | None:
        """Most recent integrity_hash in the per-organization chain (None if chain is empty)."""
        stmt = (
            select(AuditLog.integrity_hash)
            .where(
                AuditLog.organization_id == organization_id,
                AuditLog.integrity_hash.is_not(None),
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_organization_logs(
        self,
        organization_id: str,
        page: int = 1,
        size: int = 50,
        category: list[str] | None = None,
        event_type: list[str] | None = None,
        severity: list[str] | None = None,
        actor: str | None = None, # Admin, Client
        browser: list[str] | None = None,
        search: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        sort_by: str = "desc", # desc, asc
        ui_category: list[str] | None = None,
    ) -> tuple[list[AuditLog], int]:
        """Get all logs related to an organization (direct OR via its users)."""
        # Base query joining with User to get emails and verify user's organization
        # We select both to allow contains_eager to work
        stmt = (
            select(AuditLog, User)
            .outerjoin(User, AuditLog.user_id == User.id)
            .where(
                or_(
                    AuditLog.organization_id == organization_id,
                    User.organization_id == organization_id,
                )
            )
        )

        if category and "all" not in category:
            stmt = stmt.where(AuditLog.category.in_(category))

        if event_type and "all" not in event_type:
            stmt = stmt.where(AuditLog.event_type.in_(event_type))

        if severity and "all" not in severity:
            stmt = stmt.where(AuditLog.severity.in_(severity))

        actor_clause = audit_actor_filter_clause(actor)
        if actor_clause is not None:
            stmt = stmt.where(actor_clause)

        if browser and "all" not in browser:
            stmt = stmt.where(AuditLog.browser.in_(browser))

        if ui_category and "all" not in ui_category:
            ui_clauses = []
            for uc in ui_category:
                ucl = uc.lower()
                if ucl == "booking": 
                    ui_clauses.append(or_(AuditLog.category == AuditCategory.ORDER, AuditLog.event_type.ilike("%BOOKING%")))
                elif ucl == "delivery":
                    ui_clauses.append(or_(AuditLog.category == AuditCategory.ORDER, AuditLog.event_type.ilike("%DELIVERY%"), AuditLog.event_type.ilike("%POD%")))
                elif ucl == "login":
                    ui_clauses.append(or_(AuditLog.category.in_([AuditCategory.ACCESS, AuditCategory.SECURITY]), AuditLog.event_type.ilike("%LOGIN%"), AuditLog.event_type.ilike("%SESSION%")))
                elif ucl == "payment":
                    ui_clauses.append(or_(AuditLog.category == AuditCategory.BILLING, AuditLog.event_type.ilike("%PAYMENT%")))
                elif ucl == "invoice":
                    ui_clauses.append(or_(AuditLog.category == AuditCategory.BILLING, AuditLog.event_type.ilike("%INVOICE%")))
                elif ucl == "credit":
                    ui_clauses.append(AuditLog.category == AuditCategory.CREDIT)
                elif ucl == "account":
                    ui_clauses.append(or_(AuditLog.category.in_([AuditCategory.ACCOUNT, AuditCategory.CONTACT]), AuditLog.event_type.ilike("%ACCOUNT%"), AuditLog.event_type.ilike("%CONTACT%")))
                elif ucl == "system":
                    ui_clauses.append(AuditLog.category == AuditCategory.SYSTEM)
            
            if ui_clauses:
                stmt = stmt.where(or_(*ui_clauses))

        if search:
            search_pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    AuditLog.ip_address.ilike(search_pattern),
                    AuditLog.browser.ilike(search_pattern),
                    AuditLog.action.ilike(search_pattern),
                    AuditLog.reason.ilike(search_pattern),
                    AuditLog.entity_ref.ilike(search_pattern),
                    User.email.ilike(search_pattern),
                    User.first_name.ilike(search_pattern),
                    User.last_name.ilike(search_pattern),
                )
            )

        if from_date:
            stmt = stmt.where(AuditLog.created_at >= from_date)
        
        if to_date:
            stmt = stmt.where(AuditLog.created_at <= to_date)

        # Count total rows matching the filters
        subquery = stmt.subquery()
        count_stmt = select(func.count()).select_from(subquery)
        total = (await self.session.execute(count_stmt)).scalar_one()

        # Populate the .user relationship from the columns already present in the join
        stmt = stmt.options(contains_eager(AuditLog.user))
        
        if sort_by.lower() == "asc":
            stmt = stmt.order_by(AuditLog.created_at.asc())
        else:
            stmt = stmt.order_by(AuditLog.created_at.desc())

        stmt = stmt.offset((page - 1) * size).limit(size)

        result = await self.session.execute(stmt)
        # scalars() will return the first element of each row (the AuditLog)
        return list(result.scalars().unique().all()), total

    @staticmethod
    def driver_activity_scope(driver_id: str, driver_user_id: str | None) -> ColumnElement:
        """Rows that belong to a driver: actor is linked user, entity is driver, or JSONB carries driver_id."""
        new_did = func.jsonb_extract_path_text(AuditLog.new_value, "driver_id")
        old_did = func.jsonb_extract_path_text(AuditLog.old_value, "driver_id")
        parts: list[ColumnElement] = [
            and_(AuditLog.entity_type == "driver", AuditLog.entity_id == driver_id),
            new_did == driver_id,
            old_did == driver_id,
        ]
        if driver_user_id:
            parts.insert(0, AuditLog.user_id == driver_user_id)
        return or_(*parts)

    async def get_driver_activity_logs(
        self,
        *,
        driver_id: str,
        driver_user_id: str | None,
        page: int = 1,
        size: int = 50,
        search: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        sort_by: str = "desc",
    ) -> tuple[list[AuditLog], int]:
        """Paginated audit rows scoped to a single driver (see driver_activity_scope)."""
        scope = self.driver_activity_scope(driver_id, driver_user_id)
        stmt = (
            select(AuditLog, User)
            .outerjoin(User, AuditLog.user_id == User.id)
            .where(scope)
        )

        if search:
            search_pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    AuditLog.ip_address.ilike(search_pattern),
                    AuditLog.action.ilike(search_pattern),
                    AuditLog.reason.ilike(search_pattern),
                    AuditLog.audit_ref.ilike(search_pattern),
                    User.email.ilike(search_pattern),
                    User.first_name.ilike(search_pattern),
                    User.last_name.ilike(search_pattern),
                )
            )

        if from_date:
            stmt = stmt.where(AuditLog.created_at >= from_date)

        if to_date:
            stmt = stmt.where(AuditLog.created_at <= to_date)

        subquery = stmt.subquery()
        count_stmt = select(func.count()).select_from(subquery)
        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = stmt.options(contains_eager(AuditLog.user))
        if sort_by.lower() == "asc":
            stmt = stmt.order_by(AuditLog.created_at.asc())
        else:
            stmt = stmt.order_by(AuditLog.created_at.desc())

        stmt = stmt.offset((page - 1) * size).limit(size)
        result = await self.session.execute(stmt)
        return list(result.scalars().unique().all()), total

    async def get_driver_activity_log_by_id(
        self,
        *,
        driver_id: str,
        driver_user_id: str | None,
        audit_log_id: str,
    ) -> AuditLog | None:
        """Single audit row if it exists and is in scope for the driver."""
        scope = self.driver_activity_scope(driver_id, driver_user_id)
        stmt = (
            select(AuditLog)
            .outerjoin(User, AuditLog.user_id == User.id)
            .where(AuditLog.id == audit_log_id)
            .where(scope)
            .options(contains_eager(AuditLog.user))
        )
        result = await self.session.execute(stmt)
        return result.unique().scalar_one_or_none()

    async def get_summary_stats(self, organization_id: str) -> dict[str, Any]:
        """Aggregate stats for the organization audit dashboard."""
        now = datetime.now(UTC)
        day_ago = now - timedelta(days=1)
        prev_day_start = now - timedelta(days=2)
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)

        # Base filter for the organization (direct or via users)
        org_filter = or_(
            AuditLog.organization_id == organization_id,
            AuditLog.user_id.in_(
                select(User.id).where(User.organization_id == organization_id)
            )
        )

        # 1. Total Events (24h) and trend
        count_24h = (await self.session.execute(
            select(func.count()).where(org_filter, AuditLog.created_at >= day_ago)
        )).scalar_one() or 0
        
        count_prev_24h = (await self.session.execute(
            select(func.count()).where(org_filter, AuditLog.created_at >= prev_day_start, AuditLog.created_at < day_ago)
        )).scalar_one() or 0
        
        trend_pct = 0.0
        if count_prev_24h > 0:
            trend_pct = ((count_24h - count_prev_24h) / count_prev_24h) * 100

        # 2. Critical Events (7d)
        critical_stmt = select(AuditLog).where(
            org_filter, 
            AuditLog.created_at >= week_ago,
            AuditLog.severity == "CRITICAL"
        ).order_by(desc(AuditLog.created_at))
        
        critical_results = await self.session.execute(critical_stmt)
        critical_items = list(critical_results.scalars().all())
        latest_critical = critical_items[0].reason or critical_items[0].action if critical_items else None

        # 3. Warning Events (7d)
        warning_stmt = select(AuditLog).where(
            org_filter,
            AuditLog.created_at >= week_ago,
            AuditLog.severity == "WARNING"
        )
        warning_results = await self.session.execute(warning_stmt)
        warning_items = list(warning_results.scalars().all())

        # Top category logic (based on the new category column)
        top_warn_category = "General"
        if warning_items:
            cats = [(item.category.value if hasattr(item.category, "value") else item.category) for item in warning_items if item.category]
            if cats:
                top_warn_category = max(set(cats), key=cats.count).capitalize()

        # 4. Data Access Events (7d)
        access_stmt = select(AuditLog).where(
            org_filter,
            AuditLog.created_at >= week_ago,
            AuditLog.action.ilike("%view%") | AuditLog.action.ilike("%read%")
        )
        access_results = await self.session.execute(access_stmt)
        access_items = list(access_results.scalars().all())
        unique_admins_access = len({item.user_id for item in access_items if item.user_id})

        # 5. Configuration Changes (7d)
        config_stmt = select(AuditLog).where(
            org_filter,
            AuditLog.created_at >= week_ago,
            or_(
                AuditLog.action.ilike("%.updated"),
                AuditLog.action.ilike("%.changed"),
                AuditLog.action.ilike("%.deleted"),
                AuditLog.action.ilike("%.created")
            )
        ).order_by(desc(AuditLog.created_at))
        config_results = await self.session.execute(config_stmt)
        config_items = list(config_results.scalars().all())
        latest_config = config_items[0].reason or config_items[0].action if config_items else None

        # 6. Unique Actors (30d)
        actors_stmt = select(func.count(AuditLog.user_id.distinct())).where(
            org_filter,
            AuditLog.created_at >= month_ago
        )
        unique_actors = (await self.session.execute(actors_stmt)).scalar_one() or 0

        return {
            "total_events_24h": count_24h,
            "total_events_prev_24h_pct": trend_pct,
            "critical_events_7d": len(critical_items),
            "critical_events_latest": latest_critical,
            "warning_events_7d": len(warning_items),
            "warning_events_top_category": top_warn_category,
            "data_access_events_7d": len(access_items),
            "data_access_unique_admins": unique_admins_access,
            "configuration_changes_7d": len(config_items),
            "configuration_changes_latest": latest_config,
            "unique_actors_30d": unique_actors,
            "unique_actors_count": unique_actors, # For simplicity
        }

    async def get_audit_trend(self, organization_id: str, days: int = 30) -> list[dict[str, Any]]:
        """Get daily counts by severity for the last N days."""
        now = datetime.now(UTC)
        start_date = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

        # Base filter
        org_filter = or_(
            AuditLog.organization_id == organization_id,
            AuditLog.user_id.in_(
                select(User.id).where(User.organization_id == organization_id)
            )
        )

        # Query counts grouped by day and severity
        stmt = (
            select(
                func.date_trunc("day", AuditLog.created_at).label("day"),
                AuditLog.severity,
                func.count().label("count"),
            )
            .where(org_filter, AuditLog.created_at >= start_date)
            .group_by("day", AuditLog.severity)
            .order_by("day")
        )

        result = await self.session.execute(stmt)
        rows = result.all()

        # Format into a dict for easy lookup: {date_str: {severity: count}}
        trend_map = {}
        for row in rows:
            date_str = f"{row.day:%b} {row.day.day}"
            severity = row.severity.lower()
            if date_str not in trend_map:
                trend_map[date_str] = {"info": 0, "notice": 0, "warning": 0, "critical": 0}
            trend_map[date_str][severity] = row.count

        # Fill in missing dates to ensure we have a full series
        points = []
        for i in range(days):
            current_day = start_date + timedelta(days=i)
            date_str = f"{current_day:%b} {current_day.day}"
            data = trend_map.get(date_str, {"info": 0, "notice": 0, "warning": 0, "critical": 0})
            points.append({
                "date": date_str,
                **data
            })

        return points

    async def get_saved_views(
        self, user_id: str | None = None
    ) -> list[AuditSavedView]:
        """Get all saved filter views for a user (globally across organizations)."""
        # We fetch views that are either public (user_id is None) or belong to this user.
        # Organization-specific scoping is removed as per requirements.
        stmt = select(AuditSavedView)
        if user_id:
            stmt = stmt.where(
                or_(AuditSavedView.user_id == user_id, AuditSavedView.user_id.is_(None))
            )
        else:
            stmt = stmt.where(AuditSavedView.user_id.is_(None))
            
        result = await self.session.execute(stmt.order_by(AuditSavedView.name))
        return list(result.scalars().all())

    async def create_saved_view(self, user_id: str | None, data: dict, organization_id: str | None = None) -> AuditSavedView:
        """Create a new saved filter view (personal or global)."""
        view = AuditSavedView(organization_id=organization_id, user_id=user_id, **data)
        self.session.add(view)
        await self.session.flush()
        return view

    async def delete_saved_view(self, view_id: str, user_id: str | None = None) -> bool:
        """Delete a saved filter view."""
        stmt = select(AuditSavedView).where(AuditSavedView.id == view_id)
        if user_id:
            # Users can only delete their own views; null user_id (system) can delete any
            stmt = stmt.where(AuditSavedView.user_id == user_id)
            
        result = await self.session.execute(stmt)
        view = result.scalar_one_or_none()
        if not view:
            return False
        await self.session.delete(view)
        await self.session.flush()
        return True

    async def get_data_access_summary(self, organization_id: str) -> list[DataAccessSummaryRow]:
        """Get summary of data access by admin for an organization (last 30 days)."""
        month_ago = datetime.now(UTC) - timedelta(days=30)
        
        # Subquery to aggregate counts and latest access per user
        stmt = (
            select(
                AuditLog.user_id,
                User.first_name,
                User.last_name,
                User.email,
                func.count(AuditLog.id).label("events"),
                func.max(AuditLog.created_at).label("last_access")
            )
            .join(User, AuditLog.user_id == User.id)
            .where(
                AuditLog.organization_id == organization_id,
                AuditLog.category == AuditCategory.ACCESS,
                AuditLog.created_at >= month_ago
            )
            .group_by(AuditLog.user_id, User.first_name, User.last_name, User.email)
            .order_by(desc("events"))
        )
        
        result = await self.session.execute(stmt)
        rows = result.all()
        
        return [
            {
                "admin": f"{row.first_name} {row.last_name}",
                "email": row.email,
                "events": row.events,
                "last_access": row.last_access
            }
            for row in rows
        ]

    async def get_data_access_heatmap(self, organization_id: str) -> list[DataAccessHeatmapRow]:
        """Get heatmap data (counts by day and hour) for data access logs (last 30 days)."""
        month_ago = datetime.now(UTC) - timedelta(days=30)
        
        # Query counts grouped by day of week and hour
        # day_of_week (0-6, sunday is 0 or 7 depending on db config, we'll standardize)
        stmt = (
            select(
                func.extract("dow", AuditLog.created_at).label("dow"),
                func.extract("hour", AuditLog.created_at).label("hour"),
                func.count().label("count")
            )
            .where(
                AuditLog.organization_id == organization_id,
                AuditLog.category == AuditCategory.ACCESS,
                AuditLog.created_at >= month_ago
            )
            .group_by("dow", "hour")
        )
        
        result = await self.session.execute(stmt)
        rows = result.all()
        
        return [
            {
                "day": int(row.dow),
                "hour": int(row.hour),
                "count": cast(int, row._mapping["count"]),
            }
            for row in rows
        ]

    async def get_change_history(
        self,
        organization_id: str,
        page: int = 1,
        size: int = 50,
        search: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        category: list[str] | None = None,
        entity_type: list[str] | None = None,
        action_type: list[str] | None = None,
        actor: str | None = None,
    ) -> tuple[int, list[ChangeHistoryRow]]:
        """Get summarized history of data modifications (non-access events)."""
        # We start with the base logs but filter for non-access categories
        stmt = (
            select(AuditLog, User)
            .outerjoin(User, AuditLog.user_id == User.id)
            .where(AuditLog.organization_id == organization_id)
            .where(
                AuditLog.category.notin_([AuditCategory.ACCESS, AuditCategory.SECURITY])
            )
        )

        if category and "all" not in category:
            stmt = stmt.where(AuditLog.category.in_(category))
        
        if entity_type and "all" not in entity_type:
            stmt = stmt.where(AuditLog.entity_type.in_(entity_type))

        if action_type and "all" not in action_type:
            # action_type might be "Create", "Update", "Delete"
            # we need to map to AuditEventType suffixes
            clauses = []
            for at in action_type:
                if at == "Create": clauses.append(AuditLog.event_type.ilike("%CREATED%"))
                elif at == "Update": clauses.append(or_(AuditLog.event_type.ilike("%UPDATED%"), AuditLog.event_type.ilike("%MODIFIED%"), AuditLog.event_type.ilike("%CHANGED%")))
                elif at == "Delete": clauses.append(AuditLog.event_type.ilike("%DELETED%"))
                elif at == "all": continue
                else: clauses.append(AuditLog.event_type.ilike(f"%{at}%"))
            if clauses:
                stmt = stmt.where(or_(*clauses))

        actor_clause = audit_actor_filter_clause(actor)
        if actor_clause is not None:
            stmt = stmt.where(actor_clause)

        if from_date:
            stmt = stmt.where(AuditLog.created_at >= from_date)
        if to_date:
            stmt = stmt.where(AuditLog.created_at <= to_date)
        
        if search:
            search_pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    AuditLog.entity_ref.ilike(search_pattern),
                    AuditLog.entity_type.ilike(search_pattern),
                    AuditLog.reason.ilike(search_pattern),
                    User.email.ilike(search_pattern),
                )
            )

        # Count total
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.session.execute(count_stmt)).scalar_one()

        # Sort and Page
        stmt = stmt.order_by(AuditLog.created_at.desc()).offset((page - 1) * size).limit(size)
        result = await self.session.execute(stmt)
        rows = result.all()

        entries = []
        for row in rows:
            log, u = row
            # Calculate changes (diff between old_value and new_value)
            changes = []
            old_val = log.old_value or {}
            new_val = log.new_value or {}
            
            # Simple diff for flat objects (common in our audit logs)
            all_keys = set(old_val.keys()) | set(new_val.keys())
            for key in all_keys:
                ov = old_val.get(key)
                nv = new_val.get(key)
                if ov != nv:
                    changes.append({"field": key.replace("_", " ").title(), "before": ov, "after": nv})
            
            entries.append({
                "id": log.id,
                "created_at": log.created_at,
                "category": log.category,
                "entity_type": log.entity_type,
                "entity_ref": log.entity_ref,
                "action": log.action.split(".")[-1].title().replace("_", " "),
                "email": u.email if u else log.user_role,
                "actor": audit_actor_label(log.user_role),
                "fields_changed": len(changes),
                "summary": log.reason or f"{log.action} on {log.entity_type}",
                "changes": changes
            })

        return total, entries

    async def get_field_history(
        self,
        organization_id: str,
        field_name: str,
        *,
        page: int = 1,
        size: int = 50,
        search: str | None = None,
        event_type: list[str] | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> tuple[list[FieldHistoryRow], int]:
        """Paginated history of changes for a specific field within an organization.

        Filters supported (all optional):
            - search: case-insensitive match against actor name/email, reason, and
              the JSON before/after values cast to text.
            - event_type: restrict to a list of AuditEventType values.
            - from_date / to_date: closed range over ``created_at``.
        """
        key = field_name.lower().replace(" ", "_")

        stmt = (
            select(AuditLog, User)
            .outerjoin(User, AuditLog.user_id == User.id)
            .where(
                AuditLog.organization_id == organization_id,
                or_(
                    AuditLog.old_value.has_key(key),
                    AuditLog.new_value.has_key(key),
                ),
            )
        )

        if event_type and "all" not in event_type:
            stmt = stmt.where(AuditLog.event_type.in_(event_type))

        if from_date is not None:
            stmt = stmt.where(AuditLog.created_at >= from_date)
        if to_date is not None:
            stmt = stmt.where(AuditLog.created_at <= to_date)

        if search:
            pattern = f"%{search}%"
            old_text = func.jsonb_extract_path_text(AuditLog.old_value, key)
            new_text = func.jsonb_extract_path_text(AuditLog.new_value, key)
            stmt = stmt.where(
                or_(
                    AuditLog.reason.ilike(pattern),
                    User.email.ilike(pattern),
                    User.first_name.ilike(pattern),
                    User.last_name.ilike(pattern),
                    old_text.ilike(pattern),
                    new_text.ilike(pattern),
                )
            )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.session.execute(count_stmt)).scalar_one() or 0

        stmt = (
            stmt.order_by(AuditLog.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )

        result = await self.session.execute(stmt)
        rows = result.all()

        history: list[FieldHistoryRow] = []
        for row in rows:
            log, u = row
            history.append({
                "timestamp": log.created_at,
                "before": log.old_value.get(key) if log.old_value else None,
                "after": log.new_value.get(key) if log.new_value else None,
                "actor": audit_actor_label(log.user_role),
                "reason": log.reason,
                "event_type": (
                    log.event_type.value if hasattr(log.event_type, "value") else log.event_type
                ),
                "email": u.email if u else None,
            })
        return history, int(total)

    async def get_field_history_trend(
        self,
        organization_id: str,
        field_name: str,
        *,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> list[FieldHistoryTrendPoint]:
        """Monthly numeric trend for ``field_name`` (last value per month).

        Returns one bucket per month in the requested window (defaults to the last
        6 months ending today). Missing months are filled with ``value=None`` so the
        FE area chart has a continuous X-axis. Non-numeric values are skipped.
        """
        key = field_name.lower().replace(" ", "_")

        now = datetime.now(UTC)
        window_end = to_date or now
        window_start = from_date or (window_end - timedelta(days=31 * 5))

        # Normalize to month starts (timezone-aware UTC).
        try:
            start_month = datetime(
                window_start.year, window_start.month, 1, tzinfo=UTC
            )
            end_month = datetime(window_end.year, window_end.month, 1, tzinfo=UTC)
        except Exception:
            start_month = datetime(now.year, now.month, 1, tzinfo=UTC) - timedelta(days=31 * 5)
            end_month = datetime(now.year, now.month, 1, tzinfo=UTC)

        # Compute end-exclusive boundary (first day of the month AFTER end_month).
        if end_month.month == 12:
            end_exclusive = end_month.replace(year=end_month.year + 1, month=1)
        else:
            end_exclusive = end_month.replace(month=end_month.month + 1)

        months: list[datetime] = []
        cur = start_month
        while cur <= end_month:
            months.append(cur)
            if cur.month == 12:
                cur = cur.replace(year=cur.year + 1, month=1)
            else:
                cur = cur.replace(month=cur.month + 1)

        value_text = func.jsonb_extract_path_text(AuditLog.new_value, key)
        month_col = func.date_trunc("month", AuditLog.created_at)

        stmt = (
            select(
                month_col.label("bucket"),
                AuditLog.created_at,
                value_text.label("value_text"),
            )
            .where(
                AuditLog.organization_id == organization_id,
                AuditLog.new_value.has_key(key),
                value_text.op("~")(r"^-?\d+(\.\d+)?$"),
                AuditLog.created_at >= start_month,
                AuditLog.created_at < end_exclusive,
            )
            .order_by(month_col.asc(), AuditLog.created_at.desc())
        )

        result = await self.session.execute(stmt)
        rows = result.all()

        last_by_month: dict[str, float] = {}
        for row in rows:
            bucket_dt = row[0]
            if bucket_dt is None:
                continue
            mk = bucket_dt.strftime("%Y-%m")
            if mk in last_by_month:
                continue
            raw = row[2]
            if raw is None:
                continue
            try:
                last_by_month[mk] = float(raw)
            except (TypeError, ValueError):
                continue

        # Seed: find the latest numeric value recorded BEFORE the window so we can
        # carry it forward into months with no change inside the window. This gives
        # the chart a continuous line that reflects the field's *current* value
        # (not just the months in which a change happened).
        seed_stmt = (
            select(value_text)
            .where(
                AuditLog.organization_id == organization_id,
                AuditLog.new_value.has_key(key),
                value_text.op("~")(r"^-?\d+(\.\d+)?$"),
                AuditLog.created_at < start_month,
            )
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        )
        seed_raw = (await self.session.execute(seed_stmt)).scalar_one_or_none()

        # Fallback seed: when there is no prior history before the window, look at
        # the FIRST in-window change and use its `old_value` as the starting point
        # so earlier (otherwise-empty) months inherit the value that existed just
        # before the first change. This keeps the chart line continuous even when
        # all changes happen on the same day.
        if seed_raw is None:
            old_value_text = func.jsonb_extract_path_text(AuditLog.old_value, key)
            fallback_stmt = (
                select(old_value_text)
                .where(
                    AuditLog.organization_id == organization_id,
                    AuditLog.old_value.has_key(key),
                    old_value_text.op("~")(r"^-?\d+(\.\d+)?$"),
                    AuditLog.created_at >= start_month,
                    AuditLog.created_at < end_exclusive,
                )
                .order_by(AuditLog.created_at.asc())
                .limit(1)
            )
            seed_raw = (await self.session.execute(fallback_stmt)).scalar_one_or_none()

        carry: float | None = None
        if seed_raw is not None:
            try:
                carry = float(seed_raw)
            except (TypeError, ValueError):
                carry = None

        points: list[FieldHistoryTrendPoint] = []
        for m in months:
            mk = m.strftime("%Y-%m")
            if mk in last_by_month:
                carry = last_by_month[mk]
            points.append({
                "date": m.strftime("%b %Y"),
                "value": carry,
            })
        return points

    async def get_point_in_time_comparison(
        self,
        organization_id: str,
        snapshot_a: datetime,
        snapshot_b: datetime,
        fields: list[str] | None = None
    ) -> list[ComparisonResultRow]:
        """Compare the state of an organization at two points in time."""
        # For each field, find the latest log BEFORE snapshot_a and BEFORE snapshot_b
        # and also count changes between them.
        
        target_fields = fields or [
            "credit_limit", "payment_terms", "account_tier", "discount_rate", "vat_number"
        ]
        results = []
        
        for field in target_fields:
            field_key = field.lower().replace(" ", "_")
            
            # Latest BEFORE A
            stmt_a = (
                select(AuditLog.new_value)
                .where(
                    AuditLog.organization_id == organization_id,
                    AuditLog.created_at <= snapshot_a,
                    AuditLog.new_value.has_key(field_key)
                )
                .order_by(AuditLog.created_at.desc())
                .limit(1)
            )
            v_a = (await self.session.execute(stmt_a)).scalar_one_or_none()
            
            # Latest BEFORE B
            stmt_b = (
                select(AuditLog.new_value)
                .where(
                    AuditLog.organization_id == organization_id,
                    AuditLog.created_at <= snapshot_b,
                    AuditLog.new_value.has_key(field_key)
                )
                .order_by(AuditLog.created_at.desc())
                .limit(1)
            )
            v_b = (await self.session.execute(stmt_b)).scalar_one_or_none()
            
            # Count changes BETWEEN A and B
            stmt_count = (
                select(func.count(AuditLog.id))
                .where(
                    AuditLog.organization_id == organization_id,
                    AuditLog.created_at > min(snapshot_a, snapshot_b),
                    AuditLog.created_at <= max(snapshot_a, snapshot_b),
                    or_(
                        AuditLog.new_value.has_key(field_key),
                        AuditLog.old_value.has_key(field_key)
                    )
                )
            )
            change_count = (await self.session.execute(stmt_count)).scalar_one() or 0
            
            results.append({
                "field": field.replace("_", " ").title(),
                "value_a": v_a.get(field_key) if v_a else "N/A",
                "value_b": v_b.get(field_key) if v_b else "N/A",
                "changes": change_count
            })
            
        return results

    async def get_credit_activity_logs(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
        event_types: list[str] | None = None,
        user_types: list[str] | None = None,
        severities: list[str] | None = None,
        search: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> tuple[list[AuditLog], int]:
        """Recent CREDIT-category audit entries for an organisation.

        Restricts ``category = AuditCategory.CREDIT`` and joins ``user`` so the
        caller can show actor email/name. ``user_types`` is a coarse bucket on
        the recorded ``user_role``: ``"Admin"`` matches ADMIN/SUPER_ADMIN,
        ``"Client"`` matches anything else with a user attached, and
        ``"System"`` matches rows with no user (cron / inline system events).
        """
        stmt = (
            select(AuditLog, User)
            .outerjoin(User, AuditLog.user_id == User.id)
            .where(
                AuditLog.category == AuditCategory.CREDIT,
                or_(
                    AuditLog.organization_id == organization_id,
                    User.organization_id == organization_id,
                ),
            )
        )

        if event_types:
            stmt = stmt.where(AuditLog.event_type.in_(event_types))

        if severities:
            stmt = stmt.where(AuditLog.severity.in_(severities))

        if user_types:
            buckets: list[ColumnElement] = []
            for ut in user_types:
                key = ut.lower()
                if key == "admin":
                    buckets.append(AuditLog.user_role.in_(("ADMIN", "SUPER_ADMIN")))
                elif key == "system":
                    buckets.append(AuditLog.user_id.is_(None))
                elif key == "client":
                    buckets.append(
                        and_(
                            AuditLog.user_id.isnot(None),
                            AuditLog.user_role.notin_(("ADMIN", "SUPER_ADMIN")),
                        ),
                    )
            if buckets:
                stmt = stmt.where(or_(*buckets))

        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    AuditLog.action.ilike(pattern),
                    AuditLog.event_type.ilike(pattern),
                    AuditLog.reason.ilike(pattern),
                    User.email.ilike(pattern),
                    User.first_name.ilike(pattern),
                    User.last_name.ilike(pattern),
                ),
            )

        if from_date is not None:
            stmt = stmt.where(AuditLog.created_at >= from_date)
        if to_date is not None:
            stmt = stmt.where(AuditLog.created_at <= to_date)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            stmt.options(contains_eager(AuditLog.user))
            .order_by(AuditLog.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().unique().all()), total
