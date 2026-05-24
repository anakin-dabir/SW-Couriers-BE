"""Braintree SDK gateway singleton configured from application settings."""

import braintree

from app.core.config import settings

_gateway: braintree.BraintreeGateway | None = None


def get_braintree_gateway() -> braintree.BraintreeGateway:
    """Return the process-wide Braintree gateway (lazy-initialised)."""
    global _gateway
    if _gateway is None:
        environment = (
            braintree.Environment.Production
            if settings.BRAINTREE_ENVIRONMENT == "production"
            else braintree.Environment.Sandbox
        )
        _gateway = braintree.BraintreeGateway(
            braintree.Configuration(
                environment=environment,
                merchant_id=settings.BRAINTREE_MERCHANT_ID,
                public_key=settings.BRAINTREE_PUBLIC_KEY,
                private_key=settings.BRAINTREE_PRIVATE_KEY.get_secret_value(),
            )
        )
    return _gateway
