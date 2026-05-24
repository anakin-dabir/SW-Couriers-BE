from __future__ import annotations

import asyncio
from collections.abc import Sequence
from io import BytesIO
from typing import Any

import magic
import structlog
from fastapi import UploadFile

from app.common.enums.logger import LogEvent
from app.common.exceptions import StorageProviderError, ValidationError
from app.common.types import BulkUploadResult

logger = structlog.get_logger()


# Validation constants

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_DOCUMENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/heic",
    "image/heif",
}

# Extended set for org contract documents (.png, .jpeg, .pdf, .docx, .heic — up to 25 MB)
ALLOWED_ORG_DOCUMENT_TYPES = ALLOWED_DOCUMENT_TYPES
MAX_ORG_DOCUMENT_SIZE = 25 * 1024 * 1024  # 25 MB

MAX_IMAGE_SIZE = 1 * 1024 * 1024  # 1 MB
MAX_DOCUMENT_SIZE = 10 * 1024 * 1024  # 10 MB

# Remittance advice (billing): PNG, JPEG, PDF only — production cap 10 MB
ALLOWED_REMITTANCE_ADVICE_TYPES = frozenset({"image/jpeg", "image/png", "application/pdf"})
MAX_REMITTANCE_ADVICE_SIZE = 10 * 1024 * 1024  # 10 MB


# File validation


def _file_validation_details(form_field: str | None, message: str) -> list[dict[str, Any]] | None:
    if not form_field:
        return None
    return [{"field": form_field, "message": message, "type": "value_error"}]


async def read_and_validate(
    file: UploadFile,
    *,
    allowed_types: set[str],
    max_size: int,
    label: str = "file",
    form_field: str | None = None,
) -> tuple[bytes, str]:
    content = await file.read()
    if len(content) > max_size:
        mb = max_size // (1024 * 1024)
        msg = f"{label} exceeds maximum size of {mb}MB"
        raise ValidationError(msg, details=_file_validation_details(form_field, msg))
    if not content:
        msg = f"{label} is empty"
        raise ValidationError(msg, details=_file_validation_details(form_field, msg))

    detected = magic.from_buffer(content, mime=True)
    if detected not in allowed_types:
        allowed = ", ".join(sorted(allowed_types))
        msg = f"{label} type '{detected}' is not allowed. Accepted MIME types: {allowed}"
        raise ValidationError(msg, details=_file_validation_details(form_field, msg))

    return content, detected


async def validate_image(file: UploadFile) -> tuple[bytes, str]:
    return await read_and_validate(
        file,
        allowed_types=ALLOWED_IMAGE_TYPES,
        max_size=MAX_IMAGE_SIZE,
        label="Image",
    )


async def validate_document(file: UploadFile) -> tuple[bytes, str]:
    return await read_and_validate(
        file,
        allowed_types=ALLOWED_DOCUMENT_TYPES,
        max_size=MAX_DOCUMENT_SIZE,
        label="Document",
    )


async def validate_remittance_advice(file: UploadFile) -> tuple[bytes, str]:
    """Validate remittance advice upload: PNG, JPG/JPEG, PDF; max 10 MB; magic-detected MIME."""
    return await read_and_validate(
        file,
        allowed_types=set(ALLOWED_REMITTANCE_ADVICE_TYPES),
        max_size=MAX_REMITTANCE_ADVICE_SIZE,
        label="Remittance advice",
        form_field="remittance_advice",
    )


# R2 operations (documents / general files)


def _r2_put_object(key: str, content: bytes, content_type: str) -> None:
    from app.storage.r2_client import get_default_r2_client, get_r2_bucket_name

    client = get_default_r2_client()
    bucket = get_r2_bucket_name()
    client.put_object(Bucket=bucket, Key=key, Body=content, ContentType=content_type)


def _r2_delete_object(key: str) -> None:
    from app.storage.r2_client import get_default_r2_client, get_r2_bucket_name

    client = get_default_r2_client()
    bucket = get_r2_bucket_name()
    client.delete_object(Bucket=bucket, Key=key)


