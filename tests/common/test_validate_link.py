import pytest

from app.common.utils import validate_link
from app.core.config import settings


def test_https_accepted() -> None:
    assert validate_link("https://example.com/path?x=1") == "https://example.com/path?x=1"


def test_https_strips_surrounding_whitespace() -> None:
    assert validate_link("  https://example.com/  ") == "https://example.com/"


def test_http_allowed_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "development")
    assert validate_link("http://localhost:5173/x") == "http://localhost:5173/x"


def test_http_allowed_in_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "test")
    assert validate_link("http://127.0.0.1/x") == "http://127.0.0.1/x"


def test_http_rejected_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    with pytest.raises(ValueError, match="Invalid link scheme"):
        validate_link("http://example.com/x")


def test_http_rejected_in_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "staging")
    with pytest.raises(ValueError, match="Invalid link scheme"):
        validate_link("http://example.com/x")


def test_default_app_scheme_swcouriers() -> None:
    assert validate_link("swcouriers://accept-invite?token=abc") == "swcouriers://accept-invite?token=abc"


def test_javascript_scheme_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid link scheme"):
        validate_link("javascript:alert(1)")


def test_data_scheme_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid link scheme"):
        validate_link("data:text/html,<script>bad</script>")


def test_unknown_custom_scheme_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid link scheme"):
        validate_link("evil://host")


def test_extra_custom_schemes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "EMAIL_LINK_ALLOWED_APP_SCHEMES", "myapp, other")
    assert validate_link("myapp://open") == "myapp://open"
    assert validate_link("other://path") == "other://path"
    with pytest.raises(ValueError, match="Invalid link scheme"):
        validate_link("notlisted://x")


def test_relative_or_missing_scheme_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid link scheme"):
        validate_link("/only/a/path")


def test_empty_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid link"):
        validate_link("")
    with pytest.raises(ValueError, match="Invalid link"):
        validate_link("   ")
