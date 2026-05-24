from app.core.swagger.config import register_docs_routes
from app.core.swagger.utils import (
    create_doc_entry,
    custom_entry,
    error_401_entry,
    error_entry,
    error_validation_entry,
    request_body_openapi,
    success_entry,
)

__all__ = [
    "create_doc_entry",
    "custom_entry",
    "error_401_entry",
    "error_entry",
    "error_validation_entry",
    "register_docs_routes",
    "request_body_openapi",
    "success_entry",
]
