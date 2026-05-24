from __future__ import annotations

import copy
import secrets
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.common.constants import API_VERSION
from app.core.config import settings

_http_basic = HTTPBasic(auto_error=False)


def get_docs_user_credentials(
    credentials: HTTPBasicCredentials | None = Depends(_http_basic),  # noqa: B008
) -> str | None:
    user = (settings.DOCS_USER or "").strip()
    password = (settings.DOCS_PASSWORD.get_secret_value() or "").strip()
    if not user or not password:
        return None
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": 'Basic realm="Docs"'},
        )
    if not secrets.compare_digest(credentials.username, user) or not secrets.compare_digest(credentials.password, password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": 'Basic realm="Docs"'},
        )
    return credentials.username


# OpenAPI schema: 422 uses our error envelope


_VALIDATION_ERROR_RESPONSE = {
    "description": "Validation error",
    "content": {
        "application/json": {
            "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            "example": {
                "success": False,
                "message": "Validation error",
                "error": {
                    "code": "VALIDATION_ERROR",
                    "details": [
                        {
                            "field": "email",
                            "message": "value is not a valid email address",
                            "type": "value_error",
                        }
                    ],
                },
            },
        }
    },
}


def _error_schemas() -> dict[str, Any]:
    """OpenAPI schema definitions for ErrorResponse (422 and other error responses)."""
    return {
        "ValidationErrorDetail": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "description": "Field path (e.g. email, body.password)"},
                "message": {"type": "string", "description": "Error message"},
                "type": {"type": "string", "description": "Error type (e.g. value_error)"},
            },
            "required": ["field", "message", "type"],
        },
        "ErrorBody": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Stable error code (e.g. VALIDATION_ERROR)"},
                "details": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/ValidationErrorDetail"},
                    "description": "Field-level validation errors",
                },
            },
            "required": ["code"],
        },
        "ErrorResponse": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean", "enum": [False]},
                "message": {"type": "string", "description": "Human-readable message"},
                "error": {"$ref": "#/components/schemas/ErrorBody"},
            },
            "required": ["success", "message", "error"],
        },
    }


def _ensure_success_response_schemas(openapi_schema: dict[str, Any]) -> None:
    # Generic SuccessResponse[T] can be emitted with minimal/empty properties in OpenAPI.
    # Force explicit success/message/data so Swagger UI shows the envelope shape.
    schemas = openapi_schema.get("components", {}).get("schemas", {})
    for key in list(schemas.keys()):
        if not key.startswith("SuccessResponse_"):
            continue
        defn = schemas[key]
        props = defn.get("properties") or {}
        if "data" in props and "success" in props:
            continue
        inner_name = key[len("SuccessResponse_") :].removesuffix("_")
        defn["type"] = "object"
        # list[T] types are not emitted as standalone components; inline them as arrays
        if inner_name.startswith("list_"):
            item_name = inner_name[len("list_") :].removesuffix("_")
            data_schema: dict[str, Any] = {
                "type": "array",
                "items": {"$ref": f"#/components/schemas/{item_name}"},
                "description": "Response payload",
            }
        # SuccessResponse[dict] can be emitted as SuccessResponse_dict_; "dict" is not a component schema.
        elif inner_name in {"dict", "Dict"}:
            data_schema = {
                "type": "object",
                "additionalProperties": True,
                "description": "Response payload",
            }
        else:
            # If the inferred inner schema component is missing, avoid emitting an invalid $ref.
            if inner_name in schemas:
                data_schema = {"$ref": f"#/components/schemas/{inner_name}", "description": "Response payload"}
            else:
                data_schema = {
                    "type": "object",
                    "additionalProperties": True,
                    "description": "Response payload",
                }
        defn["properties"] = {
            "success": {"type": "boolean", "const": True, "description": "Always true for success"},
            "message": {"type": ["string", "null"], "description": "Optional human-readable message"},
            "data": data_schema,
        }
        defn["required"] = ["success", "data"]
        defn["additionalProperties"] = False


def _is_placeholder_object_schema(defn: dict[str, Any]) -> bool:
    return (
        defn.get("type") == "object"
        and defn.get("additionalProperties") is True
        and not defn.get("properties")
    )


