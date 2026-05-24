"""Base repository with async CRUD, pagination, data-level scoping, and optimistic locking.

Every module's repository inherits from BaseRepository. All database queries
go through repositories — services and routes never touch the session directly.

Data-level RBAC (Layer 3) is enforced here: every query method accepts
scope_filters that automatically restrict results to the caller's access level.
"""

# pyright: reportAttributeAccessIssue=false, reportInvalidTypeVarUse=false, reportReturnType=false
# CursorResult.rowcount exists at runtime but is missing from SQLAlchemy's type stubs

from datetime import UTC, datetime
from typing import Any, TypeVar, cast

from sqlalchemy import Select, func, select, update
from sqlalchemy import exists as sa_exists
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ConflictError, NotFoundError
from app.common.models import Base

ModelT = TypeVar("ModelT", bound=Base)

# Fields that must never be overwritten via setattr in update operations.
_IMMUTABLE_FIELDS: frozenset[str] = frozenset({"id", "created_at", "updated_at", "version"})


class BaseRepository:
    """Generic async repository with CRUD, pagination, and optimistic locking.

    Usage:
        class UserRepository(BaseRepository):
            def __init__(self, session: AsyncSession):
                super().__init__(session, User)

            async def find_by_email(self, email: str) -> User | None:
                return await self.find_one(email=email)
    """

    def __init__(self, session: AsyncSession, model: type[ModelT]) -> None:
        self.session = session
        self.model = model

    # ── Read ─────────────────────────────────────

    async def get_by_id(self, id: str, **scope_filters: Any) -> ModelT | None:
        """Get a single record by ID, scoped by access filters."""
        stmt = select(self.model).where(self.model.id == id)
        stmt = self._apply_where(stmt, **scope_filters)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_or_404(self, id: str, **scope_filters: Any) -> ModelT:
        """Get a single record by ID or raise NotFoundError."""
        record = await self.get_by_id(id, **scope_filters)
        if record is None:
            raise NotFoundError(resource=self.model.__tablename__, id=id)
        return record

    async def find_one(self, **filters: Any) -> ModelT | None:
        """Find a single record matching the given filters."""
        stmt = select(self.model)
        stmt = self._apply_where(stmt, **filters)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_all(
        self,
        *,
        page: int = 1,
        size: int = 20,
        order_by: str | None = None,
        order_desc: bool = True,
        scope_filters: dict[str, Any] | None = None,
        **filters: Any,
    ) -> tuple[list[ModelT], int]:
        """Paginated list with filters and data-level scoping.

        Returns (items, total_count).
        Falls back to the model's primary key for ordering when order_by is not
        provided or the column does not exist.
        """
        # Base query
        stmt = select(self.model)
        count_stmt = select(func.count()).select_from(self.model)

        # Apply data-level scope (RBAC Layer 3)
        if scope_filters:
            stmt = self._apply_where(stmt, **scope_filters)
            count_stmt = self._apply_where(count_stmt, **scope_filters)

        # Apply additional filters
        stmt = self._apply_where(stmt, **filters)
        count_stmt = self._apply_where(count_stmt, **filters)

        # Count total
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()

        # Order — fall back to created_at then id; only allow actual column names
        order_column = None
        if order_by:
            allowed = set(self.model.__table__.c.keys())
            if order_by in allowed:
                order_column = getattr(self.model, order_by)
        if order_column is None:
            order_column = getattr(self.model, "created_at", None)
        if order_column is None:
            order_column = self.model.id

        stmt = stmt.order_by(order_column.desc() if order_desc else order_column.asc())

        # Paginate
        offset = (page - 1) * size
        stmt = stmt.offset(offset).limit(size)

        result = await self.session.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def count(self, **filters: Any) -> int:
        """Count records matching filters."""
        stmt = select(func.count()).select_from(self.model)
        stmt = self._apply_where(stmt, **filters)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def exists(self, **filters: Any) -> bool:
        """Check if any record matches the filters. Uses EXISTS for efficiency."""
        inner = select(self.model.id)
        inner = self._apply_where(inner, **filters)
        stmt = select(sa_exists(inner.limit(1)))
        result = await self.session.execute(stmt)
        return result.scalar_one()

    # ── Write ────────────────────────────────────

    async def create(self, data: dict[str, Any]) -> ModelT:
        """Create a new record."""
        instance = self.model(**data)
        self.session.add(instance)
        await self.session.flush()
        await self.session.refresh(instance)
        return cast(ModelT, instance)

    async def update_by_id(
        self,
        id: str,
        data: dict[str, Any],
        expected_version: int | None = None,
        **scope_filters: Any,
    ) -> ModelT:
        """Update a record by ID with optional optimistic locking.

        If expected_version is provided and the model has a version column,
        uses a single atomic UPDATE ... WHERE version = :expected to avoid
        race conditions. Otherwise performs a read-then-write (use for
        low-concurrency paths only).
        Scope filters enforce data-level access control.
        Immutable fields (id, created_at, updated_at, version) are never
        overwritten via the data dict.
        """
        # Enforce scope: ensure caller can access this record
        await self.get_by_id_or_404(id, **scope_filters)

        if expected_version is not None and hasattr(self.model, "version"):
            # Atomic path: single UPDATE WHERE version = expected
            await self.atomic_versioned_update(id, data, expected_version)
            record = await self.get_by_id_or_404(id, **scope_filters)
            return record

        record = await self.get_by_id_or_404(id, **scope_filters)

        # Strip immutable and version fields — version is bumped explicitly
        safe_data = {k: v for k, v in data.items() if k not in _IMMUTABLE_FIELDS}

        for key, value in safe_data.items():
            if hasattr(record, key):
                setattr(record, key, value)

        if hasattr(record, "version"):
            record.version += 1

        await self.session.flush()
        await self.session.refresh(record)
        return record

    async def atomic_versioned_update(
        self,
        id: str,
        data: dict[str, Any],
        expected_version: int,
    ) -> int:
        """Atomic UPDATE with version check. Returns rows affected.

        Emits a single SQL UPDATE with WHERE version = :expected_version.
        Use this for high-concurrency operations (e.g., shipment assignment).
        0 rows affected → ConflictError (409).
        """
        safe_data = {k: v for k, v in data.items() if k not in _IMMUTABLE_FIELDS}
        safe_data["version"] = expected_version + 1
        if hasattr(self.model, "updated_at"):
            safe_data["updated_at"] = datetime.now(UTC)

        stmt = update(self.model).where(self.model.id == id, self.model.version == expected_version).values(**safe_data)
        result = await self.session.execute(stmt)
        if result.rowcount == 0:
            raise ConflictError(f"{self.model.__tablename__} was modified by another request.")
        return result.rowcount

    async def soft_delete(
        self,
        id: str,
        *,
        status_field: str = "status",
        target_status: str = "inactive",
        **scope_filters: Any,
    ) -> ModelT:
        """Soft-delete by setting the status column.

        Args:
            id: Record primary key.
            status_field: Column name to set (default 'status').
            target_status: Value to set (default 'inactive').
            **scope_filters: RBAC Layer 3 access filters.
        """
        return await self.update_by_id(id, {status_field: target_status}, **scope_filters)

    async def hard_delete(self, id: str, **scope_filters: Any) -> None:
        """Permanently delete a record. Use sparingly — prefer soft delete."""
        record = await self.get_by_id_or_404(id, **scope_filters)
        await self.session.delete(record)
        await self.session.flush()

    # ── Helpers ───────────────────────────────────

    def _apply_where(self, stmt: Select, **filters: Any) -> Select:
        """Apply equality filters to any statement.

        None values are NOT allowed — they are silently dropped in the original
        implementation, which could leak data when used for RBAC scope_filters
        (e.g. organization_id=None would return all orgs). Callers must use
        explicit IS NULL in a custom repository method if needed.
        """
        for key, value in filters.items():
            if value is None:
                raise ValueError(f"None value for filter '{key}' is not allowed (RBAC safety). " "Use a custom repository method for IS NULL checks.")
            column = getattr(self.model, key, None)
            if column is None:
                raise ValueError(f"Unknown filter key '{key}' on {self.model.__tablename__}. " "Check for typos — silent skips can break RBAC scoping.")
            stmt = stmt.where(column == value)
        return stmt
