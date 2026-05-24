import structlog
from fastapi.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import ConflictError, NotFoundError, ValidationError
from app.common.service import BaseService
from app.modules.audit.enums import AuditCategory, AuditEventType
from app.modules.audit.service import AuditService
from app.modules.delivery_attempts.domain import compact_attempt_fees, default_fee_entries, validate_strict_attempt_fees
from app.modules.delivery_attempts.repository import DeliveryAttemptConfigRepository
from app.modules.delivery_attempts.v1.schemas import (
    DeliveryAttemptConfigPatch,
    DeliveryAttemptConfigResponse,
    DeliveryAttemptConfigUpdate,
)

logger = structlog.get_logger()

class DeliveryAttemptService(BaseService):
    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        super().__init__(session, request)
        self._repo = DeliveryAttemptConfigRepository(session)
        self._audit = AuditService(session)

    async def get_config(self) -> DeliveryAttemptConfigResponse:
        """Return the global config, seeding defaults on first call."""
        config = await self._repo.get_singleton()
        if config is None:
            config = await self._repo.create(
                {
                    "max_delivery_attempts": 3,
                    "delivery_attempt_fees": default_fee_entries(3),
                    "max_return_attempts": 3,
                    "return_attempt_fees": default_fee_entries(3),
                }
            )
            logger.info("delivery_attempt_config.seeded", config_id=config.id)
        return DeliveryAttemptConfigResponse.model_validate(config)

    def _serialize_full_payload(self, data: DeliveryAttemptConfigUpdate) -> dict:
        if data.max_delivery_attempts is None or data.max_return_attempts is None:
            raise ValidationError("max attempt values could not be derived from fee arrays.")
        return {
            "max_delivery_attempts": data.max_delivery_attempts,
            "delivery_attempt_fees": validate_strict_attempt_fees(
                data.delivery_attempt_fees,
                data.max_delivery_attempts,
                "delivery_attempt_fees",
            ),
            "max_return_attempts": data.max_return_attempts,
            "return_attempt_fees": validate_strict_attempt_fees(
                data.return_attempt_fees,
                data.max_return_attempts,
                "return_attempt_fees",
            ),
        }

    async def create_config(
        self,
        data: DeliveryAttemptConfigUpdate,
        admin_user_id: str,
    ) -> DeliveryAttemptConfigResponse:
        existing = await self._repo.get_singleton()
        if existing is not None:
            raise ConflictError("Global delivery attempt configuration already exists.")

        payload = self._serialize_full_payload(data)
        config = await self._repo.create(payload)
        await self._audit.log(
            action="delivery_attempts.config.created",
            entity_type="delivery_attempt_config",
            entity_id=config.id,
            user_id=admin_user_id,
            new_value=payload,
            severity="NOTICE",
            category=AuditCategory.BILLING,
            event_type=AuditEventType.BILLING_CONFIG_CHANGED,
        )
        logger.info("delivery_attempt_config.created", config_id=config.id, admin_user_id=admin_user_id)
        return DeliveryAttemptConfigResponse.model_validate(config)

    async def update_config(
        self,
        data: DeliveryAttemptConfigUpdate,
        admin_user_id: str,
    ) -> DeliveryAttemptConfigResponse:
        config = await self._repo.get_singleton()
        fees_payload = self._serialize_full_payload(data)

        if config is None:
            config = await self._repo.create(fees_payload)
        else:
            old_value = {
                "max_delivery_attempts": config.max_delivery_attempts,
                "delivery_attempt_fees": config.delivery_attempt_fees,
                "max_return_attempts": config.max_return_attempts,
                "return_attempt_fees": config.return_attempt_fees,
            }
            config = await self._repo.update_by_id(config.id, fees_payload, expected_version=data.version)
            await self._audit.log(
                action="delivery_attempts.config.updated",
                entity_type="delivery_attempt_config",
                entity_id=config.id,
                user_id=admin_user_id,
                old_value=old_value,
                new_value=fees_payload,
                severity="NOTICE",
                category=AuditCategory.BILLING,
                event_type=AuditEventType.BILLING_CONFIG_CHANGED,
            )

        logger.info("delivery_attempt_config.updated", config_id=config.id, admin_user_id=admin_user_id)
        return DeliveryAttemptConfigResponse.model_validate(config)

    async def patch_config(
        self,
        data: DeliveryAttemptConfigPatch,
        admin_user_id: str,
    ) -> DeliveryAttemptConfigResponse:
        config = await self._repo.get_singleton()
        if config is None:
            raise NotFoundError(resource="delivery_attempt_config", id="singleton")

        incoming = data.model_dump(exclude_none=True, exclude={"version"})
        if not incoming:
            raise ValidationError("At least one mutable field is required.")

        if "delivery_attempt_fees" in incoming:
            compacted = compact_attempt_fees(incoming["delivery_attempt_fees"], "delivery_attempt_fees")
            max_delivery_attempts = incoming.get("max_delivery_attempts")
            if max_delivery_attempts is not None and max_delivery_attempts != len(compacted):
                raise ValidationError(
                    f"max_delivery_attempts={max_delivery_attempts} does not match compacted "
                    f"delivery_attempt_fees length {len(compacted)}."
                )
            incoming["delivery_attempt_fees"] = compacted
            incoming["max_delivery_attempts"] = len(compacted)
        elif "max_delivery_attempts" in incoming:
            existing_delivery = list(config.delivery_attempt_fees or [])
            if len(existing_delivery) != incoming["max_delivery_attempts"]:
                raise ValidationError(
                    "max_delivery_attempts cannot be changed without delivery_attempt_fees. "
                    "Send the full fee list for compact renumbering."
                )

        if "return_attempt_fees" in incoming:
            compacted = compact_attempt_fees(incoming["return_attempt_fees"], "return_attempt_fees")
            max_return_attempts = incoming.get("max_return_attempts")
            if max_return_attempts is not None and max_return_attempts != len(compacted):
                raise ValidationError(
                    f"max_return_attempts={max_return_attempts} does not match compacted "
                    f"return_attempt_fees length {len(compacted)}."
                )
            incoming["return_attempt_fees"] = compacted
            incoming["max_return_attempts"] = len(compacted)
        elif "max_return_attempts" in incoming:
            existing_return = list(config.return_attempt_fees or [])
            if len(existing_return) != incoming["max_return_attempts"]:
                raise ValidationError(
                    "max_return_attempts cannot be changed without return_attempt_fees. "
                    "Send the full fee list for compact renumbering."
                )

        old_value = {
            "max_delivery_attempts": config.max_delivery_attempts,
            "delivery_attempt_fees": config.delivery_attempt_fees,
            "max_return_attempts": config.max_return_attempts,
            "return_attempt_fees": config.return_attempt_fees,
        }
        updated = await self._repo.update_by_id(config.id, incoming, expected_version=data.version)
        await self._audit.log(
            action="delivery_attempts.config.patched",
            entity_type="delivery_attempt_config",
            entity_id=updated.id,
            user_id=admin_user_id,
            old_value=old_value,
            new_value=incoming,
            severity="NOTICE",
            category=AuditCategory.BILLING,
            event_type=AuditEventType.BILLING_CONFIG_CHANGED,
        )
        logger.info("delivery_attempt_config.patched", config_id=updated.id, admin_user_id=admin_user_id)
        return DeliveryAttemptConfigResponse.model_validate(updated)

    async def delete_config(self, admin_user_id: str) -> None:
        config = await self._repo.get_singleton()
        if config is None:
            raise NotFoundError(resource="delivery_attempt_config", id="singleton")

        old_value = {
            "max_delivery_attempts": config.max_delivery_attempts,
            "delivery_attempt_fees": config.delivery_attempt_fees,
            "max_return_attempts": config.max_return_attempts,
            "return_attempt_fees": config.return_attempt_fees,
        }
        await self._repo.hard_delete(config.id)
        await self._audit.log(
            action="delivery_attempts.config.deleted",
            entity_type="delivery_attempt_config",
            entity_id=config.id,
            user_id=admin_user_id,
            old_value=old_value,
            severity="WARNING",
            category=AuditCategory.BILLING,
            event_type=AuditEventType.BILLING_CONFIG_CHANGED,
        )
        logger.info("delivery_attempt_config.deleted", config_id=config.id, admin_user_id=admin_user_id)
