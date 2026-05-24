from __future__ import annotations

import ast
from pathlib import Path


def _decorator_is_limiter_limit(decorator: ast.expr) -> bool:
    """Return True for @limiter.limit(...) regardless of args."""
    if not isinstance(decorator, ast.Call):
        return False
    func = decorator.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "limit"
        and isinstance(func.value, ast.Name)
        and func.value.id == "limiter"
    )


def _function_has_response_param(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    # For this project's convention, SlowAPI needs a kw-injectable `response` parameter
    # when `headers_enabled=True` (to write X-RateLimit-* headers).
    args = fn.args
    all_args = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    return any(a.arg == "response" for a in all_args)


def _iter_python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if p.is_file()]


def test_rate_limited_routes_require_response_param() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    app_root = repo_root / "app"
    assert app_root.exists(), f"Expected app/ at {app_root}"

    offenders: list[str] = []

    for py_file in _iter_python_files(app_root):
        # Skip Alembic envs or other non-app modules if they live under app/ in future.
        if "__pycache__" in py_file.parts:
            continue

        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            # Let other tooling handle syntax issues; this test focuses on signatures.
            continue

        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.decorator_list:
                continue

            if any(_decorator_is_limiter_limit(d) for d in node.decorator_list):
                if not _function_has_response_param(node):
                    offenders.append(f"{py_file.relative_to(repo_root)}:{node.lineno} {node.name}()")

    assert not offenders, (
        "Endpoints decorated with @limiter.limit(...) must include `response: Response` "
        "in the handler signature (SlowAPI headers_enabled=True requires it).\n\n"
        + "\n".join(offenders)
    )