async def upload_to_r2(key: str, content: bytes, content_type: str) -> str:
    try:
        await asyncio.to_thread(_r2_put_object, key, content, content_type)
    except StorageProviderError:
        raise
    except Exception as exc:
        logger.error(LogEvent.STORAGE_PROVIDER_ERROR, provider="r2", reason="upload_failed", key=key, error=str(exc))
        raise StorageProviderError("File upload failed") from exc
    return key


async def bulk_upload_to_r2(items: Sequence[tuple[str, bytes, str]]) -> BulkUploadResult[str]:
    """Upload all items to R2 concurrently. Returns per-index success/failure; raises only if all fail.
    Each upload runs the sync boto3 call via asyncio.to_thread (thread pool), so the event loop is not blocked."""
    if not items:
        return BulkUploadResult(succeeded=[], failed=[])

    tasks = [upload_to_r2(key, content, ct) for key, content, ct in items]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    succeeded: list[tuple[int, str]] = []
    failed: list[tuple[int, str]] = []
    for idx, (item, result) in enumerate(zip(items, results, strict=True)):
        if isinstance(result, BaseException):
            msg = getattr(result, "message", None) or str(result)
            failed.append((idx, msg))
            logger.warning(LogEvent.STORAGE_PROVIDER_ERROR, provider="r2", reason="bulk_upload_item_failed", key=item[0], index=idx, error=msg)
        else:
            succeeded.append((idx, result))

    if not succeeded and failed:
        raise StorageProviderError(f"All {len(failed)} file(s) failed to upload")

    return BulkUploadResult(succeeded=succeeded, failed=failed)


async def delete_from_r2(key: str) -> None:
    try:
        await asyncio.to_thread(_r2_delete_object, key)
    except Exception as exc:
        logger.error(LogEvent.STORAGE_PROVIDER_ERROR, provider="r2", reason="delete_failed", key=key, error=str(exc))
        raise StorageProviderError("File deletion failed") from exc


# Cloudflare Images operations (private images)


async def upload_image(
    content: bytes,
    filename: str = "image",
    metadata: dict[str, Any] | None = None,
) -> Any:
    from app.storage.cloudflare_images import get_images_client

    client = get_images_client()
    return await client.upload_image(BytesIO(content), filename=filename, metadata=metadata)


async def bulk_upload_images(
    items: Sequence[tuple[bytes, str, dict[str, Any] | None]],
    *,
    raise_if_all_failed: bool = True,
) -> BulkUploadResult[Any]:
    """Upload all images to Cloudflare Images concurrently. Returns per-index success/failure.

    When ``raise_if_all_failed`` is True (default), raises if every item failed (legacy behaviour).
    """
    if not items:
        return BulkUploadResult(succeeded=[], failed=[])

    tasks = [upload_image(content, filename, meta) for content, filename, meta in items]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    succeeded: list[tuple[int, Any]] = []
    failed: list[tuple[int, str]] = []
    for idx, ((_, filename, _), result) in enumerate(zip(items, results, strict=True)):
        if isinstance(result, BaseException):
            msg = getattr(result, "message", None) or str(result)
            failed.append((idx, msg))
            logger.warning(LogEvent.STORAGE_PROVIDER_ERROR, provider="cloudflare_images", reason="bulk_upload_item_failed", filename=filename, index=idx, error=msg)
        else:
            succeeded.append((idx, result))

    if raise_if_all_failed and not succeeded and failed:
        raise StorageProviderError(f"All {len(failed)} image(s) failed to upload")

    return BulkUploadResult(succeeded=succeeded, failed=failed)


async def delete_image(image_id: str) -> None:
    from app.storage.cloudflare_images import get_images_client

    client = get_images_client()
    await client.delete_image(image_id)


# Signed / presigned URL generation


def generate_document_url(key: str, *, expiry_seconds: int = 3600) -> str:
    from app.storage.r2_client import generate_presigned_url

    return generate_presigned_url(key, expiry_seconds=expiry_seconds)


def generate_image_url(image_id: str, *, variant: str = "public", expiry_seconds: int = 3600) -> str:
    from app.storage.cloudflare_images import get_images_client

    client = get_images_client()
    return client.generate_signed_url(image_id, variant=variant, expiry_seconds=expiry_seconds)
