from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock


def _revision_module():
    migration_path = (
        Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0082_billing_payments_foundation.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0082_billing_payments_foundation", migration_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0082_upgrade_keeps_legacy_invoice_columns(monkeypatch) -> None:
    mod = _revision_module()
    mock_op = MagicMock()
    monkeypatch.setattr(mod, "op", mock_op, raising=True)
    mod.upgrade()

    assert mock_op.create_table.call_count == 3
    assert mock_op.drop_column.call_count == 0


def test_0082_downgrade_drops_billing_tables_only(monkeypatch) -> None:
    mod = _revision_module()
    mock_op = MagicMock()
    monkeypatch.setattr(mod, "op", mock_op, raising=True)

    mod.downgrade()

    assert mock_op.add_column.call_count == 0
    assert mock_op.drop_table.call_count == 3
