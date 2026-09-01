"""Unit tests for the F&O snapshot endpoint (fno-frontend-section, task 1.2).

Feature: fno-frontend-section

These tests exercise the thin ``GET /options/snapshot`` transport seam added to
``agents/deep-quant-loop/main.py`` (F4 task 1.1). The endpoint is **composition
only** (design AD-2): it reads the chain strikes via the existing F2 read layer,
calls ``options.compute_options_analytics`` (F2) and
``options_bias.classify_options_bias`` (F3) verbatim, and assembles their outputs
— preserving every ``null`` leaf as ``null`` — into the IPC payload. It adds NO
analytics of its own.

The whole F2/F3 read+compute layer is MONKEYPATCHED at the ``main`` module level
(the names the endpoint actually calls are bound there by ``from options import …``
/ ``from options_bias import …``), so the route runs end-to-end with NO live
QuestDB and NO real analytics. We then assert, against FastAPI's ``TestClient``:

  * (a) the available payload SHAPE — every top-level key, the per-strike chain
        rows, and the F2 analytics / F3 bias echoed back verbatim (R6.1);
  * (b) NEAREST-EXPIRY resolution when ``expiry`` is blank — the soonest expiry
        on/after today is resolved and flows to the read layer and the response,
        with a past-only fallback to the latest stored expiry (R9.1 composition);
  * (c) ``Unavailable_Marker`` PASSTHROUGH when no snapshot exists, and the F2
        marker passthrough (with ``last_snapshot_ts``) when a snapshot exists but
        F2 degrades (R8.1, R8.4);
  * (d) NO analytic is RECOMPUTED — the endpoint calls the existing F2/F3
        functions exactly once each and returns their outputs verbatim, never
        substituting a fabricated value for a ``null`` leaf (R9.1, R6.1).

The sys.path / import pattern mirrors the rest of the options test-suite (e.g.
``tests/test_options_chain_resolution_properties.py``).
"""

import os
import sys

import pytest

# Make the service package importable (main.py / options.py / options_bias.py
# live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from options import ChainSnapshot, StrikeQuote  # noqa: E402


client = TestClient(main.app)


# ── Fixtures: well-formed F2 analytics + F1 chain snapshot + F3 bias ──────────

def _analytics(snapshot_ts=1734511200000):
    """A well-formed F2 ``Options_Analytics_Result`` with a deliberate ``null``
    leaf (``futures_basis``) so the verbatim/no-fabrication assertions bite.

    ``per_strike`` carries the per-strike CE/PE IV the endpoint composes (never
    recomputes) into the chain rows' representative ``iv``.
    """
    return {
        "underlying": "NIFTY 50",
        "expiry": "2099-12-26",
        "snapshot_ts": snapshot_ts,
        "spot": 24010.5,
        "pcr_oi": 1.18,
        "pcr_volume": 0.94,
        "max_pain": 24000.0,
        "oi_buildup": {"call": "short_buildup", "put": "long_unwinding"},
        "iv_skew": {"put_minus_call": 0.021, "slope": -0.0003, "atm_iv": 0.132},
        "oi_walls": {"support": 23800.0, "resistance": 24200.0},
        "futures_basis": None,  # null leaf — must survive as null, never 0
        "per_strike": [
            {"strike": 24000.0, "ce": {"iv": 0.131}, "pe": {"iv": 0.145}},
            {"strike": 24100.0, "ce": {"iv": 0.150}, "pe": {"iv": None}},
        ],
    }


def _snapshot(snapshot_ts=1734511200000, expiry="2099-12-26"):
    """An F1 ``ChainSnapshot`` whose strikes match ``_analytics().per_strike``.

    24100 PE OI is ``None`` so the chain-row passthrough preserves a ``null``
    open-interest leaf (never substituting 0).
    """
    return ChainSnapshot(
        underlying="NIFTY 50",
        expiry=expiry,
        snapshot_ts=snapshot_ts,
        strikes=(
            StrikeQuote(
                strike=24000.0,
                ce_price=142.5, pe_price=98.0,
                ce_oi=1820000, pe_oi=2310000,
                ce_volume=12345.0, pe_volume=23456.0,
            ),
            StrikeQuote(
                strike=24100.0,
                ce_price=60.0, pe_price=140.0,
                ce_oi=900000, pe_oi=None,   # null OI leaf — preserved as null
                ce_volume=3456.0, pe_volume=None,
            ),
        ),
    )


def _bias():
    """A well-formed F3 ``Options_Bias`` label (returned verbatim by the route)."""
    return {
        "options_bias_state": "bullish",
        "alignment": "neutral",
        "chain_context": "own-chain",
        "signals": {
            "pcr_oi": 1.18,
            "max_pain": 24000.0,
            "max_pain_vs_spot": "below",
            "oi_walls": {"support": 23800.0, "resistance": 24200.0},
            "iv_skew_put_minus_call": 0.021,
            "futures_basis": None,
        },
    }


