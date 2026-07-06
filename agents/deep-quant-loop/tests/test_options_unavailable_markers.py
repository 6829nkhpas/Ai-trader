"""Unit tests for the degraded Unavailable_Marker shape (options.py, task 9.7).

Feature: options-analytics-engine

These plain ``pytest`` unit tests exercise the two degradation gates in
``options.compute_options_analytics`` (Requirements 7.1, 7.2):

  * **Missing snapshot (R7.1).** When ``read_latest_and_prior_snapshot`` yields
    ``(None, None)`` the orchestrator returns an ``Unavailable_Marker`` whose
    reason names the missing chain — never computing over fabricated data.
  * **Missing spot (R7.2).** When a snapshot exists but ``read_spot`` returns
    ``None`` the orchestrator returns an ``Unavailable_Marker`` with a
    spot-related reason rather than computing spot-relative analytics from a
    fabricated spot.

In both cases the marker carries ``unavailable: True``, a descriptive ``reason``,
and the chain identity (``underlying`` / ``expiry``), while the analytic fields
(``pcr_oi``, ``per_strike``, ``max_pain``, ...) are **omitted** — mirroring
``regime.py::_unavailable`` / ``rs.py::_rs_unavailable``.

The impure read layer is isolated with ``monkeypatch`` so no QuestDB is required:
the read functions on the ``options`` module are patched to return the sentinels
that drive each gate. Mirrors the environment-isolation convention in
``test_options_config.py`` while using plain example-based tests.
"""

import os
import sys

import pytest

# Make the service package importable (options.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import options  # noqa: E402
from options import (  # noqa: E402
    ChainSnapshot,
    StrikeQuote,
    compute_options_analytics,
)

# Identity used across the tests.
_UNDERLYING = "NIFTY 50"
_EXPIRY = "2024-12-26"

# The full set of analytic fields the success-shape Options_Analytics_Result
# carries. None of these may appear on a degraded Unavailable_Marker — they are
# omitted (never defaulted or fabricated).
_ANALYTIC_FIELDS = (
    "spot",
    "snapshot_ts",
    "pcr_oi",
    "pcr_volume",
    "max_pain",
    "per_strike",
    "call_oi_buildup",
    "put_oi_buildup",
    "iv_skew",
    "oi_walls",
    "futures_basis",
)


def _sample_snapshot() -> ChainSnapshot:
    """A minimal, well-formed two-strike chain snapshot for the missing-spot gate."""
    return ChainSnapshot(
        underlying=_UNDERLYING,
        expiry=_EXPIRY,
        snapshot_ts=1_700_000_000_000,
        strikes=(
            StrikeQuote(
                strike=24000.0,
                ce_price=120.0, pe_price=80.0,
                ce_oi=1500.0, pe_oi=1800.0,
                ce_volume=500.0, pe_volume=700.0,
            ),
            StrikeQuote(
                strike=24100.0,
                ce_price=90.0, pe_price=110.0,
                ce_oi=1200.0, pe_oi=2100.0,
                ce_volume=400.0, pe_volume=900.0,
            ),
        ),
    )


def _assert_is_unavailable_marker(result):
    """Shared assertions: marker carries identity + unavailable + reason, no analytics."""
    assert isinstance(result, dict)

    # unavailable flag is present and exactly True.
    assert result.get("unavailable") is True

    # Chain identity is reported.
    assert result.get("underlying") == _UNDERLYING
    assert result.get("expiry") == _EXPIRY

    # A descriptive, non-empty reason string is present.
    reason = result.get("reason")
    assert isinstance(reason, str)
    assert reason.strip() != ""

    # Analytic fields are OMITTED — never defaulted or fabricated.
    for field in _ANALYTIC_FIELDS:
        assert field not in result, f"analytic field {field!r} must be omitted"

    # The marker carries only the identity + unavailable + reason keys.
    assert set(result.keys()) == {"underlying", "expiry", "unavailable", "reason"}


