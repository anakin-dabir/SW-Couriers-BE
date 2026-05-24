from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header

from app.common.deps import CurrentUserDep, SessionDep
from app.common.exceptions import AuthenticationError


async def _require_vehicle_doc_access(
    user: CurrentUserDep,
    session: SessionDep,
    x_vehicle_doc_access_token: Annotated[
        str | None,
        Header(
            alias="x-vehicle-doc-access-token",
            description=(
                "Vehicle document access token from POST /v1/vehicles/documents/otp/verify. "
                "Required when listing or deleting vehicle documents, not for uploads. Valid for 1 hour."
            ),
        ),
    ] = None,
) -> None:
    if not x_vehicle_doc_access_token:
        raise AuthenticationError(
            "X-Vehicle-Doc-Access-Token header is required to list or delete vehicle documents. "
            "Request an OTP via POST /v1/vehicles/documents/otp/send, "
            "then verify it via POST /v1/vehicles/documents/otp/verify "
            "to receive your token."
        )

    from app.modules.organizations.doc_access_scope import DocAccessScope
    from app.modules.organizations.doc_access_service import DocAccessService

    svc = DocAccessService(session)
    await svc.validate_token(
        token=x_vehicle_doc_access_token,
        user_id=user.id,
        access_scope=DocAccessScope.VEHICLE_DOCUMENTS,
    )


VehicleDocAccessDep = Annotated[None, Depends(_require_vehicle_doc_access)]
