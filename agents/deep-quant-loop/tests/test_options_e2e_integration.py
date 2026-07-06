"""QuestDB-gated end-to-end integration test for the options engine (task 9.8).

Feature: options-analytics-engine

This test exercises the **full** ``compute_options_analytics`` pipeline against a
**real, running QuestDB** — the impure read layer (``read_latest_and_prior_snapshot``
/ ``read_spot`` / ``read_future_price``) wired to the pure analytic core, over the
same QuestDB HTTP ``/exec`` API the engine itself uses. It seeds a complete chain
(``option_chain_snapshots`` CE/PE legs across a few strikes at a prior and a latest
``snapshot_ts``, ``option_ticks`` volume, and ``live_ticks`` spot), then calls
``compute_options_analytics(underlying, expiry)`` and asserts the engine returns a
**populated success** ``Options_Analytics_Result`` (not an ``Unavailable_Marker``):

  * R6.1 — given a seeded latest snapshot + spot, the orchestrator returns the
           structurally-complete success shape (every top-level key present), the
           ``per_strike`` ladder is populated, and every numeric leaf is a finite
           number or ``None`` (never ``NaN`` / ``±inf``, never a missing key).

Like the sibling read-layer integration module (task 8.4) this talks to a live
store and is therefore **GATED**: when QuestDB is not reachable at
``QUESTDB_HTTP_URL`` (default ``http://127.0.0.1:9000``) the whole module SKIPS
gracefully rather than failing — a pass-by-skip is the expected outcome in an
environment with no local QuestDB.

Isolation & cleanup: all rows are seeded under a unique, far-future test
underlying / expiry (``__OAE_E2E_*`` / ``2099-12-26``) so real F1 data is never
read or clobbered. Tables are created ``IF NOT EXISTS`` (matching the F1 schema)
and the test rows live in their own far-future daily partition, which teardown
removes (dropping any table this module created outright, and otherwise dropping
just the far-future partition) on a best-effort basis.
"""

import datetime as dt
import math
import os
import sys

import pytest

# Make the service package importable (options.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import httpx  # noqa: E402

from options import (  # noqa: E402
    QUESTDB_HTTP_URL,
    compute_options_analytics,
    read_spot,
)

# ── Test fixture constants (unique + far-future so real data is never touched) ─
TEST_UNDERLYING = "__OAE_E2E_NIFTY__"
TEST_EXPIRY = "2099-12-26"
TEST_PARTITION = "2099-12-26"  # daily partition that holds every seeded row

# Two distinct capture timestamps one minute apart, in epoch microseconds.
_BASE = dt.datetime(2099, 12, 26, 9, 15, 0, tzinfo=dt.timezone.utc)
LATEST_MICROS = int(_BASE.timestamp() * 1_000_000)
PRIOR_MICROS = LATEST_MICROS - 60_000_000  # one minute earlier

# Per-strike instrument symbols (the join key between snapshots and ticks).
SYM_24000_CE = "OAE_E2E_24000_CE"
SYM_24000_PE = "OAE_E2E_24000_PE"
SYM_24100_CE = "OAE_E2E_24100_CE"
SYM_24100_PE = "OAE_E2E_24100_PE"
SYM_24200_CE = "OAE_E2E_24200_CE"
SYM_24200_PE = "OAE_E2E_24200_PE"

