"""Smoke tests for WeasyPrint + GTK/Pango/Cairo system libraries.

Production PDF jobs (invoices, credit notes, account statements) all call
``HTML(string=...).write_pdf()`` via WeasyPrint. Missing ``libgobject`` / Pango /
Cairo packages fail only at job runtime — not at import or unit-test time.

CI installs the same apt packages as ``Dockerfile`` and runs::

    poetry run pytest tests/smoke -m smoke -v

Outside CI, tests skip unless WeasyPrint can import and render a minimal PDF locally.

Keep apt package lists in sync with ``Dockerfile`` and ``.github/workflows/ci.yml``.
"""

from __future__ import annotations

import os

import pytest

_MINIMAL_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"/><title>SW Couriers PDF smoke</title></head>
<body><p>WeasyPrint smoke — invoices, credit notes, statements.</p></body>
</html>"""

_PROBE_HTML = "<!DOCTYPE html><html><body><p>probe</p></body></html>"

_weasyprint_available: bool | None = None


def _in_ci() -> bool:
    return os.environ.get("CI", "").lower() in {"1", "true", "yes"}


def _weasyprint_works() -> bool:
    global _weasyprint_available
    if _weasyprint_available is not None:
        return _weasyprint_available
    try:
        from weasyprint import HTML

        pdf_bytes = HTML(string=_PROBE_HTML).write_pdf()
    except OSError:
        _weasyprint_available = False
    else:
        _weasyprint_available = pdf_bytes is not None and pdf_bytes.startswith(b"%PDF-")
    return _weasyprint_available


def _require_weasyprint_smoke() -> None:
    """In CI: always run (fail if libs missing). Locally: skip unless WeasyPrint works."""
    if _in_ci():
        return
    if not _weasyprint_works():
        pytest.skip(
            "WeasyPrint system libraries unavailable locally; smoke runs in CI "
            "(see Dockerfile apt packages)."
        )


@pytest.mark.smoke
def test_weasyprint_imports_with_gobject_stack() -> None:
    """Import must succeed when production runtime libraries are present."""
    _require_weasyprint_smoke()
    try:
        from weasyprint import HTML  # noqa: F401
    except OSError as exc:
        pytest.fail(
            "WeasyPrint import failed — install GTK/Pango/Cairo system libraries "
            "(see Dockerfile apt packages: libglib2.0-0, libpango-1.0-0, libcairo2, …). "
            f"Original error: {exc}"
        )


@pytest.mark.smoke
def test_account_statement_html_to_pdf_produces_valid_bytes() -> None:
    """Statement PDF path used by ``generate_account_statement_pdf_task``."""
    _require_weasyprint_smoke()
    from app.modules.account_statements.pdf_builder import html_to_pdf

    pdf_bytes = html_to_pdf(_MINIMAL_HTML)
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 128


@pytest.mark.smoke
def test_invoice_html_to_pdf_produces_valid_bytes() -> None:
    """Invoice / credit-note PDF path used by ``generate_invoice_pdf_task`` and ``generate_credit_note_pdf_task``."""
    _require_weasyprint_smoke()
    from app.modules.invoices.tasks import _html_to_pdf

    pdf_bytes = _html_to_pdf(_MINIMAL_HTML)
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 128