def _ensure_concrete_response_schemas(app: FastAPI, openapi_schema: dict[str, Any]) -> None:
    """Rebuild placeholder response_model schemas from route model declarations."""
    schemas = openapi_schema.get("components", {}).get("schemas", {})
    if not schemas:
        return

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        model = route.response_model
        if model is None or not hasattr(model, "model_json_schema"):
            continue

        schema_name = getattr(model, "__name__", None)
        if not isinstance(schema_name, str) or schema_name not in schemas:
            continue
        if not _is_placeholder_object_schema(schemas[schema_name]):
            continue

        generated = model.model_json_schema(ref_template="#/components/schemas/{model}")
        for def_name, def_schema in generated.pop("$defs", {}).items():
            schemas.setdefault(def_name, def_schema)
        generated.pop("title", None)
        schemas[schema_name] = generated


def _resolve_ref(ref: str, schemas: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve a $ref string like '#/components/schemas/Foo' to its schema dict."""
    if not ref.startswith("#/components/schemas/"):
        return None
    model_name = ref.rsplit("/", 1)[-1]
    return schemas.get(model_name)


def _inline_json_form_fields(openapi_schema: dict[str, Any]) -> None:
    """Expand $ref in multipart/form-data JSON fields so Swagger UI shows
    the actual model properties instead of just 'object'.

    Handles both direct $ref and allOf wrappers that FastAPI generates for
    Form(media_type="application/json") parameters.
    """
    schemas = openapi_schema.get("components", {}).get("schemas", {})

    for path_item in openapi_schema.get("paths", {}).values():
        for method in ("post", "put", "patch"):
            operation = path_item.get(method)
            if not operation:
                continue

            body = operation.get("requestBody", {})
            multipart = body.get("content", {}).get("multipart/form-data")
            if not multipart:
                continue

            schema = multipart.get("schema", {})
            props = schema.get("properties", {})

            for field_name, field_schema in list(props.items()):
                # ── Find the $ref, whether direct or wrapped in allOf ──
                ref: str | None = None

                if "$ref" in field_schema:
                    ref = field_schema["$ref"]
                elif "allOf" in field_schema:
                    for item in field_schema["allOf"]:
                        if "$ref" in item:
                            ref = item["$ref"]
                            break

                if not ref:
                    continue

                resolved = _resolve_ref(ref, schemas)
                if not resolved:
                    continue

                # ── Build the inlined schema ──
                inlined: dict[str, Any] = {
                    "type": "object",
                    "title": field_schema.get("title") or field_name,
                }

                if "description" in field_schema:
                    inlined["description"] = field_schema["description"]

                # Deep-copy properties so we don't mutate the shared schema
                if "properties" in resolved:
                    inlined["properties"] = copy.deepcopy(resolved["properties"])
                if "required" in resolved:
                    inlined["required"] = resolved["required"]

                # Pull example from the model's json_schema_extra / examples
                if "examples" in resolved and resolved["examples"]:
                    inlined["example"] = resolved["examples"][0]
                elif "example" in resolved:
                    inlined["example"] = resolved["example"]

                props[field_name] = inlined


def _custom_openapi(app: FastAPI) -> Any:
    """Build OpenAPI schema and replace default 422 with our ErrorResponse schema."""
    if app.openapi_schema is not None:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version or API_VERSION,
        description=app.description,
        routes=app.routes,
    )
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}
    if "schemas" not in openapi_schema["components"]:
        openapi_schema["components"]["schemas"] = {}
    openapi_schema["components"]["schemas"].update(_error_schemas())
    for path_item in openapi_schema.get("paths", {}).values():
        for method in list(path_item.keys()):
            if method in ("get", "put", "post", "delete", "patch", "options", "head", "trace"):
                responses = path_item[method].get("responses", {})
                if "422" in responses:
                    responses["422"] = _VALIDATION_ERROR_RESPONSE
    _ensure_success_response_schemas(openapi_schema)
    _ensure_concrete_response_schemas(app, openapi_schema)
    _inline_json_form_fields(openapi_schema)
    app.openapi_schema = openapi_schema
    return app.openapi_schema


def register_docs_routes(app: FastAPI) -> None:
    @app.get("/docs", include_in_schema=False)
    async def get_swagger_documentation(_: str | None = Depends(get_docs_user_credentials)):
        return get_swagger_ui_html(openapi_url="/openapi.json", title="SW Couriers API")

    @app.get("/redoc", include_in_schema=False)
    async def get_redoc_documentation(_: str | None = Depends(get_docs_user_credentials)):
        return get_redoc_html(openapi_url="/openapi.json", title="SW Couriers API")

    @app.get("/openapi.json", include_in_schema=False)
    async def openapi(_: str | None = Depends(get_docs_user_credentials)):
        return app.openapi()

    app.openapi = lambda: _custom_openapi(app)