# Latest-snapshot CE/PE prices and OI per strike (a few strikes around spot).
LATEST_CHAIN = {
    24000.0: {"ce_price": 120.5, "pe_price": 80.25, "ce_oi": 1500, "pe_oi": 2200},
    24100.0: {"ce_price": 60.0, "pe_price": 140.0, "ce_oi": 900, "pe_oi": 1750},
    24200.0: {"ce_price": 30.0, "pe_price": 210.0, "ce_oi": 700, "pe_oi": 1300},
}
# Prior-snapshot CE/PE prices and OI per strike (distinct from latest, so the
# per-strike OI-buildup comparison has real ΔOI/Δprice to classify).
PRIOR_CHAIN = {
    24000.0: {"ce_price": 110.0, "pe_price": 85.0, "ce_oi": 1000, "pe_oi": 2000},
    24100.0: {"ce_price": 55.0, "pe_price": 150.0, "ce_oi": 800, "pe_oi": 1600},
    24200.0: {"ce_price": 28.0, "pe_price": 220.0, "ce_oi": 650, "pe_oi": 1250},
}
# Latest cumulative traded volume per symbol (drives PCR-by-volume).
EXPECTED_VOLUME = {
    SYM_24000_CE: 12345.0,
    SYM_24000_PE: 23456.0,
    SYM_24100_CE: 3456.0,
    SYM_24100_PE: 4567.0,
    SYM_24200_CE: 1234.0,
    SYM_24200_PE: 2345.0,
}
# Per-strike symbols keyed by (strike, option_type).
_SYM = {
    (24000.0, "CE"): SYM_24000_CE, (24000.0, "PE"): SYM_24000_PE,
    (24100.0, "CE"): SYM_24100_CE, (24100.0, "PE"): SYM_24100_PE,
    (24200.0, "CE"): SYM_24200_CE, (24200.0, "PE"): SYM_24200_PE,
}

# Spot ticks for the underlying: most-recent (later timestamp) must win.
SPOT_OLD = 23990.0
SPOT_LATEST = 24050.0

# Every top-level key the success ``Options_Analytics_Result`` must carry (R6.1).
EXPECTED_TOP_LEVEL_KEYS = {
    "underlying", "expiry", "spot", "snapshot_ts",
    "pcr_oi", "pcr_volume", "max_pain",
    "oi_buildup", "iv_skew", "oi_walls", "futures_basis", "per_strike",
}


# ── QuestDB transport helpers (mirror options._questdb_select's /exec shape) ──
def _exec(sql, timeout=10.0):
    """Run a statement against QuestDB ``/exec``; raise on transport/query error."""
    r = httpx.get(f"{QUESTDB_HTTP_URL}/exec", params={"query": sql}, timeout=timeout)
    r.raise_for_status()
    body = r.json()
    if isinstance(body, dict) and body.get("error"):
        raise RuntimeError(f"QuestDB query error: {body.get('error')} for {sql!r}")
    return body


def _questdb_available():
    """True iff a trivial query succeeds against the configured QuestDB."""
    try:
        r = httpx.get(
            f"{QUESTDB_HTTP_URL}/exec", params={"query": "SELECT 1"}, timeout=2.0
        )
        if r.status_code != 200:
            return False
        body = r.json()
        return not (isinstance(body, dict) and body.get("error"))
    except Exception:
        return False


def _table_exists(name):
    """True iff ``name`` already exists (so teardown knows whether to DROP it)."""
    try:
        body = _exec(f"SELECT 1 FROM {name} LIMIT 1", timeout=5.0)
        return isinstance(body, dict) and "dataset" in body
    except Exception:
        return False


# Module-level skip gate: every test here is skipped when QuestDB is unreachable.
_QUESTDB_UP = _questdb_available()
pytestmark = pytest.mark.skipif(
    not _QUESTDB_UP,
    reason=f"QuestDB not reachable at {QUESTDB_HTTP_URL}; end-to-end integration "
    "test is gated on a running QuestDB (pass-by-skip).",
)


def _ts_literal(micros):
    """A QuestDB timestamp literal from epoch microseconds."""
    return f"cast({int(micros)} as timestamp)"


