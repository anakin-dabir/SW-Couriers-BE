from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.common.repository import BaseRepository
from app.modules.payments.enums import PaymentMethodStatus
from app.modules.payments.models import BraintreeFeeProfile, BraintreeWebhookEvent, CreditCard


class CreditCardRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, CreditCard)

    async def find_by_owner(
        self,
        *,
        organization_id: str | None = None,
        user_id: str | None = None,
    ) -> list[CreditCard]:
        stmt = (
            select(CreditCard)
            .where(
                self._owner_filter(organization_id=organization_id, user_id=user_id),
                CreditCard.status == PaymentMethodStatus.ACTIVE,
            )
            .order_by(CreditCard.is_default.desc(), CreditCard.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_owner(
        self,
        *,
        organization_id: str | None = None,
        user_id: str | None = None,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(CreditCard)
            .where(
                self._owner_filter(organization_id=organization_id, user_id=user_id),
                CreditCard.status == PaymentMethodStatus.ACTIVE,
            )
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def clear_defaults(
        self,
        *,
        organization_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Unset is_default on all cards for this owner."""
        stmt = (
            update(CreditCard)
            .where(
                self._owner_filter(organization_id=organization_id, user_id=user_id),
                CreditCard.is_default.is_(True),
            )
            .values(is_default=False)
        )
        await self.session.execute(stmt)

    @staticmethod
    def _owner_filter(
        *,
        organization_id: str | None = None,
        user_id: str | None = None,
    ) -> ColumnElement[bool]:
        """Build the ownership filter clause."""
        if organization_id:
            return CreditCard.organization_id == organization_id
        if user_id:
            return CreditCard.user_id == user_id
        raise ValueError("Either organization_id or user_id must be provided")


class BraintreeWebhookEventRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, BraintreeWebhookEvent)


class BraintreeFeeProfileRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, BraintreeFeeProfile)

    async def get_active_profile(self, *, market: str = "UK", currency: str = "GBP", card_brand: str = "STANDARD") -> BraintreeFeeProfile | None:
        return await self.find_one(
            market=market.upper(),
            currency=currency.upper(),
            card_brand=card_brand.upper(),
            is_active=True,
        )

    @staticmethod
    def normalize_card_brand(card_type: str | None) -> str:
        raw = str(card_type or "").strip().upper()
        if raw in {"AMEX", "AMERICAN EXPRESS"}:
            return "AMEX"
        return "STANDARD"

    async def estimate_fee(
        self,
        *,
        amount: Decimal,
        currency: str = "GBP",
        card_brand: str = "STANDARD",
        card_issued_outside_market: bool = False,
        settled_in_scheme_currency: bool = False,
        settled_in_exotic_currency: bool = False,
    ) -> Decimal | None:
        profile = await self.get_active_profile(currency=currency, card_brand=card_brand)
        if profile is None:
            return None

        pct = Decimal(profile.rate_percent or 0)
        fixed = Decimal(profile.fixed_fee or 0)
        if card_issued_outside_market:
            pct += Decimal(profile.cross_border_percent or 0)
        if settled_in_scheme_currency:
            pct += Decimal(profile.scheme_currency_percent or 0)
        if settled_in_exotic_currency:
            pct += Decimal(profile.exotic_currency_percent or 0)

        variable = (amount * pct) / Decimal("100")
        return (variable + fixed).quantize(Decimal("0.01"))

    async def estimate_fee_by_card_type(
        self,
        *,
        amount: Decimal,
        card_type: str | None,
        currency: str = "GBP",
        country_of_issuance: str | None = None,
        settled_in_scheme_currency: bool = False,
        settled_in_exotic_currency: bool = False,
    ) -> Decimal | None:
        brand = self.normalize_card_brand(card_type)
        issuance = str(country_of_issuance or "").strip().upper()
        outside_market = bool(issuance and issuance not in {"GB", "UK"})
        requested_currency = str(currency or "").strip().upper() or "GBP"
        tx_fee = await self.estimate_fee(
            amount=amount,
            currency=requested_currency,
            card_brand=brand,
            card_issued_outside_market=outside_market,
            settled_in_scheme_currency=settled_in_scheme_currency,
            settled_in_exotic_currency=settled_in_exotic_currency,
        )
        if tx_fee is not None:
            return tx_fee
        return await self.estimate_fee(
            amount=amount,
            currency="GBP",
            card_brand=brand,
            card_issued_outside_market=outside_market,
            settled_in_scheme_currency=settled_in_scheme_currency,
            settled_in_exotic_currency=settled_in_exotic_currency,
        )

    async def estimate_dispute_fee(self, *, currency: str = "GBP", card_type: str | None = None) -> Decimal | None:
        brand = self.normalize_card_brand(card_type)
        requested_currency = str(currency or "").strip().upper() or "GBP"
        profile = await self.get_active_profile(currency=requested_currency, card_brand=brand)
        if profile is None and requested_currency != "GBP":
            profile = await self.get_active_profile(currency="GBP", card_brand=brand)
        if profile is None:
            return None
        return Decimal(profile.chargeback_fee or 0).quantize(Decimal("0.01"))
