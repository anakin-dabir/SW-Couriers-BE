from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, outerjoin, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.exceptions import NotFoundError
from app.common.repository import BaseRepository
from app.core.security import hash_token
from app.modules.organizations.doc_access_scope import DocAccessScope
from app.modules.organizations.enums import (
    ContactRole,
    ContactStatus,
    OrganizationStatus,
    OrgDocumentActivityType,
    OrgDocumentCategory,
    OrgDocumentShareStatus,
    OrgDocumentStatus,
    OrgDocumentType,
    PaymentModel,
)
from app.modules.organizations.models import (
    DocAccessToken,
    DocOtp,
    Organization,
    OrgContact,
    OrgDocument,
    OrgDocumentActivity,
    OrgDocumentShare,
    OrgDraft,
    OrgPaymentConfig,
    OrgPaymentMethod,
    ShareAccessToken,
    ShareOtp,
)
from app.modules.user.models import User


@dataclass
class OrgListRow:
    """Flat projection returned by search_for_list — one row per org."""

    org: Organization
    # Comma-separated list of enabled payment model strings (may be empty string or None)
    payment_models_csv: str | None
    credit_limit: Decimal | None
    # Primary contact's linked user email
    owner_account_email: str | None
    # Admin who onboarded this org (full name + role)
    onboarded_by_first_name: str | None
    onboarded_by_last_name: str | None
    onboarded_by_role: str | None
    # Account manager assigned to this org (primary)
    account_manager_first_name: str | None
    account_manager_last_name: str | None
    account_manager_role: str | None
    # Secondary account manager
    secondary_account_manager_first_name: str | None
    secondary_account_manager_last_name: str | None
    secondary_account_manager_role: str | None
    # Additional account manager
    additional_account_manager_first_name: str | None
    additional_account_manager_last_name: str | None
    additional_account_manager_role: str | None


