"""Bug-condition exploration test — Defect A (no-data marker), backend/Python.

Feature: fno-data-and-search-fix (bugfix)

Property 1 (Bug Condition), backend seam — "F&O Data Flows or Fails With a
Specific, Honest Reason":

    For all requests satisfying ``isBugCondition_A`` with
    ``ingestion_state = "never_populated"`` (a configured underlying whose
    ``option_chain_snapshots`` table is empty, so every request degrades to an
    ``Unavailable_Marker``), the marker returned by ``main.options_snapshot``
    SHALL carry a machine-readable ``reason_code`` in
    ``{ no_expiry, no_snapshot, analytics_degraded }`` (and ``last_snapshot_ts``
    when a prior snapshot exists).

    Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2, 2.4

*** EXPLORATION TEST — EXPECTED TO FAIL ON UNFIXED CODE ***

The unfixed ``options_snapshot`` returns a marker carrying only the generic human
``reason`` string ("no chain snapshot available for NIFTY 50") with NO
machine-readable ``reason_code``. The failure of this test is the informative,
expected outcome: it proves the marker cannot be classified by cause. DO NOT fix
the test or the code here — task 3.2 enriches the marker and task 3.5 re-runs
this same test to confirm the fix.

Isolation: this drives the pure endpoint function directly with the QuestDB read
primitives (``_questdb_select`` / ``read_latest_and_prior_snapshot`` /
``compute_options_analytics``) MONKEYPATCHED, so it needs no live QuestDB, no
network, and no ingestion.
"""

import os
import sys
from unittest import mock

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

# ── Make the service package importable (main.py lives one level up) ──────────
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import main  # noqa: E402

# The machine-readable causes the fixed marker must use (design 3.2 / R2.2, R2.3).
VALID_REASON_CODES = {"no_expiry", "no_snapshot", "analytics_degraded"}

# Configured index underlyings (bounded selector — resolve_fno_config; R3.6).
CONFIGURED_UNDERLYINGS = ["NIFTY 50", "BANKNIFTY", "FINNIFTY"]


class _FakeSnapshot:
    """Minimal stand-in for options.ChainSnapshot (a prior snapshot exists)."""

    def __init__(self, snapshot_ts):
        self.snapshot_ts = snapshot_ts
        self.strikes = ()  # no strikes needed for the marker branches


# ── Scenario 1: never_populated → no expiry resolvable → reason_code no_expiry ─
@settings(max_examples=5, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(underlying=st.sampled_from(CONFIGURED_UNDERLYINGS))
def test_never_populated_marker_carries_no_expiry_reason_code(underlying):
    """isBugCondition_A / never_populated: empty snapshot table → no_expiry."""
    # Empty option_chain_snapshots table: _resolve_nearest_expiry reads no rows.
    with mock.patch.object(main, "_questdb_select", return_value=[]):
        result = main.options_snapshot(underlying)

    assert result.get("unavailable") is True, result
    # EXPECTED FAIL on unfixed code: no `reason_code` key at all.
    assert result.get("reason_code") in VALID_REASON_CODES, (
        f"marker for {underlying!r} carries no machine-readable reason_code "
        f"(got {result!r})"
    )
    assert result["reason_code"] == "no_expiry", result


# ── Scenario 2: expiry resolves but no snapshot rows → reason_code no_snapshot ─
@settings(max_examples=5, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(underlying=st.sampled_from(CONFIGURED_UNDERLYINGS))
def test_expiry_resolved_but_no_rows_carries_no_snapshot_reason_code(underlying):
    """isBugCondition_A: nearest expiry resolves, but F2 read returns None."""
    # A nearest expiry resolves (one distinct expiry row), but the chain read
    # for that expiry returns None (no snapshot rows persisted).
    with mock.patch.object(main, "_questdb_select", return_value=[["2099-12-26"]]), \
         mock.patch.object(main, "read_chain_for_analytics", return_value=(None, None, None)):
        result = main.options_snapshot(underlying)

    assert result.get("unavailable") is True, result
    # EXPECTED FAIL on unfixed code: no `reason_code` key at all.
    assert result.get("reason_code") in VALID_REASON_CODES, (
        f"marker for {underlying!r} carries no machine-readable reason_code "
        f"(got {result!r})"
    )
    assert result["reason_code"] == "no_snapshot", result


# ── Scenario 3: analytics degrade despite a snapshot → analytics_degraded ─────
@settings(max_examples=5, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    underlying=st.sampled_from(CONFIGURED_UNDERLYINGS),
    snapshot_ts=st.integers(min_value=1_600_000_000_000, max_value=2_000_000_000_000),
)
def test_analytics_degraded_marker_carries_reason_code_and_last_snapshot_ts(
    underlying, snapshot_ts
):
    """isBugCondition_A: a snapshot exists but F2 degrades → analytics_degraded.

    A prior snapshot exists here, so the marker must ALSO carry ``last_snapshot_ts``
    (the unfixed code already sets it in this branch) — but it still lacks the
    machine-readable ``reason_code`` the property requires.
    """
    fake_latest = _FakeSnapshot(snapshot_ts)
    degraded_analytics = {"unavailable": True, "reason": "spot unavailable"}

    with mock.patch.object(main, "_questdb_select", return_value=[["2099-12-26"]]), \
         mock.patch.object(
             main, "read_chain_for_analytics", return_value=(fake_latest, None, None)
         ), \
         mock.patch.object(main, "compute_options_analytics", return_value=degraded_analytics):
        result = main.options_snapshot(underlying)

    assert result.get("unavailable") is True, result
    # A prior snapshot exists → last_snapshot_ts must be present (passes today).
    assert result.get("last_snapshot_ts") == snapshot_ts, result
    # EXPECTED FAIL on unfixed code: no `reason_code` key at all.
    assert result.get("reason_code") in VALID_REASON_CODES, (
        f"degraded marker for {underlying!r} carries no machine-readable "
        f"reason_code (got {result!r})"
    )
    assert result["reason_code"] == "analytics_degraded", result
