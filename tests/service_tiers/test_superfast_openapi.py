"""OpenAPI schema checks for service tier system-tier fields."""

from app.main import create_app


def test_openapi_service_tier_response_includes_system_tier_fields() -> None:
    app = create_app()
    schema = app.openapi()
    tier_schema = schema["components"]["schemas"]["ServiceTierResponse"]["properties"]
    assert "is_system_tier" in tier_schema
    assert "tier_name_locked" in tier_schema
    assert "permitted_locked" in tier_schema


def test_openapi_global_list_documents_superfast() -> None:
    app = create_app()
    schema = app.openapi()
    paths = schema["paths"]
    global_path = next(key for key in paths if key.endswith("/service-tiers/global"))
    get_op = paths[global_path]["get"]
    description = get_op.get("description", "")
    assert "Superfast" in description


def test_openapi_delete_documents_superfast_protection() -> None:
    app = create_app()
    schema = app.openapi()
    paths = schema["paths"]
    delete_path = next(key for key in paths if key.endswith("/service-tiers/{tier_id}") and "delete" in paths[key])
    description = paths[delete_path]["delete"].get("description", "")
    assert "Superfast" in description