class OrganizationRepository(BaseRepository):
    """Repository for managing Organization entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Organization)

    async def find_by_trading_name(self, trading_name: str) -> Organization | None:
        """Find an organization by trading name (exact match)."""
        return await self.find_one(trading_name=trading_name)

    async def find_by_reference(self, reference: str) -> Organization | None:
        """Find an organization by its SWC-ORG-NNNNN reference."""
        return await self.find_one(reference=reference)

    async def generate_reference(self) -> str:
        """Generate the next SWC-ORG-NNNNN reference using the DB sequence."""
        result = await self.session.execute(text("SELECT nextval('org_ref_seq')"))
        seq_val: int = result.scalar_one()
        return f"SWC-ORG-{seq_val:05d}"

    async def search(
        self,
        *,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
        status: OrganizationStatus | None = None,
    ) -> tuple[list[Organization], int]:
        """Paginated list with optional name/reference search and status filter."""
        stmt = select(Organization)
        count_stmt = select(func.count()).select_from(Organization)

        if search:
            pattern = f"%{search}%"
            search_filter = or_(
                Organization.trading_name.ilike(pattern),
                Organization.reference.ilike(pattern),
                Organization.legal_entity_name.ilike(pattern),
            )
            stmt = stmt.where(search_filter)
            count_stmt = count_stmt.where(search_filter)

        if status is not None:
            stmt = stmt.where(Organization.status == status)
            count_stmt = count_stmt.where(Organization.status == status)

        count_result = await self.session.execute(count_stmt)
        total: int = count_result.scalar_one()

        offset = (page - 1) * size
        stmt = stmt.order_by(Organization.created_at.desc()).offset(offset).limit(size)
        result = await self.session.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def search_for_list(
        self,
        *,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
        status: list[OrganizationStatus] | None = None,
        vat_registered: bool | None = None,
        pricing_type: str | None = None,
        payment_model: list[PaymentModel] | None = None,
        onboarded_by_user_id: list[str] | None = None,
        created_from: date | None = None,
        created_to: date | None = None,
        sort: str = "newest",
    ) -> tuple[list[OrgListRow], int]:
        """Paginated list for the B2B client list UI.

        Left-joins OrgPaymentConfig (for payment_model, billing_schedule, credit_limit),
        the primary contact's OrgContact row, and that contact's linked User (for owner email).
        Also joins the onboarded_by User (admin who created the org).

        Filters: search (name/reference/legal_name/industry/owner_email/account_manager),
                 status, vat_registered, pricing_type, payment_model.
        Sort: newest (default) | oldest.
        """
        # Alias User table for each join (owner lookup, onboarded_by, account_manager x3)
        OwnerUser = User.__table__.alias("owner_user")
        OwnerContact = OrgContact.__table__.alias("owner_contact")
        OnboardedByUser = User.__table__.alias("onboarded_by_user")
        AccountManagerUser = User.__table__.alias("account_manager_user")
        SecondaryAMUser = User.__table__.alias("secondary_am_user")
        AdditionalAMUser = User.__table__.alias("additional_am_user")

        # Base join chain — OrgPaymentConfig for VAT/shared settings (existence check)
        j = outerjoin(
            Organization.__table__,
            OrgPaymentConfig.__table__,
            OrgPaymentConfig.__table__.c.organization_id == Organization.__table__.c.id,
        )
        # Left-join OrgPaymentMethod — one row per method; we aggregate into CSV + get credit_limit
        j = j.outerjoin(
            OrgPaymentMethod.__table__,
            OrgPaymentMethod.__table__.c.organization_id == Organization.__table__.c.id,
        )
        owner_account_email_sq = (
            select(OwnerUser.c.email)
            .select_from(
                OwnerContact.join(OwnerUser, OwnerUser.c.id == OwnerContact.c.user_id)
            )
            .where(
                OwnerContact.c.organization_id == Organization.__table__.c.id,
                OwnerContact.c.contact_role == ContactRole.ACCOUNT_OWNER.value,
                OwnerContact.c.status != ContactStatus.INACTIVE.value,
                OwnerUser.c.email.is_not(None),
            )
            .order_by(OwnerContact.c.is_primary.desc(), OwnerContact.c.created_at.asc(), OwnerContact.c.id.asc())
            .limit(1)
            .scalar_subquery()
        )

        j = j.outerjoin(
            OnboardedByUser,
            OnboardedByUser.c.id == Organization.__table__.c.onboarded_by_user_id,
        )
        j = j.outerjoin(
            AccountManagerUser,
            AccountManagerUser.c.id == Organization.__table__.c.account_manager_user_id,
        )
        j = j.outerjoin(
            SecondaryAMUser,
            SecondaryAMUser.c.id == Organization.__table__.c.secondary_account_manager_user_id,
        )
        j = j.outerjoin(
            AdditionalAMUser,
            AdditionalAMUser.c.id == Organization.__table__.c.additional_account_manager_user_id,
        )

        # Aggregate payment models into CSV; pull credit_limit from CREDIT_ACCOUNT method
        stmt = (
            select(
                Organization,
                func.string_agg(
                    OrgPaymentMethod.__table__.c.payment_model, ","
                ).label("payment_models_csv"),
                func.max(
                    OrgPaymentMethod.__table__.c.credit_limit
                ).label("credit_limit"),
                owner_account_email_sq.label("owner_account_email"),
                OnboardedByUser.c.first_name.label("onboarded_by_first_name"),
                OnboardedByUser.c.last_name.label("onboarded_by_last_name"),
                OnboardedByUser.c.role.label("onboarded_by_role"),
                AccountManagerUser.c.first_name.label("account_manager_first_name"),
                AccountManagerUser.c.last_name.label("account_manager_last_name"),
                AccountManagerUser.c.role.label("account_manager_role"),
                SecondaryAMUser.c.first_name.label("secondary_account_manager_first_name"),
                SecondaryAMUser.c.last_name.label("secondary_account_manager_last_name"),
                SecondaryAMUser.c.role.label("secondary_account_manager_role"),
                AdditionalAMUser.c.first_name.label("additional_account_manager_first_name"),
                AdditionalAMUser.c.last_name.label("additional_account_manager_last_name"),
                AdditionalAMUser.c.role.label("additional_account_manager_role"),
            )
            .select_from(j)
            .group_by(
                Organization.__table__.c.id,
                OnboardedByUser.c.first_name,
                OnboardedByUser.c.last_name,
                OnboardedByUser.c.role,
                AccountManagerUser.c.first_name,
                AccountManagerUser.c.last_name,
                AccountManagerUser.c.role,
                SecondaryAMUser.c.first_name,
                SecondaryAMUser.c.last_name,
                SecondaryAMUser.c.role,
                AdditionalAMUser.c.first_name,
                AdditionalAMUser.c.last_name,
                AdditionalAMUser.c.role,
            )
        )

        count_stmt = (
            select(func.count(Organization.__table__.c.id.distinct()))
            .select_from(j)
        )

        # ── Filters ───────────────────────────────────────────────────────────
        if search:
            pattern = f"%{search}%"
            search_filter = or_(
                Organization.__table__.c.trading_name.ilike(pattern),
                Organization.__table__.c.reference.ilike(pattern),
                Organization.__table__.c.legal_entity_name.ilike(pattern),
                Organization.__table__.c.industry.ilike(pattern),
                owner_account_email_sq.ilike(pattern),
                (OnboardedByUser.c.first_name + " " + OnboardedByUser.c.last_name).ilike(pattern),
                (AccountManagerUser.c.first_name + " " + AccountManagerUser.c.last_name).ilike(pattern),
            )
            stmt = stmt.where(search_filter)
            count_stmt = count_stmt.where(search_filter)

        if status:
            stmt = stmt.where(Organization.__table__.c.status.in_(status))
            count_stmt = count_stmt.where(Organization.__table__.c.status.in_(status))
        else:
            # When no explicit status filter is given, hide soft-deleted (INACTIVE) orgs
            # so that DELETE → 200 OK correctly removes them from the default list view.
            # DRAFT orgs are excluded globally below; use GET /organizations/drafts.
            stmt = stmt.where(Organization.__table__.c.status != OrganizationStatus.INACTIVE.value)
            count_stmt = count_stmt.where(Organization.__table__.c.status != OrganizationStatus.INACTIVE.value)

        # Draft orgs are listed via GET /organizations/drafts only.
        stmt = stmt.where(Organization.__table__.c.status != OrganizationStatus.DRAFT.value)
        count_stmt = count_stmt.where(Organization.__table__.c.status != OrganizationStatus.DRAFT.value)

        if vat_registered is not None:
            if vat_registered:
                # vat_number is non-empty string
                stmt = stmt.where(
                    Organization.__table__.c.vat_number.isnot(None),
                    func.length(func.trim(Organization.__table__.c.vat_number)) > 0,
                )
                count_stmt = count_stmt.where(
                    Organization.__table__.c.vat_number.isnot(None),
                    func.length(func.trim(Organization.__table__.c.vat_number)) > 0,
                )
            else:
                stmt = stmt.where(
                    or_(
                        Organization.__table__.c.vat_number.is_(None),
                        func.length(func.trim(Organization.__table__.c.vat_number)) == 0,
                    )
                )
                count_stmt = count_stmt.where(
                    or_(
                        Organization.__table__.c.vat_number.is_(None),
                        func.length(func.trim(Organization.__table__.c.vat_number)) == 0,
                    )
                )

        if pricing_type is not None:
            # Match _pricing_type_from_plans(): use the selected plan's plain_type,
            # falling back to the first plan when none is marked selected.
            # We use a raw SQL fragment with a proper bindparam so SQLAlchemy can
            # cache the query shape while still binding the value safely.
            pricing_filter = text(
                "("
                "  jsonb_path_query_first(organizations.pricing_plans::jsonb,"
                "    '$[*] ? (@.selected == true).plain_type') #>> '{}'"
                "  = :pricing_type"
                "  OR ("
                "    jsonb_path_query_first(organizations.pricing_plans::jsonb,"
                "      '$[*] ? (@.selected == true)') IS NULL"
                "    AND organizations.pricing_plans::jsonb -> 0 ->> 'plain_type' = :pricing_type"
                "  )"
                ")"
            ).bindparams(pricing_type=pricing_type)
            stmt = stmt.where(pricing_filter)
            count_stmt = count_stmt.where(pricing_filter)

        if payment_model:
            stmt = stmt.where(OrgPaymentMethod.__table__.c.payment_model.in_(payment_model))
            count_stmt = count_stmt.where(OrgPaymentMethod.__table__.c.payment_model.in_(payment_model))

        if onboarded_by_user_id:
            stmt = stmt.where(Organization.__table__.c.onboarded_by_user_id.in_(onboarded_by_user_id))
            count_stmt = count_stmt.where(Organization.__table__.c.onboarded_by_user_id.in_(onboarded_by_user_id))

        if created_from is not None:
            stmt = stmt.where(Organization.__table__.c.created_at >= datetime(created_from.year, created_from.month, created_from.day, tzinfo=UTC))
            count_stmt = count_stmt.where(Organization.__table__.c.created_at >= datetime(created_from.year, created_from.month, created_from.day, tzinfo=UTC))

        if created_to is not None:
            # inclusive end-of-day (exclusive upper bound via timedelta — safe on month-end)
            end_exclusive = datetime(created_to.year, created_to.month, created_to.day, tzinfo=UTC) + timedelta(days=1)
            stmt = stmt.where(Organization.__table__.c.created_at < end_exclusive)
            count_stmt = count_stmt.where(Organization.__table__.c.created_at < end_exclusive)

        # ── Count ─────────────────────────────────────────────────────────────
        count_result = await self.session.execute(count_stmt)
        total: int = count_result.scalar_one()

        # ── Sort + pagination ─────────────────────────────────────────────────
        order_col = Organization.__table__.c.created_at
        order_expr = order_col.asc() if sort == "oldest" else order_col.desc()
        stmt = stmt.order_by(order_expr, Organization.__table__.c.id).offset((page - 1) * size).limit(size)

        result = await self.session.execute(stmt)
        rows = result.all()

        list_rows = [
            OrgListRow(
                org=row.Organization,
                payment_models_csv=row.payment_models_csv,
                credit_limit=row.credit_limit,
                owner_account_email=row.owner_account_email,
                onboarded_by_first_name=row.onboarded_by_first_name,
                onboarded_by_last_name=row.onboarded_by_last_name,
                onboarded_by_role=row.onboarded_by_role,
                account_manager_first_name=row.account_manager_first_name,
                account_manager_last_name=row.account_manager_last_name,
                account_manager_role=row.account_manager_role,
                secondary_account_manager_first_name=row.secondary_account_manager_first_name,
                secondary_account_manager_last_name=row.secondary_account_manager_last_name,
                secondary_account_manager_role=row.secondary_account_manager_role,
                additional_account_manager_first_name=row.additional_account_manager_first_name,
                additional_account_manager_last_name=row.additional_account_manager_last_name,
                additional_account_manager_role=row.additional_account_manager_role,
            )
            for row in rows
        ]
        return list_rows, total

    async def get_account_assignments_for_admins(
        self, user_ids: list[str]
    ) -> dict[str, list[dict]]:
        """Return a mapping of user_id → orgs where they hold any account manager position.

        Each entry: {id, reference, name, email} (email = primary ACCOUNT_OWNER contact).
        An org appears once per admin even if that admin holds multiple AM slots.
        """
        if not user_ids:
            return {}

        uid_set = set(user_ids)
        OwnerUser = User.__table__.alias("owner_user")
        PrimaryContact = OrgContact.__table__.alias("primary_contact")

        owner_email_sq = (
            select(OwnerUser.c.email)
            .select_from(
                PrimaryContact.join(OwnerUser, OwnerUser.c.id == PrimaryContact.c.user_id)
            )
            .where(
                (PrimaryContact.c.organization_id == Organization.__table__.c.id)
                & (PrimaryContact.c.contact_role == ContactRole.ACCOUNT_OWNER.value)
                & (OwnerUser.c.email.is_not(None))
            )
            .limit(1)
            .scalar_subquery()
        )

        stmt = (
            select(
                Organization.__table__.c.id.label("org_id"),
                Organization.__table__.c.reference.label("reference"),
                Organization.__table__.c.trading_name.label("name"),
                Organization.__table__.c.account_manager_user_id.label("am_user_id"),
                Organization.__table__.c.secondary_account_manager_user_id.label("sec_am_user_id"),
                Organization.__table__.c.additional_account_manager_user_id.label("add_am_user_id"),
                owner_email_sq.label("owner_email"),
            )
            .where(
                or_(
                    Organization.__table__.c.account_manager_user_id.in_(user_ids),
                    Organization.__table__.c.secondary_account_manager_user_id.in_(user_ids),
                    Organization.__table__.c.additional_account_manager_user_id.in_(user_ids),
                )
            )
        )

        rows = (await self.session.execute(stmt)).all()

        result: dict[str, list[dict]] = {uid: [] for uid in user_ids}
        seen: set[tuple[str, str]] = set()

        for row in rows:
            org_entry = {
                "id": row.org_id,
                "reference": row.reference,
                "name": row.name,
                "email": row.owner_email,
            }
            for uid in (row.am_user_id, row.sec_am_user_id, row.add_am_user_id):
                if uid in uid_set:
                    key = (uid, row.org_id)
                    if key not in seen:
                        seen.add(key)
                        result[uid].append(org_entry)

        return result


class OrgContactRepository(BaseRepository):
    """Repository for managing OrgContact entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgContact)

    async def get_by_organization(self, organization_id: str) -> list[OrgContact]:
        """Return all contacts for an organization."""
        stmt = select(OrgContact).where(OrgContact.organization_id == organization_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_with_user(self, organization_id: str) -> list[OrgContact]:
        """Return active contacts with their linked User loaded (single query).

        Excludes INACTIVE (soft-deleted) contacts. Used for contact list endpoint.
        """
        stmt = (
            select(OrgContact)
            .options(selectinload(OrgContact.user))
            .where(
                OrgContact.organization_id == organization_id,
                OrgContact.status != ContactStatus.INACTIVE,
            )
            .order_by(OrgContact.is_primary.desc(), OrgContact.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_with_user(self, organization_id: str, contact_id: str) -> OrgContact | None:
        """Fetch a single contact scoped to an org with User loaded.

        Returns None when the contact does not belong to the org (cross-org safety).
        """
        stmt = (
            select(OrgContact)
            .options(selectinload(OrgContact.user))
            .where(
                OrgContact.id == contact_id,
                OrgContact.organization_id == organization_id,
                OrgContact.status != ContactStatus.INACTIVE,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_active(self, organization_id: str) -> int:
        """Count non-INACTIVE contacts for an org. Guards last-contact removal."""
        stmt = (
            select(func.count())
            .select_from(OrgContact)
            .where(
                OrgContact.organization_id == organization_id,
                OrgContact.status != ContactStatus.INACTIVE,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_contact_role_for_user(self, organization_id: str, user_id: str) -> ContactRole | None:
        """Return the contact_role of the active OrgContact row for this user in this org.

        Returns None if the user has no active contact row (not a member of the org).
        Used to determine if a CUSTOMER_B2B caller is an ACCOUNT_OWNER.
        """

        stmt = select(OrgContact.contact_role).where(
            OrgContact.organization_id == organization_id,
            OrgContact.user_id == user_id,
            OrgContact.status != ContactStatus.INACTIVE,
        )
        result = await self.session.execute(stmt)
        value = result.scalar_one_or_none()
        if value is None:
            return None
        return ContactRole(value)

    async def get_active_contact_for_user(self, organization_id: str, user_id: str) -> OrgContact | None:
        """Return the active OrgContact row linking this user to the org, if any."""
        stmt = select(OrgContact).where(
            OrgContact.organization_id == organization_id,
            OrgContact.user_id == user_id,
            OrgContact.status != ContactStatus.INACTIVE,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def activate_pending_for_user(self, user_id: str) -> int:
        """Promote every ``PENDING`` contact row for a user to ``ACTIVE``.

        Called from the invite-accept flow once the user has set their password
        and their ``users.status`` has been flipped to ``ACTIVE``. Returns the
        number of rows updated (zero is a valid no-op).
        """
        stmt = (
            update(OrgContact)
            .where(
                OrgContact.user_id == user_id,
                OrgContact.status == ContactStatus.PENDING,
            )
            .values(status=ContactStatus.ACTIVE)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount or 0

    async def clear_primary(self, organization_id: str) -> None:
        """Atomically clear is_primary on all contacts in the org.

        Called before setting a new primary so only one is ever primary.
        """
        stmt = update(OrgContact).where(OrgContact.organization_id == organization_id).values(is_primary=False)
        await self.session.execute(stmt)

    async def set_primary_atomic(self, organization_id: str, contact_id: str) -> None:
        """Set exactly one contact as primary in a single UPDATE statement.

        Uses a CASE expression so only one row is ever primary — avoids the
        clear-then-set race condition that two separate UPDATE calls would create.
        """
        from sqlalchemy import case
        stmt = (
            update(OrgContact)
            .where(OrgContact.organization_id == organization_id)
            .values(
                is_primary=case(
                    (OrgContact.id == contact_id, True),
                    else_=False,
                )
            )
        )
        await self.session.execute(stmt)


class OrgPaymentConfigRepository(BaseRepository):
    """Repository for managing OrgPaymentConfig entities (one-to-one with Organization)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgPaymentConfig)

    async def get_by_organization(self, organization_id: str) -> OrgPaymentConfig | None:
        """Return the payment config for the given org, or None if not yet configured."""
        stmt = select(OrgPaymentConfig).where(OrgPaymentConfig.organization_id == organization_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_organization_or_404(self, organization_id: str) -> OrgPaymentConfig:
        """Return the payment config or raise NotFoundError."""
        config = await self.get_by_organization(organization_id)
        if config is None:
            raise NotFoundError(resource="OrgPaymentConfig", id=organization_id)
        return config


class OrgPaymentMethodRepository(BaseRepository):
    """Repository for OrgPaymentMethod (one-to-many with Organization)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgPaymentMethod)

    async def list_by_org(self, organization_id: str) -> list[OrgPaymentMethod]:
        """Return all payment methods for an org, ordered by is_default desc."""
        stmt = (
            select(OrgPaymentMethod)
            .where(OrgPaymentMethod.organization_id == organization_id)
            .order_by(OrgPaymentMethod.is_default.desc(), OrgPaymentMethod.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_org_and_model(self, organization_id: str, payment_model: str) -> OrgPaymentMethod | None:
        """Return the payment method row for a given model, or None."""
        stmt = select(OrgPaymentMethod).where(
            OrgPaymentMethod.organization_id == organization_id,
            OrgPaymentMethod.payment_model == payment_model,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def clear_default(self, organization_id: str) -> None:
        """Clear is_default on all methods for this org atomically."""
        stmt = (
            update(OrgPaymentMethod)
            .where(OrgPaymentMethod.organization_id == organization_id)
            .values(is_default=False)
        )
        await self.session.execute(stmt)


class OrgDocumentRepository(BaseRepository):
    """Repository for OrgDocument — contract & agreement documents."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgDocument)

    async def list_by_org(
        self,
        org_id: str,
        *,
        page: int = 1,
        size: int = 50,
        search: str | None = None,
        category: OrgDocumentCategory | None = None,
        category_in: list[OrgDocumentCategory] | None = None,
        document_type: OrgDocumentType | None = None,
        document_type_in: list[OrgDocumentType] | None = None,
        status: OrgDocumentStatus | None = None,
        status_in: list[OrgDocumentStatus] | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[OrgDocument], int]:
        """Return paginated active documents for an organisation, newest first.

        Filtering: search (ILIKE on title / reference / uploaded_by_email),
        category, document_type, status / status_in, upload date range. Stats cards are
        computed separately and always unfiltered — see get_document_stats().
        """
        base_where = [OrgDocument.organization_id == org_id, OrgDocument.is_active.is_(True)]

        if search:
            term = f"%{search}%"
            base_where.append(
                or_(
                    OrgDocument.title.ilike(term),
                    OrgDocument.reference.ilike(term),
                    OrgDocument.uploaded_by_email.ilike(term),
                )
            )
        if category_in:
            base_where.append(OrgDocument.category.in_(category_in))
        elif category is not None:
            base_where.append(OrgDocument.category == category)
        if document_type_in:
            base_where.append(OrgDocument.document_type.in_(document_type_in))
        elif document_type is not None:
            base_where.append(OrgDocument.document_type == document_type)
        if status_in:
            base_where.append(OrgDocument.status.in_(status_in))
        elif status is not None:
            base_where.append(OrgDocument.status == status)
        if date_from is not None:
            base_where.append(OrgDocument.created_at >= date_from)
        if date_to is not None:
            base_where.append(OrgDocument.created_at <= date_to)

        count_stmt = select(func.count()).select_from(OrgDocument).where(*base_where)
        total: int = (await self.session.execute(count_stmt)).scalar_one()

        offset = (page - 1) * size
        stmt = (
            select(OrgDocument)
            .where(*base_where)
            .order_by(OrgDocument.created_at.desc())
            .offset(offset)
            .limit(size)
        )
        items = list((await self.session.execute(stmt)).scalars().all())
        return items, total

    async def get_document_stats(self, org_id: str) -> dict:
        """Return unfiltered stats for the two summary cards.

        Returns:
            expiring_soon_count: int
            expiring_soon_next_title: str | None
            expiring_soon_next_expiry: date | None
            total_count: int
            contracts_count: int
            client_count: int
            internal_count: int
            system_count: int  (active docs with category=NULL)
        """
        active_where = [OrgDocument.organization_id == org_id, OrgDocument.is_active.is_(True)]

        # Total active count
        total_count: int = (
            await self.session.execute(
                select(func.count()).select_from(OrgDocument).where(*active_where)
            )
        ).scalar_one()

        # Category breakdown counts in one query
        breakdown_stmt = (
            select(OrgDocument.category, func.count().label("cnt"))
            .where(*active_where)
            .group_by(OrgDocument.category)
        )
        breakdown_rows = (await self.session.execute(breakdown_stmt)).all()
        contracts_count = 0
        client_count = 0
        internal_count = 0
        system_count = 0
        for row in breakdown_rows:
            cat, cnt = row.category, row.cnt
            if cat == OrgDocumentCategory.CONTRACTS:
                contracts_count = cnt
            elif cat == OrgDocumentCategory.CLIENT_UPLOADS:
                client_count = cnt
            elif cat == OrgDocumentCategory.INTERNAL:
                internal_count = cnt
            else:
                system_count += cnt  # category IS NULL or any unknown value

        # Expiring soon count + nearest expiring document
        expiring_where = [*active_where, OrgDocument.status == OrgDocumentStatus.EXPIRING_SOON]
        expiring_count: int = (
            await self.session.execute(
                select(func.count()).select_from(OrgDocument).where(*expiring_where)
            )
        ).scalar_one()

        next_expiring = None
        if expiring_count > 0:
            next_stmt = (
                select(OrgDocument)
                .where(*expiring_where)
                .order_by(OrgDocument.expiry_date.asc())
                .limit(1)
            )
            next_expiring = (await self.session.execute(next_stmt)).scalar_one_or_none()

        return {
            "expiring_soon_count": expiring_count,
            "expiring_soon_next_title": next_expiring.title if next_expiring else None,
            "expiring_soon_next_expiry": next_expiring.expiry_date if next_expiring else None,
            "total_count": total_count,
            "contracts_count": contracts_count,
            "client_count": client_count,
            "internal_count": internal_count,
            "system_count": system_count,
        }

    async def get_active_by_org_and_id(self, org_id: str, doc_id: str) -> OrgDocument:
        """Return a single active document scoped to the organisation, or raise 404."""
        stmt = select(OrgDocument).where(
            OrgDocument.id == doc_id,
            OrgDocument.organization_id == org_id,
            OrgDocument.is_active.is_(True),
        )
        result = await self.session.execute(stmt)
        doc = result.scalar_one_or_none()
        if doc is None:
            raise NotFoundError(resource="document", id=doc_id)
        return doc

    async def generate_reference(self) -> str:
        """Generate the next DOC-{YEAR}-NNNNN reference using the DB sequence."""
        year = datetime.now(UTC).year
        result = await self.session.execute(text("SELECT nextval('doc_ref_seq')"))
        seq_val: int = result.scalar_one()
        return f"DOC-{year}-{seq_val:05d}"

    async def soft_delete(self, doc: OrgDocument) -> None:
        """Mark the document as inactive (soft delete)."""
        doc.is_active = False
        await self.session.flush()


class OrgDocumentActivityRepository(BaseRepository):
    """Repository for OrgDocumentActivity — recent-activity audit log."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgDocumentActivity)

    async def list_by_org(
        self,
        org_id: str,
        *,
        page: int = 1,
        size: int = 50,
        sort_order: str = "desc",
        activity_types: list[OrgDocumentActivityType] | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        search: str | None = None,
        browser: str | None = None,
    ) -> tuple[list[dict], int]:
        """Return paginated activity rows for an organisation.

        Each row is a dict with all OrgDocumentActivity fields plus
        `document_reference` (joined from org_documents; None when the
        document has been hard-deleted).

        Filters: activity_types (multi-select), date range, full-text search
        across actor_email / document_name / details / ip_address, and browser.
        sort_order: 'desc' (newest first) or 'asc' (oldest first).
        """
        base_where = [OrgDocumentActivity.organization_id == org_id]

        if activity_types:
            base_where.append(OrgDocumentActivity.activity_type.in_(activity_types))
        if date_from is not None:
            base_where.append(OrgDocumentActivity.created_at >= date_from)
        if date_to is not None:
            base_where.append(OrgDocumentActivity.created_at <= date_to)
        if search:
            term = f"%{search}%"
            base_where.append(
                or_(
                    OrgDocumentActivity.actor_email.ilike(term),
                    OrgDocumentActivity.document_name.ilike(term),
                    OrgDocumentActivity.details.ilike(term),
                    OrgDocumentActivity.ip_address.ilike(term),
                )
            )
        if browser:
            base_where.append(OrgDocumentActivity.browser.ilike(f"%{browser}%"))

        count_stmt = (
            select(func.count())
            .select_from(OrgDocumentActivity)
            .where(*base_where)
        )
        total: int = (await self.session.execute(count_stmt)).scalar_one()

        order_col = (
            OrgDocumentActivity.created_at.desc()
            if sort_order == "desc"
            else OrgDocumentActivity.created_at.asc()
        )
        offset = (page - 1) * size
        stmt = (
            select(OrgDocumentActivity, OrgDocument.reference.label("document_reference"))
            .outerjoin(OrgDocument, OrgDocumentActivity.document_id == OrgDocument.id)
            .where(*base_where)
            .order_by(order_col)
            .offset(offset)
            .limit(size)
        )
        rows = (await self.session.execute(stmt)).all()
        items = [
            {
                "id": r.OrgDocumentActivity.id,
                "organization_id": r.OrgDocumentActivity.organization_id,
                "document_id": r.OrgDocumentActivity.document_id,
                "document_reference": r.document_reference,
                "activity_type": r.OrgDocumentActivity.activity_type,
                "actor_email": r.OrgDocumentActivity.actor_email,
                "actor_role": r.OrgDocumentActivity.actor_role,
                "document_name": r.OrgDocumentActivity.document_name,
                "details": r.OrgDocumentActivity.details,
                "ip_address": r.OrgDocumentActivity.ip_address,
                "browser": r.OrgDocumentActivity.browser,
                "device": r.OrgDocumentActivity.device,
                "os": r.OrgDocumentActivity.os,
                "created_at": r.OrgDocumentActivity.created_at,
            }
            for r in rows
        ]
        return items, total

    async def list_by_document(
        self,
        org_id: str,
        document_id: str,
        *,
        page: int = 1,
        size: int = 50,
        sort_order: str = "desc",
        activity_types: list[OrgDocumentActivityType] | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[dict], int]:
        """Return paginated activity rows for a single document."""
        base_where = [
            OrgDocumentActivity.organization_id == org_id,
            OrgDocumentActivity.document_id == document_id,
        ]

        if activity_types:
            base_where.append(OrgDocumentActivity.activity_type.in_(activity_types))
        if date_from is not None:
            base_where.append(OrgDocumentActivity.created_at >= date_from)
        if date_to is not None:
            base_where.append(OrgDocumentActivity.created_at <= date_to)

        count_stmt = (
            select(func.count())
            .select_from(OrgDocumentActivity)
            .where(*base_where)
        )
        total: int = (await self.session.execute(count_stmt)).scalar_one()

        order_col = (
            OrgDocumentActivity.created_at.desc()
            if sort_order == "desc"
            else OrgDocumentActivity.created_at.asc()
        )
        offset = (page - 1) * size
        stmt = (
            select(OrgDocumentActivity, OrgDocument.reference.label("document_reference"))
            .outerjoin(OrgDocument, OrgDocumentActivity.document_id == OrgDocument.id)
            .where(*base_where)
            .order_by(order_col)
            .offset(offset)
            .limit(size)
        )
        rows = (await self.session.execute(stmt)).all()
        items = [
            {
                "id": r.OrgDocumentActivity.id,
                "organization_id": r.OrgDocumentActivity.organization_id,
                "document_id": r.OrgDocumentActivity.document_id,
                "document_reference": r.document_reference,
                "activity_type": r.OrgDocumentActivity.activity_type,
                "actor_email": r.OrgDocumentActivity.actor_email,
                "actor_role": r.OrgDocumentActivity.actor_role,
                "document_name": r.OrgDocumentActivity.document_name,
                "details": r.OrgDocumentActivity.details,
                "ip_address": r.OrgDocumentActivity.ip_address,
                "browser": r.OrgDocumentActivity.browser,
                "device": r.OrgDocumentActivity.device,
                "os": r.OrgDocumentActivity.os,
                "created_at": r.OrgDocumentActivity.created_at,
            }
            for r in rows
        ]
        return items, total

    async def fetch_all_for_export(
        self,
        org_id: str,
        *,
        document_id: str | None = None,
        activity_types: list[OrgDocumentActivityType] | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        search: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Return all matching activity rows (no pagination) for CSV export.

        Scoped to org_id. Optionally further scoped to a single document_id.
        """
        base_where = [OrgDocumentActivity.organization_id == org_id]

        if document_id:
            base_where.append(OrgDocumentActivity.document_id == document_id)
        if activity_types:
            base_where.append(OrgDocumentActivity.activity_type.in_(activity_types))
        if date_from is not None:
            base_where.append(OrgDocumentActivity.created_at >= date_from)
        if date_to is not None:
            base_where.append(OrgDocumentActivity.created_at <= date_to)
        if search:
            term = f"%{search}%"
            base_where.append(
                or_(
                    OrgDocumentActivity.actor_email.ilike(term),
                    OrgDocumentActivity.document_name.ilike(term),
                    OrgDocumentActivity.details.ilike(term),
                    OrgDocumentActivity.ip_address.ilike(term),
                )
            )

        stmt = (
            select(OrgDocumentActivity, OrgDocument.reference.label("document_reference"))
            .outerjoin(OrgDocument, OrgDocumentActivity.document_id == OrgDocument.id)
            .where(*base_where)
            .order_by(OrgDocumentActivity.created_at.desc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = (await self.session.execute(stmt)).all()
        return [
            {
                "id": r.OrgDocumentActivity.id,
                "organization_id": r.OrgDocumentActivity.organization_id,
                "document_id": r.OrgDocumentActivity.document_id,
                "document_reference": r.document_reference,
                "activity_type": r.OrgDocumentActivity.activity_type,
                "actor_email": r.OrgDocumentActivity.actor_email,
                "actor_role": r.OrgDocumentActivity.actor_role,
                "document_name": r.OrgDocumentActivity.document_name,
                "details": r.OrgDocumentActivity.details,
                "ip_address": r.OrgDocumentActivity.ip_address,
                "browser": r.OrgDocumentActivity.browser,
                "device": r.OrgDocumentActivity.device,
                "os": r.OrgDocumentActivity.os,
                "created_at": r.OrgDocumentActivity.created_at,
            }
            for r in rows
        ]


class OrgDocumentShareRepository(BaseRepository):
    """Repository for OrgDocumentShare — document sharing links."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgDocumentShare)

    async def get_by_token(self, share_token: str) -> OrgDocumentShare | None:
        """Fetch a share by its public token."""
        stmt = select(OrgDocumentShare).where(OrgDocumentShare.share_token == share_token)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_org(
        self,
        org_id: str,
        *,
        page: int = 1,
        size: int = 50,
        status_in: list[OrgDocumentShareStatus] | None = None,
        document_type_in: list[OrgDocumentType] | None = None,
    ) -> tuple[list[OrgDocumentShare], int]:
        """Paginated sharing history for an org, newest first."""
        stmt = select(OrgDocumentShare).where(OrgDocumentShare.organization_id == org_id)
        count_stmt = select(func.count()).select_from(OrgDocumentShare).where(OrgDocumentShare.organization_id == org_id)

        if document_type_in:
            stmt = stmt.join(OrgDocument, OrgDocumentShare.document_id == OrgDocument.id).where(OrgDocument.document_type.in_(document_type_in))
            count_stmt = count_stmt.join(OrgDocument, OrgDocumentShare.document_id == OrgDocument.id).where(OrgDocument.document_type.in_(document_type_in))

        if status_in:
            stmt = stmt.where(OrgDocumentShare.status.in_(status_in))
            count_stmt = count_stmt.where(OrgDocumentShare.status.in_(status_in))

        total: int = (await self.session.execute(count_stmt)).scalar_one()
        offset = (page - 1) * size
        stmt = stmt.order_by(OrgDocumentShare.created_at.desc()).offset(offset).limit(size)
        items = list((await self.session.execute(stmt)).scalars().all())
        return items, total

    async def list_by_document(self, org_id: str, doc_id: str) -> list[OrgDocumentShare]:
        """All shares for a specific document, newest first."""
        stmt = (
            select(OrgDocumentShare)
            .where(
                OrgDocumentShare.organization_id == org_id,
                OrgDocumentShare.document_id == doc_id,
            )
            .order_by(OrgDocumentShare.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def increment_access_count(self, share: OrgDocumentShare) -> None:
        """Atomically increment the access counter and update status to DOWNLOADED."""
        share.access_count += 1
        if share.status == OrgDocumentShareStatus.ACTIVE:
            share.status = OrgDocumentShareStatus.DOWNLOADED
        await self.session.flush()


# ── Document Access OTP repositories ─────────────────────────────────────────


class DocOtpRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        user_id: str,
        otp_code: str,
        expires_at: datetime,
        *,
        access_scope: DocAccessScope = DocAccessScope.ORG_DOCUMENTS,
    ) -> DocOtp:
        otp = DocOtp(
            user_id=user_id,
            otp_code=otp_code,
            expires_at=expires_at,
            access_scope=access_scope.value,
        )
        self.session.add(otp)
        await self.session.flush()
        return otp

    async def invalidate_unused_active_for_user_scope(
        self,
        user_id: str,
        *,
        access_scope: DocAccessScope = DocAccessScope.ORG_DOCUMENTS,
    ) -> None:
        """Mark all unused, unexpired OTPs for this user and scope as used (superseded by a new send)."""
        now = datetime.now(UTC)
        stmt = (
            update(DocOtp)
            .where(
                DocOtp.user_id == user_id,
                DocOtp.access_scope == access_scope.value,
                DocOtp.is_used.is_(False),
                DocOtp.expires_at > now,
            )
            .values(is_used=True)
        )
        await self.session.execute(stmt)

    async def find_valid(
        self,
        user_id: str,
        otp_code: str | None,
        *,
        access_scope: DocAccessScope = DocAccessScope.ORG_DOCUMENTS,
    ) -> DocOtp | None:
        """Return the most recent unused, non-expired OTP for this user and scope.

        After each send, older active OTPs for the same user/scope are invalidated, so at most
        one valid row exists until it is used or expires.

        When ``otp_code`` is provided, match it exactly.
        When ``otp_code`` is None, return the latest valid OTP for the user/scope.
        """
        now = datetime.now(UTC)
        conditions = [
            DocOtp.user_id == user_id,
            DocOtp.access_scope == access_scope.value,
            DocOtp.is_used.is_(False),
            DocOtp.expires_at > now,
        ]
        if otp_code is not None:
            conditions.append(DocOtp.otp_code == otp_code)

        stmt = select(DocOtp).where(*conditions).order_by(DocOtp.created_at.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_used(self, otp_id: str) -> None:
        stmt = update(DocOtp).where(DocOtp.id == otp_id).values(is_used=True)
        await self.session.execute(stmt)

    async def count_recent(
        self,
        user_id: str,
        since: datetime,
        *,
        access_scope: DocAccessScope = DocAccessScope.ORG_DOCUMENTS,
    ) -> int:
        """Count OTPs issued for this user since the given datetime (rate-limit check)."""
        stmt = select(func.count()).where(
            DocOtp.user_id == user_id,
            DocOtp.created_at >= since,
            DocOtp.access_scope == access_scope.value,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()


class DocAccessTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        user_id: str,
        raw_token: str,
        expires_at: datetime,
        *,
        access_scope: DocAccessScope = DocAccessScope.ORG_DOCUMENTS,
    ) -> DocAccessToken:
        doc_token = DocAccessToken(
            user_id=user_id,
            token_hash=hash_token(raw_token),
            expires_at=expires_at,
            access_scope=access_scope.value,
        )
        self.session.add(doc_token)
        await self.session.flush()
        return doc_token

    async def find_valid(
        self,
        raw_token: str,
        *,
        access_scope: DocAccessScope = DocAccessScope.ORG_DOCUMENTS,
    ) -> DocAccessToken | None:
        """Return the token row only if it exists, matches scope, is not revoked, and has not expired."""
        now = datetime.now(UTC)
        fingerprint = hash_token(raw_token)
        stmt = select(DocAccessToken).where(
            DocAccessToken.token_hash == fingerprint,
            DocAccessToken.access_scope == access_scope.value,
            DocAccessToken.expires_at > now,
            DocAccessToken.revoked_at.is_(None),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def revoke_all_active_for_user(self, user_id: str) -> None:
        now = datetime.now(UTC)
        stmt = (
            update(DocAccessToken)
            .where(
                DocAccessToken.user_id == user_id,
                DocAccessToken.revoked_at.is_(None),
                DocAccessToken.expires_at > now,
            )
            .values(revoked_at=now)
        )
        await self.session.execute(stmt)


class ShareOtpRepository:
    """Repository for ShareOtp — short-lived OTPs for unauthenticated share-link recipients."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, recipient_email: str, share_token: str, otp_code: str, expires_at: datetime) -> ShareOtp:
        otp = ShareOtp(
            recipient_email=recipient_email.lower(),
            share_token=share_token,
            otp_code=otp_code,
            expires_at=expires_at,
        )
        self.session.add(otp)
        await self.session.flush()
        return otp

    async def invalidate_unused_active_for_recipient(self, recipient_email: str, share_token: str) -> None:
        """Mark unused, unexpired OTPs for this recipient+share as used (superseded by a new send)."""
        now = datetime.now(UTC)
        stmt = (
            update(ShareOtp)
            .where(
                ShareOtp.recipient_email == recipient_email.lower(),
                ShareOtp.share_token == share_token,
                ShareOtp.is_used.is_(False),
                ShareOtp.expires_at > now,
            )
            .values(is_used=True)
        )
        await self.session.execute(stmt)

    async def find_valid(self, recipient_email: str, share_token: str, otp_code: str) -> ShareOtp | None:
        now = datetime.now(UTC)
        stmt = (
            select(ShareOtp)
            .where(
                ShareOtp.recipient_email == recipient_email.lower(),
                ShareOtp.share_token == share_token,
                ShareOtp.otp_code == otp_code,
                ShareOtp.is_used.is_(False),
                ShareOtp.expires_at > now,
            )
            .order_by(ShareOtp.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_used(self, otp_id: str) -> None:
        await self.session.execute(update(ShareOtp).where(ShareOtp.id == otp_id).values(is_used=True))

    async def count_recent(self, recipient_email: str, share_token: str, since: datetime) -> int:
        stmt = select(func.count()).where(
            ShareOtp.recipient_email == recipient_email.lower(),
            ShareOtp.share_token == share_token,
            ShareOtp.created_at >= since,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()


class ShareAccessTokenRepository:
    """Repository for ShareAccessToken — 1-hour grants issued after OTP verification."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        recipient_email: str,
        share_token: str,
        raw_token: str,
        expires_at: datetime,
    ) -> ShareAccessToken:
        row = ShareAccessToken(
            recipient_email=recipient_email.lower(),
            share_token=share_token,
            token_hash=hash_token(raw_token),
            expires_at=expires_at,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def find_valid(self, raw_token: str, share_token: str) -> ShareAccessToken | None:
        now = datetime.now(UTC)
        fingerprint = hash_token(raw_token)
        stmt = select(ShareAccessToken).where(
            ShareAccessToken.token_hash == fingerprint,
            ShareAccessToken.share_token == share_token,
            ShareAccessToken.expires_at > now,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


class OrgDraftRepository(BaseRepository):
    """Repository for org_drafts pivot rows."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgDraft)

    async def generate_draft_number(self) -> str:
        """Generate the next ORG-D-NNN draft number via DB sequence."""
        result = await self.session.execute(text("SELECT nextval('org_draft_number_seq')"))
        seq_val: int = result.scalar_one()
        return f"ORG-D-{seq_val:03d}"

    async def get_by_draft_number(self, draft_number: str) -> OrgDraft | None:
        """Fetch OrgDraft by draft_number, eagerly loading its organization."""
        stmt = (
            select(OrgDraft)
            .where(OrgDraft.draft_number == draft_number)
            .options(selectinload(OrgDraft.organization))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_org_id(self, organization_id: str) -> OrgDraft | None:
        """Fetch OrgDraft by organization_id, eagerly loading its organization."""
        stmt = (
            select(OrgDraft)
            .where(OrgDraft.organization_id == organization_id)
            .options(selectinload(OrgDraft.organization))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_drafts(
        self,
        *,
        page: int = 1,
        size: int = 20,
        search: str | None = None,
    ) -> tuple[list[OrgDraft], int]:
        """Paginated list of all DRAFT-status orgs with their OrgDraft pivot loaded."""
        stmt = (
            select(OrgDraft)
            .join(Organization, Organization.id == OrgDraft.organization_id)
            .where(Organization.status == OrganizationStatus.DRAFT)
            .options(selectinload(OrgDraft.organization))
        )
        count_stmt = (
            select(func.count())
            .select_from(OrgDraft)
            .join(Organization, Organization.id == OrgDraft.organization_id)
            .where(Organization.status == OrganizationStatus.DRAFT)
        )

        if search:
            pattern = f"%{search}%"
            search_filter = or_(
                OrgDraft.draft_number.ilike(pattern),
                Organization.trading_name.ilike(pattern),
                Organization.reference.ilike(pattern),
                Organization.legal_entity_name.ilike(pattern),
            )
            stmt = stmt.where(search_filter)
            count_stmt = count_stmt.where(search_filter)

        count_result = await self.session.execute(count_stmt)
        total: int = count_result.scalar_one()

        offset = (page - 1) * size
        stmt = stmt.order_by(OrgDraft.created_at.desc()).offset(offset).limit(size)
        result = await self.session.execute(stmt)
        items = list(result.scalars().all())

        return items, total
