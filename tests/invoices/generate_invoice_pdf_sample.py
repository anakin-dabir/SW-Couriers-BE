"""Render the invoice HTML template to a PDF using fixed test data (no DB, worker, or R2).

Run from repo root:
    poetry run python scripts/generate_invoice_pdf_sample.py
    poetry run python scripts/generate_invoice_pdf_sample.py --out ./sample.pdf --paid
    poetry run python scripts/generate_invoice_pdf_sample.py --engine chromium

Engine ``auto`` (default): try WeasyPrint first; on Windows (or any machine without GTK/Pango),
fall back to Chrome or Edge headless if installed.

Override browser: set CHROME_PATH or CHROMIUM_PATH to the executable.

Production workers still use WeasyPrint in ``app.modules.invoices.tasks``; this script is for local
preview only.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

# Ensure app.* imports resolve when run as a script
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.modules.invoices.service import PDF_TEMPLATE_VERSION  # noqa: E402
from app.modules.invoices.tasks import _build_pdf_context, _render_html  # noqa: E402


def _mock_invoice(*, paid: bool) -> SimpleNamespace:
    org = SimpleNamespace(
        trading_name="Acme Logistics Ltd",
        legal_entity_name="Acme Logistics Limited",
        reg_address_line_1="100 Test Street",
        reg_address_line_2="Suite 5",
        reg_city="Cardiff",
        reg_state=None,
        reg_postcode="CF10 1AA",
        reg_country="United Kingdom",
    )
    order = SimpleNamespace(
        order_id="SWC-ORD-204891",
        contact_email="billing@acme.example",
        contact_name="Jane Example",
    )
    line_items = [
        SimpleNamespace(
            description="Same-day courier — Zone A",
            quantity=2,
            unit_price=Decimal("45.00"),
            total_price=Decimal("90.00"),
        ),
        SimpleNamespace(
            description="Fuel surcharge",
            quantity=1,
            unit_price=Decimal("10.00"),
            total_price=Decimal("10.00"),
        ),
    ]
    return SimpleNamespace(
        invoice_number="INV-000999",
        issue_date=date(2025, 12, 7),
        due_date=date(2026, 1, 6),
        subtotal=Decimal("100.00"),
        vat_rate=Decimal("20.0"),
        vat_amount=Decimal("20.00"),
        total=Decimal("120.00"),
        status="SENT",
        paid_amount=Decimal("120.00") if paid else Decimal("0"),
        order=order,
        organization=org,
        line_items=line_items,
    )


def _chromium_candidates() -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []

    def add(p: Path) -> None:
        try:
            r = p.resolve()
        except OSError:
            return
        key = str(r).lower()
        if key not in seen:
            seen.add(key)
            out.append(r)

    for env_key in ("CHROMIUM_PATH", "CHROME_PATH", "EDGE_PATH"):
        v = os.environ.get(env_key)
        if v:
            add(Path(v))

    if sys.platform == "win32":
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        for p in (
            Path(pf) / r"Google\Chrome\Application\chrome.exe",
            Path(pf86) / r"Google\Chrome\Application\chrome.exe",
            Path(local) / r"Google\Chrome\Application\chrome.exe",
            Path(pf86) / r"Microsoft\Edge\Application\msedge.exe",
            Path(pf) / r"Microsoft\Edge\Application\msedge.exe",
        ):
            add(p)
    elif sys.platform == "darwin":
        for p in (
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ):
            add(p)
    else:
        for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge-stable", "microsoft-edge"):
            w = shutil.which(name)
            if w:
                add(Path(w))

    return [p for p in out if p.is_file()]


def _html_to_pdf_weasyprint(html: str) -> bytes:
    from weasyprint import HTML

    return HTML(string=html).write_pdf()


def _html_to_pdf_chromium(html: str, pdf_path: Path) -> None:
    """Print HTML to PDF using Chrome or Edge (headless)."""
    pdf_path = pdf_path.resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if pdf_path.exists():
        pdf_path.unlink()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html)
        html_path = Path(f.name)

    try:
        html_uri = html_path.resolve().as_uri()
        candidates = _chromium_candidates()
        if not candidates:
            raise FileNotFoundError("No Chrome/Edge executable found. Install Chrome or Edge, or set CHROME_PATH to chrome.exe / msedge.exe.")

        errors: list[str] = []
        for exe in candidates:
            for headless_flag in ("--headless=new", "--headless"):
                cmd = [
                    str(exe),
                    headless_flag,
                    "--disable-gpu",
                    "--no-sandbox",
                    "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf_path}",
                    html_uri,
                ]
                try:
                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                except OSError as e:
                    errors.append(f"{exe} ({headless_flag}): {e}")
                    continue
                if proc.returncode == 0 and pdf_path.is_file() and pdf_path.stat().st_size > 0:
                    return
                err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
                errors.append(f"{exe} ({headless_flag}): {err[:500]}")

        raise RuntimeError("Headless print-to-pdf failed:\n" + "\n".join(errors[:12]))
    finally:
        html_path.unlink(missing_ok=True)


def _write_pdf(html: str, out: Path, engine: str) -> None:
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    if engine == "chromium":
        _html_to_pdf_chromium(html, out)
        return
    if engine == "weasyprint":
        out.write_bytes(_html_to_pdf_weasyprint(html))
        return

    # auto
    try:
        out.write_bytes(_html_to_pdf_weasyprint(html))
    except (OSError, ImportError) as wp_err:
        try:
            _html_to_pdf_chromium(html, out)
        except Exception as ch_err:
            msg = (
                "Could not generate PDF.\n\n"
                f"WeasyPrint: {wp_err}\n\n"
                f"Chrome/Edge fallback: {ch_err}\n\n"
                "On Windows, install Google Chrome or Microsoft Edge, or install GTK for WeasyPrint:\n"
                "https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows"
            )
            raise SystemExit(msg) from ch_err


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate invoice PDF from test data.")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("invoice-sample.pdf"),
        help="Output PDF path (default: ./invoice-sample.pdf)",
    )
    parser.add_argument(
        "--paid",
        action="store_true",
        help="Include the sample paid-by-card block on the PDF",
    )
    parser.add_argument(
        "--engine",
        choices=("auto", "weasyprint", "chromium"),
        default="auto",
        help="PDF backend: auto (WeasyPrint then Chrome/Edge), weasyprint, or chromium (default: auto)",
    )
    args = parser.parse_args()

    invoice = _mock_invoice(paid=args.paid)
    line_items = list(invoice.line_items)
    applications = [
        SimpleNamespace(
            credit_note=SimpleNamespace(credit_note_number="CN-000012"),
            applied_amount=Decimal("15.00"),
            applied_at=datetime(2025, 12, 8, 14, 30, 0),
        )
    ]

    context = _build_pdf_context(invoice, line_items, applications)
    html = _render_html(PDF_TEMPLATE_VERSION, context)
    _write_pdf(html, args.out, args.engine)
    n = args.out.stat().st_size
    print(f"Wrote {args.out.resolve()} ({n} bytes)")


if __name__ == "__main__":
    main()
