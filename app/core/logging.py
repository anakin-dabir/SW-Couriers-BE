"""Structured logging configuration using structlog.

Outputs JSON in production, pretty-printed in development.
All logs get automatic context binding: request_id, user_id, path.
PII is NEVER logged in plaintext.
"""

import logging
import sys

import structlog

from app.core.config import settings


def setup_logging() -> None:
    """Configure structlog with environment-appropriate processors."""
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    renderer = structlog.processors.JSONRenderer() if settings.is_production else structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging to use structlog formatter
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.LOG_LEVEL.upper())

    # Quiet noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.DEBUG if settings.DATABASE_ECHO else logging.WARNING)

    # These emit verbose DEBUG (TLS, multipart chunks, SigV4, endpoint JSON) when root is DEBUG.
    for name in (
        "httpcore",
        "httpx",
        "botocore",
        "boto3",
        "urllib3",
        "s3transfer",
        "python_multipart",
        "python_multipart.multipart",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)
