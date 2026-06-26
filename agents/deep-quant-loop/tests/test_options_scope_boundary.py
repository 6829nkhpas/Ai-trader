"""Scope-boundary verification tests for the Options Analytics Engine (options.py, task 9.9).

Feature: options-analytics-engine — Requirement 10 (Scope boundary).

These plain ``pytest`` tests assert that Phase F2 stays strictly inside its
analytics-only mandate and does not drift into the responsibilities of later
phases (F3 agent tool, F4 UI) or fabricate trading decisions:

  * R10.1 — ``options.py`` exposes NO agent tool: no ``@tool`` decorator and no
    import of a tool-registration decorator; no function is tool-decorated.
  * R10.2 — ``options.py`` adds NO frontend UI: it imports no UI / web-serving
    framework (streamlit, tkinter, flask, fastapi, ...).
  * R10.3 — the assembled result / unavailable-marker schema carries NO
    ``action`` / ``decision`` / ``conviction`` field and NO ``BUY`` / ``SELL`` /
    ``HOLD`` value, and the source uses none of those as output-field literals.
  * R10.4 — the read/query layer reads ONLY the F1 tables
    (``option_chain_snapshots``, ``option_ticks``, ``live_ticks``); no other
    table and no historical / archive table is referenced.
  * R10.5 — analytics operate over the bounded snapshot's strikes only: the
    per-strike output length equals the input strike count (no expansion to an
    unbounded chain).

Mirrors the import / path-bootstrap convention in ``test_options_config.py``.
Uses plain example-based assertions (no Hypothesis), as the task specifies.
"""

import ast
import os
import re
import sys

# Make the service package importable (options.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import options  # noqa: E402
from options import (  # noqa: E402
    ChainSnapshot,
    StrikeQuote,
    assemble_result,
    resolve_options_config,
    _options_unavailable,
)

# ─────────────────────────────────────────────────────────────────────────────
# Module source + parsed AST (the source-scan checks operate on these).
# ─────────────────────────────────────────────────────────────────────────────

_OPTIONS_PATH = os.path.join(_SVC_DIR, "options.py")
with open(_OPTIONS_PATH, "r", encoding="utf-8") as _fh:
    _SOURCE = _fh.read()

_TREE = ast.parse(_SOURCE)


def _imported_module_roots():
    """Top-level module names imported by options.py (e.g. ``flask`` from ``flask.app``)."""
    roots = set()
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _imported_names():
    """Bound names introduced by ``from ... import name`` statements."""
    names = set()
    for node in ast.walk(_TREE):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _decorator_name(dec):
    """Best-effort dotted name of a decorator node (``@a.b`` -> ``b``, ``@x()`` -> ``x``)."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _small_chain():
    """A bounded, ATM-band chain snapshot with a known, fixed strike count."""
    strikes = (
        StrikeQuote(strike=95.0, ce_price=7.0, pe_price=2.0,
                    ce_oi=800.0, pe_oi=1500.0, ce_volume=400.0, pe_volume=900.0),
        StrikeQuote(strike=100.0, ce_price=4.0, pe_price=4.0,
                    ce_oi=1200.0, pe_oi=1200.0, ce_volume=600.0, pe_volume=600.0),
        StrikeQuote(strike=105.0, ce_price=2.0, pe_price=7.5,
                    ce_oi=1600.0, pe_oi=700.0, ce_volume=1000.0, pe_volume=300.0),
    )
    return ChainSnapshot(
        underlying="NIFTY", expiry="2025-12-25", snapshot_ts=1_700_000_000_000,
        strikes=strikes,
    )


def _collect_keys_and_str_values(obj, keys, str_values):
    """Recursively gather every dict key and every str leaf into ``keys`` / ``str_values``."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                keys.add(k)
            _collect_keys_and_str_values(v, keys, str_values)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_keys_and_str_values(v, keys, str_values)
    elif isinstance(obj, str):
        str_values.add(obj)


# ─────────────────────────────────────────────────────────────────────────────
# R10.1 — no agent tool is exposed (that is Phase F3).
# ─────────────────────────────────────────────────────────────────────────────

def test_no_tool_decorator_in_source():
    """The source contains no ``@tool`` decorator usage (R10.1)."""
    assert re.search(r"@tool\b", _SOURCE) is None, (
        "options.py must not use an @tool decorator (agent tool is Phase F3)"
    )


def test_no_tool_registration_import():
    """No tool-registration decorator (a bound name ``tool``) is imported (R10.1)."""
    assert "tool" not in _imported_names(), (
        "options.py must not import a `tool` decorator (agent tool is Phase F3)"
    )


