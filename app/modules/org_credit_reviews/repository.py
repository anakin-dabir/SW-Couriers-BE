from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.common.repository import BaseRepository
from app.modules.org_credit_reviews.models import OrgCreditReview


class OrgCreditReviewRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrgCreditReview)

    async def list_for_org(
        self,
        organization_id: str,
        *,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[OrgCreditReview], int]:
        base = OrgCreditReview.organization_id == organization_id
        count_stmt = select(func.count()).select_from(OrgCreditReview).where(base)
        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            select(OrgCreditReview)
            .where(base)
            .options(joinedload(OrgCreditReview.reviewer))
            .order_by(OrgCreditReview.review_date.desc(), OrgCreditReview.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        items = (await self.session.execute(stmt)).scalars().unique().all()
        return list(items), total

    async def get_by_id_and_org_with_reviewer(
        self,
        review_id: str,
        organization_id: str,
    ) -> OrgCreditReview | None:
        stmt = (
            select(OrgCreditReview)
            .where(
                OrgCreditReview.id == review_id,
                OrgCreditReview.organization_id == organization_id,
            )
            .options(joinedload(OrgCreditReview.reviewer))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_latest_for_org(self, organization_id: str) -> OrgCreditReview | None:
        stmt = (
            select(OrgCreditReview)
            .where(OrgCreditReview.organization_id == organization_id)
            .options(joinedload(OrgCreditReview.reviewer))
            .order_by(OrgCreditReview.review_date.desc(), OrgCreditReview.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
