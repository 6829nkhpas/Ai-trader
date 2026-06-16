# Feature: multi-agent-debate (task 15.6): calibration source smoke test
"""Smoke test for the conviction-calibration source (calibration.py, task 15.6).

Feature: multi-agent-debate

Validates: Requirements 10.5.

This smoke test proves the calibration entry point reads ONLY journal rows and
has NO backtest dependency:

  * ``import calibration`` succeeds and the pure ``conviction_calibration`` never
    imports or calls a backtest — ``conviction_calibration([])`` returns a dict
    that is not-applicable on empty input and never raises.
  * The ``calibration`` module has no hard dependency on ``backtest``: it neither
    exposes a ``backtest`` attribute nor contains a top-level ``import backtest`` /
    ``from backtest`` statement in its source.
  * ``conviction_calibration_from_journal()`` reads only persisted journal rows:
    pointed at an empty temp DB, it returns a not-applicable dict and never
    raises and never triggers a backtest. Its source lazily ``import journal``
    and references no ``backtest``.

The sys.path / import and temp-DB isolation patterns mirror the sibling
calibration and journal tests in this directory.
"""

import ast
import os
import sys

import pytest

# Make the service package importable (calibration.py / journal.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import calibration  # noqa: E402


def _calibration_source() -> str:
    """Return the calibration.py source text (for static import inspection)."""
    path = os.path.join(_SVC_DIR, "calibration.py")
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _top_level_imported_modules(source: str) -> set:
    """Set of module names imported at calibration.py's MODULE top level.

    Only ``import x`` / ``from x import ...`` statements that are direct children
    of the module body count — a lazy ``import journal`` inside a function is
    intentionally excluded, so this captures exactly the module's hard
    dependencies.
    """
    tree = ast.parse(source)
    modules: set = set()
    for node in tree.body:  # module top level only — not nested in functions
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module.split(".")[0])
    return modules


def test_import_succeeds_and_pure_calibration_is_total_on_empty():
    """``conviction_calibration([])`` is not-applicable and never raises."""
    result = calibration.conviction_calibration([])

    assert isinstance(result, dict)
    assert result["applicable"] is False
    assert result["trades_scored"] == 0
    # No backtest dependency leaked onto the module via the pure call.
    assert not hasattr(calibration, "backtest")


def test_calibration_module_has_no_backtest_dependency():
    """calibration.py imports no backtest at module load and exposes none."""
    # Runtime: the imported module object carries no backtest attribute.
    assert not hasattr(calibration, "backtest")

    # Static: no top-level ``import backtest`` / ``from backtest`` in the source.
    top_level = _top_level_imported_modules(_calibration_source())
    assert "backtest" not in top_level, (
        f"calibration.py must not import backtest at module load; "
        f"top-level imports were: {sorted(top_level)}"
    )


def test_from_journal_reads_only_journal_rows_no_backtest(tmp_path, monkeypatch):
    """The journal entry point reads an (empty) temp DB and triggers no backtest."""
    db_path = str(tmp_path / "smoke_journal.db")
    # Point the journal store at an empty throwaway DB BEFORE journal is imported.
    monkeypatch.setenv("JOURNAL_DB_PATH", db_path)

    # If journal was already imported by an earlier test, redirect its live path too.
    journal = sys.modules.get("journal")
    if journal is None:
        import journal  # noqa: F811  (lazy parity with calibration's own import)
    monkeypatch.setattr(journal, "JOURNAL_DB_PATH", db_path, raising=False)

    # Reads only recorded rows from the empty DB — never raises, never backtests.
    result = calibration.conviction_calibration_from_journal()

    assert isinstance(result, dict)
    assert result["applicable"] is False
    assert result["trades_scored"] == 0
    # No backtest module was pulled in as a side effect of the journal read.
    assert "backtest" not in calibration.__dict__


def test_from_journal_source_imports_journal_not_backtest():
    """conviction_calibration_from_journal lazily imports journal, never backtest."""
    src = _calibration_source()
    tree = ast.parse(src)

    func = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "conviction_calibration_from_journal"
    )

    # Collect every module name imported ANYWHERE inside the function body.
    imported: set = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])

    # It lazily imports journal (its only data source) and never imports backtest.
    assert "journal" in imported
    assert "backtest" not in imported

    # No name referenced inside the function resolves to a backtest symbol — the
    # function touches journal/conviction_calibration only, never a backtest API.
    referenced_names = {n.id for n in ast.walk(func) if isinstance(n, ast.Name)}
    referenced_attrs = {n.attr for n in ast.walk(func) if isinstance(n, ast.Attribute)}
    assert not any("backtest" in name for name in referenced_names | referenced_attrs)
