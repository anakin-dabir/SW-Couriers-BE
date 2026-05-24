from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.common.deps import Allowed, AuthUser
from app.common.enums import UserRole
from app.common.response import ok
from app.common.schemas import MessageResponse, SuccessResponse
from app.core.config import settings
from app.core.rate_limit import limiter
from app.modules.pickup_addresses.service import PickupAddressService
from app.modules.pickup_addresses.v1.docs import (
    CREATE_PICKUP_ADDRESS,
    DELETE_PICKUP_ADDRESS,
    GEOCODE_ADDRESS,
    GET_PICKUP_ADDRESS,
    LIST_PICKUP_ADDRESSES,
    UPDATE_PICKUP_ADDRESS,
)
from app.modules.pickup_addresses.v1.schemas import (
    CreatePickupAddressesRequest,
    GeocodeAddressRequest,
    GeocodeResultResponse,
    PickupAddressResponse,
    PickupAddressUpdate,
)

router = APIRouter()

PickupReadDep = Annotated[
    AuthUser,
    Allowed(
        UserRole.CUSTOMER_B2B,
        UserRole.CUSTOMER_B2C,
    ),
]
PickupWriteDep = Annotated[
    AuthUser,
    Allowed(
        UserRole.CUSTOMER_B2B,
        UserRole.CUSTOMER_B2C,
    ),
]
PickupServiceDep = Annotated[PickupAddressService, Depends(PickupAddressService.dep)]


@router.post(
    "/geocode",
    response_model=SuccessResponse[GeocodeResultResponse],
    **GEOCODE_ADDRESS,
)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def geocode_pickup_address(
    request: Request,
    response: Response,
    body: GeocodeAddressRequest,
    user: PickupReadDep,
    svc: PickupServiceDep,
) -> dict:
    data = await svc.geocode_address(user, body)
    return ok(data=data)


@router.get(
    "",
    response_model=SuccessResponse[list[PickupAddressResponse]],
    **LIST_PICKUP_ADDRESSES,
)
async def list_pickup_addresses(
    user: PickupReadDep,
    svc: PickupServiceDep,
) -> dict:
    items = await svc.list_addresses(user)
    return ok(data=items)


@router.get(
    "/{address_id}",
    response_model=SuccessResponse[PickupAddressResponse],
    **GET_PICKUP_ADDRESS,
)
async def get_pickup_address(
    address_id: str,
    user: PickupReadDep,
    svc: PickupServiceDep,
) -> dict:
    item = await svc.get_address(user, address_id)
    return ok(data=item)


@router.post(
    "",
    response_model=SuccessResponse[list[PickupAddressResponse]],
    status_code=status.HTTP_201_CREATED,
    **CREATE_PICKUP_ADDRESS,
)
async def create_pickup_address(
    body: CreatePickupAddressesRequest,
    user: PickupWriteDep,
    svc: PickupServiceDep,
) -> dict:
    items = await svc.create_addresses(user, body)
    return ok(data=items, message="Pickup addresses created")


@router.patch(
    "/{address_id}",
    response_model=SuccessResponse[PickupAddressResponse],
    **UPDATE_PICKUP_ADDRESS,
)
async def update_pickup_address(
    address_id: str,
    body: PickupAddressUpdate,
    user: PickupWriteDep,
    svc: PickupServiceDep,
) -> dict:
    item = await svc.update_address(user, address_id, body)
    return ok(data=item)


@router.delete(
    "/{address_id}",
    response_model=MessageResponse,
    **DELETE_PICKUP_ADDRESS,
)
async def delete_pickup_address(
    address_id: str,
    user: PickupWriteDep,
    svc: PickupServiceDep,
) -> dict:
    await svc.delete_address(user, address_id)
    return ok(message="Pickup address deleted")