# ─────────────────────────────────────────────────────────────────────────────
# Missing snapshot → Unavailable_Marker (Requirement 7.1)
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_snapshot_returns_unavailable_marker(monkeypatch):
    """(None, None) from the snapshot reader degrades to an Unavailable_Marker (R7.1)."""
    monkeypatch.setattr(
        options, "read_latest_and_prior_snapshot",
        lambda underlying, expiry: (None, None),
    )
    # These should never be reached, but guard so a regression can't read QuestDB.
    monkeypatch.setattr(options, "read_spot", lambda underlying: 24000.0)
    monkeypatch.setattr(options, "read_future_price", lambda underlying: None)

    result = compute_options_analytics(_UNDERLYING, _EXPIRY)

    _assert_is_unavailable_marker(result)


def test_missing_snapshot_reason_mentions_missing_chain(monkeypatch):
    """The missing-snapshot reason names the missing chain (snapshot + identity)."""
    monkeypatch.setattr(
        options, "read_latest_and_prior_snapshot",
        lambda underlying, expiry: (None, None),
    )
    monkeypatch.setattr(options, "read_spot", lambda underlying: 24000.0)
    monkeypatch.setattr(options, "read_future_price", lambda underlying: None)

    result = compute_options_analytics(_UNDERLYING, _EXPIRY)

    reason = result["reason"].lower()
    assert "snapshot" in reason
    # Identity surfaced in the reason text.
    assert _UNDERLYING.lower() in reason
    assert _EXPIRY.lower() in reason


def test_missing_snapshot_does_not_read_spot(monkeypatch):
    """The missing-snapshot gate short-circuits before spot is read."""
    monkeypatch.setattr(
        options, "read_latest_and_prior_snapshot",
        lambda underlying, expiry: (None, None),
    )

    spot_calls = {"n": 0}

    def _tracking_spot(underlying):
        spot_calls["n"] += 1
        return 24000.0

    monkeypatch.setattr(options, "read_spot", _tracking_spot)
    monkeypatch.setattr(options, "read_future_price", lambda underlying: None)

    compute_options_analytics(_UNDERLYING, _EXPIRY)

    assert spot_calls["n"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Missing spot → Unavailable_Marker (Requirement 7.2)
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_spot_returns_unavailable_marker(monkeypatch):
    """A snapshot with no spot degrades to an Unavailable_Marker (R7.2)."""
    snapshot = _sample_snapshot()
    monkeypatch.setattr(
        options, "read_latest_and_prior_snapshot",
        lambda underlying, expiry: (snapshot, None),
    )
    monkeypatch.setattr(options, "read_spot", lambda underlying: None)
    monkeypatch.setattr(options, "read_future_price", lambda underlying: None)

    result = compute_options_analytics(_UNDERLYING, _EXPIRY)

    _assert_is_unavailable_marker(result)


def test_missing_spot_reason_mentions_spot(monkeypatch):
    """The missing-spot reason is spot-related and names the underlying."""
    snapshot = _sample_snapshot()
    monkeypatch.setattr(
        options, "read_latest_and_prior_snapshot",
        lambda underlying, expiry: (snapshot, None),
    )
    monkeypatch.setattr(options, "read_spot", lambda underlying: None)
    monkeypatch.setattr(options, "read_future_price", lambda underlying: None)

    result = compute_options_analytics(_UNDERLYING, _EXPIRY)

    reason = result["reason"].lower()
    assert "spot" in reason
    assert _UNDERLYING.lower() in reason


def test_missing_spot_omits_analytic_fields(monkeypatch):
    """Even with a valid snapshot present, no analytic fields leak when spot is None."""
    snapshot = _sample_snapshot()
    monkeypatch.setattr(
        options, "read_latest_and_prior_snapshot",
        lambda underlying, expiry: (snapshot, None),
    )
    monkeypatch.setattr(options, "read_spot", lambda underlying: None)
    monkeypatch.setattr(options, "read_future_price", lambda underlying: None)

    result = compute_options_analytics(_UNDERLYING, _EXPIRY)

    for field in _ANALYTIC_FIELDS:
        assert field not in result
