from __future__ import annotations

from typing import Any
from pydantic import BaseModel


def _api_success_example(
    data: dict[str, Any] | list[Any] | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    example: dict[str, Any] = {"success": True}
    if message is not None:
        example["message"] = message
    if data is not None:
        example["data"] = data
    return example


def _api_error_example(code: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "message": message,
        "error": {"code": code},
    }


def success_entry(
    description: str,
    *,
    data: dict[str, Any] | list[Any] | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {
                "example": _api_success_example(data=data, message=message),
            }
        },
    }


def error_entry(description: str, *, code: str, message: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {
                "example": _api_error_example(code, message),
            }
        },
    }


def error_validation_entry(
    description: str,
    *,
    message: str,
    field: str,
    field_message: str,
) -> dict[str, Any]:
    """OpenAPI example for app ValidationError (422) with error.details[]."""
    return {
        "description": description,
        "content": {
            "application/json": {
                "example": {
                    "success": False,
                    "message": message,
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "details": [
                            {
                                "field": field,
                                "message": field_message,
                                "type": "value_error",
                            }
                        ],
                    },
                }
            }
        },
    }


def error_401_entry(
    description: str = "Authentication error",
    code: str = "AUTHENTICATION_ERROR",
    message: str = "Invalid credentials",
) -> dict[str, Any]:
    return error_entry(description=description, code=code, message=message)


def custom_entry(description: str, example: dict[str, Any]) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"example": example}},
    }


