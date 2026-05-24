"""Normalize payer ``client_type`` for record-payment flows (query, JSON body, multipart)."""

from __future__ import annotations

from app.common.exceptions import ValidationError

_CANONICAL_RECORD_PAYMENT_CLIENT_TYPE = {
    "B2B": "CUSTOMER_B2B",
    "B2C": "CUSTOMER_B2C",
    "CUSTOMER_B2B": "CUSTOMER_B2B",
    "CUSTOMER_B2C": "CUSTOMER_B2C",
}


def parse_record_payment_client_type(raw: str) -> str:
    v = (raw or "").strip().upper()
    out = _CANONICAL_RECORD_PAYMENT_CLIENT_TYPE.get(v)
    if out is None:
        raise ValidationError(
            "Invalid client_type",
            details=[
                {
                    "field": "client_type",
                    "message": "Must be CUSTOMER_B2B or CUSTOMER_B2C (short forms B2B or B2C are accepted)",
                    "type": "enum",
                }
            ],
        )
    return out