def _seed_snapshots():
    """Seed the latest + prior ``option_chain_snapshots`` rows (CE & PE legs)."""
    rows = []
    for micros, chain in ((LATEST_MICROS, LATEST_CHAIN), (PRIOR_MICROS, PRIOR_CHAIN)):
        for strike, q in chain.items():
            rows.append(
                f"('{TEST_UNDERLYING}','{TEST_EXPIRY}',{strike},'CE',"
                f"'{_SYM[(strike, 'CE')]}',{q['ce_price']},{q['ce_oi']},"
                f"{_ts_literal(micros)})"
            )
            rows.append(
                f"('{TEST_UNDERLYING}','{TEST_EXPIRY}',{strike},'PE',"
                f"'{_SYM[(strike, 'PE')]}',{q['pe_price']},{q['pe_oi']},"
                f"{_ts_literal(micros)})"
            )
    _exec(
        "INSERT INTO option_chain_snapshots "
        "(underlying, expiry, strike, option_type, symbol, last_price, "
        "open_interest, snapshot_ts) VALUES " + ",".join(rows)
    )


def _seed_option_ticks():
    """Seed ``option_ticks`` so the LATEST volume per symbol is EXPECTED_VOLUME."""
    base = LATEST_MICROS
    rows = []
    for strike, q in LATEST_CHAIN.items():
        for opt in ("CE", "PE"):
            sym = _SYM[(strike, opt)]
            price = q["ce_price"] if opt == "CE" else q["pe_price"]
            oi = q["ce_oi"] if opt == "CE" else q["pe_oi"]
            vol = int(EXPECTED_VOLUME[sym])
            rows.append(
                f"('{sym}','{TEST_UNDERLYING}','{TEST_EXPIRY}',{strike},'{opt}',"
                f"{_ts_literal(base)},{price},{vol},{oi},{price - 0.5},{price + 0.5})"
            )
    _exec(
        "INSERT INTO option_ticks "
        "(symbol, underlying, expiry, strike, option_type, timestamp, "
        "last_traded_price, volume, open_interest, best_bid, best_ask) VALUES "
        + ",".join(rows)
    )


def _seed_live_ticks():
    """Seed two ``live_ticks`` rows; the later timestamp carries SPOT_LATEST."""
    rows = [
        f"('{TEST_UNDERLYING}',{SPOT_OLD},{_ts_literal(PRIOR_MICROS)})",
        f"('{TEST_UNDERLYING}',{SPOT_LATEST},{_ts_literal(LATEST_MICROS)})",
    ]
    _exec(
        "INSERT INTO live_ticks (symbol, last_traded_price, timestamp) VALUES "
        + ",".join(rows)
    )


@pytest.fixture(scope="module")
def seeded_questdb():
    """Create (IF NOT EXISTS) the F1 tables, seed the unique test rows, clean up.

    Yields nothing; the test reads back through ``compute_options_analytics``.
    Teardown drops any table this module created, and otherwise drops just the
    far-future test partition, on a best-effort basis so real F1 data is never
    disturbed.
    """
    pre_exist = {
        name: _table_exists(name)
        for name in ("option_chain_snapshots", "option_ticks", "live_ticks")
    }

    # Tables matching the F1 schema (no-ops when the real tables already exist).
    _exec(
        "CREATE TABLE IF NOT EXISTS option_chain_snapshots ("
        "underlying SYMBOL, expiry SYMBOL, strike DOUBLE, option_type SYMBOL, "
        "symbol SYMBOL, last_price DOUBLE, open_interest LONG, "
        "snapshot_ts TIMESTAMP) TIMESTAMP(snapshot_ts) PARTITION BY DAY"
    )
    _exec(
        "CREATE TABLE IF NOT EXISTS option_ticks ("
        "symbol SYMBOL, underlying SYMBOL, expiry SYMBOL, strike DOUBLE, "
        "option_type SYMBOL, timestamp TIMESTAMP, last_traded_price DOUBLE, "
        "volume LONG, open_interest LONG, best_bid DOUBLE, best_ask DOUBLE) "
        "TIMESTAMP(timestamp) PARTITION BY DAY"
    )
    _exec(
        "CREATE TABLE IF NOT EXISTS live_ticks ("
        "symbol SYMBOL, last_traded_price DOUBLE, timestamp TIMESTAMP) "
        "TIMESTAMP(timestamp) PARTITION BY DAY"
    )

    _seed_snapshots()
    _seed_option_ticks()
    _seed_live_ticks()

    # QuestDB applies writes asynchronously; wait until the seeded latest spot is
    # visible (bounded) so the read-back is not racing ingestion.
    import time

    deadline = time.time() + 10.0
    while time.time() < deadline:
        if read_spot(TEST_UNDERLYING) == SPOT_LATEST:
            break
        time.sleep(0.25)

    try:
        yield
    finally:
        for name in ("option_chain_snapshots", "option_ticks", "live_ticks"):
            try:
                if not pre_exist.get(name):
                    _exec(f"DROP TABLE IF EXISTS {name}")
                else:
                    # Real table pre-existed: remove only the far-future test
                    # partition that holds this module's rows.
                    _exec(f"ALTER TABLE {name} DROP PARTITION LIST '{TEST_PARTITION}'")
            except Exception:
                pass  # best-effort cleanup; never fail teardown