@pytest.fixture
def patched_layer(monkeypatch):
    """Patch the F1/F2/F3 functions the endpoint composes and record their calls.

    Returns the ``calls`` dict so a test can assert the endpoint invoked each
    existing function (composition) rather than recomputing analytics.
    """
    calls = {"compute": [], "classify": [], "read": [], "config": 0}

    analytics = _analytics()
    snapshot = _snapshot()
    bias = _bias()

    def fake_read(underlying, expiry):
        calls["read"].append((underlying, expiry))
        # `(latest, prior, live_spot)`. The endpoint now reads through
        # `read_chain_for_analytics`, which falls back to the exchange for a chain
        # QuestDB does not hold; `live_spot` is set only on that fallback path, so a
        # QuestDB hit reports None here.
        return snapshot, None, None

    def fake_compute(underlying, expiry):
        calls["compute"].append((underlying, expiry))
        return analytics

    def fake_classify(a, config, *args, **kwargs):
        # Record the EXACT analytics object handed to F3 so the test can prove
        # the endpoint forwards F2's output rather than rebuilding it.
        calls["classify"].append(a)
        return bias

    def fake_config():
        calls["config"] += 1
        return {"__sentinel__": "config"}

    monkeypatch.setattr(main, "read_chain_for_analytics", fake_read)
    monkeypatch.setattr(main, "compute_options_analytics", fake_compute)
    monkeypatch.setattr(main, "classify_options_bias", fake_classify)
    monkeypatch.setattr(main, "resolve_options_bias_config", fake_config)
    # Keep market status deterministic regardless of when the suite runs.
    monkeypatch.setattr(main, "_market_status", lambda: "open")

    return {
        "calls": calls,
        "analytics": analytics,
        "snapshot": snapshot,
        "bias": bias,
    }


# ─────────────────────────────────────────────────────────────────────────────
# (a) Available payload shape
# ─────────────────────────────────────────────────────────────────────────────