def request_body_openapi(
    *,
    examples: dict[str, dict[str, Any]] | None = None,
    example: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build ``openapi_extra`` with Swagger requestBody examples for JSON endpoints."""
    content: dict[str, Any] = {}
    if examples:
        content["examples"] = examples
    if example is not None:
        content["example"] = example
    if not content:
        return {}
    return {"openapi_extra": {"requestBody": {"content": {"application/json": content}}}}


def create_doc_entry(
    summary: str,
    responses: dict[int | str, dict[str, Any]],
    description: str | None = None,
    *,
    request_examples: dict[str, dict[str, Any]] | None = None,
    request_example: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"summary": summary, "responses": responses}
    if description is not None:
        out["description"] = description
    if request_examples or request_example is not None:
        out.update(request_body_openapi(examples=request_examples, example=request_example))
    return out

def schema_description(model: type[BaseModel], *, array: bool = False) -> str:
    """Auto-generate a compact schema string from a Pydantic model."""
    schema = model.model_json_schema()
    defs = schema.get("$defs", {})
    props = schema.get("properties", {})
    required = set(schema.get("required", []))

    lines = []
    for name, info in props.items():
        star = "*" if name in required else ""
        type_str = _format_type(info, defs)
        constraint = _format_constraints(info, defs)
        default = _format_default(info)

        parts = [t for t in [type_str, constraint, default] if t]
        lines.append(f"  {name}{star}: {', '.join(parts)}")

    body = "{\n" + ",\n".join(lines) + "\n}"
    if array:
        body = "[\n  " + body.replace("\n", "\n  ") + "\n]"
    return f"```\n{body}\n```"


def _resolve_ref(info: dict, defs: dict) -> dict:
    """Resolve a $ref to its definition."""
    ref = info.get("$ref", "")
    if ref.startswith("#/$defs/"):
        name = ref.split("/")[-1]
        return defs.get(name, info)
    return info


def _is_enum(schema: dict) -> bool:
    return "enum" in schema


def _is_model(schema: dict) -> bool:
    return schema.get("type") == "object" and "properties" in schema


def _format_enum(schema: dict) -> str:
    """Format enum values, truncating if too many."""
    values = schema["enum"]
    if len(values) <= 4:
        return " | ".join(f'"{v}"' for v in values)
    # Show first 3 + count of remaining
    shown = " | ".join(f'"{v}"' for v in values[:3])
    return f'{shown} | ... (+{len(values) - 3} more)'


def _format_type(info: dict, defs: dict) -> str:
    """Extract a readable type string from a JSON schema property."""
    # Direct $ref (enum or nested model without null)
    if "$ref" in info:
        resolved = _resolve_ref(info, defs)
        if _is_enum(resolved):
            return _format_enum(resolved)
        if _is_model(resolved):
            return _format_nested_model(resolved, defs)
        return resolved.get("type", "object")

    # Inline enum
    if "enum" in info:
        return _format_enum(info)

    # allOf wrapper (enum/model with default)
    if "allOf" in info:
        for item in info["allOf"]:
            resolved = _resolve_ref(item, defs) if "$ref" in item else item
            if _is_enum(resolved):
                return _format_enum(resolved)
            if _is_model(resolved):
                return _format_nested_model(resolved, defs)
        return "object"

    # anyOf (nullable types like `str | None`, `SomeEnum | None`)
    if "anyOf" in info:
        types = []
        for variant in info["anyOf"]:
            if variant.get("type") == "null":
                types.append("null")
            elif "$ref" in variant:
                resolved = _resolve_ref(variant, defs)
                if _is_enum(resolved):
                    types.append(_format_enum(resolved))
                elif _is_model(resolved):
                    types.append(_format_nested_model(resolved, defs))
                else:
                    types.append(resolved.get("type", "object"))
            elif "enum" in variant:
                types.append(_format_enum(variant))
            else:
                types.append(variant.get("format") or variant.get("type", "any"))
        return " | ".join(types)

    # Array types
    if info.get("type") == "array":
        items = info.get("items", {})
        if "$ref" in items:
            resolved = _resolve_ref(items, defs)
            if _is_enum(resolved):
                item_type = _format_enum(resolved)
            elif _is_model(resolved):
                item_type = _format_nested_model(resolved, defs)
            else:
                item_type = resolved.get("type", "object")
        else:
            item_type = items.get("format") or items.get("type", "any")
        return f"[{item_type}]"

    return info.get("format") or info.get("type", "any")


def _format_nested_model(schema: dict, defs: dict) -> str:
    """Format a nested model's properties inline."""
    props = schema.get("properties", {})
    required = set(schema.get("required", []))

    fields = []
    for name, info in props.items():
        star = "*" if name in required else ""
        type_str = _format_type(info, defs)
        constraint = _format_constraints(info, defs)
        default = _format_default(info)

        parts = [t for t in [type_str, constraint, default] if t]
        fields.append(f"{name}{star}: {', '.join(parts)}")

    return "{ " + ", ".join(fields) + " }"

def _format_constraints(info: dict, defs: dict) -> str:
    """Extract constraint hints like ranges and lengths."""
    parts = []

    # For allOf, check the ref'd schema for constraints too
    if "allOf" in info:
        for item in info["allOf"]:
            resolved = _resolve_ref(item, defs) if "$ref" in item else item
            sub = _format_constraints(resolved, defs)
            if sub:
                parts.append(sub)
        return ", ".join(parts)

    if "minLength" in info and "maxLength" in info:
        parts.append(f"({info['minLength']}–{info['maxLength']} chars)")
    elif "maxLength" in info:
        parts.append(f"(max {info['maxLength']} chars)")
    elif "minLength" in info:
        parts.append(f"(min {info['minLength']} chars)")

    if "minimum" in info and "maximum" in info:
        parts.append(f"({info['minimum']}–{info['maximum']})")
    elif "minimum" in info:
        parts.append(f"(>= {info['minimum']})")
    elif "maximum" in info:
        parts.append(f"(<= {info['maximum']})")

    # Array length constraints
    if "minItems" in info or "maxItems" in info:
        min_i = info.get("minItems")
        max_i = info.get("maxItems")
        if min_i and max_i:
            parts.append(f"({min_i}–{max_i} items)")
        elif min_i:
            parts.append(f"(min {min_i} items)")
        elif max_i:
            parts.append(f"(max {max_i} items)")

    for variant in info.get("anyOf", []):
        if variant.get("type") == "null":
            continue
        sub = _format_constraints(variant, defs)
        if sub:
            parts.append(sub)

    return ", ".join(parts)


def _format_default(info: dict) -> str:
    """Show default value if present."""
    if "default" not in info:
        return ""
    default = info["default"]
    if default is None:
        return ""
    if isinstance(default, str):
        return f'default: "{default}"'
    return f"default: {default}"