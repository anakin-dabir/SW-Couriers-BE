"""Driver service — business logic for driver management.

Handles list, get, create, update, and soft-delete with audit logging.
Uses DriverRepository for data access; never touches the DB directly.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import math
import pathlib
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from decimal import Decimal
from enum import Enum
from io import BytesIO

import structlog

try:
    import tzdata  # noqa: F401 — registers IANA zones for ``zoneinfo`` when OS bundle missing (Windows).
except ImportError:
    pass

from fastapi import Request, UploadFile
from sqlalchemy import Float, String, and_, case, cast, exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.enums import UserStatus
from app.common.exceptions import ConflictError, NotFoundError, StorageProviderError, ValidationError
from app.common.service import BaseService
from app.common.utils import get_client_ip, mark_user_suspended, mask_ip_address, unmark_user_suspended
from app.core.config import settings
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.drivers.enums import (
    CalendarEventSource,
    DriverAccountStatus,
    DriverDocumentKind,
    DriverDocumentStatus,
    DriverLiveStatus,
    DriverMapPreference,
    ShiftStatus,
    TrafficViolationStatus,
    TrafficViolationType,
)
from app.modules.drivers.models import (
    Driver,
    DriverDocument,
    DriverShift,
    DriverTermsAcceptanceRecord,
    DriverTimeOff,
    DriverTrafficViolation,
    DriverTrafficViolationProof,
    DriverWeeklySchedule,
)
from app.modules.drivers.repository import (
    DriverDocumentRepository,
    DriverDraftRepository,
    DriverRepository,
    DriverShiftRepository,
    DriverTermsAndConditionsRepository,
    DriverTimeOffRepository,
    DriverTrafficViolationProofRepository,
    DriverTrafficViolationRepository,
    DriverWeeklyScheduleRepository,
)
from app.modules.holidays.enums import HolidayAudience
from app.modules.holidays.models import Holiday
from app.modules.orders.enums import (
    PACKAGE_DRIVER_PATCH_DELIVERY_STATUSES,
    PACKAGE_PRE_PICKUP_FOR_SCAN_STATUSES,
    PACKAGE_RETURN_FLOW_REQUIRES_STOP_POD_STATUSES,
    PACKAGE_STOP_DELIVERY_OUTCOME_STATUSES,
    PACKAGE_STOP_RETURN_HUB_COMPLETE_STATUSES,
    PackageStatus,
)
from app.modules.orders.models import DeliveryStop, Order, Package, PackageMissingReport
from app.modules.orders.stop_note_utils import batch_package_ids_for_stop_notes
from app.modules.orders.repository import PackageExecutionRepository, StopNoteRepository
from app.modules.planning.enums import RouteStatus, RouteStopFlowType, RouteStopStatus, RouteType
from app.modules.planning.models import Route, RouteEvent, RoutePlan, RouteStop
from app.modules.planning.repository import StopExecutionRepository
from app.modules.planning.route_navigation import compute_route_navigation_fingerprint
from app.modules.user.repository import UserRepository
from app.modules.vehicles.models import Vehicle
from app.storage.cloudflare_images import get_images_client
from app.storage.r2_client import generate_presigned_url, get_default_r2_client, get_r2_bucket_name
from app.storage.upload import delete_from_r2, delete_image, generate_image_url

logger = structlog.get_logger()


class DriverService(BaseService):
    _TELEMETRY_SPEEDING_THRESHOLD_MPH: float = 70.0
    _TELEMETRY_HARSH_BRAKE_DELTA_MPH: float = 15.0
    _TELEMETRY_HARSH_BRAKE_WINDOW_SECONDS: float = 120.0

    _TRAFFIC_VIOLATION_ALLOWED_PROOF_TYPES: set[str] = {
        "image/jpeg",
        "image/png",
        "application/pdf",
        "image/heic",
    }
    _TRAFFIC_VIOLATION_MAX_PROOFS: int = 10
    _TRAFFIC_VIOLATION_MAX_PROOF_BYTES: int = 25 * 1024 * 1024

    @staticmethod
    def _normalize_capacities(
        *,
        capacity: str | None = None,
        capacities: list[str] | None = None,
    ) -> list[str]:
        normalized: list[str] = []
        if capacity:
            normalized.append(str(capacity))
        if capacities:
            normalized.extend(str(item) for item in capacities if item)
        deduped = list(dict.fromkeys(normalized))
        if not deduped:
            raise ValidationError("Either capacity or capacities must be provided")
        return deduped

    async def _next_driver_code(self) -> str:
        """Get next DR code using DB sequence when available; fallback for pre-migration DBs."""
        try:
            result = await self._driver_repo.session.execute(text("SELECT nextval('driver_code_seq')"))
            num = int(result.scalar_one())
            return f"DR-{num:03d}" if num < 1000 else f"DR-{num}"
        except Exception:
            # Transitional fallback: supports environments where migration is not yet applied.
            stmt = select(func.max(Driver.driver_code))
            result = await self._driver_repo.session.execute(stmt)
            max_code: str | None = result.scalar_one_or_none()
            if not max_code:
                return "DR-001"
            try:
                _, num_str = max_code.split("-")
                num = int(num_str) + 1
            except Exception:
                num = 1
            return f"DR-{num:03d}" if num < 1000 else f"DR-{num}"

    """Driver management service."""

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._driver_repo = DriverRepository(session)
        self._draft_repo = DriverDraftRepository(session)
        self._document_repo = DriverDocumentRepository(session)
        self._terms_repo = DriverTermsAndConditionsRepository(session)
        self._time_off_repo = DriverTimeOffRepository(session)
        self._weekly_repo = DriverWeeklyScheduleRepository(session)
        self._shift_repo = DriverShiftRepository(session)
        self._violation_repo = DriverTrafficViolationRepository(session)
        self._violation_proof_repo = DriverTrafficViolationProofRepository(session)
        self._stop_note_repo = StopNoteRepository(session)
        self._package_exec_repo = PackageExecutionRepository(session)
        self._stop_exec_repo = StopExecutionRepository(session)
        self._user_repo = UserRepository(session)
        self._audit = AuditService(session)
        self._ip = get_client_ip(request) if request else None
        self._user_agent = request.headers.get("user-agent") if request else None

    # ── Storage helpers (R2) ─────────────────────────────

    @staticmethod
    def _sanitize_filename(filename: str, max_length: int = 80) -> str:
        """Return a safe, URL-clean basename from a user-supplied filename.

        - Keeps only the basename (strips any directory components / path traversal).
        - Removes null bytes and ASCII control characters.
        - Replaces any character that isn't alphanumeric, dot, hyphen, or underscore
          with an underscore — safe for S3 keys and presigned URLs.
        - Collapses repeated underscores and strips leading/trailing dots/underscores.
        - Truncates to max_length, preserving the file extension.
        - Falls back to "file" if nothing useful remains.
        """
        # Strip path components — prevents ../../../etc/passwd style injection.
        name = pathlib.Path(filename).name
        # Remove null bytes and control characters.
        name = re.sub(r"[\x00-\x1f\x7f]", "", name)
        # Allow only safe characters for S3 keys and URLs.
        name = re.sub(r"[^\w.\-]", "_", name)
        # Collapse runs of underscores; strip unsafe edge chars.
        name = re.sub(r"_+", "_", name).strip("_.")
        # Truncate while preserving extension.
        if len(name) > max_length:
            ext = pathlib.Path(name).suffix[:16]  # keep extension up to 16 chars
            name = name[: max_length - len(ext)] + ext
        return name or "file"

    async def _validate_upload(
        self,
        file: UploadFile,
        *,
        allowed_content_types: Iterable[str],
        max_bytes: int = 2 * 1024 * 1024,
    ) -> bytes:
        """Validate uploaded file MIME type and size, then return its bytes.

        NOTE: content_type comes from the client's multipart header and is not
        independently verified. For stronger guarantees, callers should add magic-byte
        validation on the returned bytes for their specific allowed types.
        """
        content_type = (file.content_type or "").lower()
        if content_type not in {c.lower() for c in allowed_content_types}:
            raise ValidationError("Unsupported file type")

        data = await file.read()
        max_mb = max_bytes / (1024 * 1024)
        if len(data) > max_bytes:
            raise ValidationError(f"File too large (max {max_mb:.0f}MB)" if max_mb >= 1 else f"File too large (max {max_bytes} bytes)")
        return data

    async def _upload_driver_file(
        self,
        *,
        driver_id: str,
        upload: UploadFile,
        prefix: str,
        allowed_content_types: Iterable[str],
        max_bytes: int = 2 * 1024 * 1024,
    ) -> tuple[str, str, int]:
        """Validate and upload a driver-related file to R2.

        Returns (file_key, content_type, size_bytes).

        The boto3 put_object call is dispatched to a thread pool executor so
        that the async event loop is not blocked while bytes transfer to R2.
        """
        data = await self._validate_upload(upload, allowed_content_types=allowed_content_types, max_bytes=max_bytes)

        bucket = get_r2_bucket_name()
        client = get_default_r2_client()
        content_type = upload.content_type or "application/octet-stream"

        # URL-safe timestamp — no colons, no timezone offset, no URL-encoding needed.
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        safe_name = self._sanitize_filename(upload.filename or "file")
        file_key = f"drivers/{driver_id}/{prefix}/{ts}_{safe_name}"

        # Run the synchronous boto3 call in a thread pool so we don't block the
        # event loop for the duration of the network transfer to R2.
        await asyncio.get_running_loop().run_in_executor(
            None,
            functools.partial(
                client.put_object,
                Bucket=bucket,
                Key=file_key,
                Body=data,
                ContentType=content_type,
                ContentLength=len(data),
            ),
        )

        logger.info(
            "driver_file_uploaded",
            driver_id=driver_id,
            key=file_key,
            prefix=prefix,
            size=len(data),
            content_type=content_type,
        )

        return file_key, content_type, len(data)

    def get_profile_photo_url(self, profile_photo_key: str | None, *, expiry_seconds: int = 3600) -> str | None:
        """Generate a signed CDN URL for a driver profile photo (local HMAC — no network call).

        Returns None if profile_photo_key is None or signing is not configured.
        """
        if not profile_photo_key:
            return None
        try:
            client = get_images_client()
            return client.generate_signed_url(profile_photo_key, expiry_seconds=expiry_seconds)
        except Exception as exc:
            logger.warning(
                "failed_to_generate_profile_photo_url",
                profile_photo_key=profile_photo_key,
                error=str(exc),
            )
            return None

    def get_file_url(self, file_key: str | None, *, expiry_seconds: int = 3600, content_type: str | None = None) -> str | None:
        """Generate a presigned URL for an R2 file (document, proof, etc.).

        Returns None if file_key is None, otherwise returns a presigned URL
        that can be used to access the private R2 object.
        """
        if not file_key:
            return None
        try:
            return generate_presigned_url(file_key, expiry_seconds=expiry_seconds, content_type=content_type)
        except Exception as exc:
            logger.warning(
                "failed_to_generate_file_url",
                file_key=file_key,
                error=str(exc),
            )
            return None

    @staticmethod
    def _serialize_audit_dict(d: dict | None) -> dict | None:
        """Convert any datetime/date values to ISO strings for JSON serialisation."""
        if d is None:
            return None

        def _serialize_value(v: object) -> object:
            if isinstance(v, (datetime, date)):
                return v.isoformat()
            if isinstance(v, Enum):
                return v.value
            if isinstance(v, Decimal):
                return str(v)
            return v

        return {k: _serialize_value(v) for k, v in d.items()}

    async def _log_audit(
        self,
        action: str,
        entity_id: str | None = None,
        user_id: str | None = None,
        user_role: str | None = None,
        old_value: dict | None = None,
        new_value: dict | None = None,
        reason: str | None = None,
        organization_id: str | None = None,
        severity: str = "INFO",
        category: AuditCategory = AuditCategory.ACCOUNT,
        event_type: AuditEventType | str = AuditEventType.ACCOUNT_UPDATED,
    ) -> None:
        """Helper to log driver-related actions to audit."""
        if self._audit is None:
            return
        await self._audit.log(
            action=action,
            entity_type="driver",
            entity_id=entity_id,
            user_id=user_id,
            user_role=user_role,
            old_value=self._serialize_audit_dict(old_value),
            new_value=self._serialize_audit_dict(new_value),
            reason=reason,
            organization_id=organization_id,
            ip_address=self._ip,
            user_agent=self._user_agent,
            severity=severity,
            category=category,
            event_type=event_type,
        )

    async def list_drivers(
        self,
        *,
        page: int = 1,
        size: int = 20,
        order_by: str | None = "created_at",
        order_desc: bool = True,
        account_status: list[str] | None = None,
        live_status: list[str] | None = None,
        depot_id: str | None = None,
        search: str | None = None,
    ) -> tuple[list[Driver], int]:
        """Paginated list of drivers with optional filters and search. Returns (items, total)."""
        return await self._driver_repo.search_and_filter(
            page=page,
            size=size,
            search=search,
            account_status=account_status,
            exclude_drafts=True,
            live_status=live_status,
            depot_id=depot_id,
            order_by=order_by or "created_at",
            order_desc=order_desc,
        )

    async def list_driver_drafts(
        self,
        *,
        page: int = 1,
        size: int = 20,
        order_by: str | None = "created_at",
        order_desc: bool = True,
        depot_id: str | None = None,
        search: str | None = None,
    ) -> tuple[list[Driver], int]:
        """Paginated list of draft drivers with search across draft_id + draft JSONB identity."""
        return await self._driver_repo.search_and_filter_drafts(
            page=page,
            size=size,
            search=search,
            depot_id=depot_id,
            order_by=order_by or "created_at",
            order_desc=order_desc,
        )

    async def get_driver(self, driver_id: str) -> Driver:
        """Get a driver by id with user loaded. Raises NotFoundError if not found."""
        driver = await self._driver_repo.get_by_id_with_user(driver_id)
        if driver is None:
            raise NotFoundError(resource="driver", id=driver_id)
        return driver

    # ── Draft pivot helpers ───────────────────────────────────────────────

    async def get_driver_draft_row(self, driver_id: str):
        return await self._draft_repo.find_one(driver_id=driver_id)

    async def get_driver_draft_row_by_draft_id(self, draft_id: str):
        return await self._draft_repo.find_one(draft_id=draft_id)

    async def merge_driver_draft_data(
        self,
        *,
        driver_id: str,
        incoming: dict[str, object],
        created_by: str | None = None,
    ) -> dict[str, object]:
        """Merge `incoming` into driver_drafts.draft_data.

        - Existing keys are preserved unless overridden by `incoming`.
        - This is used to store draft UI state (identity + form fields) while
          drivers have no linked user_id yet.
        """
        row = await self.get_driver_draft_row(driver_id)
        if row is None:
            row = await self.ensure_driver_draft_row(driver_id=driver_id, created_by=created_by)

        existing = dict(getattr(row, "draft_data", None) or {})
        merged: dict[str, object] = {**existing, **incoming}
        await self._draft_repo.update_by_id(row.id, {"draft_data": merged})
        return merged

    async def ensure_driver_draft_row(
        self,
        *,
        driver_id: str,
        created_by: str | None,
    ):
        row = await self._draft_repo.find_one(driver_id=driver_id)
        if row is not None:
            return row
        return await self._draft_repo.create({"driver_id": driver_id, "created_by": created_by, "is_submitted": False})

    # ── Driver creation with initial documents ─────────────────────────────

    async def create_driver(
        self,
        user_id: str,
        *,
        capacities: list[str],
        driver_type: str,
        address_line1: str,
        address_line2: str | None,
        country: str | None,
        state: str | None,
        city: str,
        postcode: str,
        latitude: float | None = None,
        longitude: float | None = None,
        depot_id: str | None = None,
        vehicle_id: str | None = None,
        license_number: str | None = None,
        license_category: str | None = None,
        max_stops: int = 30,
        territory_tags: list[str] | None = None,
        account_status: str = DriverAccountStatus.DRAFT,
        live_status: str = DriverLiveStatus.OFFLINE,
        notes: str | None = None,
        okay_with_layover: bool = False,
        layover_cost_per_night: Decimal | float | str | None = None,
        max_layover_nights: int | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Driver:
        """Create a new driver linked to an existing user. One driver per user."""
        existing = await self._driver_repo.find_by_user_id(user_id)
        if existing is not None:
            raise ConflictError("A driver profile already exists for this user.")
        user = await self._user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError(resource="user", id=user_id)

        normalized_capacities = self._normalize_capacities(capacities=capacities)
        driver_code = await self._next_driver_code()
        lc_raw = layover_cost_per_night if layover_cost_per_night is not None else Decimal("0")
        lc = lc_raw if isinstance(lc_raw, Decimal) else Decimal(str(lc_raw))
        mn = int(max_layover_nights) if max_layover_nights is not None else 0
        data = {
            "driver_code": driver_code,
            "user_id": user_id,
            "capacities": normalized_capacities,
            "driver_type": driver_type,
            "address_line1": address_line1,
            "address_line2": address_line2,
            "country": country,
            "state": state,
            "city": city,
            "postcode": postcode,
            "depot_id": depot_id,
            "vehicle_id": vehicle_id,
            "license_number": license_number,
            "license_category": license_category,
            "max_stops": max_stops,
            "territory_tags": territory_tags,
            "account_status": account_status,
            "live_status": live_status,
            "notes": notes,
            "okay_with_layover": okay_with_layover,
            "layover_cost_per_night": lc,
            "max_layover_nights": mn,
        }
        driver = await self._driver_repo.create(data)
        await self._log_audit(
            "driver.create",
            entity_id=driver.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={"driver_id": driver.id, "user_id": user_id, "driver_code": driver.driver_code},
            severity="NOTICE",
            category=AuditCategory.CONTACT,
            event_type=AuditEventType.CONTACT_CREATED,
        )
        return driver

    async def create_driver_draft(
        self,
        *,
        capacities: list[str] | None = None,
        driver_type: str | None = None,
        address_line1: str | None = None,
        address_line2: str | None = None,
        country: str | None = None,
        state: str | None = None,
        city: str | None = None,
        postcode: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        depot_id: str | None = None,
        vehicle_id: str | None = None,
        license_number: str | None = None,
        license_category: str | None = None,
        max_stops: int | None = None,
        territory_tags: list[str] | None = None,
        notes: str | None = None,
        created_by: str | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Driver:
        """Create a DRAFT driver with partial/nullable profile fields."""
        driver_code = await self._next_driver_code()

        # Explicitly set draftable fields to None when omitted so DB defaults
        # (e.g. capacities=['VAN']) don't mask missing data in drafts.
        data: dict[str, object] = {
            "driver_code": driver_code,
            "user_id": None,
            "account_status": DriverAccountStatus.DRAFT,
            "live_status": DriverLiveStatus.OFFLINE,
            "capacities": None,
            "driver_type": None,
            "address_line1": None,
            "address_line2": None,
            "country": None,
            "state": None,
            "city": None,
            "postcode": None,
            "depot_id": None,
            "vehicle_id": None,
            "license_number": None,
            "license_category": None,
            "max_stops": None,
            "territory_tags": None,
            "notes": None,
            # Operational columns are NOT NULL — neutral defaults until draft submit / add-new-driver.
            "okay_with_layover": False,
            "layover_cost_per_night": Decimal("0"),
            "max_layover_nights": 0,
        }

        if capacities is not None:
            data["capacities"] = list(dict.fromkeys(str(item) for item in capacities if item))
        if driver_type is not None:
            data["driver_type"] = driver_type
        if address_line1 is not None:
            data["address_line1"] = address_line1
        if address_line2 is not None:
            data["address_line2"] = address_line2
        if country is not None:
            data["country"] = country
        if state is not None:
            data["state"] = state
        if city is not None:
            data["city"] = city
        if postcode is not None:
            data["postcode"] = postcode
        if latitude is not None:
            data["latitude"] = latitude
        if longitude is not None:
            data["longitude"] = longitude
        if depot_id is not None:
            data["depot_id"] = depot_id
        if vehicle_id is not None:
            data["vehicle_id"] = vehicle_id
        if license_number is not None:
            data["license_number"] = license_number
        if license_category is not None:
            data["license_category"] = license_category
        if max_stops is not None:
            data["max_stops"] = max_stops
        if territory_tags is not None:
            data["territory_tags"] = territory_tags
        if notes is not None:
            data["notes"] = notes

        async with self._driver_repo.session.begin_nested():
            driver = await self._driver_repo.create(data)
            await self.ensure_driver_draft_row(driver_id=driver.id, created_by=created_by)

        await self._log_audit(
            "driver.draft.create",
            entity_id=driver.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={"driver_id": driver.id, "user_id": None, "driver_code": driver.driver_code},
            severity="NOTICE",
            category=AuditCategory.CONTACT,
            event_type=AuditEventType.CONTACT_CREATED,
        )
        return driver

    async def submit_driver_draft(
        self,
        *,
        driver_id: str,
        expected_version: int | None,
        user_id: str,
        capacities: list[str],
        driver_type: str,
        address_line1: str,
        address_line2: str | None,
        country: str | None,
        state: str,
        city: str,
        postcode: str,
        depot_id: str | None,
        vehicle_id: str | None,
        license_number: str | None,
        license_category: str | None,
        max_stops: int,
        okay_with_layover: bool,
        layover_cost_per_night: Decimal,
        max_layover_nights: int,
        notes: str | None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Driver:
        """Finalize a DRAFT driver by linking/activating driver atomically."""
        driver = await self.get_driver(driver_id)
        if driver.account_status != DriverAccountStatus.DRAFT:
            raise ValidationError("Only DRAFT drivers can be submitted")

        draft_row = await self.get_driver_draft_row(driver_id)
        if draft_row is None:
            raise NotFoundError(resource="driver_draft", id=driver_id)
        if getattr(draft_row, "is_submitted", False):
            # Idempotent replay: return current driver state.
            return driver

        async with self._driver_repo.session.begin_nested():
            lc = (
                layover_cost_per_night
                if isinstance(layover_cost_per_night, Decimal)
                else Decimal(str(layover_cost_per_night))
            )
            await self._driver_repo.update_by_id(
                driver_id,
                {
                    "user_id": user_id,
                    "capacities": list(dict.fromkeys(str(item) for item in capacities if item)),
                    "driver_type": driver_type,
                    "address_line1": address_line1,
                    "address_line2": address_line2,
                    "country": country,
                    "state": state,
                    "city": city,
                    "postcode": postcode,
                    "depot_id": depot_id,
                    "vehicle_id": vehicle_id,
                    "license_number": license_number,
                    "license_category": license_category,
                    "max_stops": max_stops,
                    "okay_with_layover": okay_with_layover,
                    "layover_cost_per_night": lc,
                    "max_layover_nights": max_layover_nights,
                    "notes": notes,
                    "account_status": DriverAccountStatus.PENDING_ACTIVATION,
                    "live_status": DriverLiveStatus.OFFLINE,
                },
                expected_version=expected_version,
            )

            await self.merge_driver_draft_data(
                driver_id=driver_id,
                incoming={
                    "okay_with_layover": okay_with_layover,
                    "layover_cost_per_night": str(lc),
                    "max_layover_nights": max_layover_nights,
                },
                created_by=audit_user_id,
            )

            if driver.profile_photo_key:
                await self._user_repo.update_by_id(user_id, {"avatar_url": driver.profile_photo_key})

            await self._draft_repo.update_by_id(
                draft_row.id,
                {"is_submitted": True},
            )

        await self._log_audit(
            "driver.draft.submit",
            entity_id=driver_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={"driver_id": driver_id, "user_id": user_id, "is_submitted": True},
            severity="NOTICE",
            category=AuditCategory.CONTACT,
            event_type=AuditEventType.CONTACT_UPDATED,
        )
        return await self.get_driver(driver_id)

    async def delete_draft_by_draft_id(
        self,
        *,
        draft_id: str,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> None:
        """Hard delete draft by business id (draft_id)."""
        draft_row = await self.get_driver_draft_row_by_draft_id(draft_id)
        if draft_row is None:
            raise NotFoundError(resource="driver_draft", id=draft_id)

        driver = await self._driver_repo.get_by_id_or_404(draft_row.driver_id)
        if driver.account_status != DriverAccountStatus.DRAFT or getattr(draft_row, "is_submitted", False):
            raise ValidationError("Only non-submitted DRAFT drivers can be deleted")

        # Capture R2/CF file identifiers before deleting DB rows.
        profile_photo_key = driver.profile_photo_key
        docs = await self._document_repo.find_all(page=1, size=500, driver_id=driver.id)
        file_keys: list[str] = [doc.file_key for doc in docs[0]]

        # Best-effort external deletion (DB hard-delete must proceed even if storage deletion fails).
        for key in file_keys:
            try:
                await delete_from_r2(key)
            except Exception as exc:
                logger.warning(
                    "driver_draft.delete.r2_best_effort_failed",
                    driver_id=driver.id,
                    draft_id=draft_id,
                    file_key=key,
                    error=str(exc),
                )

        if profile_photo_key:
            try:
                await delete_image(profile_photo_key)
            except Exception as exc:
                logger.warning(
                    "driver_draft.delete.profile_photo_best_effort_failed",
                    driver_id=driver.id,
                    draft_id=draft_id,
                    profile_photo_key=profile_photo_key,
                    error=str(exc),
                )

        async with self._driver_repo.session.begin_nested():
            for d in docs[0]:
                await self._driver_repo.session.delete(d)
            await self._driver_repo.session.delete(draft_row)
            await self._driver_repo.session.delete(driver)
            await self._driver_repo.session.flush()

        await self._log_audit(
            "driver.draft.delete",
            entity_id=driver.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={"draft_id": draft_id, "driver_id": driver.id},
            new_value=None,
            severity="CRITICAL",
            category=AuditCategory.CONTACT,
            event_type=AuditEventType.CONTACT_DELETED,
        )

    async def create_driver_with_documents(
        self,
        *,
        user_id: str,
        capacity: str | None = None,
        capacities: list[str] | None = None,
        driver_type: str,
        address_line1: str,
        address_line2: str | None,
        country: str | None = None,
        state: str | None = None,
        city: str,
        postcode: str,
        latitude: float | None = None,
        longitude: float | None = None,
        depot_id: str | None = None,
        vehicle_id: str | None = None,
        license_number: str | None = None,
        license_category: str | None = None,
        max_stops: int = 30,
        territory_tags: list[str] | None = None,
        notes: str | None = None,
        documents: list[tuple[bytes, str, str]] | None = None,
        documents_metadata: list[dict[str, object]] | None = None,
        profile_photo: UploadFile | None = None,
        okay_with_layover: bool = False,
        layover_cost_per_night: Decimal | float | str | None = None,
        max_layover_nights: int | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> tuple[Driver, list[tuple[DriverDocumentKind, str, str | None]]]:
        """Create driver with optional driving licence upload and optional profile photo.

        Onboarding accepts only DRIVING_LICENCE; custom documents use create_driver_document.
        Returns the created driver plus per-document results for the licence upload.
        """
        async with self._driver_repo.session.begin_nested():
            account_status = DriverAccountStatus.PENDING_ACTIVATION

            normalized_capacities = self._normalize_capacities(capacity=capacity, capacities=capacities)
            driver = await self.create_driver(
                user_id=user_id,
                capacities=normalized_capacities,
                driver_type=driver_type,
                address_line1=address_line1,
                address_line2=address_line2,
                country=country,
                state=state,
                city=city,
                postcode=postcode,
                latitude=latitude,
                longitude=longitude,
                depot_id=depot_id,
                vehicle_id=vehicle_id,
                license_number=license_number,
                license_category=license_category,
                max_stops=max_stops,
                territory_tags=territory_tags,
                account_status=account_status,
                live_status=DriverLiveStatus.OFFLINE,
                notes=notes,
                okay_with_layover=okay_with_layover,
                layover_cost_per_night=layover_cost_per_night,
                max_layover_nights=max_layover_nights,
                audit_user_id=audit_user_id,
                audit_user_role=audit_user_role,
            )

        # Process initial documents outside the main driver transaction so that
        # failures here do not roll back driver creation.
        results: list[tuple[DriverDocumentKind, str, str | None]] = []

        # Onboarding documents are optional; when provided they must be aligned with metadata by index.
        docs = documents or []
        meta_list = documents_metadata or []
        if docs and not meta_list:
            raise ValidationError("documents_metadata is required when documents are provided")
        if len(meta_list) != len(docs):
            raise ValidationError(f"documents_metadata length ({len(meta_list)}) must match documents count ({len(docs)})")

        for idx, (file_content, file_name, content_type) in enumerate(docs):
            meta = meta_list[idx]
            try:
                kind = DriverDocumentKind(str(meta.get("document_type", "")))
            except Exception:
                results.append((DriverDocumentKind.DRIVING_LICENCE, "failed", f"Invalid document_type at index {idx}"))
                continue

            if kind is not DriverDocumentKind.DRIVING_LICENCE:
                results.append(
                    (
                        kind,
                        "failed",
                        "Only DRIVING_LICENCE is allowed when creating a driver; use the documents API for other types.",
                    )
                )
                continue

            expiry_raw = meta.get("expiry_date")
            expiry: date | None = None
            if isinstance(expiry_raw, str) and expiry_raw:
                try:
                    expiry = date.fromisoformat(expiry_raw)
                except ValueError:
                    results.append((kind, "failed", f"Invalid expiry_date format at index {idx}; expected YYYY-MM-DD"))
                    continue
            elif isinstance(expiry_raw, date):
                expiry = expiry_raw

            title = DriverDocumentKind.DRIVING_LICENCE.to_display_title()

            # Driving licence is mandatory at onboarding; require expiry always.
            if expiry is None:
                results.append((kind, "failed", "expiry_date is required for DRIVING_LICENCE"))
                continue

            try:
                ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                safe_name = self._sanitize_filename(file_name or "document")
                file_key = f"drivers/{driver.id}/compliance/{ts}_{safe_name}"
                bucket = get_r2_bucket_name()
                client = get_default_r2_client()
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    functools.partial(
                        client.put_object,
                        Bucket=bucket,
                        Key=file_key,
                        Body=file_content,
                        ContentType=content_type or "application/octet-stream",
                        ContentLength=len(file_content),
                    ),
                )

                doc = DriverDocument(
                    driver_id=driver.id,
                    kind=kind.value,
                    title=title,
                    file_key=file_key,
                    expiry_date=expiry,
                    content_type=content_type or "application/octet-stream",
                    size_bytes=len(file_content),
                    is_initial=True,
                )
                self._driver_repo.session.add(doc)
                await self._driver_repo.session.flush()
                results.append((kind, "success", None))
            except Exception:
                logger.exception("Failed to create initial driver document", extra={"driver_id": driver.id, "kind": kind.value})
                results.append((kind, "failed", "Document could not be uploaded. Please try again later."))

        if profile_photo is not None:
            driver = await self.update_profile_photo(
                driver.id,
                profile_photo,
                audit_user_id=audit_user_id,
                audit_user_role=audit_user_role,
            )

        await self._log_audit(
            "driver.onboard",
            entity_id=driver.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={
                "driver_id": driver.id,
                "user_id": user_id,
                "driver_code": driver.driver_code,
                "account_status": account_status,
            },
            severity="NOTICE",
            category=AuditCategory.CONTACT,
            event_type=AuditEventType.CONTACT_CREATED,
        )

        return driver, results

    async def update_driver(
        self,
        driver_id: str,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        capacity: str | None = None,
        capacities: list[str] | None = None,
        driver_type: str | None = None,
        address_line1: str | None = None,
        address_line2: str | None = None,
        country: str | None = None,
        state: str | None = None,
        city: str | None = None,
        postcode: str | None = None,
        depot_id: str | None = None,
        vehicle_id: str | None = None,
        license_number: str | None = None,
        license_category: str | None = None,
        max_stops: int | None = None,
        territory_tags: list[str] | None = None,
        account_status: str | None = None,
        live_status: str | None = None,
        notes: str | None = None,
        okay_with_layover: bool | None = None,
        layover_cost_per_night: Decimal | float | None = None,
        max_layover_nights: int | None = None,
        expected_version: int | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Driver:
        """Update driver by id. Supports optimistic locking via expected_version."""
        driver = await self._driver_repo.get_by_id_with_user(driver_id)
        if driver is None:
            raise NotFoundError(resource="driver", id=driver_id)
        user = driver.user
        old_value = {
            # Draft drivers may not have a linked user yet.
            "first_name": user.first_name if user is not None else None,
            "last_name": user.last_name if user is not None else None,
            "phone": user.phone if user is not None else None,
            "email": user.email if user is not None else None,
            "capacities": driver.capacities,
            "driver_type": driver.driver_type,
            "address_line1": driver.address_line1,
            "address_line2": driver.address_line2,
            "country": driver.country,
            "state": driver.state,
            "city": driver.city,
            "postcode": driver.postcode,
            "depot_id": driver.depot_id,
            "vehicle_id": driver.vehicle_id,
            "license_number": driver.license_number,
            "license_category": driver.license_category,
            "max_stops": driver.max_stops,
            "territory_tags": driver.territory_tags,
            "account_status": driver.account_status,
            "live_status": driver.live_status,
            "notes": driver.notes,
            "okay_with_layover": getattr(driver, "okay_with_layover", False),
            "layover_cost_per_night": getattr(driver, "layover_cost_per_night", None),
            "max_layover_nights": getattr(driver, "max_layover_nights", None),
        }
        user_data: dict[str, object] = {}
        data: dict[str, object] = {}
        if first_name is not None:
            user_data["first_name"] = first_name
        if last_name is not None:
            user_data["last_name"] = last_name
        if phone is not None:
            user_data["phone"] = phone
        if email is not None:
            user_data["email"] = email
        if capacity is not None or capacities is not None:
            normalized_capacities = self._normalize_capacities(capacity=capacity, capacities=capacities)
            data["capacities"] = normalized_capacities
        if driver_type is not None:
            data["driver_type"] = driver_type
        if address_line1 is not None:
            data["address_line1"] = address_line1
        if address_line2 is not None:
            data["address_line2"] = address_line2
        if country is not None:
            data["country"] = country
        if state is not None:
            data["state"] = state
        if city is not None:
            data["city"] = city
        if postcode is not None:
            data["postcode"] = postcode
        if depot_id is not None:
            data["depot_id"] = depot_id
        if vehicle_id is not None:
            data["vehicle_id"] = vehicle_id
        if license_number is not None:
            data["license_number"] = license_number
        if license_category is not None:
            data["license_category"] = license_category
        if max_stops is not None:
            data["max_stops"] = max_stops
        if territory_tags is not None:
            data["territory_tags"] = territory_tags
        if account_status is not None:
            data["account_status"] = account_status
        if live_status is not None:
            data["live_status"] = live_status
        if notes is not None:
            data["notes"] = notes
        if okay_with_layover is not None:
            data["okay_with_layover"] = okay_with_layover
        if layover_cost_per_night is not None:
            lc = layover_cost_per_night if isinstance(layover_cost_per_night, Decimal) else Decimal(str(layover_cost_per_night))
            data["layover_cost_per_night"] = lc
        if max_layover_nights is not None:
            data["max_layover_nights"] = max_layover_nights
        if data.get("okay_with_layover") is False:
            # Layover-specific values must be zeroed when layovers are disabled.
            data["layover_cost_per_night"] = Decimal("0")
            data["max_layover_nights"] = 0
        if not data and not user_data:
            return driver

        # Keep driver/user profile writes in one transactional block so a failure
        # in either write path rolls back the whole operation.
        async with self._driver_repo.session.begin_nested():
            if data:
                updated = await self._driver_repo.update_by_id(
                    driver_id,
                    data,
                    expected_version=expected_version,
                )
            else:
                # If caller provided optimistic lock version for identity-only updates,
                # still enforce a version check + bump to protect against stale writes.
                if expected_version is not None:
                    updated = await self._driver_repo.update_by_id(
                        driver_id,
                        {},
                        expected_version=expected_version,
                    )
                else:
                    updated = driver

            if user_data and driver.user_id:
                await self._user_repo.update_by_id(driver.user_id, user_data)
            elif user_data and not driver.user_id:
                raise ValidationError("Identity fields can only be updated after submit creates the linked user")
        audit_new_value = dict(data)
        audit_new_value.update(user_data)
        await self._log_audit(
            "driver.update",
            entity_id=driver_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value=old_value,
            new_value=audit_new_value,
            severity="NOTICE",
            category=AuditCategory.CONTACT,
            event_type=AuditEventType.CONTACT_UPDATED,
        )
        return updated

    async def delete_driver(
        self,
        driver_id: str,
        *,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Driver:
        """Soft-delete driver (set account_status to INACTIVE)."""
        driver = await self._driver_repo.get_by_id_or_404(driver_id)
        updated = await self._driver_repo.soft_delete(
            driver_id,
            status_field="account_status",
            target_status=DriverAccountStatus.INACTIVE,
        )
        await self._log_audit(
            "driver.delete",
            entity_id=driver_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={"account_status": driver.account_status},
            new_value={"account_status": DriverAccountStatus.INACTIVE},
            severity="CRITICAL",
            category=AuditCategory.CONTACT,
            event_type=AuditEventType.CONTACT_UPDATED,
        )
        return updated

    # ── Documents helper methods (status, KPI) ─────────────────────────────

    @staticmethod
    def compute_document_status(expiry_date: date | None, *, today: date | None = None) -> DriverDocumentStatus:
        """Compute VALID / EXPIRING_SOON / EXPIRED based on expiry_date."""
        if expiry_date is None:
            return DriverDocumentStatus.VALID
        today = today or datetime.now(UTC).date()
        if expiry_date < today:
            return DriverDocumentStatus.EXPIRED
        if expiry_date <= today + timedelta(days=30):
            return DriverDocumentStatus.EXPIRING_SOON
        return DriverDocumentStatus.VALID

    # ── Driver documents CRUD ───────────────────────────────────────────────

    async def list_driver_documents(self, driver_id: str) -> list[DriverDocument]:
        """List all documents for a driver. Raises if driver does not exist."""
        await self._driver_repo.get_by_id_or_404(driver_id)
        stmt = select(DriverDocument).where(DriverDocument.driver_id == driver_id)
        result = await self._document_repo.session.execute(stmt)
        return list(result.scalars().all())

    async def get_driver_document(self, document_id: str) -> DriverDocument:
        """Get a single driver document by ID or raise NotFoundError."""
        return await self._document_repo.get_by_id_or_404(document_id)

    async def create_driver_document(
        self,
        *,
        driver_id: str,
        kind: str,
        title: str | None,
        expiry_date: date | None,
        upload: UploadFile,
        is_initial: bool = False,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> DriverDocument:
        await self._driver_repo.get_by_id_or_404(driver_id)
        allowed = {
            "image/jpeg",
            "image/png",
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        file_key, content_type, size_bytes = await self._upload_driver_file(
            driver_id=driver_id,
            upload=upload,
            prefix="documents",
            allowed_content_types=allowed,
        )
        # Store canonical title: display form for non-CUSTOM, custom title for CUSTOM
        kind_enum = DriverDocumentKind(kind)
        stored_title = title if kind_enum is DriverDocumentKind.CUSTOM else kind_enum.to_display_title()
        doc = await self._document_repo.create(
            {
                "driver_id": driver_id,
                "kind": kind,
                "title": stored_title,
                "file_key": file_key,
                "expiry_date": expiry_date,
                "content_type": content_type,
                "size_bytes": size_bytes,
                "is_initial": is_initial,
            }
        )
        await self._log_audit(
            "driver.document.create",
            entity_id=doc.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={"driver_id": driver_id, "kind": kind, "title": stored_title},
            severity="NOTICE",
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.DOCUMENT_UPLOADED,
        )
        return doc

    async def update_driver_document(
        self,
        *,
        document_id: str,
        title: str | None = None,
        expiry_date: date | None = None,
        upload: UploadFile | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> DriverDocument:
        doc = await self._document_repo.get_by_id_or_404(document_id)
        data: dict[str, object] = {}

        if upload is not None:
            allowed = {
                "image/jpeg",
                "image/png",
                "application/pdf",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
            file_key, content_type, size_bytes = await self._upload_driver_file(
                driver_id=doc.driver_id,
                upload=upload,
                prefix="documents",
                allowed_content_types=allowed,
            )
            data["file_key"] = file_key
            data["content_type"] = content_type
            data["size_bytes"] = size_bytes

        if title is not None:
            kind_enum = DriverDocumentKind(doc.kind)
            data["title"] = title if kind_enum is DriverDocumentKind.CUSTOM else kind_enum.to_display_title()
        if expiry_date is not None:
            data["expiry_date"] = expiry_date
        if not data:
            return doc

        updated = await self._document_repo.update_by_id(document_id, data)
        await self._log_audit(
            "driver.document.update",
            entity_id=document_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={
                "title": doc.title,
                "expiry_date": doc.expiry_date.isoformat() if doc.expiry_date else None,
            },
            new_value=self._serialize_audit_dict(data),
            severity="NOTICE",
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.DOCUMENT_UPLOADED,
        )
        return updated

    async def delete_driver_document(
        self,
        *,
        document_id: str,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> None:
        doc = await self._document_repo.get_by_id_or_404(document_id)
        await self._document_repo.hard_delete(document_id)
        await self._log_audit(
            "driver.document.delete",
            entity_id=document_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={"driver_id": doc.driver_id, "kind": doc.kind},
            new_value=None,
            severity="NOTICE",
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.DOCUMENT_DELETED,
        )

    # ── Time off CRUD + KPI ─────────────────────────────────────────────────

    async def list_time_off(self, driver_id: str) -> tuple[list[DriverTimeOff], int, int]:
        """Return all time off entries plus KPIs for paid and unpaid leave taken this year."""
        await self._driver_repo.get_by_id_or_404(driver_id)
        stmt = select(DriverTimeOff).where(DriverTimeOff.driver_id == driver_id)
        result = await self._time_off_repo.session.execute(stmt)
        items = list(result.scalars().all())

        current_year = datetime.now(UTC).year
        paid_leave_taken = sum((entry.days or 0) for entry in items if entry.is_paid and entry.start_date.year == current_year)
        unpaid_leave_taken = sum((entry.days or 0) for entry in items if not entry.is_paid and entry.start_date.year == current_year)
        return items, paid_leave_taken, unpaid_leave_taken

    async def get_time_off(self, time_off_id: str) -> DriverTimeOff:
        """Get a single time off entry by ID or raise NotFoundError."""
        return await self._time_off_repo.get_by_id_or_404(time_off_id)

    async def create_time_off(
        self,
        *,
        driver_id: str,
        start_date: date,
        end_date: date,
        type: str,
        notes: str | None = None,
        is_paid: bool = True,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> DriverTimeOff:
        await self._driver_repo.get_by_id_or_404(driver_id)
        if end_date < start_date:
            raise ValidationError("end_date cannot be before start_date")
        days = (end_date - start_date).days + 1

        entry = await self._time_off_repo.create(
            {
                "driver_id": driver_id,
                "start_date": start_date,
                "end_date": end_date,
                "type": type,
                "days": days,
                "notes": notes,
                "is_paid": is_paid,
            }
        )
        await self._log_audit(
            "driver.time_off.create",
            entity_id=entry.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={
                "driver_id": driver_id,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "type": type,
                "days": days,
                "notes": notes,
                "is_paid": is_paid,
            },
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.ACCOUNT_UPDATED,
        )
        return entry

    async def update_time_off(
        self,
        *,
        time_off_id: str,
        start_date: date | None = None,
        end_date: date | None = None,
        type: str | None = None,
        notes: str | None = None,
        is_paid: bool | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> DriverTimeOff:
        entry = await self._time_off_repo.get_by_id_or_404(time_off_id)
        new_start = start_date or entry.start_date
        new_end = end_date or entry.end_date
        if new_end < new_start:
            raise ValidationError("end_date cannot be before start_date")
        data: dict[str, object] = {}
        if start_date is not None:
            data["start_date"] = start_date
        if end_date is not None:
            data["end_date"] = end_date
        if type is not None:
            data["type"] = type
        if notes is not None:
            data["notes"] = notes
        if is_paid is not None:
            data["is_paid"] = is_paid
        if start_date is not None or end_date is not None:
            new_days = (new_end - new_start).days + 1
            data["days"] = new_days
        else:
            new_days = entry.days or ((entry.end_date - entry.start_date).days + 1)

        if not data:
            return entry

        updated = await self._time_off_repo.update_by_id(time_off_id, data)
        old_value: dict[str, object] = {
            "start_date": entry.start_date.isoformat(),
            "end_date": entry.end_date.isoformat(),
            "type": entry.type,
            "days": entry.days,
            "notes": entry.notes,
            "is_paid": entry.is_paid,
        }
        await self._log_audit(
            "driver.time_off.update",
            entity_id=time_off_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value=old_value,
            new_value=data,
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.ACCOUNT_UPDATED,
        )
        return updated

    async def delete_time_off(
        self,
        *,
        time_off_id: str,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> None:
        entry = await self._time_off_repo.get_by_id_or_404(time_off_id)
        await self._time_off_repo.hard_delete(time_off_id)
        await self._log_audit(
            "driver.time_off.delete",
            entity_id=time_off_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={
                "driver_id": entry.driver_id,
                "start_date": entry.start_date.isoformat(),
                "end_date": entry.end_date.isoformat(),
                "type": entry.type,
            },
            new_value=None,
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.ACCOUNT_UPDATED,
        )

    # ── Weekly work schedule ────────────────────────────────────────────────

    async def get_weekly_schedule(self, driver_id: str) -> tuple[list[DriverWeeklySchedule], float]:
        """Return full Mon–Sun weekly schedule and total hours."""
        await self._driver_repo.get_by_id_or_404(driver_id)
        stmt = select(DriverWeeklySchedule).where(DriverWeeklySchedule.driver_id == driver_id)
        result = await self._weekly_repo.session.execute(stmt)
        rows = list(result.scalars().all())
        by_day: dict[int, DriverWeeklySchedule] = {row.day_of_week: row for row in rows}

        total_hours = 0.0
        for row in rows:
            if not row.is_active or row.start_time is None or row.end_time is None:
                continue
            delta = datetime.combine(date.today(), row.end_time) - datetime.combine(date.today(), row.start_time)
            total_hours += max(delta.total_seconds(), 0) / 3600.0

        # Ensure 0-6 entries exist (inactive by default)
        complete: list[DriverWeeklySchedule] = []
        for dow in range(7):
            existing = by_day.get(dow)
            if existing:
                complete.append(existing)
            else:
                placeholder = DriverWeeklySchedule(
                    driver_id=driver_id,
                    day_of_week=dow,
                    is_active=False,
                    start_time=None,
                    end_time=None,
                )
                complete.append(placeholder)
        complete.sort(key=lambda r: r.day_of_week)
        return complete, total_hours

    async def update_schedule_day(
        self,
        *,
        driver_id: str,
        day_of_week: int,
        is_active: bool,
        start_time: time | None,
        end_time: time | None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> DriverWeeklySchedule:
        if day_of_week < 0 or day_of_week > 6:
            raise ValidationError("day_of_week must be between 0 (Monday) and 6 (Sunday)")
        await self._driver_repo.get_by_id_or_404(driver_id)
        if is_active:
            if start_time is None or end_time is None:
                raise ValidationError("start_time and end_time are required when is_active is true")
            if end_time <= start_time:
                raise ValidationError("end_time must be after start_time")

        # Persist change
        existing = await self._weekly_repo.find_one(driver_id=driver_id, day_of_week=day_of_week)
        old_value = None
        if existing:
            old_value = {
                "is_active": existing.is_active,
                "start_time": existing.start_time.isoformat() if existing.start_time else None,
                "end_time": existing.end_time.isoformat() if existing.end_time else None,
            }
            updated = await self._weekly_repo.update_by_id(
                existing.id,
                {
                    "is_active": is_active,
                    "start_time": start_time,
                    "end_time": end_time,
                },
            )
            await self._log_audit(
                "driver.schedule.update_day",
                entity_id=updated.id,
                user_id=audit_user_id,
                user_role=audit_user_role,
                old_value=old_value,
                new_value={
                    "is_active": is_active,
                    "start_time": start_time.isoformat() if start_time else None,
                    "end_time": end_time.isoformat() if end_time else None,
                },
                severity="NOTICE",
                category=AuditCategory.FLEET,
                event_type=AuditEventType.SHIFT_UPDATED,
            )
            return updated

        created = await self._weekly_repo.create(
            {
                "driver_id": driver_id,
                "day_of_week": day_of_week,
                "is_active": is_active,
                "start_time": start_time,
                "end_time": end_time,
            }
        )
        await self._log_audit(
            "driver.schedule.create_day",
            entity_id=created.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={
                "day_of_week": day_of_week,
                "is_active": is_active,
                "start_time": start_time.isoformat() if start_time else None,
                "end_time": end_time.isoformat() if end_time else None,
            },
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.SHIFT_UPDATED,
        )
        return created

    async def bulk_update_weekly_schedule(
        self,
        *,
        driver_id: str,
        days: list[tuple[int, bool, time | None, time | None]],
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> None:
        """Bulk update the entire weekly schedule in a single transaction."""
        await self._driver_repo.get_by_id_or_404(driver_id)
        for dow, is_active, start_time, end_time in days:
            if dow < 0 or dow > 6:
                raise ValidationError("day_of_week must be between 0 and 6")
            if is_active:
                if start_time is None or end_time is None:
                    raise ValidationError("start_time and end_time are required when is_active is true")
                if end_time <= start_time:
                    raise ValidationError("end_time must be after start_time")

        async with self._weekly_repo.session.begin_nested():
            for dow, is_active, start_time, end_time in days:
                await self.update_schedule_day(
                    driver_id=driver_id,
                    day_of_week=dow,
                    is_active=is_active,
                    start_time=start_time,
                    end_time=end_time,
                    audit_user_id=audit_user_id,
                    audit_user_role=audit_user_role,
                )

    async def get_schedule_availability_calendar(
        self,
        *,
        driver_id: str,
        from_date: date,
        to_date: date,
        event_source: list[str] | None = None,
        shift_status: list[str] | None = None,
        time_off_type: list[str] | None = None,
        route_type: list[str] | None = None,
        route_status: list[str] | None = None,
    ) -> dict:
        """Aggregate shifts/time off/holidays/routes into calendar events for a driver."""
        if to_date < from_date:
            raise ValidationError("from_date cannot be after to_date")
        if (to_date - from_date).days > 120:
            raise ValidationError("Date range cannot exceed 120 days")

        driver = await self._driver_repo.get_by_id_or_404(driver_id)
        sess = self._driver_repo.session  # type: ignore[attr-defined]

        selected_sources = {s.strip().upper() for s in (event_source or []) if s and s.strip()}
        include_all_sources = not selected_sources

        include_shift = include_all_sources or CalendarEventSource.SHIFT.value in selected_sources
        include_time_off = include_all_sources or CalendarEventSource.TIME_OFF.value in selected_sources
        include_holiday = include_all_sources or CalendarEventSource.HOLIDAY.value in selected_sources
        include_route = include_all_sources or CalendarEventSource.ROUTE.value in selected_sources

        events: list[dict] = []
        summary = {
            "shifts_count": 0,
            "time_off_count": 0,
            "holidays_count": 0,
            "routes_count": 0,
        }

        if include_shift:
            shift_stmt = select(DriverShift).where(
                DriverShift.driver_id == driver_id,
                DriverShift.shift_date >= from_date,
                DriverShift.shift_date <= to_date,
            )
            if shift_status:
                normalized_shift_status = [s.strip().upper() for s in shift_status if s and s.strip()]
                if normalized_shift_status:
                    shift_stmt = shift_stmt.where(DriverShift.status.in_(normalized_shift_status))
            shifts = list((await sess.execute(shift_stmt.order_by(DriverShift.shift_date.asc(), DriverShift.start_time.asc()))).scalars().all())
            for shift in shifts:
                summary["shifts_count"] += 1
                events.append(
                    {
                        "id": shift.id,
                        "source": CalendarEventSource.SHIFT.value,
                        "title": f"{shift.start_time.strftime('%H:%M')} - {shift.end_time.strftime('%H:%M')}",
                        "start_at": shift.start_time,
                        "end_at": shift.end_time,
                        "is_all_day": False,
                        "status": shift.status,
                        "shift_status": shift.status,
                    }
                )

        if include_time_off:
            time_off_stmt = select(DriverTimeOff).where(
                DriverTimeOff.driver_id == driver_id,
                DriverTimeOff.start_date <= to_date,
                DriverTimeOff.end_date >= from_date,
            )
            if time_off_type:
                normalized_types = [s.strip().upper() for s in time_off_type if s and s.strip()]
                if normalized_types:
                    time_off_stmt = time_off_stmt.where(DriverTimeOff.type.in_(normalized_types))
            leaves = list((await sess.execute(time_off_stmt.order_by(DriverTimeOff.start_date.asc()))).scalars().all())
            for leave in leaves:
                start_dt = datetime.combine(leave.start_date, time.min, tzinfo=UTC)
                end_dt = datetime.combine(leave.end_date, time.max, tzinfo=UTC)
                summary["time_off_count"] += 1
                events.append(
                    {
                        "id": leave.id,
                        "source": CalendarEventSource.TIME_OFF.value,
                        "title": leave.type,
                        "start_at": start_dt,
                        "end_at": end_dt,
                        "is_all_day": True,
                        "time_off_type": leave.type,
                        "is_paid": leave.is_paid,
                    }
                )

        if include_holiday:
            audience_allowed = [HolidayAudience.BOTH.value]
            if driver.driver_type:
                audience_allowed.append(str(driver.driver_type).upper())
            holiday_stmt = (
                select(Holiday)
                .where(
                    Holiday.start_date <= to_date,
                    Holiday.end_date >= from_date,
                    Holiday.audience.in_(audience_allowed),
                )
                .order_by(Holiday.start_date.asc())
            )
            holidays = list((await sess.execute(holiday_stmt)).scalars().all())
            for holiday in holidays:
                start_dt = datetime.combine(holiday.start_date, time.min, tzinfo=UTC)
                end_dt = datetime.combine(holiday.end_date, time.max, tzinfo=UTC)
                summary["holidays_count"] += 1
                events.append(
                    {
                        "id": holiday.id,
                        "source": CalendarEventSource.HOLIDAY.value,
                        "title": holiday.name,
                        "start_at": start_dt,
                        "end_at": end_dt,
                        "is_all_day": True,
                        "holiday_name": holiday.name,
                        "status": holiday.audience,
                    }
                )

        if include_route:
            route_stmt = select(Route).where(
                Route.driver_id == driver_id,
                func.date(Route.created_at) >= from_date,
                func.date(Route.created_at) <= to_date,
            )
            if route_type:
                normalized_route_types = [s.strip().upper() for s in route_type if s and s.strip()]
                if normalized_route_types:
                    route_stmt = route_stmt.where(Route.route_type.in_(normalized_route_types))
            if route_status:
                normalized_route_statuses = [s.strip().upper() for s in route_status if s and s.strip()]
                if normalized_route_statuses:
                    route_stmt = route_stmt.where(Route.status.in_(normalized_route_statuses))
            routes = list((await sess.execute(route_stmt.order_by(Route.created_at.asc()))).scalars().all())
            for route in routes:
                summary["routes_count"] += 1
                events.append(
                    {
                        "id": route.id,
                        "source": CalendarEventSource.ROUTE.value,
                        "title": route.route_code,
                        "start_at": route.created_at,
                        "end_at": route.created_at,
                        "is_all_day": False,
                        "status": route.status,
                        "route_type": route.route_type,
                        "route_status": route.status,
                        "route_code": route.route_code,
                    }
                )

        events.sort(key=lambda e: e["start_at"])
        return {
            "from_date": from_date,
            "to_date": to_date,
            "summary": summary,
            "events": events,
        }

    async def list_driver_routes_history(
        self,
        *,
        driver_id: str,
        page: int,
        size: int,
        route_type: list[str] | None = None,
        search: str | None = None,
        sort_by: str | None = "date",
        sort_desc: bool = True,
    ) -> tuple[list[dict], int]:
        """Paginated list of routes for a driver with aggregated telematics counts."""
        stmt = select(Route).where(Route.driver_id == driver_id)
        if route_type:
            normalized = [t.strip().upper() for t in route_type if t and t.strip()]
            allowed = [t for t in normalized if t in {RouteType.PICKUP.value, RouteType.DELIVERY.value}]
            if allowed:
                stmt = stmt.where(Route.route_type.in_(allowed))
        if search:
            like = f"%{search.strip()}%"
            stmt = (
                stmt.outerjoin(Vehicle, Route.vehicle_id == Vehicle.id)
                .where(
                    (Route.route_code.ilike(like))
                    | (Vehicle.registration_number.ilike(like)),
                )
            )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        total = (await sess.execute(count_stmt)).scalar_one()

        order_column = Route.created_at
        sort_by = sort_by or "date"
        if sort_by == "date":
            order_column = Route.created_at

        offset = (page - 1) * size
        stmt = (
            stmt.order_by(order_column.desc() if sort_desc else order_column.asc())
            .offset(offset)
            .limit(size)
            .options(selectinload(Route.vehicle))
        )

        routes = list((await sess.execute(stmt)).scalars().all())

        # Aggregate events counts by route + event_type.
        if routes:
            route_ids = [r.id for r in routes]
            events_stmt = (
                select(
                    RouteEvent.route_id,
                    RouteEvent.event_type,
                    func.count().label("cnt"),
                )
                .where(RouteEvent.route_id.in_(route_ids))
                .group_by(RouteEvent.route_id, RouteEvent.event_type)
            )
            events_rows = await sess.execute(events_stmt)
            events_map = {(row.route_id, row.event_type): row.cnt for row in events_rows}
        else:
            events_map = {}

        out_rows: list[dict] = []
        for r in routes:
            speeding = events_map.get((r.id, "SPEEDING"), 0)
            harsh = events_map.get((r.id, "HARSH_BRAKING"), 0)
            vehicle_reg = getattr(r.vehicle, "registration_number", None)

            operational_summary = None
            if r.total_stops or r.total_duration_min:
                stops = r.total_stops or 0
                mins = r.total_duration_min or 0
                operational_summary = f"{stops} Stops - {mins:.1f} mins Drive Time"

            out_rows.append(
                {
                    "date": r.created_at.date(),
                    "route_id": r.id,
                    "route_code": r.route_code,
                    "vehicle_reg": vehicle_reg,
                    "type": r.route_type,
                    "operational_summary": operational_summary,
                    "speeding_count": int(speeding),
                    "harsh_braking_count": int(harsh),
                }
            )

        return out_rows, total

    async def get_route_summary_payload(self, route_id: str) -> dict:
        """Build the route summary payload for the driver route detail screen."""
        sess = self._driver_repo.session  # type: ignore[attr-defined]

        route_stmt = select(Route).options(selectinload(Route.vehicle)).where(Route.id == route_id)
        route = (await sess.execute(route_stmt)).scalars().first()
        if route is None:
            raise NotFoundError(resource="route", id=route_id)

        from app.modules.orders.models import DeliveryStop

        stops_stmt = (
            select(
                RouteStop,
                DeliveryStop,
            )
            .join(DeliveryStop, RouteStop.delivery_stop_id == DeliveryStop.id, isouter=True)
            .where(RouteStop.route_id == route_id)
            .order_by(RouteStop.sequence.asc())
        )
        rows = list((await sess.execute(stops_stmt)).all())

        total_stops = route.total_stops or len(rows)
        completed = 0

        stops_list: list[dict] = []
        for s, dstop in rows:
            if s.status.upper() == "COMPLETED":
                completed += 1

            postcode = getattr(dstop, "postcode", None) if dstop else None
            line_1 = getattr(dstop, "line_1", None) if dstop else None
            first = (getattr(dstop, "recipient_first_name", None) or "").strip() if dstop else ""
            last = (getattr(dstop, "recipient_last_name", None) or "").strip() if dstop else ""
            recipient = " ".join(p for p in (first, last) if p) or None
            parts: list[str] = []
            if postcode:
                parts.append(postcode)
            if recipient or line_1:
                parts.append(str(recipient or line_1 or ""))
            label = " – ".join(parts) if parts else None

            tracking_id: str | None = getattr(dstop, "tracking_id", None) if dstop else None

            stops_list.append(
                {
                    "sequence": s.sequence,
                    "status": s.status,
                    "stop_flow_type": s.stop_flow_type,
                    "label": label,
                    "tracking_id": tracking_id,
                    "lat": None,
                    "lng": None,
                    "estimated_arrival": s.estimated_arrival,
                    "actual_arrival": s.actual_arrival,
                }
            )

        percent = int((completed / total_stops) * 100) if total_stops else 0

        return {
            "route_id": route.id,
            "route_code": route.route_code,
            # For now, omit concrete date to match schema expectation (nullable date field).
            "date": None,
            "status": route.status,
            "driver_id": route.driver_id,
            "vehicle_reg": getattr(route.vehicle, "registration_number", None),
            "stops": total_stops,
            "estimated_drive_time_minutes": route.estimated_drive_time_min,
            "actual_drive_time_minutes": route.actual_drive_time_min,
            "progress": {
                "completed_stops": completed,
                "total_stops": total_stops,
                "percent": percent,
            },
            "stops_list": stops_list,
            "map_points": [],
        }

    @staticmethod
    def _delivery_stop_location_display_name(dstop: object | None) -> str | None:
        """Short place label for driver home (e.g. business / street line)."""
        if dstop is None:
            return None
        line_1 = (getattr(dstop, "line_1", None) or "").strip()
        if line_1:
            return line_1
        city = (getattr(dstop, "city", None) or "").strip()
        if city:
            return city
        first = (getattr(dstop, "recipient_first_name", None) or "").strip()
        last = (getattr(dstop, "recipient_last_name", None) or "").strip()
        name = " ".join(p for p in (first, last) if p)
        return name or None

    @staticmethod
    def _default_depot_timezone_name() -> str:
        """Fallback IANA zone when driver has no depot; matches ``Depot.timezone`` column default."""
        return "Europe/London"

    @staticmethod
    def _depot_timezone_name_for_driver(driver: Driver) -> tuple[str, bool]:
        """IANA timezone for calendar-day resolution; (tz, used_fallback)."""
        if getattr(driver, "depot_id", None) is None:
            return DriverService._default_depot_timezone_name(), True
        depot = getattr(driver, "depot", None)
        raw = getattr(depot, "timezone", None) if depot is not None else None
        if raw and str(raw).strip():
            return str(raw).strip(), False
        return DriverService._default_depot_timezone_name(), True

    @staticmethod
    def _calendar_date_in_zone(*, utc_now: datetime, tz_name: str) -> date:
        """Calendar date in ``tz_name`` corresponding to ``utc_now`` (handles DST via ZoneInfo)."""
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        for candidate in (tz_name, DriverService._default_depot_timezone_name()):
            try:
                return utc_now.astimezone(ZoneInfo(candidate)).date()
            except ZoneInfoNotFoundError:
                continue
        # Without ``tzdata`` (Windows) no IANA DB may exist; UTC date avoids crashing while logging elsewhere.
        return utc_now.date()

    async def _resolve_plan_service_date_for_today_route(
        self,
        *,
        driver_id: str,
        explicit_service_date: date | None,
    ) -> tuple[date, dict[str, Any]]:
        """Resolve which ``RoutePlan.service_date`` the driver "today" card targets.

        Production rule:
        - If ``explicit_service_date`` is set (query param), use it (support / QA / client override).
        - Otherwise use **local calendar date in the driver's depot timezone** so it matches how
          planners store ``RoutePlan.service_date`` (depot-local day, not server UTC midnight).
        """
        if explicit_service_date is not None:
            return explicit_service_date, {
                "resolution_source": "explicit_query_param",
                "driver_depot_id": None,
                "timezone_evaluated": None,
                "timezone_fallback_used": False,
            }

        sess = self._driver_repo.session  # type: ignore[attr-defined]
        stmt = select(Driver).options(selectinload(Driver.depot)).where(Driver.id == driver_id)
        driver = (await sess.execute(stmt)).scalars().first()
        if driver is None:
            raise NotFoundError(resource="driver", id=driver_id)

        tz_name, tz_fallback = self._depot_timezone_name_for_driver(driver)
        local_day = self._calendar_date_in_zone(utc_now=datetime.now(UTC), tz_name=tz_name)
        return local_day, {
            "resolution_source": "driver_depot_local",
            "driver_depot_id": driver.depot_id,
            "timezone_evaluated": tz_name,
            "timezone_fallback_used": tz_fallback,
        }

    async def get_driver_today_route_dashboard_payload(
        self,
        *,
        driver_id: str,
        explicit_service_date: date | None = None,
    ) -> dict | None:
        """ASSIGNED/ACTIVE route whose plan's ``service_date`` matches the resolved target day.

        Target day: explicit query param, else **today in the driver's depot IANA timezone** (see
        ``_resolve_plan_service_date_for_today_route``).
        """
        from app.modules.orders.models import DeliveryStop

        filter_date, resolution_meta = await self._resolve_plan_service_date_for_today_route(
            driver_id=driver_id,
            explicit_service_date=explicit_service_date,
        )

        sess = self._driver_repo.session  # type: ignore[attr-defined]
        status_rank = case((Route.status == RouteStatus.ACTIVE.value, 0), else_=1)
        stmt = (
            select(Route)
            .join(RoutePlan, Route.plan_id == RoutePlan.id)
            .options(selectinload(Route.vehicle), selectinload(Route.plan))
            .where(
                Route.driver_id == driver_id,
                Route.status.in_([RouteStatus.ASSIGNED.value, RouteStatus.ACTIVE.value]),
                RoutePlan.service_date == filter_date,
            )
            .order_by(
                status_rank.asc(),
                Route.updated_at.desc(),
            )
            .limit(1)
        )
        route = (await sess.execute(stmt)).scalars().first()

        logger.info(
            "driver_today_route_lookup",
            driver_id=driver_id,
            plan_service_date_target=filter_date.isoformat(),
            resolution_source=resolution_meta["resolution_source"],
            driver_depot_id=resolution_meta.get("driver_depot_id"),
            timezone_evaluated=resolution_meta.get("timezone_evaluated"),
            timezone_fallback_used=resolution_meta.get("timezone_fallback_used"),
            route_found=route is not None,
            route_id=route.id if route else None,
            route_status=route.status if route else None,
        )

        if route is None:
            return None

        prev_day = filter_date - timedelta(days=1)
        prev_stmt = (
            select(Route)
            .join(RoutePlan, Route.plan_id == RoutePlan.id)
            .where(
                Route.driver_id == driver_id,
                Route.status.in_([RouteStatus.ASSIGNED.value, RouteStatus.ACTIVE.value]),
                RoutePlan.service_date == prev_day,
            )
            .order_by(
                status_rank.asc(),
                Route.updated_at.desc(),
            )
            .limit(1)
        )
        prev_route = (await sess.execute(prev_stmt)).scalars().first()

        stops_stmt = (
            select(RouteStop, DeliveryStop)
            .join(DeliveryStop, RouteStop.delivery_stop_id == DeliveryStop.id, isouter=True)
            .where(RouteStop.route_id == route.id)
            .order_by(RouteStop.sequence.asc())
        )
        rows = list((await sess.execute(stops_stmt)).all())

        total_stops = route.total_stops or len(rows)
        completed = 0
        for s, _ in rows:
            if str(s.status).upper() == "COMPLETED":
                completed += 1
        percent = int((completed / total_stops) * 100) if total_stops else 0

        plan = route.plan
        plan_service_date = getattr(plan, "service_date", None)

        terminal = frozenset(
            {
                RouteStopStatus.COMPLETED.value,
                RouteStopStatus.FAILED.value,
                RouteStopStatus.CANCELLED.value,
            }
        )
        next_stop_payload: dict | None = None
        for s, dstop in rows:
            if str(s.status).upper() in terminal:
                continue
            next_stop_payload = {
                "stop_id": s.id,
                "sequence": s.sequence,
                "stop_type": route.route_type,
                "stop_flow_type": s.stop_flow_type,
                "location_name": self._delivery_stop_location_display_name(dstop),
                "tracking_id": getattr(dstop, "tracking_id", None) if dstop else None,
                "scheduled_at": s.estimated_arrival,
            }
            break

        return {
            "route_id": route.id,
            "route_code": route.route_code,
            "status": route.status,
            "route_type": route.route_type,
            "service_date": plan_service_date,
            "vehicle_reg": getattr(route.vehicle, "registration_number", None),
            "estimated_drive_time_minutes": route.estimated_drive_time_min,
            "actual_drive_time_minutes": route.actual_drive_time_min,
            "progress": {
                "completed_stops": completed,
                "total_stops": total_stops,
                "percent": percent,
            },
            "todays_deliveries_count": int(total_stops),
            "todays_deliveries_change_pct": self._pct_change(
                float(total_stops),
                float(prev_route.total_stops or 0) if prev_route is not None else 0.0,
            ),
            "estimated_drive_time_change_pct": (
                self._pct_change(
                    float(route.estimated_drive_time_min),
                    float(prev_route.estimated_drive_time_min or 0),
                )
                if route.estimated_drive_time_min is not None and prev_route is not None
                else None
            ),
            "next_stop": next_stop_payload,
        }

    @staticmethod
    def _safe_float(value: object) -> float | None:
        if value is None:
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    @staticmethod
    def _event_metadata_dict(event: RouteEvent) -> dict[str, object]:
        meta = getattr(event, "event_metadata", None)
        return meta if isinstance(meta, dict) else {}

    @staticmethod
    def _speed_severity_from_over_limit(max_over_mph: float | None) -> str:
        if max_over_mph is None or max_over_mph <= 0:
            return "MILD"
        if max_over_mph >= 20:
            return "HIGH"
        if max_over_mph >= 10:
            return "MODERATE"
        return "MILD"

    async def list_driver_average_speed_report(
        self,
        *,
        driver_id: str,
        start_date: date,
        end_date: date,
        page: int,
        size: int,
    ) -> tuple[list[dict[str, object]], int]:
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        filters = and_(
            Route.driver_id == driver_id,
            RoutePlan.service_date >= start_date,
            RoutePlan.service_date <= end_date,
        )
        count_stmt = (
            select(func.count(Route.id))
            .select_from(Route)
            .join(RoutePlan, Route.plan_id == RoutePlan.id)
            .where(filters)
        )
        total = int((await sess.execute(count_stmt)).scalar_one() or 0)

        offset = (page - 1) * size
        paged_stmt = (
            select(Route, RoutePlan.service_date.label("plan_service_date"))
            .join(RoutePlan, Route.plan_id == RoutePlan.id)
            .where(filters)
            .order_by(RoutePlan.service_date.desc(), Route.updated_at.desc())
            .offset(offset)
            .limit(size)
        )
        route_rows = list((await sess.execute(paged_stmt)).all())
        if not route_rows:
            return [], total

        route_ids = [route.id for route, _ in route_rows]
        events_stmt = (
            select(RouteEvent)
            .where(
                RouteEvent.route_id.in_(route_ids),
                RouteEvent.event_type.in_(["LOCATION_PING", "SPEEDING"]),
            )
            .order_by(RouteEvent.occurred_at.asc())
        )
        events = list((await sess.execute(events_stmt)).scalars().all())
        by_route: dict[str, list[RouteEvent]] = {}
        for ev in events:
            by_route.setdefault(ev.route_id, []).append(ev)

        rows: list[dict[str, object]] = []
        for route, plan_service_date in route_rows:
            route_events = by_route.get(route.id, [])
            ping_speeds: list[float] = []
            speed_over_values: list[float] = []
            for ev in route_events:
                meta = self._event_metadata_dict(ev)
                if ev.event_type == "LOCATION_PING":
                    speed = self._safe_float(meta.get("speed_mph"))
                    if speed is not None:
                        ping_speeds.append(speed)
                elif ev.event_type == "SPEEDING":
                    over = self._safe_float(meta.get("speed_over_mph"))
                    if over is not None:
                        speed_over_values.append(over)

            range_min = round(min(ping_speeds), 1) if ping_speeds else None
            range_max = round(max(ping_speeds), 1) if ping_speeds else None
            average_speed = self._average_speed_mph_from_distance_and_time(
                total_distance_km=route.total_distance_km,
                actual_drive_time_min=route.actual_drive_time_min,
            )
            if average_speed is None and ping_speeds:
                average_speed = round(sum(ping_speeds) / len(ping_speeds), 1)
            if average_speed is not None and not math.isfinite(average_speed):
                average_speed = None
            max_over = max(speed_over_values) if speed_over_values else None
            rows.append(
                {
                    "route_id": route.id,
                    "route_code": route.route_code,
                    "service_date": plan_service_date,
                    "average_speed_mph": average_speed,
                    "speed_range_min_mph": range_min,
                    "speed_range_max_mph": range_max,
                    "severity": self._speed_severity_from_over_limit(max_over),
                }
            )
        return rows, total

    async def list_driver_above_70_mph_report(
        self,
        *,
        driver_id: str,
        start_date: date,
        end_date: date,
        page: int,
        size: int,
    ) -> tuple[list[dict], int]:
        """Paginated SPEEDING events with recorded ``speed_mph`` above telemetry threshold.

        Scoped to routes owned by ``driver_id`` whose plan ``service_date`` falls in
        ``[start_date, end_date]``. Uses DB-side JSON filter and pagination (no full-table
        scans in Python).
        """
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        threshold = self._TELEMETRY_SPEEDING_THRESHOLD_MPH
        speed_mph_json = cast(RouteEvent.event_metadata["speed_mph"].astext, Float)
        base_stmt = (
            select(RouteEvent, Route.route_code)
            .join(Route, Route.id == RouteEvent.route_id)
            .join(RoutePlan, Route.plan_id == RoutePlan.id)
            .where(
                Route.driver_id == driver_id,
                RoutePlan.service_date >= start_date,
                RoutePlan.service_date <= end_date,
                RouteEvent.event_type == "SPEEDING",
                speed_mph_json > threshold,
            )
        )
        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        total = int((await sess.execute(count_stmt)).scalar_one() or 0)
        stmt = (
            base_stmt.order_by(RouteEvent.occurred_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        events = list((await sess.execute(stmt)).all())
        rows = [self._route_event_row_dict(e, route_code) for e, route_code in events]
        return rows, total

    async def list_driver_sharp_brake_report(
        self,
        *,
        driver_id: str,
        start_date: date,
        end_date: date,
        page: int,
        size: int,
    ) -> tuple[list[dict], int]:
        """Paginated HARSH_BRAKING events for routes in the given ``service_date`` window."""
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        base_stmt = (
            select(RouteEvent, Route.route_code)
            .join(Route, Route.id == RouteEvent.route_id)
            .join(RoutePlan, Route.plan_id == RoutePlan.id)
            .where(
                Route.driver_id == driver_id,
                RoutePlan.service_date >= start_date,
                RoutePlan.service_date <= end_date,
                RouteEvent.event_type == "HARSH_BRAKING",
            )
        )
        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        total = int((await sess.execute(count_stmt)).scalar_one() or 0)
        stmt = (
            base_stmt.order_by(RouteEvent.occurred_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        events = list((await sess.execute(stmt)).all())
        rows = [self._route_event_row_dict(e, route_code) for e, route_code in events]
        return rows, total

    async def list_driver_assigned_routes_payload(
        self,
        *,
        driver_id: str,
        page: int,
        size: int,
    ) -> tuple[list[dict], int]:
        """Paginated ASSIGNED-only routes (any plan ``service_date`` — depot-local calendar days)."""
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        stmt = (
            select(Route)
            .join(RoutePlan, Route.plan_id == RoutePlan.id)
            .where(
                Route.driver_id == driver_id,
                Route.status == RouteStatus.ASSIGNED.value,
            )
        )
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await sess.execute(count_stmt)).scalar_one()

        offset = (page - 1) * size
        stmt = (
            stmt.options(selectinload(Route.vehicle), selectinload(Route.plan))
            .order_by(RoutePlan.service_date.asc(), Route.route_code.asc())
            .offset(offset)
            .limit(size)
        )
        routes = list((await sess.execute(stmt)).scalars().all())
        out_rows: list[dict] = []
        for r in routes:
            plan = r.plan
            sd = getattr(plan, "service_date", None)
            out_rows.append(
                {
                    "route_id": r.id,
                    "route_code": r.route_code,
                    "service_date": sd,
                    "route_type": r.route_type,
                    "vehicle_reg": getattr(r.vehicle, "registration_number", None),
                    "total_stops": r.total_stops,
                    "status": RouteStatus.ASSIGNED.value,
                }
            )
        return out_rows, total

    @staticmethod
    def _average_speed_mph_from_distance_and_time(
        *,
        total_distance_km: float | None,
        actual_drive_time_min: float | None,
    ) -> float | None:
        """Same formula as ``get_average_route_speed_payload`` (distance ÷ time → mph)."""
        td = DriverService._safe_float(total_distance_km) or 0.0
        tm = DriverService._safe_float(actual_drive_time_min) or 0.0
        if td <= 0 or tm <= 0:
            return None
        average_speed_kph = td / (tm / 60.0)
        result = round(average_speed_kph * 0.621371, 1)
        return result if math.isfinite(result) else None

    async def list_driver_routes_board_tab(
        self,
        *,
        driver_id: str,
        tab: str,
        page: int,
        size: int,
        route_type: list[str] | None = None,
        search: str | None = None,
        sort: str | None = None,
    ) -> tuple[list[dict], int]:
        """Paginated routes for the driver **All Routes** screen (Upcoming vs Past).

        **upcoming** — ``ASSIGNED`` and ``ACTIVE`` routes (not finished).
        **past** — ``COMPLETED`` routes only.

        Default ordering by plan ``service_date``: upcoming soonest-first (oldest_first), past most-recent-first (newest_first).
        Optional ``sort`` flips service_date direction. Upcoming tie-break: ACTIVE before ASSIGNED, then route_code.
        """
        tab_norm = str(tab or "").strip().lower()
        if tab_norm not in ("upcoming", "past"):
            raise ValidationError("tab must be 'upcoming' or 'past'")

        sort_raw = str(sort or "").strip().lower().replace("-", "_")
        default_sort = "oldest_first" if tab_norm == "upcoming" else "newest_first"
        sort_norm = sort_raw if sort_raw else default_sort
        if sort_norm not in ("newest_first", "oldest_first"):
            raise ValidationError("sort must be 'newest_first' or 'oldest_first'")

        sess = self._driver_repo.session  # type: ignore[attr-defined]
        stmt = (
            select(Route)
            .join(RoutePlan, Route.plan_id == RoutePlan.id)
            .where(Route.driver_id == driver_id)
        )
        if tab_norm == "upcoming":
            stmt = stmt.where(Route.status.in_([RouteStatus.ASSIGNED.value, RouteStatus.ACTIVE.value]))
        else:
            stmt = stmt.where(Route.status == RouteStatus.COMPLETED.value)

        if route_type:
            normalized = [t.strip().upper() for t in route_type if t and t.strip()]
            allowed = [t for t in normalized if t in {RouteType.PICKUP.value, RouteType.DELIVERY.value}]
            if allowed:
                stmt = stmt.where(Route.route_type.in_(allowed))

        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.outerjoin(Vehicle, Route.vehicle_id == Vehicle.id).where(
                (Route.route_code.ilike(like)) | (Vehicle.registration_number.ilike(like)),
            )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = int((await sess.execute(count_stmt)).scalar_one() or 0)

        status_rank = case((Route.status == RouteStatus.ACTIVE.value, 0), else_=1)
        if tab_norm == "upcoming":
            if sort_norm == "oldest_first":
                stmt = stmt.order_by(RoutePlan.service_date.asc(), status_rank.asc(), Route.route_code.asc())
            else:
                stmt = stmt.order_by(RoutePlan.service_date.desc(), status_rank.asc(), Route.route_code.asc())
        elif sort_norm == "newest_first":
            stmt = stmt.order_by(RoutePlan.service_date.desc(), Route.updated_at.desc())
        else:
            stmt = stmt.order_by(RoutePlan.service_date.asc(), Route.updated_at.asc())

        offset = (page - 1) * size
        stmt = stmt.offset(offset).limit(size).options(selectinload(Route.vehicle), selectinload(Route.plan))

        routes = list((await sess.execute(stmt)).scalars().all())

        local_today, _ = await self._resolve_plan_service_date_for_today_route(
            driver_id=driver_id,
            explicit_service_date=None,
        )

        out_rows: list[dict] = []
        for r in routes:
            plan = r.plan
            sd = plan.service_date
            avg = self._average_speed_mph_from_distance_and_time(
                total_distance_km=r.total_distance_km,
                actual_drive_time_min=r.actual_drive_time_min,
            )
            out_rows.append(
                {
                    "route_id": r.id,
                    "route_code": r.route_code,
                    "route_type": r.route_type,
                    "service_date": sd,
                    "vehicle_reg": getattr(r.vehicle, "registration_number", None),
                    "status": r.status,
                    "total_stops": int(r.total_stops or 0),
                    "estimated_drive_time_minutes": r.estimated_drive_time_min,
                    "actual_drive_time_minutes": r.actual_drive_time_min,
                    "average_route_speed_mph": avg,
                    "is_service_date_today": sd == local_today,
                }
            )

        return out_rows, total

    @staticmethod
    def _pct_change(current: float, previous: float) -> float | None:
        if not math.isfinite(current) or not math.isfinite(previous) or previous <= 0:
            return None
        result = round(((current - previous) / previous) * 100.0, 1)
        return result if math.isfinite(result) else None

    @staticmethod
    def _previous_window(start: date, end: date) -> tuple[date, date]:
        days = (end - start).days + 1
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=days - 1)
        return prev_start, prev_end

    @classmethod
    def resolve_home_summary_windows(
        cls,
        *,
        period: str | None,
        start_date: date | None,
        end_date: date | None,
        today: date | None = None,
    ) -> tuple[date, date, date, date]:
        ref = today or datetime.now(UTC).date()
        if period:
            return cls._resolve_period_bounds(period, today=ref)
        window_start, window_end = cls.resolve_report_date_range(
            period=None,
            start_date=start_date,
            end_date=end_date,
            today=ref,
        )
        prev_start, prev_end = cls._previous_window(window_start, window_end)
        return window_start, window_end, prev_start, prev_end

    @staticmethod
    def resolve_report_date_range(
        *,
        period: str | None,
        start_date: date | None,
        end_date: date | None,
        today: date | None = None,
    ) -> tuple[date, date]:
        """Resolve inclusive report window from ``period`` preset or explicit dates."""
        ref = today or datetime.now(UTC).date()
        if period:
            start, end, _, _ = DriverService._resolve_period_bounds(period, today=ref)
            return start, end
        if start_date is None or end_date is None:
            raise ValidationError("Provide period or both start_date and end_date")
        if end_date < start_date:
            raise ValidationError("end_date must be on or after start_date")
        return start_date, end_date

    @staticmethod
    def _resolve_period_bounds(period: str, *, today: date) -> tuple[date, date, date, date]:
        normalized_period = str(period or "today").strip().lower()
        if normalized_period == "today":
            start = today
            end = today
        elif normalized_period == "yesterday":
            start = today - timedelta(days=1)
            end = start
        elif normalized_period == "this_week":
            start = today - timedelta(days=today.weekday())
            end = today
        elif normalized_period == "last_week":
            current_week_start = today - timedelta(days=today.weekday())
            end = current_week_start - timedelta(days=1)
            start = end - timedelta(days=6)
        elif normalized_period == "last_month":
            this_month_start = today.replace(day=1)
            end = this_month_start - timedelta(days=1)
            start = end.replace(day=1)
        else:
            raise ValidationError("period must be one of: today, yesterday, this_week, last_week, last_month")

        prev_start, prev_end = DriverService._previous_window(start, end)
        return start, end, prev_start, prev_end

    async def _compute_home_metrics_window(self, *, driver_id: str, start: date, end: date) -> tuple[int, float | None]:
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        route_stmt = (
            select(Route)
            .join(RoutePlan, Route.plan_id == RoutePlan.id)
            .where(
                Route.driver_id == driver_id,
                RoutePlan.service_date >= start,
                RoutePlan.service_date <= end,
            )
        )
        routes = list((await sess.execute(route_stmt)).scalars().all())
        route_ids = [r.id for r in routes]

        addresses_attended = 0
        if route_ids:
            stops_stmt = select(cast(RouteStop.status, String)).where(RouteStop.route_id.in_(route_ids))
            statuses = [str(s or "").upper() for s in (await sess.execute(stops_stmt)).scalars().all()]
            addresses_attended = sum(1 for s in statuses if s == RouteStopStatus.COMPLETED.value)

        total_actual_drive_time_minutes = sum(self._safe_float(r.actual_drive_time_min) or 0.0 for r in routes)
        total_distance_km = sum(self._safe_float(r.total_distance_km) or 0.0 for r in routes)
        average_speed_mph = self._average_speed_mph_from_distance_and_time(
            total_distance_km=total_distance_km,
            actual_drive_time_min=total_actual_drive_time_minutes,
        )

        return addresses_attended, average_speed_mph

    async def get_driver_home_summary(
        self,
        *,
        driver_id: str,
        period: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, object]:
        """Compute mobile home KPIs for selected period vs previous matching window."""
        period_start, period_end, prev_start, prev_end = self.resolve_home_summary_windows(
            period=period,
            start_date=start_date,
            end_date=end_date,
        )

        addresses_attended, average_speed_mph = await self._compute_home_metrics_window(
            driver_id=driver_id,
            start=period_start,
            end=period_end,
        )
        prev_addresses_attended, prev_average_speed_mph = await self._compute_home_metrics_window(
            driver_id=driver_id,
            start=prev_start,
            end=prev_end,
        )

        return {
            "addresses_attended": addresses_attended,
            "addresses_change_pct": self._pct_change(float(addresses_attended), float(prev_addresses_attended)),
            "average_speed_mph": average_speed_mph,
            "average_speed_change_pct": self._pct_change(
                float(average_speed_mph),
                float(prev_average_speed_mph),
            )
            if average_speed_mph is not None and prev_average_speed_mph is not None
            else None,
        }

    async def _get_route_for_driver_or_404(self, *, route_id: str, driver_id: str) -> Route:
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        route_stmt = select(Route).where(Route.id == route_id, Route.driver_id == driver_id)
        route = (await sess.execute(route_stmt)).scalars().first()
        if route is None:
            raise NotFoundError(resource="route", id=route_id)
        return route

    async def ensure_route_owned_by_driver(self, *, route_id: str, driver_id: str) -> None:
        await self._get_route_for_driver_or_404(route_id=route_id, driver_id=driver_id)

    async def driver_set_route_status(
        self,
        *,
        route_id: str,
        driver_id: str,
        status: RouteStatus,
        event_type: str,
        metadata: dict[str, object] | None = None,
    ) -> Route:
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        route = await self._get_route_for_driver_or_404(route_id=route_id, driver_id=driver_id)
        route.status = status.value
        event = RouteEvent(
            route_id=route.id,
            driver_id=driver_id,
            event_type=event_type,
            occurred_at=datetime.now(UTC),
            event_metadata=metadata or {},
        )
        sess.add(event)
        await sess.flush()
        return route

    async def driver_update_stop_status(
        self,
        *,
        stop_id: str,
        driver_id: str,
        status: RouteStopStatus,
        notes: str | None = None,
    ) -> RouteStop:
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        stmt = (
            select(RouteStop, Route)
            .join(Route, Route.id == RouteStop.route_id)
            .where(RouteStop.id == stop_id, Route.driver_id == driver_id)
        )
        row = (await sess.execute(stmt)).first()
        if row is None:
            raise NotFoundError(resource="route_stop", id=stop_id)
        stop, route = row
        stop.status = status.value
        if status == RouteStopStatus.ARRIVED:
            stop.actual_arrival = datetime.now(UTC)
        if notes is not None:
            stop.notes = notes
        event = RouteEvent(
            route_id=route.id,
            driver_id=driver_id,
            event_type=f"STOP_{status.value}",
            occurred_at=datetime.now(UTC),
            event_metadata={"stop_id": stop.id, "notes": notes} if notes is not None else {"stop_id": stop.id},
        )
        sess.add(event)
        await sess.flush()
        return stop

    async def ingest_driver_telematics_batch(
        self,
        *,
        driver_id: str,
        items: list[dict[str, object]],
    ) -> int:
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        if not items:
            return 0
        last_ping_by_route: dict[str, tuple[datetime, float] | None] = {}

        def _to_float(value: object) -> float | None:
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        async def _get_last_ping_state(route_id: str) -> tuple[datetime, float] | None:
            cached = last_ping_by_route.get(route_id, None)
            if route_id in last_ping_by_route:
                return cached
            stmt = (
                select(RouteEvent)
                .where(
                    RouteEvent.route_id == route_id,
                    RouteEvent.event_type == "LOCATION_PING",
                )
                .order_by(RouteEvent.occurred_at.desc())
                .limit(1)
            )
            last = (await sess.execute(stmt)).scalars().first()
            if last is None:
                last_ping_by_route[route_id] = None
                return None
            speed = _to_float((getattr(last, "event_metadata", {}) or {}).get("speed_mph"))
            if speed is None:
                last_ping_by_route[route_id] = None
                return None
            last_ping_by_route[route_id] = (last.occurred_at, speed)
            return last_ping_by_route[route_id]

        for item in items:
            route_id = str(item.get("route_id") or "")
            if not route_id:
                raise ValidationError("route_id is required for each telemetry point")
            await self._get_route_for_driver_or_404(route_id=route_id, driver_id=driver_id)
            occurred_at_raw = item.get("occurred_at")
            occurred_at: datetime
            if isinstance(occurred_at_raw, datetime):
                occurred_at = occurred_at_raw
            else:
                occurred_at = datetime.now(UTC)
            speed_mph = _to_float(item.get("speed_mph"))
            heading = _to_float(item.get("heading"))
            accuracy_m = _to_float(item.get("accuracy_m"))
            lat = _to_float(item.get("lat"))
            lng = _to_float(item.get("lng"))
            event = RouteEvent(
                route_id=route_id,
                driver_id=driver_id,
                event_type="LOCATION_PING",
                occurred_at=occurred_at,
                lat=lat,
                lng=lng,
                event_metadata={
                    "speed_mph": speed_mph,
                    "heading": heading,
                    "accuracy_m": accuracy_m,
                    "source": item.get("source"),
                },
            )
            sess.add(event)
            previous = await _get_last_ping_state(route_id)
            if speed_mph is not None and speed_mph > self._TELEMETRY_SPEEDING_THRESHOLD_MPH:
                sess.add(
                    RouteEvent(
                        route_id=route_id,
                        driver_id=driver_id,
                        event_type="SPEEDING",
                        occurred_at=occurred_at,
                        lat=lat,
                        lng=lng,
                        event_metadata={
                            "speed_mph": speed_mph,
                            "limit_mph": self._TELEMETRY_SPEEDING_THRESHOLD_MPH,
                            "speed_over_mph": round(speed_mph - self._TELEMETRY_SPEEDING_THRESHOLD_MPH, 1),
                            "heading": heading,
                            "accuracy_m": accuracy_m,
                            "source": item.get("source"),
                        },
                    )
                )
            if previous is not None and speed_mph is not None:
                previous_at, previous_speed = previous
                elapsed = (occurred_at - previous_at).total_seconds()
                speed_drop = previous_speed - speed_mph
                if 0 <= elapsed <= self._TELEMETRY_HARSH_BRAKE_WINDOW_SECONDS and speed_drop >= self._TELEMETRY_HARSH_BRAKE_DELTA_MPH:
                    severity = "LOW" if speed_drop < 25 else "MEDIUM" if speed_drop < 35 else "HIGH"
                    sess.add(
                        RouteEvent(
                            route_id=route_id,
                            driver_id=driver_id,
                            event_type="HARSH_BRAKING",
                            occurred_at=occurred_at,
                            lat=lat,
                            lng=lng,
                            event_metadata={
                                "start_speed_mph": round(previous_speed, 1),
                                "end_speed_mph": round(speed_mph, 1),
                                "reduction_speed_mph": round(speed_drop, 1),
                                "severity": severity,
                                "heading": heading,
                                "accuracy_m": accuracy_m,
                                "source": item.get("source"),
                            },
                        )
                    )
            last_ping_by_route[route_id] = (occurred_at, speed_mph) if speed_mph is not None else None
        await sess.flush()
        return len(items)

    async def list_route_stops_for_driver(self, *, route_id: str, driver_id: str) -> list[dict[str, object]]:
        """List stops for a route owned by the given driver."""
        await self._get_route_for_driver_or_404(route_id=route_id, driver_id=driver_id)
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        from app.modules.orders.models import DeliveryStop, Package

        stmt = (
            select(
                RouteStop,
                DeliveryStop,
                func.count(Package.id).label("packages_count"),
            )
            .join(DeliveryStop, RouteStop.delivery_stop_id == DeliveryStop.id, isouter=True)
            .join(Package, Package.delivery_stop_id == DeliveryStop.id, isouter=True)
            .where(RouteStop.route_id == route_id)
            .group_by(RouteStop.id, DeliveryStop.id)
            .order_by(RouteStop.sequence.asc())
        )
        rows = list((await sess.execute(stmt)).all())

        out: list[dict[str, object]] = []
        for stop, dstop, packages_count in rows:
            stop_tracking_id = getattr(dstop, "tracking_id", None)
            tracking_summary = f"#{stop_tracking_id}" if stop_tracking_id else None
            first = (getattr(dstop, "recipient_first_name", None) or "").strip() if dstop else ""
            last = (getattr(dstop, "recipient_last_name", None) or "").strip() if dstop else ""
            recipient = " ".join(p for p in (first, last) if p) or None
            out.append(
                {
                    "stop_id": stop.id,
                    "sequence": stop.sequence,
                    "tracking_id": stop_tracking_id,
                    "name": (recipient or getattr(dstop, "line_1", None) if dstop else None),
                    "recipient_phone": getattr(dstop, "recipient_phone", None) if dstop else None,
                    "tracking_summary": tracking_summary,
                    "postal_code": getattr(dstop, "postcode", None) if dstop else None,
                    "latitude": getattr(dstop, "latitude", None) if dstop else None,
                    "longitude": getattr(dstop, "longitude", None) if dstop else None,
                    "status": stop.status,
                    "stop_flow_type": stop.stop_flow_type,
                    "estimated_delivery_time": stop.estimated_arrival,
                    "actual_delivery_time": stop.actual_arrival,
                    "packages_count": int(packages_count or 0),
                }
            )
        return out

    async def list_stop_packages_for_driver(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
    ) -> dict[str, object]:
        """List package tracking IDs for a stop on a route owned by the driver."""
        await self._get_route_for_driver_or_404(route_id=route_id, driver_id=driver_id)
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        from app.modules.orders.models import DeliveryStop, Package

        stop_stmt = select(RouteStop).where(RouteStop.id == stop_id, RouteStop.route_id == route_id)
        stop = (await sess.execute(stop_stmt)).scalars().first()
        if stop is None:
            raise NotFoundError(resource="route_stop", id=stop_id)

        if stop.delivery_stop_id is None:
            return {"tracking_id": None, "items": []}

        dstop_stmt = select(DeliveryStop).where(DeliveryStop.id == stop.delivery_stop_id)
        dstop_row = (await sess.execute(dstop_stmt)).first()
        dstop = dstop_row[0] if dstop_row else None

        pkg_stmt = (
            select(Package)
            .where(Package.delivery_stop_id == stop.delivery_stop_id)
            .order_by(Package.created_at.asc())
        )
        packages = list((await sess.execute(pkg_stmt)).scalars().all())
        return {
            "tracking_id": getattr(dstop, "tracking_id", None),
            "items": [{"package_id": p.id, "status": p.status} for p in packages],
        }

    async def _resolve_execution_context(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
    ) -> tuple[RouteStop, str]:
        await self._get_route_for_driver_or_404(route_id=route_id, driver_id=driver_id)
        stop = await self._stop_exec_repo.get_route_stop(route_id=route_id, stop_id=stop_id)
        if stop is None:
            raise NotFoundError(resource="route_stop", id=stop_id)
        if stop.delivery_stop_id is None:
            raise ValidationError("Selected route stop is not linked to a delivery stop")
        return stop, stop.delivery_stop_id

    async def pickup_scan_packages_for_order(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
        package_ids: list[str] | None = None,
    ) -> dict[str, object]:
        """Bulk-scan packages for a PICKUP route_stop whose target is an Order (``order_id``).

        For pickup stops the driver app's "load truck" action collects every package on the order
        in one go (or a subset by ``package_ids``). This path is narrow on purpose: it sidesteps
        the gated ``scan_stop_package`` flow (which requires ``delivery_stop_id``) because for a
        pickup-flow stop the coordinates and packages come from ``orders.pickup_address_id`` /
        ``packages.order_id`` respectively.

        Each scanned package transitions to :class:`PackageStatus.LOADED_FOR_DELIVERY` and a row is
        appended to ``package_scan_logs`` using the package's own ``delivery_stop_id`` (its eventual
        delivery destination) so the audit trail remains queryable per delivery stop.

        Returns a dict with the scanned package summaries and a count of items skipped.
        """
        await self._get_route_for_driver_or_404(route_id=route_id, driver_id=driver_id)
        stop = await self._stop_exec_repo.get_route_stop(route_id=route_id, stop_id=stop_id)
        if stop is None:
            raise NotFoundError(resource="route_stop", id=stop_id)
        if stop.order_id is None:
            raise ValidationError("Selected route stop is not a pickup-flow stop linked to an order")

        sess = self._driver_repo.session  # type: ignore[attr-defined]
        order = (await sess.execute(select(Order).where(Order.id == stop.order_id))).scalars().first()
        if order is None:
            raise NotFoundError(resource="order", id=stop.order_id)

        packages = await self._package_exec_repo.list_for_order(order.id)
        if not packages:
            raise ValidationError("No packages found for this pickup order")

        if package_ids:
            allowed = set(package_ids)
            packages = [p for p in packages if p.id in allowed]
            if not packages:
                raise ValidationError("None of the supplied package_ids belong to this order")

        scanned: list[dict[str, str]] = []
        skipped = 0
        for pkg in packages:
            if pkg.status == PackageStatus.LOADED_FOR_DELIVERY:
                skipped += 1
                continue
            updated = await self._package_exec_repo.update_package_status(
                package=pkg,
                status=PackageStatus.LOADED_FOR_DELIVERY,
                actor_user_id=None,
                suppress_automation=True,
            )
            if pkg.delivery_stop_id:
                await self._package_exec_repo.create_scan_log(
                    route_id=route_id,
                    route_stop_id=stop.id,
                    delivery_stop_id=pkg.delivery_stop_id,
                    package_id=updated.id,
                    driver_id=driver_id,
                    scan_value=updated.package_id or updated.id,
                    result="loaded_for_delivery",
                )
            scanned.append({"package_id": updated.id, "status": str(updated.status)})

        logger.info(
            "driver.pickup.packages_loaded",
            route_id=route_id,
            stop_id=stop.id,
            order_id=order.id,
            scanned=len(scanned),
            skipped=skipped,
        )
        return {
            "route_stop_id": stop.id,
            "order_id": order.id,
            "scanned_count": len(scanned),
            "skipped_count": skipped,
            "scanned": scanned,
        }

    @staticmethod
    def _route_stop_flow_value(stop: RouteStop) -> str:
        return str(stop.stop_flow_type or RouteStopFlowType.DELIVERY.value).strip().upper()

    @staticmethod
    def _package_status_from_driver_patch_request(*, flow: str, status_raw: str) -> PackageStatus:
        """Map PATCH …/packages/{id}/status body to ``PackageStatus`` (delivery vs return UI labels)."""
        raw = str(status_raw or "").strip().upper()
        if not raw:
            raise ValidationError("status is required")

        if flow == RouteStopFlowType.DELIVERY.value:
            if raw in {"RETURNED_TO_SENDER", "SENDER_NOT_HOME"}:
                raise ValidationError(
                    "On delivery stops use CUSTOMER_NOT_HOME for Customer not home, not SENDER_NOT_HOME or RETURNED_TO_SENDER",
                )
            try:
                st = PackageStatus(raw)
            except ValueError as err:
                raise ValidationError("Unknown package status") from err
            if st not in PACKAGE_DRIVER_PATCH_DELIVERY_STATUSES:
                raise ValidationError(
                    "This status is not valid on delivery stops. Use DELIVERED_TO_CUSTOMER, LEFT_AT_SAFE_PLACE, "
                    "CUSTOMER_NOT_HOME, or REFUSED_BY_CUSTOMER.",
                )
            return st

        if flow == RouteStopFlowType.RETURN.value:
            if raw == "RETURNED":
                raise ValidationError(
                    "On return stops use RETURNED_TO_SENDER (not the legacy RETURNED value).",
                )
            if raw == "CUSTOMER_NOT_HOME":
                raise ValidationError(
                    "On return stops use SENDER_NOT_HOME (not CUSTOMER_NOT_HOME; that label is for delivery only).",
                )
            if raw == "DISPOSED":
                st = PackageStatus.DISPOSED
            elif raw == "RETURNED_TO_SENDER":
                st = PackageStatus.RETURNED
            elif raw == "SENDER_NOT_HOME":
                st = PackageStatus.CUSTOMER_NOT_HOME
            else:
                raise ValidationError(
                    "This status is not valid on return stops. Use RETURNED_TO_SENDER, SENDER_NOT_HOME, or DISPOSED.",
                )
            return st

        raise ValidationError("Unsupported stop_flow_type for package status finalization")

    @staticmethod
    def _pending_package_ids_for_stop_flow(*, flow: str, packages: list[Package]) -> list[str]:
        flow_u = flow.upper()
        if flow_u == RouteStopFlowType.PICKUP.value:
            return [p.id for p in packages if p.status in PACKAGE_PRE_PICKUP_FOR_SCAN_STATUSES]
        if flow_u == RouteStopFlowType.RETURN.value:
            return [p.id for p in packages if p.status not in PACKAGE_STOP_RETURN_HUB_COMPLETE_STATUSES]
        return [p.id for p in packages if p.status not in PACKAGE_STOP_DELIVERY_OUTCOME_STATUSES]

    @staticmethod
    def _compute_stop_notes_hash(
        *,
        notes: list[dict[str, object]],
    ) -> str:
        ordered = sorted(
            notes,
            key=lambda x: (
                int(x.get("sort_order") or 0),
                str(x.get("note_type") or ""),
                str(x.get("id") or ""),
            ),
        )
        payload = json.dumps(ordered, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def get_stop_notes_payload(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
    ) -> dict[str, object]:
        _stop, delivery_stop_id = await self._resolve_execution_context(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        notes = await self._stop_note_repo.list_for_delivery_stop(delivery_stop_id)
        dstop = await self._stop_note_repo.session.get(DeliveryStop, delivery_stop_id)
        if dstop is None:
            raise NotFoundError(resource="delivery_stop", id=delivery_stop_id)
        pkg_by_note = await batch_package_ids_for_stop_notes(
            self._stop_note_repo.session,
            delivery_stop_id=delivery_stop_id,
            order_id=dstop.order_id,
            notes=notes,
        )
        note_ids = [n.id for n in notes]
        images = await self._stop_note_repo.list_images_for_note_ids(note_ids)
        images_by_note: dict[str, list[dict[str, object]]] = {}
        for image in images:
            images_by_note.setdefault(image.stop_note_id, []).append(
                {
                    "id": image.id,
                    "image_key": image.image_key,
                    "sort_order": image.sort_order,
                }
            )
        note_payload = [
            {
                "id": n.id,
                "note_type": n.note_type,
                "message": n.message,
                "is_blocking": n.is_blocking,
                "sort_order": n.sort_order,
                "package_ids": pkg_by_note.get(n.id, []),
                "images": images_by_note.get(n.id, []),
            }
            for n in notes
        ]
        notes_hash = self._compute_stop_notes_hash(notes=note_payload)
        ack = await self._stop_note_repo.get_ack(
            delivery_stop_id=delivery_stop_id,
            driver_id=driver_id,
            notes_hash=notes_hash,
        )
        requires_ack = any(bool(n.is_blocking) for n in notes)
        return {
            "route_id": route_id,
            "stop_id": stop_id,
            "delivery_stop_id": delivery_stop_id,
            "notes_hash": notes_hash,
            "requires_acknowledgement": requires_ack,
            "acknowledged": ack is not None,
            "acknowledged_at": getattr(ack, "acknowledged_at", None),
            "items": note_payload,
        }

    async def acknowledge_stop_notes(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
        notes_hash: str,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> dict[str, object]:
        _stop, delivery_stop_id = await self._resolve_execution_context(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        payload = await self.get_stop_notes_payload(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        current_hash = str(payload["notes_hash"])
        if notes_hash != current_hash:
            raise ValidationError("Notes changed; refresh notes and acknowledge latest version")
        now = datetime.now(UTC)
        ack = await self._stop_note_repo.upsert_ack(
            delivery_stop_id=delivery_stop_id,
            driver_id=driver_id,
            notes_hash=notes_hash,
            acknowledged_at=now,
        )
        await self._log_audit(
            "driver.stop.notes.ack",
            entity_id=delivery_stop_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={"notes_hash": notes_hash, "acknowledged_at": ack.acknowledged_at.isoformat()},
            severity="NOTICE",
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.CONTRACT_EXECUTED,
        )
        return {"acknowledged": True, "acknowledged_at": ack.acknowledged_at, "notes_hash": notes_hash}

    async def scan_stop_package(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
        scan_value: str,
    ) -> dict[str, object]:
        stop, delivery_stop_id = await self._resolve_execution_context(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        await self._validate_notes_ack_gate(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        flow = self._route_stop_flow_value(stop)
        raw_scan = scan_value.strip()

        package = await self._package_exec_repo.resolve_by_scan_value(raw_scan)
        if package is not None:
            if package.delivery_stop_id != delivery_stop_id:
                await self._package_exec_repo.create_scan_log(
                    route_id=route_id,
                    route_stop_id=stop.id,
                    delivery_stop_id=delivery_stop_id,
                    package_id=package.id,
                    driver_id=driver_id,
                    scan_value=scan_value,
                    result="WRONG_STOP",
                )
                raise ValidationError("This package does not belong to the selected stop")
            if flow == RouteStopFlowType.PICKUP.value:
                terminal = package.status not in PACKAGE_PRE_PICKUP_FOR_SCAN_STATUSES
            elif flow == RouteStopFlowType.RETURN.value:
                terminal = package.status in PACKAGE_STOP_RETURN_HUB_COMPLETE_STATUSES
            else:
                terminal = package.status in PACKAGE_STOP_DELIVERY_OUTCOME_STATUSES
            if terminal:
                await self._package_exec_repo.create_scan_log(
                    route_id=route_id,
                    route_stop_id=stop.id,
                    delivery_stop_id=delivery_stop_id,
                    package_id=package.id,
                    driver_id=driver_id,
                    scan_value=scan_value,
                    result="ALREADY_FINALIZED",
                )
                raise ValidationError("Package is already finalized")
            await self._package_exec_repo.create_scan_log(
                route_id=route_id,
                route_stop_id=stop.id,
                delivery_stop_id=delivery_stop_id,
                package_id=package.id,
                driver_id=driver_id,
                scan_value=scan_value,
                result="MATCHED",
            )
            return {
                "package_id": package.id,
                "reference_number": package.package_id,
                "status": package.status,
                "matched_by": "PACKAGE",
                "master_label_id": None,
                "packages_confirmed": 1,
            }

        if flow == RouteStopFlowType.PICKUP.value:
            sess = self._driver_repo.session  # type: ignore[attr-defined]
            dstop = await sess.get(DeliveryStop, delivery_stop_id)
            if dstop is not None:
                order = await sess.get(Order, dstop.order_id)
                if (
                    order is not None
                    and order.master_label_id
                    and raw_scan == order.master_label_id.strip()
                ):
                    packages = await self._package_exec_repo.list_for_delivery_stop(delivery_stop_id)
                    if not packages:
                        await self._package_exec_repo.create_scan_log(
                            route_id=route_id,
                            route_stop_id=stop.id,
                            delivery_stop_id=delivery_stop_id,
                            package_id=None,
                            driver_id=driver_id,
                            scan_value=scan_value,
                            result="MASTER_LABEL_NO_PACKAGES",
                        )
                        raise ValidationError("No packages linked to this stop")
                    to_update = [p for p in packages if p.status in PACKAGE_PRE_PICKUP_FOR_SCAN_STATUSES]
                    for p in to_update:
                        await self._package_exec_repo.update_package_status(
                            package=p,
                            status=PackageStatus.LOADED_FOR_DELIVERY,
                            actor_user_id=None,
                        )
                        await self._package_exec_repo.create_scan_log(
                            route_id=route_id,
                            route_stop_id=stop.id,
                            delivery_stop_id=delivery_stop_id,
                            package_id=p.id,
                            driver_id=driver_id,
                            scan_value=scan_value,
                            result="MASTER_LABEL_MATCHED",
                        )
                    await self._driver_repo.session.flush()
                    for p in to_update:
                        await self._driver_repo.session.refresh(p)
                    head = to_update[-1] if to_update else packages[-1]
                    if not to_update:
                        await self._package_exec_repo.create_scan_log(
                            route_id=route_id,
                            route_stop_id=stop.id,
                            delivery_stop_id=delivery_stop_id,
                            package_id=None,
                            driver_id=driver_id,
                            scan_value=scan_value,
                            result="MASTER_LABEL_ALREADY_COLLECTED",
                        )
                    return {
                        "package_id": head.id,
                        "reference_number": head.package_id,
                        "status": head.status,
                        "matched_by": "MASTER_LABEL",
                        "master_label_id": order.master_label_id,
                        "packages_confirmed": len(to_update),
                    }

        await self._package_exec_repo.create_scan_log(
            route_id=route_id,
            route_stop_id=stop.id,
            delivery_stop_id=delivery_stop_id,
            package_id=None,
            driver_id=driver_id,
            scan_value=scan_value,
            result="NOT_FOUND",
        )
        raise ValidationError("Package not found for scanned value")

    async def list_stop_pending_packages(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
    ) -> dict[str, object]:
        stop, delivery_stop_id = await self._resolve_execution_context(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        packages = await self._package_exec_repo.list_for_delivery_stop(delivery_stop_id)
        flow = self._route_stop_flow_value(stop)
        if flow == RouteStopFlowType.PICKUP.value:
            pending_items = [
                {
                    "package_id": p.id,
                    "reference_number": p.package_id,
                    "status": p.status,
                }
                for p in packages
                if p.status in PACKAGE_PRE_PICKUP_FOR_SCAN_STATUSES
            ]
        elif flow == RouteStopFlowType.RETURN.value:
            pending_items = [
                {
                    "package_id": p.id,
                    "reference_number": p.package_id,
                    "status": p.status,
                }
                for p in packages
                if p.status not in PACKAGE_STOP_RETURN_HUB_COMPLETE_STATUSES
            ]
        else:
            pending_items = [
                {
                    "package_id": p.id,
                    "reference_number": p.package_id,
                    "status": p.status,
                }
                for p in packages
                if p.status not in PACKAGE_STOP_DELIVERY_OUTCOME_STATUSES
            ]
        return {
            "route_id": route_id,
            "stop_id": stop_id,
            "delivery_stop_id": delivery_stop_id,
            "items": pending_items,
        }

    async def get_stop_package_progress(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
    ) -> dict[str, object]:
        stop, delivery_stop_id = await self._resolve_execution_context(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        from app.modules.orders.models import DeliveryStop

        dstop = (await sess.execute(select(DeliveryStop).where(DeliveryStop.id == delivery_stop_id))).scalars().first()
        packages = await self._package_exec_repo.list_for_delivery_stop(delivery_stop_id)
        total = len(packages)
        flow = self._route_stop_flow_value(stop)
        pending_ids = self._pending_package_ids_for_stop_flow(flow=flow, packages=packages)
        pending_count = len(pending_ids)
        scanned = total - pending_count
        completion_percent = int((scanned * 100) / total) if total > 0 else 0
        first = (getattr(dstop, "recipient_first_name", None) or "").strip() if dstop else ""
        last = (getattr(dstop, "recipient_last_name", None) or "").strip() if dstop else ""
        stop_name = " ".join(p for p in (first, last) if p) or None
        order = await sess.get(Order, dstop.order_id) if dstop is not None else None
        return {
            "route_id": route_id,
            "stop_id": stop_id,
            "delivery_stop_id": delivery_stop_id,
            "stop_name": stop_name,
            "tracking_id": getattr(dstop, "tracking_id", None),
            "stop_flow_type": flow,
            "master_label_id": order.master_label_id if order is not None else None,
            "packages_to_scan": total,
            "scanned_packages": scanned,
            "completion_percent": completion_percent,
        }

    async def _validate_notes_ack_gate(self, *, route_id: str, stop_id: str, driver_id: str) -> None:
        notes_payload = await self.get_stop_notes_payload(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        if bool(notes_payload["requires_acknowledgement"]) and not bool(notes_payload["acknowledged"]):
            raise ValidationError("NOTES_ACK_REQUIRED")

    async def set_stop_package_status(
        self,
        *,
        route_id: str,
        stop_id: str,
        package_id: str,
        driver_id: str,
        status: str,
        notes: str | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> dict[str, object]:
        stop, delivery_stop_id = await self._resolve_execution_context(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        await self._validate_notes_ack_gate(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        package = await self._package_exec_repo.get_by_id_or_404(package_id)
        if package.delivery_stop_id != delivery_stop_id:
            raise ValidationError("This package does not belong to the selected stop")
        flow = self._route_stop_flow_value(stop)

        if flow == RouteStopFlowType.PICKUP.value:
            raise ValidationError(
                "Package status cannot be set on pickup stops; use package scan or master-label scan",
            )

        st = self._package_status_from_driver_patch_request(flow=flow, status_raw=status)

        if flow == RouteStopFlowType.RETURN.value:
            if package.status in PACKAGE_STOP_RETURN_HUB_COMPLETE_STATUSES and package.status != st:
                raise ValidationError(
                    "Return outcome is already recorded for this package; contact dispatch to change it",
                )
        package = await self._package_exec_repo.update_package_status(
            package=package,
            status=st,
            actor_user_id=audit_user_id,
        )
        audit_payload: dict[str, object] = {
            "route_id": route_id,
            "route_stop_id": stop.id,
            "delivery_stop_id": delivery_stop_id,
            "status": status,
        }
        if notes:
            audit_payload["notes"] = notes
        await self._log_audit(
            "driver.package.status.finalize",
            entity_id=package.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value=audit_payload,
            severity="NOTICE",
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.ACCOUNT_UPDATED,
        )
        return {
            "package_id": package.id,
            "status": package.status,
        }

    RETURN_STOP_BATCH_MAX_PACKAGES = 100

    async def set_return_stop_packages_status_batch(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
        package_ids: list[str],
        status: str,
        notes: str | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> dict[str, object]:
        """Apply the same return-hub terminal status to many packages in one request (return stops only)."""
        stop, delivery_stop_id = await self._resolve_execution_context(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        await self._validate_notes_ack_gate(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        flow = self._route_stop_flow_value(stop)
        if flow != RouteStopFlowType.RETURN.value:
            raise ValidationError(
                "Batch package status is only supported on return stops. "
                "For delivery stops use PATCH …/packages/{package_id}/status per package; "
                "for pickup stops use scans and complete the stop.",
            )

        unique = list(dict.fromkeys(pid.strip() for pid in package_ids if pid and pid.strip()))
        if not unique:
            raise ValidationError("package_ids must include at least one package id")
        if len(unique) > self.RETURN_STOP_BATCH_MAX_PACKAGES:
            raise ValidationError(
                f"At most {self.RETURN_STOP_BATCH_MAX_PACKAGES} distinct packages are allowed per batch request",
            )

        st = self._package_status_from_driver_patch_request(flow=flow, status_raw=status)
        packages = await self._package_exec_repo.list_for_delivery_stop_by_package_ids(delivery_stop_id, unique)
        if len(packages) != len(unique):
            raise ValidationError("One or more package_ids are not on this stop or do not exist")
        by_id = {p.id: p for p in packages}
        ordered = [by_id[i] for i in unique]

        for package in ordered:
            if package.status in PACKAGE_STOP_RETURN_HUB_COMPLETE_STATUSES and package.status != st:
                raise ValidationError(
                    "Return outcome is already recorded for one or more packages; contact dispatch to change it",
                )

        items: list[dict[str, str]] = []
        for package in ordered:
            updated = await self._package_exec_repo.update_package_status(
                package=package,
                status=st,
                actor_user_id=audit_user_id,
            )
            audit_payload: dict[str, object] = {
                "route_id": route_id,
                "route_stop_id": stop.id,
                "delivery_stop_id": delivery_stop_id,
                "status": status,
                "batch": True,
            }
            if notes:
                audit_payload["notes"] = notes
            await self._log_audit(
                "driver.package.status.finalize",
                entity_id=updated.id,
                user_id=audit_user_id,
                user_role=audit_user_role,
                new_value=audit_payload,
                severity="NOTICE",
                category=AuditCategory.DOCUMENT,
                event_type=AuditEventType.ACCOUNT_UPDATED,
            )
            items.append({"package_id": updated.id, "status": str(updated.status)})

        return {"items": items, "updated_count": len(items)}

    async def report_missing_package(
        self,
        *,
        route_id: str,
        stop_id: str,
        package_id: str,
        driver_id: str,
        reason_code: str,
        details: str | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> dict[str, object]:
        stop, delivery_stop_id = await self._resolve_execution_context(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        package = await self._package_exec_repo.get_by_id_or_404(package_id)
        if package.delivery_stop_id != delivery_stop_id:
            raise ValidationError("This package does not belong to the selected stop")
        await self._validate_notes_ack_gate(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        package = await self._package_exec_repo.update_package_status(
            package=package,
            status=PackageStatus.MISSING,
            actor_user_id=audit_user_id,
        )
        report = await self._package_exec_repo.create_missing_report(
            package_id=package.id,
            route_id=route_id,
            route_stop_id=stop.id,
            delivery_stop_id=delivery_stop_id,
            driver_id=driver_id,
            reason_code=reason_code,
            details=details,
        )
        await self._log_audit(
            "driver.package.missing.report",
            entity_id=package.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={"reason_code": reason_code, "report_id": report.id},
            severity="NOTICE",
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.CONTRACT_EXECUTED,
        )
        return {
            "package_id": package.id,
            "status": package.status,
            "reason_code": reason_code,
            "report_id": report.id,
        }

    async def create_stop_pod_upload_url(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
        uploads: list[UploadFile],
    ) -> dict[str, object]:
        _stop, delivery_stop_id = await self._resolve_execution_context(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        pod = await self._stop_exec_repo.get_or_create_stop_pod(delivery_stop_id)
        photos = await self._stop_exec_repo.list_pod_photos(delivery_stop_id)
        if not uploads:
            raise ValidationError("At least one photo is required")
        if len(uploads) > 5:
            raise ValidationError("Maximum 5 photos can be uploaded per request")
        if len(photos) + len(uploads) > 5:
            raise ValidationError("Maximum of 5 POD photos is allowed")

        allowed = {"image/jpeg", "image/png"}
        client = get_images_client()
        created_items: list[dict[str, object]] = []
        next_sort = len(photos) + 1
        for upload in uploads:
            if (upload.content_type or "").lower() not in allowed:
                raise ValidationError("Unsupported image type; only JPEG and PNG are allowed")
            data = await self._validate_upload(upload, allowed_content_types=allowed, max_bytes=5 * 1024 * 1024)
            result = await client.upload_image(
                BytesIO(data),
                filename=upload.filename or "pod-photo",
                require_signed_urls=True,
                metadata={"kind": "driver_stop_pod_photo", "driver_id": driver_id, "delivery_stop_id": delivery_stop_id},
            )
            photo = await self._stop_exec_repo.create_pod_photo(
                delivery_stop_id=delivery_stop_id,
                image_key=result.id,
                sort_order=next_sort,
                uploaded_by_driver_id=driver_id,
            )
            created_items.append(
                {
                    "id": photo.id,
                    "image_id": photo.image_key,
                    "image_url": self.get_profile_photo_url(photo.image_key),
                    "sort_order": int(photo.sort_order or 0),
                }
            )
            next_sort += 1
        pod.photos_count = len(photos) + len(created_items)
        await self._driver_repo.session.flush()
        return {
            "delivery_stop_id": delivery_stop_id,
            "items": created_items,
            "photos_count": int(pod.photos_count),
        }

    async def list_stop_pod_photos(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
    ) -> dict[str, object]:
        _stop, delivery_stop_id = await self._resolve_execution_context(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        photos = await self._stop_exec_repo.list_pod_photos(delivery_stop_id)
        return {
            "delivery_stop_id": delivery_stop_id,
            "photos_count": len(photos),
            "items": [
                {
                    "id": p.id,
                    "image_id": p.image_key,
                    "image_url": self.get_profile_photo_url(p.image_key),
                    "sort_order": int(p.sort_order or 0),
                }
                for p in photos
            ],
        }

    async def confirm_stop_pod_photo(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
        image_key: str,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> dict[str, object]:
        _stop, delivery_stop_id = await self._resolve_execution_context(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        pod = await self._stop_exec_repo.get_or_create_stop_pod(delivery_stop_id)
        photos = await self._stop_exec_repo.list_pod_photos(delivery_stop_id)
        if len(photos) >= 5:
            raise ValidationError("Maximum of 5 POD photos is allowed")
        existing_keys = {p.image_key for p in photos}
        if image_key not in existing_keys:
            await self._stop_exec_repo.create_pod_photo(
                delivery_stop_id=delivery_stop_id,
                image_key=image_key,
                sort_order=len(photos) + 1,
                uploaded_by_driver_id=driver_id,
            )
            pod.photos_count = len(photos) + 1
            await self._driver_repo.session.flush()
        await self._log_audit(
            "driver.stop.pod.photo.add",
            entity_id=delivery_stop_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={"image_key": image_key},
            severity="NOTICE",
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.CONTRACT_EXECUTED,
        )
        return {"delivery_stop_id": delivery_stop_id, "photos_count": int(pod.photos_count)}

    async def delete_stop_pod_photo(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
        photo_id: str,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> dict[str, object]:
        _stop, delivery_stop_id = await self._resolve_execution_context(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        deleted = await self._stop_exec_repo.delete_pod_photo(delivery_stop_id=delivery_stop_id, photo_id=photo_id)
        if not deleted:
            raise NotFoundError(resource="stop_pod_photo", id=photo_id)
        pod = await self._stop_exec_repo.get_or_create_stop_pod(delivery_stop_id)
        photos = await self._stop_exec_repo.list_pod_photos(delivery_stop_id)
        pod.photos_count = len(photos)
        await self._driver_repo.session.flush()
        await self._log_audit(
            "driver.stop.pod.photo.delete",
            entity_id=delivery_stop_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={"photo_id": photo_id},
            severity="NOTICE",
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.CONTRACT_EXECUTED,
        )
        return {"delivery_stop_id": delivery_stop_id, "photos_count": int(pod.photos_count)}

    async def save_stop_signature(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
        signature_image_key: str,
        signature_required: bool | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> dict[str, object]:
        _stop, delivery_stop_id = await self._resolve_execution_context(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        pod = await self._stop_exec_repo.get_or_create_stop_pod(delivery_stop_id)
        pod.signature_image_key = signature_image_key
        if signature_required is not None:
            pod.signature_required_snapshot = signature_required
        await self._driver_repo.session.flush()
        await self._log_audit(
            "driver.stop.signature.save",
            entity_id=delivery_stop_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={"signature_image_key": signature_image_key},
            severity="NOTICE",
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.CONTRACT_EXECUTED,
        )
        return {
            "delivery_stop_id": delivery_stop_id,
            "signature_image_key": signature_image_key,
            "signature_required": bool(pod.signature_required_snapshot),
        }

    async def _build_stop_delivery_readiness(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
    ) -> dict[str, object]:
        stop, delivery_stop_id = await self._resolve_execution_context(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        flow = self._route_stop_flow_value(stop)
        notes_payload = await self.get_stop_notes_payload(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        packages = await self._package_exec_repo.list_for_delivery_stop(delivery_stop_id)
        pending = self._pending_package_ids_for_stop_flow(flow=flow, packages=packages)
        pod = await self._stop_exec_repo.get_or_create_stop_pod(delivery_stop_id)
        photos = await self._stop_exec_repo.list_pod_photos(delivery_stop_id)
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        from app.modules.orders.models import DeliveryStop as DeliveryStopModel

        dstop = await sess.get(DeliveryStopModel, delivery_stop_id)
        signature_required = bool(pod.signature_required_snapshot or (dstop is not None and dstop.signature_required))
        return_needs_pod = flow == RouteStopFlowType.RETURN.value and any(
            p.status in PACKAGE_RETURN_FLOW_REQUIRES_STOP_POD_STATUSES for p in packages
        )
        if flow == RouteStopFlowType.DELIVERY.value:
            pod_ok = 1 <= len(photos) <= 5
            signature_ok = (not signature_required) or bool(pod.signature_image_key)
        elif flow == RouteStopFlowType.RETURN.value:
            pod_ok = (1 <= len(photos) <= 5) if return_needs_pod else True
            signature_ok = True
        else:
            pod_ok = True
            signature_ok = True
        order = await sess.get(Order, dstop.order_id) if dstop is not None else None
        master_label_id = order.master_label_id if order is not None and flow == RouteStopFlowType.PICKUP.value else None
        requires_ack = bool(notes_payload["requires_acknowledgement"])
        stop_pod_required = flow == RouteStopFlowType.DELIVERY.value or return_needs_pod
        return {
            "route_id": route_id,
            "stop_id": stop_id,
            "delivery_stop_id": delivery_stop_id,
            "stop_flow_type": flow,
            "master_label_id": master_label_id,
            "return_requires_pod": return_needs_pod,
            "notes_ok": (not requires_ack) or bool(notes_payload["acknowledged"]),
            "packages_ok": len(pending) == 0,
            "pod_ok": pod_ok,
            "signature_ok": signature_ok,
            "pending_package_ids": pending,
            "photo_count": len(photos),
            "signature_required": signature_required,
            "notes_hash": notes_payload["notes_hash"],
            "acknowledged": notes_payload["acknowledged"],
            "requires_acknowledgement": requires_ack,
            "signature_captured": bool(pod.signature_image_key),
            "stop_pod_required": stop_pod_required,
        }

    async def get_stop_delivery_readiness(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
    ) -> dict[str, object]:
        r = await self._build_stop_delivery_readiness(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        return {k: r[k] for k in (
            "route_id",
            "stop_id",
            "delivery_stop_id",
            "stop_flow_type",
            "master_label_id",
            "return_requires_pod",
            "notes_ok",
            "packages_ok",
            "pod_ok",
            "signature_ok",
            "pending_package_ids",
            "photo_count",
            "signature_required",
            "notes_hash",
            "acknowledged",
        )}

    async def get_stop_readiness_gate_notes(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
    ) -> dict[str, object]:
        r = await self._build_stop_delivery_readiness(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        return {
            "route_id": r["route_id"],
            "stop_id": r["stop_id"],
            "delivery_stop_id": r["delivery_stop_id"],
            "ok": r["notes_ok"],
            "requires_acknowledgement": r["requires_acknowledgement"],
            "acknowledged": r["acknowledged"],
            "notes_hash": r["notes_hash"],
        }

    async def get_stop_readiness_gate_packages(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
    ) -> dict[str, object]:
        r = await self._build_stop_delivery_readiness(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        return {
            "route_id": r["route_id"],
            "stop_id": r["stop_id"],
            "delivery_stop_id": r["delivery_stop_id"],
            "stop_flow_type": r["stop_flow_type"],
            "ok": r["packages_ok"],
            "pending_package_ids": r["pending_package_ids"],
        }

    async def get_stop_readiness_gate_pod(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
    ) -> dict[str, object]:
        r = await self._build_stop_delivery_readiness(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        return {
            "route_id": r["route_id"],
            "stop_id": r["stop_id"],
            "delivery_stop_id": r["delivery_stop_id"],
            "stop_flow_type": r["stop_flow_type"],
            "ok": r["pod_ok"],
            "photo_count": r["photo_count"],
            "return_requires_pod": r["return_requires_pod"],
            "stop_pod_required": r["stop_pod_required"],
            "min_photos_when_required": 1,
            "max_photos_allowed": 5,
        }

    async def get_stop_readiness_gate_signature(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
    ) -> dict[str, object]:
        r = await self._build_stop_delivery_readiness(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        return {
            "route_id": r["route_id"],
            "stop_id": r["stop_id"],
            "delivery_stop_id": r["delivery_stop_id"],
            "stop_flow_type": r["stop_flow_type"],
            "ok": r["signature_ok"],
            "signature_required": r["signature_required"],
            "captured": r["signature_captured"],
        }

    async def complete_stop_delivery(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
        notes: str | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> dict[str, object]:
        stop, delivery_stop_id = await self._resolve_execution_context(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        readiness = await self.get_stop_delivery_readiness(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        if not bool(readiness["notes_ok"]):
            raise ValidationError("NOTES_ACK_REQUIRED")
        if not bool(readiness["packages_ok"]):
            raise ValidationError("STOP_NOT_READY")
        if not bool(readiness["pod_ok"]):
            raise ValidationError("POD_INCOMPLETE")
        if not bool(readiness["signature_ok"]):
            raise ValidationError("SIGNATURE_REQUIRED")
        pod = await self._stop_exec_repo.get_or_create_stop_pod(delivery_stop_id)
        pod.completed_at = datetime.now(UTC)
        stop.status = RouteStopStatus.COMPLETED.value
        if stop.actual_arrival is None:
            stop.actual_arrival = datetime.now(UTC)
        if notes is not None:
            stop.notes = notes
        await self._driver_repo.session.flush()
        await self._log_audit(
            "driver.stop.complete",
            entity_id=stop.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={"route_id": route_id, "status": stop.status},
            severity="NOTICE",
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.CONTRACT_EXECUTED,
        )
        return {
            "stop_id": stop.id,
            "status": stop.status,
            "message": "Stop marked as completed",
            "readiness": readiness,
        }

    @staticmethod
    def _is_important_note(note: dict[str, object]) -> bool:
        note_type = str(note.get("note_type") or "").strip().upper()
        return bool(note.get("is_blocking")) or note_type in {"IMPORTANT", "URGENT", "CRITICAL"}

    @staticmethod
    def _package_issue_stop_note_images_payload(raw: object) -> list[dict[str, object | None]]:
        if not isinstance(raw, list):
            return []
        out: list[dict[str, object | None]] = []
        for img in raw:
            if not isinstance(img, dict):
                continue
            key = str(img.get("image_key") or "").strip()
            out.append(
                {
                    "id": str(img.get("id") or ""),
                    "image_key": key,
                    "sort_order": int(img.get("sort_order") or 0),
                    "image_url": generate_image_url(key) if key else None,
                }
            )
        return out

    async def get_important_delivery_note(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
    ) -> dict[str, object]:
        notes_payload = await self.get_stop_notes_payload(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        important = [item for item in notes_payload.get("items", []) if isinstance(item, dict) and self._is_important_note(item)]
        return {
            "route_id": notes_payload["route_id"],
            "stop_id": notes_payload["stop_id"],
            "delivery_stop_id": notes_payload["delivery_stop_id"],
            "notes_hash": notes_payload["notes_hash"],
            "requires_acknowledgement": notes_payload["requires_acknowledgement"],
            "acknowledged": notes_payload["acknowledged"],
            "acknowledged_at": notes_payload["acknowledged_at"],
            "items": important,
        }

    async def get_delivery_detail_payload(
        self,
        *,
        route_id: str,
        stop_id: str,
        driver_id: str,
    ) -> dict[str, object]:
        stop, delivery_stop_id = await self._resolve_execution_context(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        stops = await self.list_route_stops_for_driver(route_id=route_id, driver_id=driver_id)
        stop_row = next((row for row in stops if str(row.get("stop_id")) == stop.id), None)
        if stop_row is None:
            raise NotFoundError(resource="route_stop", id=stop_id)
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        from app.modules.orders.models import DeliveryStop as DeliveryStopModel

        dstop = await sess.get(DeliveryStopModel, delivery_stop_id)
        if dstop is None:
            raise NotFoundError(resource="delivery_stop", id=delivery_stop_id)
        packages = await self._package_exec_repo.list_for_delivery_stop(delivery_stop_id)
        notes = await self.get_stop_notes_payload(route_id=route_id, stop_id=stop_id, driver_id=driver_id)
        admin_note = next(
            (
                str(n.get("message"))
                for n in notes.get("items", [])
                if isinstance(n, dict) and str(n.get("note_type") or "").upper() == "ADMIN"
            ),
            None,
        )
        customer_note = next(
            (
                str(n.get("message"))
                for n in notes.get("items", [])
                if isinstance(n, dict) and str(n.get("note_type") or "").upper() == "CUSTOMER"
            ),
            None,
        )
        package_issue_stop_notes = [
            {
                "message": str(n.get("message") or ""),
                "package_ids": list(n.get("package_ids") or []) if isinstance(n.get("package_ids"), list) else [],
                "images": self._package_issue_stop_note_images_payload(n.get("images")),
            }
            for n in notes.get("items", [])
            if isinstance(n, dict) and str(n.get("note_type") or "").upper() == "PACKAGE_ISSUE_NOTE"
        ]
        weight_total = 0.0
        package_breakdown: list[dict[str, object]] = []
        has_damaged = False
        for p in packages:
            weight_value = float(p.weight_kg or p.declared_weight_kg or 0)
            weight_total += weight_value
            has_damaged = has_damaged or bool(p.is_damaged)
            size_value = None
            if p.length_cm is not None and p.width_cm is not None and p.height_cm is not None:
                size_value = f"{int(p.length_cm)} x {int(p.width_cm)} x {int(p.height_cm)} cm"
            package_breakdown.append(
                {
                    "package_id": p.id,
                    "size": size_value,
                    "weight": f"{weight_value:g} kg" if weight_value > 0 else None,
                }
            )
        missing_reports = list(
            (
                await sess.execute(
                    select(PackageMissingReport)
                    .where(PackageMissingReport.delivery_stop_id == delivery_stop_id)
                    .order_by(PackageMissingReport.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        latest_missing = missing_reports[0] if missing_reports else None
        package_issue_description = None
        if latest_missing is not None:
            package_issue_description = str(latest_missing.details or latest_missing.reason_code)

        def _to_status(value: object) -> str:
            normalized = str(value or "").upper()
            if normalized == "COMPLETED":
                return "COMPLETED"
            if normalized in {"ARRIVED", "ACTIVE", "IN_PROGRESS"}:
                return "IN-PROGRESS"
            return "PENDING"

        def _to_time_string(dt: datetime | None) -> str | None:
            if dt is None:
                return None
            hour = dt.hour % 12 or 12
            suffix = "am" if dt.hour < 12 else "pm"
            return f"{hour}:{dt.minute:02d} {suffix}"

        return {
            "location": stop_row.get("name"),
            "trackingId": stop_row.get("tracking_id"),
            "postalCode": stop_row.get("postal_code"),
            "status": _to_status(stop_row.get("status")),
            "estimatedDeliveryTime": _to_time_string(stop_row.get("estimated_delivery_time")),
            "actualDeliveryTime": _to_time_string(stop_row.get("actual_delivery_time")),
            "packagesCount": len(packages),
            "show_admin_note": bool(admin_note),
            "show_customer_note": bool(customer_note),
            "show_package_issue_stop_notes": bool(package_issue_stop_notes),
            "package_issue_stop_notes": package_issue_stop_notes,
            "show_signature_required": bool(dstop.signature_required),
            "show_safe_place_allowed": bool(dstop.safe_place_allowed),
            "admin_note": {"text": admin_note} if admin_note else None,
            "customer_note": {"text": customer_note} if customer_note else None,
            "package_issue": {
                "hasIssue": bool(has_damaged or latest_missing is not None),
                "description": package_issue_description,
                "thumbnail_image": None,
                "images": [],
            },
            "packages_summary": {
                "totalPackages": len(packages),
                "totalWeight": f"{weight_total:g} kg",
            },
            "package_breakdown": package_breakdown,
            "signature_required": {
                "required": bool(dstop.signature_required),
                "message": (
                    "Customer must sign upon delivery."
                    if dstop.signature_required
                    else "Customer signature is not required for this delivery."
                ),
            },
            "safe_place_allowed": {
                "required": bool(dstop.safe_place_allowed),
                "message": (
                    "Safe place delivery is allowed for this stop."
                    if dstop.safe_place_allowed
                    else "This package must be handed over in person."
                ),
            },
        }

    @staticmethod
    def _paginate_rows(rows: list[dict], *, page: int, size: int) -> tuple[list[dict], int]:
        total = len(rows)
        start = max(page - 1, 0) * size
        end = start + size
        return rows[start:end], total

    @staticmethod
    def _route_event_row_dict(e: RouteEvent, route_code: str | None) -> dict:
        metadata = DriverService._event_metadata_dict(e)
        speed_mph = DriverService._safe_float(metadata.get("speed_mph"))
        limit_mph = DriverService._safe_float(metadata.get("limit_mph"))
        speed_over_mph = DriverService._safe_float(metadata.get("speed_over_mph"))
        if speed_over_mph is None and speed_mph is not None and limit_mph is not None:
            speed_over_mph = speed_mph - limit_mph

        return {
            "id": e.id,
            "route_id": e.route_id,
            "driver_id": e.driver_id,
            "route_code": metadata.get("route_code") or route_code,
            "event_type": e.event_type,
            "occurred_at": e.occurred_at,
            "location_text": metadata.get("location_text"),
            "distance_miles": DriverService._safe_float(metadata.get("distance_miles")),
            "speed_mph": speed_mph,
            "limit_mph": limit_mph,
            "speed_over_mph": speed_over_mph,
            "start_speed_mph": DriverService._safe_float(metadata.get("start_speed_mph")),
            "end_speed_mph": DriverService._safe_float(metadata.get("end_speed_mph")),
            "severity": metadata.get("severity"),
            "lat": e.lat,
            "lng": e.lng,
            "metadata": metadata,
        }

    async def get_above_70_mph_report(
        self,
        *,
        route_id: str,
        driver_id: str,
        page: int,
        size: int,
    ) -> tuple[list[dict], int]:
        await self.ensure_route_owned_by_driver(route_id=route_id, driver_id=driver_id)
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        threshold = self._TELEMETRY_SPEEDING_THRESHOLD_MPH
        speed_mph_json = cast(RouteEvent.event_metadata["speed_mph"].astext, Float)
        base_stmt = (
            select(RouteEvent, Route.route_code)
            .join(Route, Route.id == RouteEvent.route_id)
            .where(
                RouteEvent.route_id == route_id,
                Route.driver_id == driver_id,
                RouteEvent.event_type == "SPEEDING",
                speed_mph_json > threshold,
            )
        )
        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        total = int((await sess.execute(count_stmt)).scalar_one() or 0)
        stmt = (
            base_stmt.order_by(RouteEvent.occurred_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        events = list((await sess.execute(stmt)).all())
        rows = [self._route_event_row_dict(e, route_code) for e, route_code in events]
        return rows, total

    async def get_sharp_brake_report(
        self,
        *,
        route_id: str,
        driver_id: str,
        page: int,
        size: int,
    ) -> tuple[list[dict], int]:
        await self.ensure_route_owned_by_driver(route_id=route_id, driver_id=driver_id)
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        base_stmt = (
            select(RouteEvent, Route.route_code)
            .join(Route, Route.id == RouteEvent.route_id)
            .where(
                RouteEvent.route_id == route_id,
                Route.driver_id == driver_id,
                RouteEvent.event_type == "HARSH_BRAKING",
            )
        )
        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        total = int((await sess.execute(count_stmt)).scalar_one() or 0)
        stmt = (
            base_stmt.order_by(RouteEvent.occurred_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        events = list((await sess.execute(stmt)).all())
        rows = [self._route_event_row_dict(e, route_code) for e, route_code in events]
        return rows, total

    async def get_average_route_speed_payload(
        self,
        *,
        route_id: str,
        driver_id: str,
    ) -> dict[str, object]:
        route = await self._get_route_for_driver_or_404(route_id=route_id, driver_id=driver_id)
        rows, _ = await self.list_route_events_payload(route_id=route_id, event_type=["LOCATION_PING"], page=1, size=10000)
        total_distance_km = float(route.total_distance_km or 0)
        actual_drive_time_min = float(route.actual_drive_time_min or 0)
        average_speed_mph: float | None = None
        if total_distance_km > 0 and actual_drive_time_min > 0:
            average_speed_kph = total_distance_km / (actual_drive_time_min / 60.0)
            average_speed_mph = round(average_speed_kph * 0.621371, 1)
        return {
            "route_id": route.id,
            "route_code": route.route_code,
            "total_distance_km": total_distance_km,
            "actual_drive_time_min": actual_drive_time_min,
            "average_speed_mph": average_speed_mph,
            "location_points_count": len(rows),
        }

    async def _latest_location_ping_for_route(self, *, route_id: str) -> RouteEvent | None:
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        stmt = (
            select(RouteEvent)
            .where(
                RouteEvent.route_id == route_id,
                RouteEvent.event_type == "LOCATION_PING",
                RouteEvent.lat.isnot(None),
                RouteEvent.lng.isnot(None),
            )
            .order_by(RouteEvent.occurred_at.desc())
            .limit(1)
        )
        return (await sess.execute(stmt)).scalars().first()

    async def _earliest_location_ping_for_route(self, *, route_id: str) -> RouteEvent | None:
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        stmt = (
            select(RouteEvent)
            .where(
                RouteEvent.route_id == route_id,
                RouteEvent.event_type == "LOCATION_PING",
                RouteEvent.lat.isnot(None),
                RouteEvent.lng.isnot(None),
            )
            .order_by(RouteEvent.occurred_at.asc())
            .limit(1)
        )
        return (await sess.execute(stmt)).scalars().first()

    @staticmethod
    def _navigation_response_chunk(*, route: Route, current_fingerprint: str) -> dict[str, object]:
        # Reads DB only. Polyline/meta/fingerprint are set by planning/async job after route build
        # or stop reorder (directions provider); see ``app.modules.planning`` navigation comments.
        stored_poly = route.navigation_encoded_polyline
        stored_fp = route.navigation_fingerprint
        raw_meta = route.navigation_meta
        meta_out: dict[str, object] = dict(raw_meta) if isinstance(raw_meta, dict) else {}
        poly_out = stored_poly
        if poly_out and stored_fp and stored_fp != current_fingerprint:
            poly_out = None
            meta_out = {**meta_out, "polyline_stale": True}
        elif poly_out and not stored_fp:
            meta_out = {**meta_out, "polyline_unverified": True}
        return {
            "encoded_polyline": poly_out,
            "meta": meta_out if meta_out else None,
        }

    async def get_active_driving_map_payload(
        self,
        *,
        route_id: str,
        driver_id: str,
    ) -> dict[str, object]:
        """Drive-mode snapshot: stops, pings, cached ``Route`` navigation (no directions API here)."""
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        route_stmt = (
            select(Route)
            .options(selectinload(Route.vehicle))
            .where(Route.id == route_id, Route.driver_id == driver_id)
        )
        route = (await sess.execute(route_stmt)).scalars().first()
        if route is None:
            raise NotFoundError(resource="route", id=route_id)
        stops = await self.list_route_stops_for_driver(route_id=route_id, driver_id=driver_id)

        fp_pairs = [(int(row["sequence"]), str(row["stop_id"])) for row in stops]
        current_fingerprint = compute_route_navigation_fingerprint(sequences_and_route_stop_ids=fp_pairs)

        latest_ping = await self._latest_location_ping_for_route(route_id=route_id)
        earliest_ping = await self._earliest_location_ping_for_route(route_id=route_id)

        def _to_status(value: object) -> str:
            normalized = str(value or "").upper()
            if normalized == "COMPLETED":
                return "COMPLETED"
            if normalized in {"ARRIVED", "ACTIVE", "IN_PROGRESS"}:
                return "ONROUTE"
            return "PENDING"

        data_rows: list[dict[str, object]] = []
        for row in stops:
            tid = row.get("tracking_id")
            data_rows.append(
                {
                    "stop_id": row.get("stop_id"),
                    "sequence": int(row.get("sequence") or 0),
                    "stop_flow_type": row.get("stop_flow_type"),
                    "tracking_id": tid,
                    "location": row.get("name"),
                    "longitude": row.get("longitude"),
                    "latitude": row.get("latitude"),
                    "packages_count": int(row.get("packages_count") or 0),
                    "status": _to_status(row.get("status")),
                }
            )

        vehicle: dict[str, object | None] = {
            "latitude": float(latest_ping.lat) if latest_ping and latest_ping.lat is not None else None,
            "longitude": float(latest_ping.lng) if latest_ping and latest_ping.lng is not None else None,
            "recorded_at": latest_ping.occurred_at if latest_ping else None,
        }

        # Legacy ``location``: earliest vs latest ping with coordinates on this route (not a sliding window).
        return {
            "location": {
                "start_lat": float(earliest_ping.lat) if earliest_ping and earliest_ping.lat is not None else None,
                "start_long": float(earliest_ping.lng) if earliest_ping and earliest_ping.lng is not None else None,
                "end_lat": float(latest_ping.lat) if latest_ping and latest_ping.lat is not None else None,
                "end_long": float(latest_ping.lng) if latest_ping and latest_ping.lng is not None else None,
            },
            "vehicle": vehicle,
            "navigation": self._navigation_response_chunk(route=route, current_fingerprint=current_fingerprint),
            "data": data_rows,
        }

    async def list_route_events_payload(
        self,
        *,
        route_id: str,
        event_type: list[str] | None,
        page: int,
        size: int,
    ) -> tuple[list[dict], int]:
        """Paginated list of telematics events for a route."""
        sess = self._driver_repo.session  # type: ignore[attr-defined]

        stmt = select(RouteEvent, Route.route_code).join(Route, Route.id == RouteEvent.route_id).where(RouteEvent.route_id == route_id)
        if event_type:
            normalized = [e.strip().upper() for e in event_type if e and e.strip()]
            if normalized:
                stmt = stmt.where(RouteEvent.event_type.in_(normalized))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await sess.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(RouteEvent.occurred_at.desc())
        stmt = stmt.offset((page - 1) * size).limit(size)

        events = list((await sess.execute(stmt)).all())
        rows = [self._route_event_row_dict(e, route_code) for e, route_code in events]
        return rows, total

    # ── Driver shifts CRUD ──────────────────────────────────────────────────

    async def list_shifts(
        self,
        *,
        driver_id: str | None = None,
        depot_id: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[DriverShift]:
        """List shifts filtered by driver or depot and optional date range."""
        stmt = select(DriverShift)
        if driver_id is not None:
            stmt = stmt.where(DriverShift.driver_id == driver_id)
        if depot_id is not None:
            # DriverShift currently has no depot_id field; keep placeholder for future extension.
            pass
        if date_from is not None:
            stmt = stmt.where(DriverShift.shift_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(DriverShift.shift_date <= date_to)
        stmt = stmt.order_by(DriverShift.shift_date, DriverShift.start_time)
        result = await self._shift_repo.session.execute(stmt)
        return list(result.scalars().all())

    async def get_shift(self, shift_id: str) -> DriverShift:
        """Get a single driver shift by ID or raise NotFoundError."""
        return await self._shift_repo.get_by_id_or_404(shift_id)

    async def _ensure_no_shift_conflict(
        self,
        *,
        driver_id: str,
        shift_date: date,
        start_time: time,
        end_time: time,
        exclude_shift_id: str | None = None,
    ) -> None:
        """Ensure there is no overlapping shift for the same driver and date."""
        start_dt = datetime.combine(shift_date, start_time, tzinfo=UTC)
        end_dt = datetime.combine(shift_date, end_time, tzinfo=UTC)

        stmt = select(DriverShift).where(
            DriverShift.driver_id == driver_id,
            DriverShift.shift_date == shift_date,
            DriverShift.start_time < end_dt,
            DriverShift.end_time > start_dt,
        )
        if exclude_shift_id is not None:
            stmt = stmt.where(DriverShift.id != exclude_shift_id)
        result = await self._shift_repo.session.execute(stmt)
        existing = result.scalars().first()
        if existing:
            raise ValidationError("Shift overlaps with an existing shift for this driver")

    async def create_shift(
        self,
        *,
        driver_id: str,
        shift_date: date,
        start_time: time,
        end_time: time,
        shift_type: str,
        status: str = ShiftStatus.PLANNED,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> DriverShift:
        await self._driver_repo.get_by_id_or_404(driver_id)
        if end_time <= start_time:
            raise ValidationError("end_time must be after start_time")
        await self._ensure_no_shift_conflict(
            driver_id=driver_id,
            shift_date=shift_date,
            start_time=start_time,
            end_time=end_time,
        )
        start_dt = datetime.combine(shift_date, start_time, tzinfo=UTC)
        end_dt = datetime.combine(shift_date, end_time, tzinfo=UTC)
        shift = await self._shift_repo.create(
            {
                "driver_id": driver_id,
                "shift_date": shift_date,
                "start_time": start_dt,
                "end_time": end_dt,
                "status": status,
            }
        )
        await self._log_audit(
            "driver.shift.create",
            entity_id=shift.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={
                "driver_id": driver_id,
                "shift_date": shift_date.isoformat(),
                "start_time": start_dt.isoformat(),
                "end_time": end_dt.isoformat(),
                "status": status,
            },
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.SHIFT_CREATED,
        )
        return shift

    async def update_shift(
        self,
        *,
        shift_id: str,
        shift_date: date | None = None,
        start_time: time | None = None,
        end_time: time | None = None,
        status: str | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> DriverShift:
        shift = await self._shift_repo.get_by_id_or_404(shift_id)
        new_date = shift_date or shift.shift_date
        new_start_dt = shift.start_time
        new_end_dt = shift.end_time
        if start_time is not None:
            new_start_dt = datetime.combine(new_date, start_time, tzinfo=UTC)
        if end_time is not None:
            new_end_dt = datetime.combine(new_date, end_time, tzinfo=UTC)
        if new_end_dt <= new_start_dt:
            raise ValidationError("end_time must be after start_time")

        await self._ensure_no_shift_conflict(
            driver_id=shift.driver_id,
            shift_date=new_date,
            start_time=new_start_dt.timetz(),
            end_time=new_end_dt.timetz(),
            exclude_shift_id=shift.id,
        )

        data: dict[str, object] = {}
        if shift_date is not None:
            data["shift_date"] = shift_date
        if start_time is not None:
            data["start_time"] = new_start_dt
        if end_time is not None:
            data["end_time"] = new_end_dt
        if status is not None:
            data["status"] = status
        if not data:
            return shift
        updated = await self._shift_repo.update_by_id(shift_id, data)
        await self._log_audit(
            "driver.shift.update",
            entity_id=shift_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={
                "shift_date": shift.shift_date.isoformat(),
                "start_time": shift.start_time.isoformat(),
                "end_time": shift.end_time.isoformat(),
                "status": shift.status,
            },
            new_value=data,
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.SHIFT_UPDATED,
        )
        return updated

    async def delete_shift(
        self,
        *,
        shift_id: str,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> None:
        shift = await self._shift_repo.get_by_id_or_404(shift_id)
        await self._shift_repo.hard_delete(shift_id)
        await self._log_audit(
            "driver.shift.delete",
            entity_id=shift_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={
                "driver_id": shift.driver_id,
                "shift_date": shift.shift_date.isoformat(),
                "start_time": shift.start_time.isoformat(),
                "end_time": shift.end_time.isoformat(),
            },
            new_value=None,
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.SHIFT_DELETED,
        )

    async def activate_driver_on_login(self, *, user_id: str) -> bool:
        """Activate a driver on their first successful driver-app login.

        When a driver is in PENDING_ACTIVATION, we promote it to ACTIVE exactly once.
        Returns True if a transition happened.
        """
        driver = await self._driver_repo.find_by_user_id(user_id)
        if driver is None:
            return False
        if driver.account_status != DriverAccountStatus.PENDING_ACTIVATION:
            return False

        old_status = driver.account_status
        try:
            updated = await self._driver_repo.update_by_id(
                driver.id,
                {"account_status": DriverAccountStatus.ACTIVE},
                expected_version=driver.version,
            )
        except ConflictError:
            # Another concurrent login updated the driver first.
            # Re-check the latest status and treat it as a successful activation.
            latest = await self._driver_repo.find_by_user_id(user_id)
            return latest is not None and latest.account_status == DriverAccountStatus.ACTIVE

        await self._log_audit(
            "driver.activation.on_login",
            entity_id=updated.id,
            user_id=user_id,
            user_role=None,
            old_value={"account_status": old_status},
            new_value={"account_status": DriverAccountStatus.ACTIVE},
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_ACTIVATED,
        )
        return True

    # ── Account status transitions (suspend / reactivate) ──────────────────

    async def suspend_driver(
        self,
        driver_id: str,
        *,
        reason: str | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Driver:
        driver = await self._driver_repo.get_by_id_or_404(driver_id)
        if driver.account_status == DriverAccountStatus.SUSPENDED:
            return driver
        if driver.account_status not in (DriverAccountStatus.ACTIVE, DriverAccountStatus.PENDING_ACTIVATION, DriverAccountStatus.DRAFT):
            from app.common.exceptions import InvalidStateTransitionError  # local import to avoid cycle

            raise InvalidStateTransitionError(
                current_state=driver.account_status,
                target_state=DriverAccountStatus.SUSPENDED,
                entity="driver",
            )
        old_status = driver.account_status
        updated = await self._driver_repo.update_by_id(
            driver_id,
            {"account_status": DriverAccountStatus.SUSPENDED},
        )
        await self._user_repo.update_by_id(driver.user_id, {"status": UserStatus.SUSPENDED})
        await mark_user_suspended(
            driver.user_id,
            ttl_seconds=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        )
        await self._log_audit(
            "driver.suspend",
            entity_id=driver_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={"account_status": old_status},
            new_value={"account_status": DriverAccountStatus.SUSPENDED},
            severity="CRITICAL",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_DEACTIVATED,
        )
        return updated

    async def reactivate_driver(
        self,
        driver_id: str,
        *,
        reason: str | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Driver:
        driver = await self._driver_repo.get_by_id_or_404(driver_id)
        old_status = driver.account_status
        if old_status == DriverAccountStatus.ACTIVE:
            return driver
        updated = await self._driver_repo.update_by_id(
            driver_id,
            {"account_status": DriverAccountStatus.ACTIVE},
        )
        await self._user_repo.update_by_id(driver.user_id, {"status": UserStatus.ACTIVE})
        await unmark_user_suspended(driver.user_id)
        await self._log_audit(
            "driver.reactivate",
            entity_id=driver_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={"account_status": old_status},
            severity="NOTICE",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_REACTIVATED,
        )
        return updated

    async def hard_delete_driver(
        self,
        driver_id: str,
        *,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> None:
        """Permanently delete driver and linked user, plus best-effort storage cleanup."""
        driver = await self._driver_repo.get_by_id_with_user(driver_id)
        if driver is None:
            raise NotFoundError(resource="driver", id=driver_id)

        profile_photo_key = driver.profile_photo_key
        user_id = driver.user_id

        # Collect R2 file keys before DB hard deletes.
        docs = await self._document_repo.find_all(page=1, size=500, driver_id=driver.id)
        document_keys: list[str] = [doc.file_key for doc in docs[0]]

        violations, _total = await self._violation_repo.find_all_with_proofs(
            page=1,
            size=500,
            driver_id=driver.id,
            order_by="occurred_at",
            order_desc=True,
        )
        proof_keys: list[str] = []
        for v in violations:
            for p in getattr(v, "proofs", []) or []:
                proof_keys.append(p.file_key)

        # Best-effort external deletion.
        for key in document_keys + proof_keys:
            try:
                await delete_from_r2(key)
            except Exception as exc:
                logger.warning(
                    "driver.hard_delete.r2_best_effort_failed",
                    driver_id=driver.id,
                    file_key=key,
                    error=str(exc),
                )

        if profile_photo_key:
            try:
                await delete_image(profile_photo_key)
            except Exception as exc:
                logger.warning(
                    "driver.hard_delete.profile_photo_best_effort_failed",
                    driver_id=driver.id,
                    profile_photo_key=profile_photo_key,
                    error=str(exc),
                )

        # Hard delete DB rows.
        await self._driver_repo.hard_delete(driver.id)
        if user_id:
            await self._user_repo.hard_delete(user_id)

        await self._log_audit(
            "driver.hard_delete",
            entity_id=driver.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={
                "account_status": driver.account_status,
                "user_id": user_id,
            },
            new_value=None,
            severity="CRITICAL",
            category=AuditCategory.ACCOUNT,
            event_type=AuditEventType.ACCOUNT_DEACTIVATED,
        )

    # ── Traffic violations CRUD ─────────────────────────────────────────────

    async def list_traffic_violations(
        self,
        driver_id: str,
        *,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[DriverTrafficViolation], int]:
        """Paginated list of traffic violations for a driver."""
        await self._driver_repo.get_by_id_or_404(driver_id)
        return await self._violation_repo.find_all_with_proofs(page=page, size=size, order_by="occurred_at", order_desc=True, driver_id=driver_id)

    async def get_traffic_violation(self, violation_id: str) -> DriverTrafficViolation:
        """Get a single traffic violation by ID or raise NotFoundError."""
        row = await self._violation_repo.get_by_id_with_proofs(violation_id)
        if row is None:
            raise NotFoundError(resource="driver_traffic_violations", id=violation_id)
        return row

    async def create_traffic_violation(
        self,
        *,
        driver_id: str,
        occurred_at: datetime,
        violation_type: str,
        amount: Decimal,
        status: str,
        notes: str | None,
        proofs: list[UploadFile] | None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> tuple[DriverTrafficViolation, list[dict[str, object]]]:
        await self._driver_repo.get_by_id_or_404(driver_id)
        # Basic validation – ensure status is one of the known enum values.
        try:
            status_value = TrafficViolationStatus(status)
        except ValueError as err:
            raise ValidationError("Invalid traffic violation status; expected PAID or UNPAID") from err
        violation = await self._violation_repo.create(
            {
                "driver_id": driver_id,
                "occurred_at": occurred_at,
                "violation_type": violation_type,
                "amount": amount,
                "status": status_value,
                "notes": notes,
            }
        )
        uploads = list(proofs or [])
        proof_results: list[dict[str, object]] = []
        if uploads:
            if len(uploads) > self._TRAFFIC_VIOLATION_MAX_PROOFS:
                raise ValidationError(f"Too many proofs (max {self._TRAFFIC_VIOLATION_MAX_PROOFS} files)")

            for idx, upload in enumerate(uploads):
                filename = getattr(upload, "filename", None) or "file"
                try:
                    file_key, content_type, size_bytes = await self._upload_driver_file(
                        driver_id=driver_id,
                        upload=upload,
                        prefix=f"traffic-violations/{violation.id}",
                        allowed_content_types=self._TRAFFIC_VIOLATION_ALLOWED_PROOF_TYPES,
                        max_bytes=self._TRAFFIC_VIOLATION_MAX_PROOF_BYTES,
                    )
                    proof = DriverTrafficViolationProof(
                        violation_id=violation.id,
                        file_key=file_key,
                        content_type=content_type,
                        size_bytes=size_bytes,
                    )
                    self._violation_repo.session.add(proof)
                    await self._violation_repo.session.flush([proof])
                    proof_results.append(
                        {
                            "index": idx,
                            "filename": filename,
                            "status": "success",
                            "error": None,
                            "proof_id": proof.id,
                        }
                    )
                except ValidationError as err:
                    proof_results.append(
                        {
                            "index": idx,
                            "filename": filename,
                            "status": "failed",
                            "error": str(err),
                            "proof_id": None,
                        }
                    )
                except StorageProviderError as err:
                    proof_results.append(
                        {
                            "index": idx,
                            "filename": filename,
                            "status": "failed",
                            "error": str(err),
                            "proof_id": None,
                        }
                    )
                except Exception:
                    logger.exception("Traffic violation proof upload failed", driver_id=driver_id, violation_id=str(violation.id))
                    proof_results.append(
                        {
                            "index": idx,
                            "filename": filename,
                            "status": "failed",
                            "error": "Failed to upload proof file",
                            "proof_id": None,
                        }
                    )
        await self._log_audit(
            "driver.traffic_violation.create",
            entity_id=violation.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={
                "driver_id": driver_id,
                "occurred_at": occurred_at.isoformat(),
                "violation_type": violation_type,
                "amount": str(amount),
                "status": status_value,
            },
            severity="WARNING",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.VIOLATION_LOGGED,
        )
        return violation, proof_results

    async def update_traffic_violation(
        self,
        *,
        violation_id: str,
        occurred_at: datetime | None = None,
        violation_type: str | None = None,
        amount: Decimal | None = None,
        status: str | None = None,
        notes: str | None = None,
        proofs: list[UploadFile] | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> tuple[DriverTrafficViolation, list[dict[str, object]]]:
        violation = await self._violation_repo.get_by_id_or_404(violation_id)
        data: dict[str, object] = {}
        if occurred_at is not None:
            data["occurred_at"] = occurred_at
        if violation_type is not None:
            try:
                data["violation_type"] = TrafficViolationType(str(violation_type))
            except ValueError as err:
                raise ValidationError("Invalid traffic violation type") from err
        if amount is not None:
            data["amount"] = amount
        if status is not None:
            try:
                status_value = TrafficViolationStatus(status)
            except ValueError as err:
                raise ValidationError("Invalid traffic violation status; expected PAID or UNPAID") from err
            data["status"] = status_value
        if notes is not None:
            data["notes"] = notes

        proof_results: list[dict[str, object]] = []
        if data:
            updated = await self._violation_repo.update_by_id(violation_id, data)
        else:
            updated = violation

        uploads = list(proofs or [])
        if uploads:
            proof_results = await self.add_traffic_violation_proofs(
                violation_id=violation_id,
                proofs=uploads,
                audit_user_id=audit_user_id,
                audit_user_role=audit_user_role,
            )

        await self._log_audit(
            "driver.traffic_violation.update",
            entity_id=violation_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={
                "occurred_at": violation.occurred_at.isoformat(),
                "violation_type": violation.violation_type,
                "amount": str(violation.amount),
                "status": violation.status,
                "notes": violation.notes,
            },
            new_value={**data, "proofs_added": len(uploads)},
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.VIOLATION_LOGGED,
        )
        return updated, proof_results

    async def add_traffic_violation_proofs(
        self,
        *,
        violation_id: str,
        proofs: list[UploadFile],
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> list[dict[str, object]]:
        # Load with proofs so we can enforce a max total proofs limit.
        violation = await self.get_traffic_violation(violation_id)
        existing_count = len(list(getattr(violation, "proofs", []) or []))
        if existing_count + len(proofs) > self._TRAFFIC_VIOLATION_MAX_PROOFS:
            raise ValidationError(f"Too many proofs (max {self._TRAFFIC_VIOLATION_MAX_PROOFS} files)")

        proof_results: list[dict[str, object]] = []
        for idx, upload in enumerate(proofs):
            filename = getattr(upload, "filename", None) or "file"
            try:
                file_key, content_type, size_bytes = await self._upload_driver_file(
                    driver_id=violation.driver_id,
                    upload=upload,
                    prefix=f"traffic-violations/{violation.id}",
                    allowed_content_types=self._TRAFFIC_VIOLATION_ALLOWED_PROOF_TYPES,
                    max_bytes=self._TRAFFIC_VIOLATION_MAX_PROOF_BYTES,
                )
                proof = DriverTrafficViolationProof(
                    violation_id=violation.id,
                    file_key=file_key,
                    content_type=content_type,
                    size_bytes=size_bytes,
                )
                self._violation_repo.session.add(proof)
                await self._violation_repo.session.flush([proof])
                proof_results.append(
                    {
                        "index": idx,
                        "filename": filename,
                        "status": "success",
                        "error": None,
                        "proof_id": proof.id,
                    }
                )
            except ValidationError as err:
                proof_results.append(
                    {
                        "index": idx,
                        "filename": filename,
                        "status": "failed",
                        "error": str(err),
                        "proof_id": None,
                    }
                )
            except StorageProviderError as err:
                proof_results.append(
                    {
                        "index": idx,
                        "filename": filename,
                        "status": "failed",
                        "error": str(err),
                        "proof_id": None,
                    }
                )
            except Exception:
                logger.exception("Traffic violation proof upload failed", violation_id=str(violation.id))
                proof_results.append(
                    {
                        "index": idx,
                        "filename": filename,
                        "status": "failed",
                        "error": "Failed to upload proof file",
                        "proof_id": None,
                    }
                )
        await self._log_audit(
            "driver.traffic_violation.add_proofs",
            entity_id=violation_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={
                "count": len(proofs),
                "uploaded": sum(1 for r in proof_results if r.get("status") == "success"),
                "failed": sum(1 for r in proof_results if r.get("status") == "failed"),
            },
            severity="INFO",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.VIOLATION_LOGGED,
        )
        return proof_results

    async def delete_traffic_violation_proof(
        self,
        *,
        proof_id: str,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> None:
        proof = await self._violation_proof_repo.get_by_id_or_404(proof_id)
        await self._violation_proof_repo.hard_delete(proof_id)
        await self._log_audit(
            "driver.traffic_violation.proof.delete",
            entity_id=proof_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={"violation_id": proof.violation_id, "file_key": proof.file_key},
            new_value=None,
            severity="NOTICE",
            category=AuditCategory.FLEET,
            event_type=AuditEventType.VIOLATION_LOGGED,
        )

    async def delete_traffic_violation(
        self,
        *,
        violation_id: str,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> None:
        violation = await self._violation_repo.get_by_id_or_404(violation_id)
        await self._violation_repo.hard_delete(violation_id)
        await self._log_audit(
            "driver.traffic_violation.delete",
            entity_id=violation_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={
                "driver_id": violation.driver_id,
                "occurred_at": violation.occurred_at.isoformat(),
                "violation_type": violation.violation_type,
                "amount": str(violation.amount),
            },
            new_value=None,
            severity="WARNING",
            category=AuditCategory.CONTACT,
            event_type=AuditEventType.CONTACT_UPDATED,
        )

    # ── Profile photo (Cloudflare Images) ───────────────────────────────────

    async def update_profile_photo(
        self,
        driver_id: str,
        upload: UploadFile,
        *,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Driver:
        """Upload a new profile photo for the driver via Cloudflare Images."""
        driver = await self._driver_repo.get_by_id_or_404(driver_id)

        # Basic validation: content type and max size (5MB)
        allowed = {"image/jpeg", "image/png"}
        if (upload.content_type or "").lower() not in allowed:
            raise ValidationError("Unsupported image type; only JPEG and PNG are allowed")
        data = await self._validate_upload(upload, allowed_content_types=allowed, max_bytes=5 * 1024 * 1024)

        # Upload to Cloudflare Images
        client = get_images_client()
        result = await client.upload_image(
            BytesIO(data),
            filename=upload.filename or "profile-photo",
            require_signed_urls=True,
            metadata={"kind": "driver_profile_photo", "driver_id": driver_id},
        )

        old_value = {"profile_photo_key": driver.profile_photo_key}
        async with self._driver_repo.session.begin_nested():
            updated = await self._driver_repo.update_by_id(
                driver_id,
                {"profile_photo_key": result.id},
            )
            if driver.user_id:
                await self._user_repo.update_by_id(driver.user_id, {"avatar_url": result.id})
        await self._log_audit(
            "driver.profile_photo.update",
            entity_id=driver_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value=old_value,
            new_value={"profile_photo_key": result.id},
            severity="NOTICE",
            category=AuditCategory.CONTACT,
            event_type=AuditEventType.CONTACT_UPDATED,
        )
        return updated

    async def get_driver_by_user_id(self, user_id: str) -> Driver:
        """Resolve a driver profile from the authenticated user id."""
        driver = await self._driver_repo.find_by_user_id(user_id)
        if driver is None:
            raise NotFoundError(resource="driver", id=user_id)
        # Reload with joined user for response mapping.
        return await self.get_driver(driver.id)

    async def update_driver_self_profile(
        self,
        *,
        user_id: str,
        first_name: str | None = None,
        last_name: str | None = None,
        phone: str | None = None,
        expected_version: int | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Driver:
        """Update driver-owned identity fields for the authenticated driver user (not email)."""
        driver = await self._driver_repo.find_by_user_id(user_id)
        if driver is None:
            raise NotFoundError(resource="driver", id=user_id)

        return await self.update_driver(
            driver.id,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            expected_version=expected_version,
            audit_user_id=audit_user_id,
            audit_user_role=audit_user_role,
        )

    async def remove_profile_photo(
        self,
        driver_id: str,
        *,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Driver:
        """Remove driver profile photo reference and delete the remote image when possible."""
        driver = await self._driver_repo.get_by_id_or_404(driver_id)
        old_key = driver.profile_photo_key
        if old_key:
            try:
                client = get_images_client()
                await client.delete_image(old_key)
            except StorageProviderError:
                logger.warning(
                    "driver_profile_photo_delete_failed",
                    driver_id=driver_id,
                    profile_photo_key=old_key,
                )

        async with self._driver_repo.session.begin_nested():
            updated = await self._driver_repo.update_by_id(
                driver_id,
                {"profile_photo_key": None},
            )
            if driver.user_id:
                await self._user_repo.update_by_id(driver.user_id, {"avatar_url": None})
        await self._log_audit(
            "driver.profile_photo.delete",
            entity_id=driver_id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={"profile_photo_key": old_key},
            new_value={"profile_photo_key": None},
            severity="NOTICE",
            category=AuditCategory.CONTACT,
            event_type=AuditEventType.CONTACT_UPDATED,
        )
        return updated

    @staticmethod
    def _normalize_device_installation_id(value: str | None) -> str | None:
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        if len(s) < 8:
            raise ValidationError("device_installation_id must be at least 8 characters when provided")
        if len(s) > 128:
            raise ValidationError("device_installation_id must be at most 128 characters")
        return s

    async def _driver_has_device_terms_acceptance(
        self,
        *,
        driver_id: str,
        device_installation_id: str,
        content_hash: str,
    ) -> bool:
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        stmt = select(
            exists().where(
                DriverTermsAcceptanceRecord.driver_id == driver_id,
                DriverTermsAcceptanceRecord.device_installation_id == device_installation_id,
                DriverTermsAcceptanceRecord.content_hash == content_hash,
            )
        )
        row = await sess.execute(stmt)
        return bool(row.scalar_one())

    async def get_driver_self_onboarding_status(
        self, *, user_id: str, device_installation_id: str | None = None
    ) -> dict[str, object]:
        driver = await self.get_driver_by_user_id(user_id)
        active_terms = await self._terms_repo.find_current_active()
        if active_terms is not None:
            clauses = await self._terms_repo.list_clauses(active_terms.id)
            clauses_payload = [
                {"clause_order": c.clause_order, "heading": c.heading, "body": c.body}
                for c in clauses
            ]
            current_hash = self._compute_terms_content_hash(active_terms.title, clauses_payload)
        else:
            current_hash = None
        accepted_hash = getattr(driver, "terms_accepted_content_hash", None)
        requires_reacceptance_from_hash = (
            current_hash is not None
            and accepted_hash is not None
            and accepted_hash != current_hash
        )
        device_id_norm = self._normalize_device_installation_id(device_installation_id)
        profile_terms_done = getattr(driver, "terms_accepted_at", None) is not None
        requires_reacceptance_from_new_install = False
        if device_id_norm and current_hash is not None and profile_terms_done:
            install_has_current = await self._driver_has_device_terms_acceptance(
                driver_id=driver.id,
                device_installation_id=device_id_norm,
                content_hash=current_hash,
            )
            requires_reacceptance_from_new_install = not install_has_current
        requires_reacceptance = requires_reacceptance_from_hash or requires_reacceptance_from_new_install
        return {
            "terms_accepted": profile_terms_done,
            "requires_terms_reacceptance": requires_reacceptance,
            "location_consent_given": getattr(driver, "location_consent_at", None) is not None,
            "terms_accepted_at": getattr(driver, "terms_accepted_at", None),
            "location_consent_at": getattr(driver, "location_consent_at", None),
            "map_preference": getattr(driver, "map_preference", None),
        }

    @staticmethod
    def _compute_terms_content_hash(title: str, clauses: list[dict[str, object]]) -> str:
        ordered = sorted(
            [
                {
                    "clause_order": int(c["clause_order"]),
                    "heading": str(c["heading"]),
                    "body": str(c["body"]),
                }
                for c in clauses
            ],
            key=lambda c: c["clause_order"],
        )
        payload = json.dumps({"title": title, "clauses": ordered}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def get_current_driver_terms(self) -> dict[str, object]:
        terms = await self._terms_repo.find_current_active()
        if terms is None:
            raise NotFoundError(resource="driver_terms_and_conditions", id="active")
        clauses = await self._terms_repo.list_clauses(terms.id)
        return {
            "id": terms.id,
            "title": terms.title,
            "clauses": [
                {
                    "clause_order": c.clause_order,
                    "heading": c.heading,
                    "body": c.body,
                }
                for c in clauses
            ],
            "effective_from": terms.effective_from,
        }

    async def list_driver_terms(self) -> list[dict[str, object]]:
        rows = await self._terms_repo.list_all()
        payload: list[dict[str, object]] = []
        for row in rows:
            clauses = await self._terms_repo.list_clauses(row.id)
            payload.append(
                {
                    "id": row.id,
                    "title": row.title,
                    "clauses": [
                        {
                            "clause_order": c.clause_order,
                            "heading": c.heading,
                            "body": c.body,
                        }
                        for c in clauses
                    ],
                    "is_active": row.is_active,
                    "effective_from": row.effective_from,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
            )
        return payload

    async def create_driver_terms(
        self,
        *,
        title: str,
        clauses: list[dict[str, object]],
        effective_from: datetime | None = None,
        is_active: bool = True,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> dict[str, object]:
        if is_active:
            await self._terms_repo.deactivate_all()
        row = await self._terms_repo.create(
            {
                "title": title,
                "effective_from": effective_from,
                "is_active": is_active,
            }
        )
        created_clauses = await self._terms_repo.replace_clauses(terms_id=row.id, clauses=clauses)
        await self._log_audit(
            "driver.terms.create",
            entity_id=row.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={
                "title": row.title,
                "is_active": row.is_active,
                "effective_from": row.effective_from,
            },
            severity="NOTICE",
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.CONTRACT_CREATED,
        )
        return {
            "id": row.id,
            "title": row.title,
            "clauses": [
                {
                    "clause_order": c.clause_order,
                    "heading": c.heading,
                    "body": c.body,
                }
                for c in created_clauses
            ],
            "is_active": row.is_active,
            "effective_from": row.effective_from,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def update_driver_terms(
        self,
        *,
        terms_id: str,
        title: str | None = None,
        clauses: list[dict[str, object]] | None = None,
        effective_from: datetime | None = None,
        is_active: bool | None = None,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> dict[str, object]:
        old_row = await self._terms_repo.get_by_id_or_404(terms_id)
        data: dict[str, object] = {}
        if title is not None:
            data["title"] = title
        if effective_from is not None:
            data["effective_from"] = effective_from
        if is_active is not None:
            if is_active:
                await self._terms_repo.deactivate_all()
            data["is_active"] = is_active
        row = await self._terms_repo.update_terms_by_id(terms_id, data)
        if clauses is not None:
            updated_clauses = await self._terms_repo.replace_clauses(terms_id=terms_id, clauses=clauses)
        else:
            updated_clauses = await self._terms_repo.list_clauses(terms_id)
        await self._log_audit(
            "driver.terms.update",
            entity_id=row.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={
                "title": old_row.title,
                "is_active": old_row.is_active,
                "effective_from": old_row.effective_from,
            },
            new_value={
                "title": row.title,
                "is_active": row.is_active,
                "effective_from": row.effective_from,
            },
            severity="NOTICE",
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.ACCOUNT_UPDATED,
        )
        return {
            "id": row.id,
            "title": row.title,
            "clauses": [
                {
                    "clause_order": c.clause_order,
                    "heading": c.heading,
                    "body": c.body,
                }
                for c in updated_clauses
            ],
            "is_active": row.is_active,
            "effective_from": row.effective_from,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def activate_driver_terms(
        self,
        *,
        terms_id: str,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> dict[str, object]:
        old_row = await self._terms_repo.get_by_id_or_404(terms_id)
        await self._terms_repo.deactivate_all()
        row = await self._terms_repo.update_terms_by_id(terms_id, {"is_active": True})
        clauses = await self._terms_repo.list_clauses(terms_id)
        await self._log_audit(
            "driver.terms.activate",
            entity_id=row.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            old_value={
                "title": old_row.title,
                "is_active": old_row.is_active,
            },
            new_value={
                "title": row.title,
                "is_active": row.is_active,
            },
            severity="NOTICE",
            category=AuditCategory.DOCUMENT,
            event_type=AuditEventType.CONTRACT_EXECUTED,
        )
        return {
            "id": row.id,
            "title": row.title,
            "clauses": [
                {
                    "clause_order": c.clause_order,
                    "heading": c.heading,
                    "body": c.body,
                }
                for c in clauses
            ],
            "is_active": row.is_active,
            "effective_from": row.effective_from,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def accept_driver_self_onboarding_consents(
        self,
        *,
        user_id: str,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
        consent_context: dict[str, Any] | None = None,
    ) -> Driver:
        driver = await self._driver_repo.find_by_user_id(user_id)
        if driver is None:
            raise NotFoundError(resource="driver", id=user_id)
        active_terms = await self._terms_repo.find_current_active()
        if active_terms is None:
            raise ValidationError("No active terms and conditions configured")
        active_clauses = await self._terms_repo.list_clauses(active_terms.id)
        clauses_payload = [
            {"clause_order": c.clause_order, "heading": c.heading, "body": c.body}
            for c in active_clauses
        ]
        content_hash = self._compute_terms_content_hash(active_terms.title, clauses_payload)
        now = datetime.now(UTC)
        updated = await self._driver_repo.update_by_id(
            driver.id,
            {
                "terms_and_conditions_id": active_terms.id,
                "terms_accepted_content_hash": content_hash,
                "terms_accepted_at": now,
                "location_consent_at": now,
            },
        )

        ctx = consent_context or {}
        raw_ip = ctx.get("client_ip")
        ip_s = str(raw_ip)[:45] if raw_ip else None
        ua = ctx.get("user_agent")
        ua_s = str(ua)[:8000] if ua else None
        ct = ctx.get("client_type")
        ct_s = str(ct)[:32] if ct else None
        dev = ctx.get("device_info")
        device_payload: dict[str, object] | None = dev if isinstance(dev, dict) else None
        raw_install = ctx.get("device_installation_id")
        install_raw: str | None = None
        if raw_install is not None:
            install_raw = raw_install if isinstance(raw_install, str) else str(raw_install)
        install_id = self._normalize_device_installation_id(install_raw)

        sess = self._driver_repo.session  # type: ignore[attr-defined]
        sess.add(
            DriverTermsAcceptanceRecord(
                driver_id=driver.id,
                terms_id=active_terms.id,
                content_hash=content_hash,
                ip_address=ip_s,
                user_agent=ua_s,
                client_type=ct_s,
                device_info=device_payload,
                device_installation_id=install_id,
            )
        )
        await sess.flush()

        logger.info(
            "driver_terms_acceptance_recorded",
            driver_id=driver.id,
            terms_id=active_terms.id,
            client_type=ct_s,
            ip_masked=mask_ip_address(ip_s),
            has_user_agent=bool(ua_s),
            has_device_info=bool(device_payload),
            has_device_installation_id=bool(install_id),
        )

        await self._log_audit(
            "driver.self.onboarding_consents.accept",
            entity_id=driver.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={
                "terms_and_conditions_id": active_terms.id,
                "terms_accepted_content_hash": content_hash,
                "terms_accepted_at": now.isoformat(),
                "location_consent_at": now.isoformat(),
                "acceptance_ip_masked": mask_ip_address(ip_s),
                "acceptance_client_type": ct_s,
                "acceptance_has_user_agent": bool(ua_s),
                "acceptance_has_device_installation_id": bool(install_id),
            },
            severity="NOTICE",
            category=AuditCategory.CONTACT,
            event_type=AuditEventType.CONTACT_UPDATED,
        )
        return updated

    async def set_driver_self_map_preference(
        self,
        *,
        user_id: str,
        map_preference: DriverMapPreference | str,
        audit_user_id: str | None = None,
        audit_user_role: str | None = None,
    ) -> Driver:
        driver = await self._driver_repo.find_by_user_id(user_id)
        if driver is None:
            raise NotFoundError(resource="driver", id=user_id)
        pref = map_preference.value if isinstance(map_preference, DriverMapPreference) else str(map_preference)
        updated = await self._driver_repo.update_by_id(driver.id, {"map_preference": pref})
        await self._log_audit(
            "driver.self.map_preference.set",
            entity_id=driver.id,
            user_id=audit_user_id,
            user_role=audit_user_role,
            new_value={"map_preference": pref},
            severity="NOTICE",
            category=AuditCategory.CONTACT,
            event_type=AuditEventType.CONTACT_UPDATED,
        )
        return updated

    # ── Work schedule (mobile calendar) ────────────────────────────────────

    async def get_driver_work_schedule(
        self,
        *,
        driver_id: str,
        from_date: date,
        to_date: date,
    ) -> list[dict]:
        """Return one entry per calendar day in [from_date, to_date] with shift, time-off, holiday, and route info.

        Priority per day: TIME_OFF > HOLIDAY > shift (WORKING/REST).
        A route is attached when one exists for that service_date regardless of day type.
        """
        sess = self._driver_repo.session  # type: ignore[attr-defined]
        driver = await self._driver_repo.get_by_id_or_404(driver_id)

        # Fetch shifts in range
        shift_rows = await self.list_shifts(driver_id=driver_id, date_from=from_date, date_to=to_date)
        shifts_by_date: dict[date, DriverShift] = {s.shift_date: s for s in shift_rows}

        # Fetch time-off records overlapping range
        time_off_stmt = (
            select(DriverTimeOff)
            .where(
                DriverTimeOff.driver_id == driver_id,
                DriverTimeOff.start_date <= to_date,
                DriverTimeOff.end_date >= from_date,
            )
            .order_by(DriverTimeOff.start_date)
        )
        time_off_rows = list((await sess.execute(time_off_stmt)).scalars().all())

        # Build a set of (date → time_off row) — first match wins
        time_off_by_date: dict[date, DriverTimeOff] = {}
        for to_row in time_off_rows:
            cur = to_row.start_date
            while cur <= to_row.end_date:
                if from_date <= cur <= to_date and cur not in time_off_by_date:
                    time_off_by_date[cur] = to_row
                cur += timedelta(days=1)

        # Fetch holidays overlapping range (audience = BOTH or driver_type)
        audience_allowed = [HolidayAudience.BOTH.value]
        if driver.driver_type:
            audience_allowed.append(str(driver.driver_type).upper())
        holiday_stmt = (
            select(Holiday)
            .where(
                Holiday.start_date <= to_date,
                Holiday.end_date >= from_date,
                Holiday.audience.in_(audience_allowed),
            )
            .order_by(Holiday.start_date)
        )
        holiday_rows = list((await sess.execute(holiday_stmt)).scalars().all())

        holidays_by_date: dict[date, Holiday] = {}
        for h in holiday_rows:
            cur = h.start_date
            while cur <= h.end_date:
                if from_date <= cur <= to_date and cur not in holidays_by_date:
                    holidays_by_date[cur] = h
                cur += timedelta(days=1)

        # Fetch routes by service_date in range
        route_stmt = (
            select(Route, RoutePlan.service_date, Vehicle.registration_number)
            .join(RoutePlan, Route.plan_id == RoutePlan.id)
            .outerjoin(Vehicle, Route.vehicle_id == Vehicle.id)
            .where(
                Route.driver_id == driver_id,
                RoutePlan.service_date >= from_date,
                RoutePlan.service_date <= to_date,
            )
            .order_by(RoutePlan.service_date)
        )
        route_result = (await sess.execute(route_stmt)).all()
        routes_by_date: dict[date, dict] = {}
        for route, svc_date, reg in route_result:
            if svc_date not in routes_by_date:
                routes_by_date[svc_date] = {
                    "route_id": route.id,
                    "route_code": route.route_code,
                    "route_status": route.status,
                    "vehicle_registration": reg,
                }

        # Build one entry per day
        days: list[dict] = []
        cur_day = from_date
        while cur_day <= to_date:
            entry: dict = {"date": cur_day, "route": routes_by_date.get(cur_day)}

            if cur_day in time_off_by_date:
                to_row = time_off_by_date[cur_day]
                entry["day_type"] = "TIME_OFF"
                entry["time_off_type"] = to_row.type
                entry["time_off_is_paid"] = to_row.is_paid
            elif cur_day in holidays_by_date:
                h = holidays_by_date[cur_day]
                entry["day_type"] = "HOLIDAY"
                entry["holiday_name"] = h.name
            elif cur_day in shifts_by_date:
                shift = shifts_by_date[cur_day]
                entry["day_type"] = "WORKING"
                entry["shift_hours"] = f"{shift.start_time.strftime('%H:%M')} - {shift.end_time.strftime('%H:%M')}"
                entry["shift_status"] = shift.status
            else:
                entry["day_type"] = "REST"

            days.append(entry)
            cur_day += timedelta(days=1)

        return days