def test_available_payload_shape(patched_layer):
    """The success payload has the documented shape and echoes F2/F3 verbatim.

    Validates: Requirements 6.1
    """
    resp = client.get(
        "/options/snapshot",
        params={"underlying": "NIFTY 50", "expiry": "2099-12-26"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Top-level keys present, marker keys absent.
    assert set(body.keys()) == {
        "underlying", "expiry", "snapshot_ts", "market_status",
        "chain", "analytics", "bias",
    }
    assert "unavailable" not in body

    assert body["underlying"] == "NIFTY 50"
    assert body["expiry"] == "2099-12-26"
    assert body["snapshot_ts"] == patched_layer["analytics"]["snapshot_ts"]
    assert body["market_status"] == "open"

    # Analytics and bias are echoed back verbatim (no recompute, no reshaping).
    assert body["analytics"] == patched_layer["analytics"]
    assert body["bias"] == patched_layer["bias"]

    # One chain row per snapshot strike, ascending, with the documented fields.
    chain = body["chain"]
    assert [row["strike"] for row in chain] == [24000.0, 24100.0]
    for row in chain:
        assert set(row.keys()) == {
            "strike", "ce_oi", "pe_oi", "ce_price", "pe_price", "iv",
        }

    # OI / price passthrough from the F1 snapshot, preserving the null OI leaf.
    assert chain[0]["ce_oi"] == 1820000
    assert chain[0]["pe_oi"] == 2310000
    assert chain[1]["ce_oi"] == 900000
    assert chain[1]["pe_oi"] is None  # null preserved, never fabricated to 0

    # Representative IV composed from F2 per_strike (OTM-side rule): spot=24010.5,
    # so 24000<=spot -> pe IV (0.145); 24100>spot -> ce IV (0.150).
    assert chain[0]["iv"] == pytest.approx(0.145)
    assert chain[1]["iv"] == pytest.approx(0.150)


# ─────────────────────────────────────────────────────────────────────────────
# (b) Nearest-expiry resolution when expiry is blank
# ─────────────────────────────────────────────────────────────────────────────

def test_blank_expiry_resolves_nearest_and_flows_through(patched_layer, monkeypatch):
    """A blank expiry is resolved to the nearest available and flows to the read
    layer and the response.

    Validates: Requirements 9.1
    """
    # The chain has a past expiry and two future ones; the nearest future wins.
    monkeypatch.setattr(
        main, "_resolve_nearest_expiry", lambda u: "2099-12-26"
    )

    resp = client.get("/options/snapshot", params={"underlying": "NIFTY 50"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # The resolved expiry is reflected in the response and was the expiry passed
    # to the F2 read + compute layer (composition).
    assert body["expiry"] == "2099-12-26"
    assert patched_layer["calls"]["read"] == [("NIFTY 50", "2099-12-26")]
    assert patched_layer["calls"]["compute"] == [("NIFTY 50", "2099-12-26")]


def test_resolve_nearest_expiry_picks_soonest_future(monkeypatch):
    """``_resolve_nearest_expiry`` returns the soonest expiry on/after today."""
    rows = [["2000-01-01"], ["2099-01-01"], ["2099-12-26"]]
    monkeypatch.setattr(main, "_questdb_select", lambda sql: rows)

    assert main._resolve_nearest_expiry("NIFTY 50") == "2099-01-01"


def test_resolve_nearest_expiry_falls_back_to_latest_past(monkeypatch):
    """With only past expiries, the latest stored expiry is returned."""
    rows = [["2000-01-01"], ["2001-06-15"], ["2002-03-10"]]
    monkeypatch.setattr(main, "_questdb_select", lambda sql: rows)

    assert main._resolve_nearest_expiry("NIFTY 50") == "2002-03-10"


def test_resolve_nearest_expiry_empty_returns_blank(monkeypatch):
    """No stored expiry -> empty string (never raises)."""
    monkeypatch.setattr(main, "_questdb_select", lambda sql: [])
    assert main._resolve_nearest_expiry("NIFTY 50") == ""


# ─────────────────────────────────────────────────────────────────────────────
# (c) Unavailable_Marker passthrough
# ─────────────────────────────────────────────────────────────────────────────

def test_unavailable_when_no_expiry_resolvable(monkeypatch):
    """No resolvable expiry -> Unavailable_Marker (no read/compute attempted).

    Validates: Requirements 8.1
    """
    monkeypatch.setattr(main, "_resolve_nearest_expiry", lambda u: "")

    resp = client.get("/options/snapshot", params={"underlying": "NIFTY 50"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["unavailable"] is True
    assert body["underlying"] == "NIFTY 50"
    assert body["expiry"] == ""
    assert isinstance(body["reason"], str) and body["reason"].strip()
    assert "chain" not in body and "analytics" not in body


def test_unavailable_when_no_snapshot(monkeypatch):
    """A resolved expiry but no snapshot -> Unavailable_Marker with the expiry.

    Validates: Requirements 8.1
    """
    monkeypatch.setattr(
        # Neither QuestDB nor the exchange has a chain. The fallback coming back
        # empty too is what makes this genuinely unavailable rather than merely
        # un-ingested — the distinction this endpoint now draws.
        main, "read_chain_for_analytics", lambda u, e: (None, None, None)
    )

    resp = client.get(
        "/options/snapshot",
        params={"underlying": "NIFTY 50", "expiry": "2099-12-26"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["unavailable"] is True
    assert body["underlying"] == "NIFTY 50"
    assert body["expiry"] == "2099-12-26"
    assert isinstance(body["reason"], str) and body["reason"].strip()


def test_f2_marker_passthrough_with_last_snapshot_ts(monkeypatch):
    """A snapshot exists but F2 degrades -> F2 marker passed through verbatim,
    annotated with the last snapshot's timestamp.

    Validates: Requirements 8.4
    """
    snap = _snapshot(snapshot_ts=1734507600000)
    monkeypatch.setattr(
        main, "read_chain_for_analytics", lambda u, e: (snap, None, None)
    )
    f2_marker = {
        "underlying": "NIFTY 50",
        "expiry": "2099-12-26",
        "unavailable": True,
        "reason": "spot unavailable",
    }
    monkeypatch.setattr(main, "compute_options_analytics", lambda u, e: f2_marker)

    resp = client.get(
        "/options/snapshot",
        params={"underlying": "NIFTY 50", "expiry": "2099-12-26"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["unavailable"] is True
    assert body["reason"] == "spot unavailable"
    # The most-recent snapshot ts is surfaced so the UI can show the last state.
    assert body["last_snapshot_ts"] == 1734507600000


# ─────────────────────────────────────────────────────────────────────────────
# (d) No analytic recomputed — composes F2/F3 only
# ─────────────────────────────────────────────────────────────────────────────

def test_composes_f2_f3_without_recomputing(patched_layer):
    """The endpoint calls the existing F2/F3 functions exactly once each and
    returns their outputs verbatim — it recomputes no analytic.

    Validates: Requirements 9.1, 6.1
    """
    resp = client.get(
        "/options/snapshot",
        params={"underlying": "NIFTY 50", "expiry": "2099-12-26"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    calls = patched_layer["calls"]

    # F2 compute + F3 classify are each invoked exactly once (composition).
    assert calls["compute"] == [("NIFTY 50", "2099-12-26")]
    assert len(calls["classify"]) == 1
    assert calls["read"] == [("NIFTY 50", "2099-12-26")]

    # F3 was handed the EXACT object F2 produced (no intermediate recompute).
    assert calls["classify"][0] is patched_layer["analytics"]

    # The analytics/bias in the response are F2/F3's outputs verbatim, including
    # the null leaf which must never be fabricated into a number.
    assert body["analytics"] == patched_layer["analytics"]
    assert body["analytics"]["futures_basis"] is None
    assert body["bias"] == patched_layer["bias"]
    assert body["bias"]["signals"]["futures_basis"] is None