def _assert_finite_or_null(value, path):
    """Recursively assert every numeric leaf is a finite float/int or None.

    Walks dicts / lists / tuples; a ``float`` leaf must be finite (never NaN /
    ±inf), ``int`` / ``str`` / ``bool`` / ``None`` pass through. ``path`` names
    the leaf for a readable failure message (R6.2 / Property 11).
    """
    if isinstance(value, dict):
        for k, v in value.items():
            _assert_finite_or_null(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _assert_finite_or_null(v, f"{path}[{i}]")
    elif isinstance(value, bool):
        return  # bool is fine (and must be checked before int/float)
    elif isinstance(value, float):
        assert math.isfinite(value), f"non-finite numeric leaf at {path}: {value!r}"
    # int / str / None are all acceptable leaves.


def test_compute_options_analytics_returns_populated_result(seeded_questdb):
    """R6.1 — full pipeline over a seeded chain returns a populated success result."""
    result = compute_options_analytics(TEST_UNDERLYING, TEST_EXPIRY)

    # It must be the success shape, NOT an Unavailable_Marker.
    assert isinstance(result, dict), "expected a dict result"
    assert "unavailable" not in result, (
        f"expected a populated success result, got an Unavailable_Marker: {result!r}"
    )

    # Structurally complete: every top-level key present (R6.1).
    assert set(result.keys()) == EXPECTED_TOP_LEVEL_KEYS, (
        f"top-level keys mismatch: {sorted(result.keys())}"
    )

    # Chain identity echoes the seeded underlying / expiry, and the spot gate
    # passed with the most-recent seeded spot.
    assert result["underlying"] == TEST_UNDERLYING
    assert result["expiry"] == TEST_EXPIRY
    assert result["spot"] == SPOT_LATEST
    assert result["snapshot_ts"] == LATEST_MICROS // 1000

    # Nested analytic containers are present and well-shaped.
    assert set(result["oi_buildup"].keys()) == {"call", "put"}
    assert set(result["oi_walls"].keys()) == {"support", "resistance"}

    # The per-strike ladder is populated, one entry per seeded latest strike.
    per_strike = result["per_strike"]
    assert isinstance(per_strike, list)
    assert len(per_strike) == len(LATEST_CHAIN), (
        f"expected {len(LATEST_CHAIN)} per-strike entries, got {len(per_strike)}"
    )
    seen_strikes = set()
    for leg in per_strike:
        assert set(leg.keys()) == {"strike", "ce", "pe"}
        seen_strikes.add(leg["strike"])
        for side in ("ce", "pe"):
            assert set(leg[side].keys()) == {
                "iv", "delta", "gamma", "theta", "vega", "oi_buildup"
            }
            assert isinstance(leg[side]["oi_buildup"], str)
    assert seen_strikes == set(LATEST_CHAIN.keys())

    # PCR-by-OI is computable from the seeded OI (non-null, finite, positive).
    assert result["pcr_oi"] is not None
    assert math.isfinite(result["pcr_oi"]) and result["pcr_oi"] > 0.0

    # Every numeric leaf anywhere in the result is finite-or-null (R6.2).
    _assert_finite_or_null(result, "result")
