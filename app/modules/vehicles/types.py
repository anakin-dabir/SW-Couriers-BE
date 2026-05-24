from dataclasses import dataclass

from app.modules.vehicles.models import VehicleDocument


@dataclass(frozen=True, slots=True)
class BulkUploadFailure:
    index: int
    message: str


@dataclass(slots=True)
class BulkImageUploadOutcome:
    """Result of vehicle image bulk upload: signed URLs for created rows and per-index failures."""

    urls: list[str]
    failed: list[BulkUploadFailure]


@dataclass(slots=True)
class BulkDocumentUploadOutcome:
    created: list[VehicleDocument]
    failed: list[BulkUploadFailure]
