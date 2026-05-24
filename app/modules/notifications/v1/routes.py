"""Notification API routes — prefix-based, one family per UI surface.

Preferences (3 × 3 routes):
    /preferences/admin/{notification_type}                                (GET / PATCH)
    /preferences/admin/{notification_type}/reset                          (POST)
    /preferences/organization/{organization_id}/{notification_type}       (GET / PATCH)
    /preferences/organization/{organization_id}/{notification_type}/reset (POST)
    /preferences/b2b_dashboard/{notification_type}                        (GET / PATCH)
    /preferences/b2b_dashboard/{notification_type}/reset                  (POST)

Templates (3 × 3 routes — GET, PUT, single-template reset):
    /templates/admin/{notification_type}/{event}/{channel}                (GET / PUT)
    /templates/admin/{notification_type}/{event}/{channel}/reset          (POST)
    /templates/organization/{organization_id}/{notification_type}/{event}/{channel} (GET / PUT)
    /templates/organization/.../{event}/{channel}/reset                    (POST)
    /templates/b2b_dashboard/{notification_type}/{event}/{channel}        (GET / PUT)
    /templates/b2b_dashboard/.../{event}/{channel}/reset                  (POST)

``POST /preferences/.../reset`` still wipes **all** toggles and **all** custom
templates at that scope. Use ``POST .../templates/.../reset`` to clear only the
template pin for one event + channel at the current layer (toggles unchanged).

Prefix → caller scope mapping (internal storage layer derived from scope + notification type):
    /admin/          → ``ADMIN`` scope; ADMIN_INTERNAL writes to user prefs, B2B_CUSTOMER / RECIPIENT to system
    /organization/   → ``ORGANIZATION`` scope; all writes to org prefs (scoped to ``{organization_id}``)
    /b2b_dashboard/  → ``B2B_CUSTOMER`` scope; B2B_CUSTOMER writes to that contact's user prefs

Routes are thin — all cascade logic lives in ``NotificationManagementService``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.common.deps import CurrentUserDep
from app.common.exceptions import NotFoundError, ValidationError
from app.common.response import ok
from app.common.schemas import MessageResponse, PaginatedResponse, SuccessResponse
from app.modules.notifications.access import (
    AdminNotificationReadPerm,
    AdminNotificationWritePerm,
    AdminOrB2BNotificationReadPerm,
    AdminOrB2BOrgNotificationReadPerm,
    AdminOrB2BOrgNotificationWritePerm,
    B2BNotificationReadPerm,
    B2BNotificationWritePerm,
)
from app.modules.notifications.enums import (
    NotificationEvent,
    NotificationType,
    PreferenceScope,
    PreferenceStream,
    TemplateChannel,
)
from app.modules.notifications.service import NotificationManagementService
from app.modules.notifications.v1.docs import (
    GET_ADMIN_PREFERENCES,
    GET_ADMIN_TEMPLATE,
    GET_B2B_PREFERENCES,
    GET_B2B_TEMPLATE,
    GET_ORG_PREFERENCES,
    GET_ORG_TEMPLATE,
    GET_UNREAD_COUNT,
    LIST_EVENTS_FOR_TYPE,
    LIST_MY_NOTIFICATIONS,
    MARK_ALL_READ,
    MARK_READ,
    REGISTER_DEVICE,
    RESET_ADMIN_PREFERENCES,
    RESET_B2B_PREFERENCES,
    RESET_ADMIN_TEMPLATE,
    RESET_B2B_TEMPLATE,
    RESET_ORG_PREFERENCES,
    RESET_ORG_TEMPLATE,
    SEND_TEST,
    UNREGISTER_DEVICE,
    UPDATE_ADMIN_PREFERENCES,
    UPDATE_B2B_PREFERENCES,
    UPDATE_ORG_PREFERENCES,
    UPSERT_ADMIN_TEMPLATE,
    UPSERT_B2B_TEMPLATE,
    UPSERT_ORG_TEMPLATE,
)
from app.modules.notifications.v1.schemas import (
    CategoryGroup,
    DeviceTokenResponse,
    EventMeta,
    InboxListParams,
    NotificationItem,
    RegisterDeviceRequest,
    TemplateResponse,
    TestNotificationRequest,
    TestNotificationResponse,
    UnreadCountResponse,
    UpdatePreferencesRequest,
    UpsertTemplateRequest,
)

router = APIRouter()

MgmtServiceDep = Annotated[NotificationManagementService, Depends(NotificationManagementService.dep)]


# Parsing helpers — raise 422 on unknown values


def _parse_notification_type(notification_type: str) -> NotificationType:
    try:
        PreferenceStream(notification_type)
    except ValueError:
        valid = ", ".join(s.value for s in PreferenceStream)
        raise ValidationError(f"Invalid notification_type '{notification_type}'. Must be one of: {valid}") from None
    return NotificationType(notification_type)


def _parse_event(event: str) -> NotificationEvent:
    try:
        return NotificationEvent(event)
    except ValueError:
        raise ValidationError(f"Invalid event '{event}'") from None


def _parse_channel(channel: str) -> TemplateChannel:
    try:
        return TemplateChannel(channel)
    except ValueError:
        raise ValidationError(f"Invalid channel '{channel}'. Templates are editable for EMAIL and SMS only") from None


# Inbox


@router.get(
    "/inbox",
    response_model=SuccessResponse[PaginatedResponse[NotificationItem]],
    **LIST_MY_NOTIFICATIONS,
)
async def list_my_notifications(
    user: CurrentUserDep,
    mgmt: MgmtServiceDep,
    params: Annotated[InboxListParams, Query()],
) -> dict:
    data = await mgmt.list_my_notifications(user.id, page=params.page, size=params.size, unread_only=params.unread_only)
    return ok(data)


@router.get(
    "/inbox/unread/count",
    response_model=SuccessResponse[UnreadCountResponse],
    **GET_UNREAD_COUNT,
)
async def get_unread_count(user: CurrentUserDep, mgmt: MgmtServiceDep) -> dict:
    data = await mgmt.get_unread_count(user.id)
    return ok(data)


@router.put(
    "/inbox/{notification_id}/read",
    response_model=MessageResponse,
    **MARK_READ,
)
async def mark_notification_read(notification_id: str, user: CurrentUserDep, mgmt: MgmtServiceDep) -> dict:
    updated = await mgmt.mark_notification_read(notification_id, user.id)
    if not updated:
        raise NotFoundError(resource="notification", id=notification_id)
    return ok(message="Notification marked as read")


@router.put(
    "/inbox/read-all",
    response_model=MessageResponse,
    **MARK_ALL_READ,
)
async def mark_all_notifications_read(user: CurrentUserDep, mgmt: MgmtServiceDep) -> dict:
    count = await mgmt.mark_all_notifications_read(user.id)
    return ok(message=f"{count} notification(s) marked as read")


# Events listing — GET /events/{notification_type}


@router.get(
    "/events/{notification_type}",
    response_model=SuccessResponse[list[EventMeta]],
    **LIST_EVENTS_FOR_TYPE,
)
async def list_events_for_type(
    notification_type: str,
    user: AdminOrB2BNotificationReadPerm,
    mgmt: MgmtServiceDep,
) -> dict:
    nt = _parse_notification_type(notification_type)
    data = await mgmt.list_events_for_type(nt)
    return ok(data)


# Preferences — /admin/{notification_type}


@router.get(
    "/preferences/admin/{notification_type}",
    response_model=SuccessResponse[list[CategoryGroup]],
    **GET_ADMIN_PREFERENCES,
)
async def get_admin_preferences(
    notification_type: str,
    user: AdminNotificationReadPerm,
    mgmt: MgmtServiceDep,
) -> dict:
    nt = _parse_notification_type(notification_type)
    data = await mgmt.get_preferences(scope=PreferenceScope.ADMIN, stream=nt, user=user)
    return ok(data)


@router.patch(
    "/preferences/admin/{notification_type}",
    response_model=MessageResponse,
    **UPDATE_ADMIN_PREFERENCES,
)
async def update_admin_preferences(
    notification_type: str,
    data: UpdatePreferencesRequest,
    user: AdminNotificationWritePerm,
    mgmt: MgmtServiceDep,
) -> dict:
    nt = _parse_notification_type(notification_type)
    await mgmt.update_preferences(scope=PreferenceScope.ADMIN, stream=nt, data=data, user=user)
    return ok(message="Preferences updated")


@router.post(
    "/preferences/admin/{notification_type}/reset",
    response_model=MessageResponse,
    **RESET_ADMIN_PREFERENCES,
)
async def reset_admin_preferences(
    notification_type: str,
    user: AdminNotificationWritePerm,
    mgmt: MgmtServiceDep,
) -> dict:
    nt = _parse_notification_type(notification_type)
    await mgmt.reset_preferences(scope=PreferenceScope.ADMIN, stream=nt, user=user)
    return ok(message="Preferences reset")


# Preferences — /organization/{organization_id}/{notification_type}


@router.get(
    "/preferences/organization/{organization_id}/{notification_type}",
    response_model=SuccessResponse[list[CategoryGroup]],
    **GET_ORG_PREFERENCES,
)
async def get_organization_preferences(
    organization_id: str,
    notification_type: str,
    user: AdminOrB2BOrgNotificationReadPerm,
    mgmt: MgmtServiceDep,
) -> dict:
    nt = _parse_notification_type(notification_type)
    data = await mgmt.get_preferences(
        scope=PreferenceScope.ORGANIZATION,
        stream=nt,
        user=user,
        organization_id=organization_id,
    )
    return ok(data)


@router.patch(
    "/preferences/organization/{organization_id}/{notification_type}",
    response_model=MessageResponse,
    **UPDATE_ORG_PREFERENCES,
)
async def update_organization_preferences(
    organization_id: str,
    notification_type: str,
    data: UpdatePreferencesRequest,
    user: AdminOrB2BOrgNotificationWritePerm,
    mgmt: MgmtServiceDep,
) -> dict:
    nt = _parse_notification_type(notification_type)
    await mgmt.update_preferences(
        scope=PreferenceScope.ORGANIZATION,
        stream=nt,
        data=data,
        user=user,
        organization_id=organization_id,
    )
    return ok(message="Organization preferences updated")


@router.post(
    "/preferences/organization/{organization_id}/{notification_type}/reset",
    response_model=MessageResponse,
    **RESET_ORG_PREFERENCES,
)
async def reset_organization_preferences(
    organization_id: str,
    notification_type: str,
    user: AdminOrB2BOrgNotificationWritePerm,
    mgmt: MgmtServiceDep,
) -> dict:
    nt = _parse_notification_type(notification_type)
    await mgmt.reset_preferences(
        scope=PreferenceScope.ORGANIZATION,
        stream=nt,
        user=user,
        organization_id=organization_id,
    )
    return ok(message="Organization preferences reset")


# Preferences — /b2b_dashboard/{notification_type}


@router.get(
    "/preferences/b2b_dashboard/{notification_type}",
    response_model=SuccessResponse[list[CategoryGroup]],
    **GET_B2B_PREFERENCES,
)
async def get_b2b_preferences(
    notification_type: str,
    user: B2BNotificationReadPerm,
    mgmt: MgmtServiceDep,
) -> dict:
    nt = _parse_notification_type(notification_type)
    data = await mgmt.get_preferences(scope=PreferenceScope.B2B_DASHBOARD, stream=nt, user=user)
    return ok(data)


@router.patch(
    "/preferences/b2b_dashboard/{notification_type}",
    response_model=MessageResponse,
    **UPDATE_B2B_PREFERENCES,
)
async def update_b2b_preferences(
    notification_type: str,
    data: UpdatePreferencesRequest,
    user: B2BNotificationWritePerm,
    mgmt: MgmtServiceDep,
) -> dict:
    nt = _parse_notification_type(notification_type)
    await mgmt.update_preferences(scope=PreferenceScope.B2B_DASHBOARD, stream=nt, data=data, user=user)
    return ok(message="Preferences updated")


@router.post(
    "/preferences/b2b_dashboard/{notification_type}/reset",
    response_model=MessageResponse,
    **RESET_B2B_PREFERENCES,
)
async def reset_b2b_preferences(
    notification_type: str,
    user: B2BNotificationWritePerm,
    mgmt: MgmtServiceDep,
) -> dict:
    nt = _parse_notification_type(notification_type)
    await mgmt.reset_preferences(scope=PreferenceScope.B2B_DASHBOARD, stream=nt, user=user)
    return ok(message="Preferences reset")


# Templates — /admin/{notification_type}/{event}/{channel}


@router.get(
    "/templates/admin/{notification_type}/{event}/{channel}",
    response_model=SuccessResponse[TemplateResponse],
    **GET_ADMIN_TEMPLATE,
)
async def get_admin_template(
    notification_type: str,
    event: str,
    channel: str,
    user: AdminNotificationReadPerm,
    mgmt: MgmtServiceDep,
) -> dict:
    nt = _parse_notification_type(notification_type)
    data = await mgmt.get_template_by_context(
        scope=PreferenceScope.ADMIN,
        stream=nt,
        event=_parse_event(event),
        channel=_parse_channel(channel),
        user=user,
    )
    return ok(data)


@router.put(
    "/templates/admin/{notification_type}/{event}/{channel}",
    response_model=SuccessResponse[TemplateResponse],
    **UPSERT_ADMIN_TEMPLATE,
)
async def upsert_admin_template(
    notification_type: str,
    event: str,
    channel: str,
    data: UpsertTemplateRequest,
    user: AdminNotificationWritePerm,
    mgmt: MgmtServiceDep,
) -> dict:
    nt = _parse_notification_type(notification_type)
    result = await mgmt.upsert_template_by_context(
        scope=PreferenceScope.ADMIN,
        stream=nt,
        event=_parse_event(event),
        channel=_parse_channel(channel),
        data=data,
        user=user,
    )
    return ok(result)


@router.post(
    "/templates/admin/{notification_type}/{event}/{channel}/reset",
    response_model=SuccessResponse[TemplateResponse],
    **RESET_ADMIN_TEMPLATE,
)
async def reset_admin_template(
    notification_type: str,
    event: str,
    channel: str,
    user: AdminNotificationWritePerm,
    mgmt: MgmtServiceDep,
) -> dict:
    nt = _parse_notification_type(notification_type)
    data = await mgmt.reset_template_by_context(
        scope=PreferenceScope.ADMIN,
        stream=nt,
        event=_parse_event(event),
        channel=_parse_channel(channel),
        user=user,
    )
    return ok(data)


# Templates — /organization/{organization_id}/{notification_type}/{event}/{channel}


@router.get(
    "/templates/organization/{organization_id}/{notification_type}/{event}/{channel}",
    response_model=SuccessResponse[TemplateResponse],
    **GET_ORG_TEMPLATE,
)
async def get_organization_template(
    organization_id: str,
    notification_type: str,
    event: str,
    channel: str,
    user: AdminOrB2BNotificationReadPerm,
    mgmt: MgmtServiceDep,
) -> dict:
    nt = _parse_notification_type(notification_type)
    data = await mgmt.get_template_by_context(
        scope=PreferenceScope.ORGANIZATION,
        stream=nt,
        event=_parse_event(event),
        channel=_parse_channel(channel),
        user=user,
        organization_id=organization_id,
    )
    return ok(data)


@router.put(
    "/templates/organization/{organization_id}/{notification_type}/{event}/{channel}",
    response_model=SuccessResponse[TemplateResponse],
    **UPSERT_ORG_TEMPLATE,
)
async def upsert_organization_template(
    organization_id: str,
    notification_type: str,
    event: str,
    channel: str,
    data: UpsertTemplateRequest,
    user: AdminOrB2BOrgNotificationWritePerm,
    mgmt: MgmtServiceDep,
) -> dict:
    nt = _parse_notification_type(notification_type)
    result = await mgmt.upsert_template_by_context(
        scope=PreferenceScope.ORGANIZATION,
        stream=nt,
        event=_parse_event(event),
        channel=_parse_channel(channel),
        data=data,
        user=user,
        organization_id=organization_id,
    )
    return ok(result)


@router.post(
    "/templates/organization/{organization_id}/{notification_type}/{event}/{channel}/reset",
    response_model=SuccessResponse[TemplateResponse],
    **RESET_ORG_TEMPLATE,
)
async def reset_organization_template(
    organization_id: str,
    notification_type: str,
    event: str,
    channel: str,
    user: AdminOrB2BOrgNotificationWritePerm,
    mgmt: MgmtServiceDep,
) -> dict:
    nt = _parse_notification_type(notification_type)
    data = await mgmt.reset_template_by_context(
        scope=PreferenceScope.ORGANIZATION,
        stream=nt,
        event=_parse_event(event),
        channel=_parse_channel(channel),
        user=user,
        organization_id=organization_id,
    )
    return ok(data)


## NOT ALLOWED ON B2B DASHBOARD FOR NOW
# # Templates — /b2b_dashboard/{notification_type}/{event}/{channel}


# @router.get(
#     "/templates/b2b_dashboard/{notification_type}/{event}/{channel}",
#     response_model=SuccessResponse[TemplateResponse],
#     **GET_B2B_TEMPLATE,
# )
# async def get_b2b_template(
#     notification_type: str,
#     event: str,
#     channel: str,
#     user: B2BNotificationReadPerm,
#     mgmt: MgmtServiceDep,
# ) -> dict:
#     nt = _parse_notification_type(notification_type)
#     data = await mgmt.get_template_by_context(
#         scope=PreferenceScope.B2B_DASHBOARD,
#         stream=nt,
#         event=_parse_event(event),
#         channel=_parse_channel(channel),
#         user=user,
#     )
#     return ok(data)


# @router.put(
#     "/templates/b2b_dashboard/{notification_type}/{event}/{channel}",
#     response_model=SuccessResponse[TemplateResponse],
#     **UPSERT_B2B_TEMPLATE,
# )
# async def upsert_b2b_template(
#     notification_type: str,
#     event: str,
#     channel: str,
#     data: UpsertTemplateRequest,
#     user: B2BDep,
#     mgmt: MgmtServiceDep,
# ) -> dict:
#     nt = _parse_notification_type(notification_type)
#     result = await mgmt.upsert_template_by_context(
#         scope=PreferenceScope.B2B_DASHBOARD,
#         stream=nt,
#         event=_parse_event(event),
#         channel=_parse_channel(channel),
#         data=data,
#         user=user,
#     )
#     return ok(result)


# @router.post(
#     "/templates/b2b_dashboard/{notification_type}/{event}/{channel}/reset",
#     response_model=SuccessResponse[TemplateResponse],
#     **RESET_B2B_TEMPLATE,
# )
# async def reset_b2b_template(
#     notification_type: str,
#     event: str,
#     channel: str,
#     user: B2BDep,
#     mgmt: MgmtServiceDep,
# ) -> dict:
#     nt = _parse_notification_type(notification_type)
#     data = await mgmt.reset_template_by_context(
#         scope=PreferenceScope.B2B_DASHBOARD,
#         stream=nt,
#         event=_parse_event(event),
#         channel=_parse_channel(channel),
#         user=user,
#     )
#     return ok(data)


# Device tokens


@router.post(
    "/devices",
    response_model=SuccessResponse[DeviceTokenResponse],
    status_code=status.HTTP_201_CREATED,
    **REGISTER_DEVICE,
)
async def register_device(data: RegisterDeviceRequest, user: CurrentUserDep, mgmt: MgmtServiceDep) -> dict:
    token = await mgmt.register_device(user.id, data)
    return ok(token)


@router.delete(
    "/devices/{token_id}",
    response_model=MessageResponse,
    **UNREGISTER_DEVICE,
)
async def unregister_device(token_id: str, user: CurrentUserDep, mgmt: MgmtServiceDep) -> dict:
    await mgmt.unregister_device(token_id, user.id)
    return ok(message="Device unregistered")


# Test notification


@router.post(
    "/test",
    response_model=SuccessResponse[TestNotificationResponse],
    **SEND_TEST,
)
async def send_test_notification(
    data: TestNotificationRequest,
    user: AdminOrB2BOrgNotificationWritePerm,
    mgmt: MgmtServiceDep,
) -> dict:
    result = await mgmt.send_test_notification(data, user=user)
    return ok(result)