def test_no_function_is_tool_decorated():
    """No function/async-function in the module is decorated as a tool (R10.1)."""
    tool_decorated = []
    for node in ast.walk(_TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if _decorator_name(dec) == "tool":
                    tool_decorated.append(node.name)
    assert tool_decorated == [], (
        f"options.py must define no tool-decorated functions; found: {tool_decorated}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# R10.2 — no frontend UI / web-serving framework is added (that is Phase F4).
# ─────────────────────────────────────────────────────────────────────────────

_FORBIDDEN_UI_MODULES = {
    "streamlit", "tkinter", "flask", "fastapi", "dash", "gradio",
    "uvicorn", "starlette", "django", "jinja2", "matplotlib", "plotly",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "kivy", "wx", "pygame",
}


def test_no_ui_or_serving_imports():
    """options.py imports no UI / web-serving framework (R10.2)."""
    offending = _imported_module_roots() & _FORBIDDEN_UI_MODULES
    assert offending == set(), (
        f"options.py must add no UI/frontend (Phase F4); found imports: {sorted(offending)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# R10.3 — the result/marker schema emits no decision field or BUY/SELL/HOLD value.
# ─────────────────────────────────────────────────────────────────────────────

_DECISION_KEYS = {"action", "decision", "conviction"}
_DECISION_VALUES = {"BUY", "SELL", "HOLD"}


def test_assembled_result_has_no_decision_fields_or_values():
    """A computed result carries no action/decision/conviction key and no BUY/SELL/HOLD value (R10.3)."""
    config = resolve_options_config()
    result = assemble_result(
        latest=_small_chain(), prior=None, spot=100.0,
        future_price=None, config=config,
    )

    keys, str_values = set(), set()
    _collect_keys_and_str_values(result, keys, str_values)

    leaked_keys = keys & _DECISION_KEYS
    assert leaked_keys == set(), f"result schema leaks decision keys: {sorted(leaked_keys)}"

    upper_values = {v.strip().upper() for v in str_values}
    leaked_values = upper_values & _DECISION_VALUES
    assert leaked_values == set(), f"result carries decision values: {sorted(leaked_values)}"


def test_unavailable_marker_has_no_decision_fields_or_values():
    """The unavailable marker carries no action/decision/conviction key and no BUY/SELL/HOLD value (R10.3)."""
    marker = _options_unavailable("NIFTY", "2025-12-25", "no chain snapshot available")
    assert marker.get("unavailable") is True

    keys, str_values = set(), set()
    _collect_keys_and_str_values(marker, keys, str_values)

    assert keys & _DECISION_KEYS == set()
    assert {v.strip().upper() for v in str_values} & _DECISION_VALUES == set()


def test_source_uses_no_decision_field_or_value_literals():
    """The source uses no action/decision/conviction key literal nor a quoted BUY/SELL/HOLD value (R10.3).

    Scans only for *quoted* literals (Python string literals), so the docstring's
    prose mention of "emits no BUY/SELL/HOLD" is not a false positive.
    """
    for key in _DECISION_KEYS:
        pattern = r"""["']""" + key + r"""["']\s*:"""
        assert re.search(pattern, _SOURCE) is None, (
            f"options.py must not use {key!r} as an output-field key"
        )
    for value in _DECISION_VALUES:
        pattern = r"""["']""" + value + r"""["']"""
        assert re.search(pattern, _SOURCE) is None, (
            f"options.py must not emit {value!r} as a literal output value"
        )


# ─────────────────────────────────────────────────────────────────────────────
# R10.4 — the read layer reads only the F1 tables; no archive/historical table.
# ─────────────────────────────────────────────────────────────────────────────

_F1_TABLES = {"option_chain_snapshots", "option_ticks", "live_ticks"}


def test_read_layer_reads_only_f1_tables():
    """Every ``FROM <table>`` in the source targets an F1 table — nothing else (R10.4)."""
    # Match the uppercase SQL keyword (docstring prose uses lowercase "from").
    referenced = set(re.findall(r"FROM\s+(\w+)", _SOURCE))
    assert referenced, "expected the read layer to issue SELECT ... FROM <table> queries"
    extraneous = referenced - _F1_TABLES
    assert extraneous == set(), (
        f"read layer must reference only the F1 tables {_F1_TABLES}; "
        f"found extra tables: {sorted(extraneous)}"
    )


def test_read_layer_references_no_archive_table():
    """No referenced table is a historical / archive / backfill store (R10.4)."""
    referenced = set(re.findall(r"FROM\s+(\w+)", _SOURCE))
    archive_like = {
        t for t in referenced
        if any(token in t.lower() for token in ("archive", "historical", "backfill", "history"))
    }
    assert archive_like == set(), (
        f"options.py must not read a backfilled historical archive; found: {sorted(archive_like)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# R10.5 — analytics operate over the bounded snapshot's strikes only.
# ─────────────────────────────────────────────────────────────────────────────

def test_per_strike_length_equals_input_strike_count():
    """The per-strike output spans exactly the snapshot's strikes (R10.5)."""
    chain = _small_chain()
    config = resolve_options_config()
    result = assemble_result(
        latest=chain, prior=None, spot=100.0, future_price=None, config=config,
    )

    assert "per_strike" in result
    assert len(result["per_strike"]) == len(chain.strikes)

    # Every emitted strike is one that was present in the bounded input snapshot
    # (no fabricated / interpolated strikes beyond the configured band).
    input_strikes = {q.strike for q in chain.strikes}
    output_strikes = {row["strike"] for row in result["per_strike"]}
    assert output_strikes == input_strikes


def test_analytics_scale_with_bounded_strike_count():
    """A larger bounded chain yields proportionally more per-strike rows — bounded, not unbounded (R10.5)."""
    config = resolve_options_config()

    five = tuple(
        StrikeQuote(strike=float(k), ce_price=4.0, pe_price=4.0,
                    ce_oi=1000.0, pe_oi=1000.0, ce_volume=500.0, pe_volume=500.0)
        for k in (90, 95, 100, 105, 110)
    )
    chain5 = ChainSnapshot(
        underlying="NIFTY", expiry="2025-12-25",
        snapshot_ts=1_700_000_000_000, strikes=five,
    )
    result5 = assemble_result(
        latest=chain5, prior=None, spot=100.0, future_price=None, config=config,
    )

    assert len(result5["per_strike"]) == 5 == len(chain5.strikes)
