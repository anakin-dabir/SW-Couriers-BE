from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.common.exceptions import NotFoundError
from app.common.repository import BaseRepository
from app.modules.org_credit_applications.enums import (
    AttachmentType,
    CreditApplicationLifecycleState,
    CreditApplicationStatus,
    OrgCreditLimitIncreaseRequestStatus,
)
from app.modules.org_credit_applications.models import (
    OrgCreditApplication,
    OrgCreditApplicationAttachment,
    OrgCreditApplicationDraft,
    OrgCreditApplicationTradeReference,
    OrgCreditLimitIncreaseRequest,
)


class OrgCreditApplicationRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgCreditApplication)

    async def generate_application_number(self) -> str:
        from app.modules.org_credit_applications.models import credit_app_seq

        result = await self.session.execute(select(credit_app_seq.next_value()))
        seq_val = result.scalar_one()
        year = datetime.now(UTC).year
        return f"APP-{year}-{int(seq_val):05d}"

    async def list_for_org(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
        status: CreditApplicationStatus | None = None,
        search: str | None = None,
    ) -> tuple[list[OrgCreditApplication], int]:
        base = (
            select(OrgCreditApplication)
            .options(
                selectinload(OrgCreditApplication.trade_references),
                joinedload(OrgCreditApplication.reviewer),
                joinedload(OrgCreditApplication.submitted_by_user),
                joinedload(OrgCreditApplication.approved_by_user),
                joinedload(OrgCreditApplication.rejected_by_user),
                joinedload(OrgCreditApplication.cancelled_by_user),
                joinedload(OrgCreditApplication.withdrawn_by_user),
            )
            .where(
                OrgCreditApplication.organization_id == organization_id,
                OrgCreditApplication.deleted_at.is_(None),
                OrgCreditApplication.state == CreditApplicationLifecycleState.ACTIVE,
            )
        )
        count_base = (
            select(func.count())
            .select_from(OrgCreditApplication)
            .where(
                OrgCreditApplication.organization_id == organization_id,
                OrgCreditApplication.deleted_at.is_(None),
                OrgCreditApplication.state == CreditApplicationLifecycleState.ACTIVE,
            )
        )
        if status is not None:
            base = base.where(OrgCreditApplication.status == status)
            count_base = count_base.where(OrgCreditApplication.status == status)
        if search:
            f = OrgCreditApplication.application_number.ilike(f"%{search}%")
            base = base.where(f)
            count_base = count_base.where(f)

        total = int((await self.session.execute(count_base)).scalar_one())
        stmt = base.order_by(OrgCreditApplication.created_at.desc()).offset((page - 1) * size).limit(size)
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def count_by_status_for_org(self, organization_id: str) -> dict[CreditApplicationStatus, int]:
        stmt = (
            select(OrgCreditApplication.status, func.count())
            .where(
                OrgCreditApplication.organization_id == organization_id,
                OrgCreditApplication.deleted_at.is_(None),
            )
            .group_by(OrgCreditApplication.status)
        )
        result = await self.session.execute(stmt)
        out: dict[CreditApplicationStatus, int] = {s: 0 for s in CreditApplicationStatus}
        for status, cnt in result.all():
            out[status] = int(cnt)
        return out

    async def get_latest_non_draft_application(self, organization_id: str) -> OrgCreditApplication | None:
        stmt = (
            select(OrgCreditApplication)
            .where(
                OrgCreditApplication.organization_id == organization_id,
                OrgCreditApplication.deleted_at.is_(None),
                OrgCreditApplication.state == CreditApplicationLifecycleState.ACTIVE,
            )
            .order_by(OrgCreditApplication.updated_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active(self, application_id: str, organization_id: str) -> OrgCreditApplication | None:
        stmt = select(OrgCreditApplication).where(
            OrgCreditApplication.id == application_id,
            OrgCreditApplication.organization_id == organization_id,
            OrgCreditApplication.deleted_at.is_(None),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_or_404(self, application_id: str, organization_id: str) -> OrgCreditApplication:
        app = await self.get_active(application_id, organization_id)
        if app is None:
            raise NotFoundError(resource="org_credit_applications", id=application_id)
        return app

    async def get_active_with_refs_or_404(
        self, application_id: str, organization_id: str,
    ) -> OrgCreditApplication:
        stmt = (
            select(OrgCreditApplication)
            .options(
                selectinload(OrgCreditApplication.trade_references),
                joinedload(OrgCreditApplication.reviewer),
                joinedload(OrgCreditApplication.submitted_by_user),
                joinedload(OrgCreditApplication.approved_by_user),
                joinedload(OrgCreditApplication.rejected_by_user),
                joinedload(OrgCreditApplication.cancelled_by_user),
                joinedload(OrgCreditApplication.withdrawn_by_user),
            )
            .where(
                OrgCreditApplication.id == application_id,
                OrgCreditApplication.organization_id == organization_id,
                OrgCreditApplication.deleted_at.is_(None),
            )
        )
        result = await self.session.execute(stmt)
        app = result.scalar_one_or_none()
        if app is None:
            raise NotFoundError(resource="org_credit_applications", id=application_id)
        return app

    async def get_latest_active_with_refs_or_404(self, organization_id: str) -> OrgCreditApplication:
        stmt = (
            select(OrgCreditApplication)
            .options(
                selectinload(OrgCreditApplication.trade_references),
                joinedload(OrgCreditApplication.reviewer),
                joinedload(OrgCreditApplication.submitted_by_user),
                joinedload(OrgCreditApplication.approved_by_user),
                joinedload(OrgCreditApplication.rejected_by_user),
                joinedload(OrgCreditApplication.cancelled_by_user),
                joinedload(OrgCreditApplication.withdrawn_by_user),
            )
            .where(
                OrgCreditApplication.organization_id == organization_id,
                OrgCreditApplication.deleted_at.is_(None),
                OrgCreditApplication.state == CreditApplicationLifecycleState.ACTIVE,
            )
            .order_by(OrgCreditApplication.updated_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        app = result.scalar_one_or_none()
        if app is None:
            raise NotFoundError(resource="credit_application")
        return app

    async def soft_delete(
        self,
        id: str,
        *,
        status_field: str = "status",
        target_status: str = "inactive",
        **scope_filters: object,
    ) -> OrgCreditApplication:
        del status_field, target_status
        app = await self.get_by_id_or_404(id, **scope_filters)
        app.deleted_at = datetime.now(UTC)
        app.updated_at = datetime.now(UTC)
        app.version += 1
        await self.session.flush()
        await self.session.refresh(app)
        return app

class OrgCreditApplicationTradeReferenceRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgCreditApplicationTradeReference)

    async def list_by_application(self, application_id: str) -> list[OrgCreditApplicationTradeReference]:
        stmt = (
            select(OrgCreditApplicationTradeReference)
            .where(OrgCreditApplicationTradeReference.application_id == application_id)
            .order_by(OrgCreditApplicationTradeReference.ref_index)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_application(self, application_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(OrgCreditApplicationTradeReference)
            .where(OrgCreditApplicationTradeReference.application_id == application_id)
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def get_by_id_and_application_or_404(
        self, ref_id: str, application_id: str,
    ) -> OrgCreditApplicationTradeReference:
        stmt = select(OrgCreditApplicationTradeReference).where(
            OrgCreditApplicationTradeReference.id == ref_id,
            OrgCreditApplicationTradeReference.application_id == application_id,
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundError(resource="org_credit_application_trade_references", id=ref_id)
        return row

    async def next_ref_index(self, application_id: str) -> int:
        stmt = (
            select(func.coalesce(func.max(OrgCreditApplicationTradeReference.ref_index), -1))
            .where(OrgCreditApplicationTradeReference.application_id == application_id)
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one()) + 1

    async def delete_all_for_application(self, application_id: str) -> None:
        refs = await self.list_by_application(application_id)
        for ref in refs:
            await self.session.delete(ref)
        await self.session.flush()

    async def count_unverified(self, application_id: str) -> int:
        from app.modules.org_credit_applications.enums import TradeReferenceVerificationStatus

        verified = (
            TradeReferenceVerificationStatus.VERIFIED,
            TradeReferenceVerificationStatus.UNABLE_TO_VERIFY,
        )
        stmt = (
            select(func.count())
            .select_from(OrgCreditApplicationTradeReference)
            .where(
                OrgCreditApplicationTradeReference.application_id == application_id,
                OrgCreditApplicationTradeReference.verification_status.not_in(verified),
            )
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())


class OrgCreditApplicationDraftRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgCreditApplicationDraft)

    async def get_by_id_with_application(
        self,
        draft_id: str,
        organization_id: str,
    ) -> OrgCreditApplicationDraft | None:
        stmt = (
            select(OrgCreditApplicationDraft)
            .options(
                selectinload(OrgCreditApplicationDraft.application)
                .selectinload(OrgCreditApplication.trade_references),
            )
            .where(OrgCreditApplicationDraft.id == draft_id)
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        app = row.application
        if app.organization_id != organization_id or app.deleted_at is not None:
            return None
        if row.published_by_id is not None:
            return None
        return row

    async def get_by_id_with_application_or_404(
        self,
        draft_id: str,
        organization_id: str,
    ) -> OrgCreditApplicationDraft:
        row = await self.get_by_id_with_application(draft_id, organization_id)
        if row is None:
            raise NotFoundError(resource="org_credit_application_drafts", id=draft_id)
        return row

    async def list_open_for_org(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[OrgCreditApplicationDraft], int]:
        base = (
            select(OrgCreditApplicationDraft)
            .join(OrgCreditApplication, OrgCreditApplicationDraft.application_id == OrgCreditApplication.id)
            .options(
                selectinload(OrgCreditApplicationDraft.application),
                joinedload(OrgCreditApplicationDraft.created_by_user),
            )
            .where(
                OrgCreditApplication.organization_id == organization_id,
                OrgCreditApplication.deleted_at.is_(None),
                OrgCreditApplication.state == CreditApplicationLifecycleState.DRAFT,
                OrgCreditApplicationDraft.published_by_id.is_(None),
            )
        )
        count_stmt = (
            select(func.count())
            .select_from(OrgCreditApplicationDraft)
            .join(OrgCreditApplication, OrgCreditApplicationDraft.application_id == OrgCreditApplication.id)
            .where(
                OrgCreditApplication.organization_id == organization_id,
                OrgCreditApplication.deleted_at.is_(None),
                OrgCreditApplication.state == CreditApplicationLifecycleState.DRAFT,
                OrgCreditApplicationDraft.published_by_id.is_(None),
            )
        )
        total = int((await self.session.execute(count_stmt)).scalar_one())
        stmt = base.order_by(OrgCreditApplicationDraft.created_at.desc()).offset((page - 1) * size).limit(size)
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, total


class OrgCreditApplicationAttachmentRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgCreditApplicationAttachment)

    async def list_by_application(self, application_id: str) -> list[OrgCreditApplicationAttachment]:
        stmt = select(OrgCreditApplicationAttachment).where(
            OrgCreditApplicationAttachment.application_id == application_id,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_bank_reference(self, application_id: str) -> OrgCreditApplicationAttachment | None:
        stmt = (
            select(OrgCreditApplicationAttachment)
            .where(
                OrgCreditApplicationAttachment.application_id == application_id,
                OrgCreditApplicationAttachment.attachment_type == AttachmentType.BANK_REFERENCE,
            )
            .order_by(OrgCreditApplicationAttachment.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


class OrgCreditLimitIncreaseRequestRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgCreditLimitIncreaseRequest)

    async def count_pending_for_org(self, organization_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(OrgCreditLimitIncreaseRequest)
            .where(
                OrgCreditLimitIncreaseRequest.organization_id == organization_id,
                OrgCreditLimitIncreaseRequest.status == OrgCreditLimitIncreaseRequestStatus.PENDING,
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def get_latest_pending_for_org(self, organization_id: str) -> OrgCreditLimitIncreaseRequest | None:
        stmt = (
            select(OrgCreditLimitIncreaseRequest)
            .where(
                OrgCreditLimitIncreaseRequest.organization_id == organization_id,
                OrgCreditLimitIncreaseRequest.status == OrgCreditLimitIncreaseRequestStatus.PENDING,
            )
            .options(
                joinedload(OrgCreditLimitIncreaseRequest.requested_by_user),
                joinedload(OrgCreditLimitIncreaseRequest.reviewed_by_user),
            )
            .order_by(OrgCreditLimitIncreaseRequest.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_id_and_org_with_users(
        self,
        request_id: str,
        organization_id: str,
    ) -> OrgCreditLimitIncreaseRequest | None:
        stmt = (
            select(OrgCreditLimitIncreaseRequest)
            .where(
                OrgCreditLimitIncreaseRequest.id == request_id,
                OrgCreditLimitIncreaseRequest.organization_id == organization_id,
            )
            .options(
                joinedload(OrgCreditLimitIncreaseRequest.requested_by_user),
                joinedload(OrgCreditLimitIncreaseRequest.reviewed_by_user),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_org(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[OrgCreditLimitIncreaseRequest], int]:
        base_filter = OrgCreditLimitIncreaseRequest.organization_id == organization_id
        count_stmt = select(func.count()).select_from(OrgCreditLimitIncreaseRequest).where(base_filter)
        total = int((await self.session.execute(count_stmt)).scalar_one())
        stmt = (
            select(OrgCreditLimitIncreaseRequest)
            .where(base_filter)
            .options(
                joinedload(OrgCreditLimitIncreaseRequest.requested_by_user),
                joinedload(OrgCreditLimitIncreaseRequest.reviewed_by_user),
            )
            .order_by(OrgCreditLimitIncreaseRequest.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, total
