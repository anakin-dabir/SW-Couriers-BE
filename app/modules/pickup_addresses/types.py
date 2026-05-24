from __future__ import annotations

from dataclasses import dataclass

from app.common.deps import AuthUser
from app.common.enums import UserRole
from app.common.exceptions import ForbiddenError, ValidationError


@dataclass(frozen=True, slots=True)
class PickupAddressOwner:
    organization_id: str | None = None
    user_id: str | None = None

    @property
    def label(self) -> str:
        if self.organization_id:
            return f"org={self.organization_id}"
        return f"user={self.user_id}"


def resolve_pickup_address_owner_from_auth(auth: AuthUser) -> PickupAddressOwner:
    if auth.role == UserRole.CUSTOMER_B2B.value:
        if not auth.organization_id:
            raise ValidationError("B2B user must belong to an organization to manage pickup addresses")
        return PickupAddressOwner(organization_id=auth.organization_id)
    if auth.role == UserRole.CUSTOMER_B2C.value:
        return PickupAddressOwner(user_id=auth.id)
    raise ForbiddenError("Pickup addresses can only be managed by B2B or B2C customers")


def owner_scope_for_repo(owner: PickupAddressOwner) -> dict[str, str]:
    if owner.organization_id:
        return {"organization_id": owner.organization_id}
    if owner.user_id:
        return {"user_id": owner.user_id}
    raise ValidationError("Invalid scope for pickup addresses")


def require_organization_id(owner: PickupAddressOwner) -> str:
    if owner.organization_id is None:
        raise ValidationError("Organization scope is required for this operation")
    return owner.organization_id
