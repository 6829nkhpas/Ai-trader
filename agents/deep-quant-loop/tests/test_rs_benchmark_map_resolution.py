"""Unit test for Benchmark_Map resolution and override (rs.py, task 5.5).

Feature: relative-strength-context

These example-based unit tests pin down two concrete behaviours of
:func:`rs.resolve_benchmark` (the Benchmark_Map):

  * A known mapped symbol resolves to its documented default Benchmark_Index —
    a bank symbol (``SBIN``) resolves to ``BANKNIFTY`` via ``DEFAULT_BENCHMARK_MAP``
    (Requirement 2.1).
  * An ``RS_BENCHMARK_MAP`` override supplied via configuration is respected —
    both adding a brand-new symbol→benchmark entry and overriding a documented
    default entry to a custom benchmark (Requirement 2.3).

Env isolation is handled with pytest's ``monkeypatch`` fixture, which records
and restores the prior process environment for each test, so the ``RS_*``
variables one test sets never leak into another.

The sys.path / import pattern mirrors the sibling RS test modules: the service
directory (one level up, where ``rs.py`` lives) is prepended to ``sys.path`` so
``rs`` is importable when pytest is run from anywhere.

Validates: Requirements 2.1, 2.3
"""

import os
import sys

# Make the service package importable (rs.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import rs  # noqa: E402
from rs import (  # noqa: E402
    DEFAULT_BENCHMARK_MAP,
    ENV_RS_BENCHMARK_MAP,
    ENV_RS_DEFAULT_BENCHMARK,
    resolve_benchmark,
)

# The Benchmark_Map env vars the resolver reads. Each test clears BOTH so the
# documented defaults are the sole influence unless a test sets an override.
_ALL_BENCHMARK_ENV_VARS = (ENV_RS_DEFAULT_BENCHMARK, ENV_RS_BENCHMARK_MAP)


def _clear_benchmark_env(monkeypatch):
    """Remove every RS benchmark env var so only defaults / explicit overrides
    set within a test influence resolution. ``monkeypatch.delenv`` restores the
    prior value automatically at test teardown."""
    for name in _ALL_BENCHMARK_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_mapped_bank_symbol_resolves_to_banknifty(monkeypatch):
    """A known mapped bank symbol resolves to its documented benchmark.

    ``SBIN`` is a documented ``DEFAULT_BENCHMARK_MAP`` entry → ``BANKNIFTY``
    (Requirement 2.1). With no explicit benchmark argument and no environment
    override, the default map alone drives the result.
    """
    _clear_benchmark_env(monkeypatch)

    # Guard the test's premise: SBIN is genuinely a documented default entry.
    assert DEFAULT_BENCHMARK_MAP.get("SBIN") == "BANKNIFTY"

    assert resolve_benchmark("SBIN") == "BANKNIFTY"
    # Case-insensitive on the symbol side, and an explicit ``None`` is the same
    # as omitting the argument.
    assert resolve_benchmark("sbin") == "BANKNIFTY"
    assert resolve_benchmark("SBIN", None) == "BANKNIFTY"


def test_rs_benchmark_map_override_adds_new_entry(monkeypatch):
    """An ``RS_BENCHMARK_MAP`` entry added via configuration is respected.

    A symbol with no documented default entry (``RELIANCE``) gains a benchmark
    purely through the ``RS_BENCHMARK_MAP`` environment override (Requirement
    2.3) — extensible without code changes.
    """
    _clear_benchmark_env(monkeypatch)

    # Premise: RELIANCE is NOT a documented default entry, so without the
    # override it would fall through to the default Benchmark_Index.
    assert "RELIANCE" not in DEFAULT_BENCHMARK_MAP

    monkeypatch.setenv(ENV_RS_BENCHMARK_MAP, "RELIANCE:NIFTY 50")

    assert resolve_benchmark("RELIANCE") == "NIFTY 50"


def test_rs_benchmark_map_override_replaces_default_entry(monkeypatch):
    """An ``RS_BENCHMARK_MAP`` entry overrides a documented default entry.

    The configurable override takes precedence over ``DEFAULT_BENCHMARK_MAP`` so
    a mapped symbol can be redirected to a custom benchmark without code changes
    (Requirement 2.3).
    """
    _clear_benchmark_env(monkeypatch)

    # Premise: SBIN's documented default is BANKNIFTY; we override it.
    assert DEFAULT_BENCHMARK_MAP.get("SBIN") == "BANKNIFTY"

    monkeypatch.setenv(ENV_RS_BENCHMARK_MAP, "SBIN:CUSTOMINDEX")

    assert resolve_benchmark("SBIN") == "CUSTOMINDEX"


def test_rs_benchmark_map_override_supports_multiple_entries(monkeypatch):
    """A multi-entry ``RS_BENCHMARK_MAP`` honours each comma-separated entry,
    mixing a new entry with a default override (Requirement 2.3)."""
    _clear_benchmark_env(monkeypatch)

    monkeypatch.setenv(ENV_RS_BENCHMARK_MAP, "RELIANCE:NIFTY 50,SBIN:CUSTOMINDEX")

    assert resolve_benchmark("RELIANCE") == "NIFTY 50"
    assert resolve_benchmark("SBIN") == "CUSTOMINDEX"
    # An unrelated mapped symbol still resolves via the documented default map.
    assert resolve_benchmark("HDFCBANK") == "BANKNIFTY"


def test_explicit_benchmark_argument_wins_over_map(monkeypatch):
    """An explicit non-empty ``benchmark`` argument takes precedence over both
    the override map and the documented defaults (Requirement 4.2 precedence)."""
    _clear_benchmark_env(monkeypatch)
    monkeypatch.setenv(ENV_RS_BENCHMARK_MAP, "SBIN:CUSTOMINDEX")

    # Explicit argument beats the override which itself beats the default map.
    assert resolve_benchmark("SBIN", "FINNIFTY") == "FINNIFTY"
