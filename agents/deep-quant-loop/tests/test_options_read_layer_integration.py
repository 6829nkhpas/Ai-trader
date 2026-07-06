"""QuestDB-gated integration tests for the options read layer (task 8.4).

Feature: options-analytics-engine

These tests exercise the *impure* read/query layer of ``options.py`` against a
**real, running QuestDB** over the same HTTP ``/exec`` API the engine itself uses
(``options._questdb_select`` -> ``httpx.get(f"{QUESTDB_HTTP_URL}/exec", ...)``).
They cover:

  * R5.1 — ``read_latest_and_prior_snapshot`` returns the latest snapshot (max
           ``snapshot_ts``) and the immediately-prior snapshot, each projected to
           a :class:`options.ChainSnapshot` with the correct per-strike CE/PE
           ``last_price`` and ``open_interest``, and with the latest
           ``option_ticks.volume`` joined onto the matching strikes by ``symbol``
           (a strike whose instrument has no stored volume -> ``None``).
  * R5.2 — ``read_spot`` returns the most-recent ``live_ticks.last_traded_price``
           for the underlying, and ``None`` when no tick exists.

Unlike the rest of the options test-suite (which feeds in-memory snapshots to the
pure core, or mocks ``httpx``), this module talks to a live store. It is
therefore **GATED**: when QuestDB is not reachable at ``QUESTDB_HTTP_URL``
(default ``http://127.0.0.1:9000``) every test SKIPS gracefully rather than
failing — a pass-by-skip is the expected outcome in an environment with no local
QuestDB.

Isolation & cleanup: all rows are seeded under a unique, far-future test
underlying / expiry (``__OAE_TEST_*`` / ``2099-12-26``) so real F1 data is never
read or clobbered. Tables are created ``IF NOT EXISTS`` (matching the F1 schema)
and the test rows live in their own far-future daily partition, which teardown
removes (dropping any table this module created outright, and otherwise dropping
just the far-future partition) on a best-effort basis.
"""

import datetime as dt
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
    read_latest_and_prior_snapshot,
    read_spot,
)

# ── Test fixture constants (unique + far-future so real data is never touched) ─
TEST_UNDERLYING = "__OAE_TEST_NIFTY__"
TEST_EXPIRY = "2099-12-26"
TEST_PARTITION = "2099-12-26"  # daily partition that holds every seeded row
EMPTY_UNDERLYING = "__OAE_TEST_NO_SUCH_UNDERLYING__"

# Two distinct capture timestamps one minute apart, in epoch microseconds.
_BASE = dt.datetime(2099, 12, 26, 9, 15, 0, tzinfo=dt.timezone.utc)
LATEST_MICROS = int(_BASE.timestamp() * 1_000_000)
PRIOR_MICROS = LATEST_MICROS - 60_000_000  # one minute earlier

# Per-strike instrument symbols (the join key between snapshots and ticks).
SYM_24000_CE = "OAE_TEST_24000_CE"
SYM_24000_PE = "OAE_TEST_24000_PE"
SYM_24100_CE = "OAE_TEST_24100_CE"
SYM_24100_PE = "OAE_TEST_24100_PE"

# Latest-snapshot CE/PE prices and OI per strike.
LATEST_CHAIN = {
    24000.0: {"ce_price": 120.5, "pe_price": 80.25, "ce_oi": 1500, "pe_oi": 2200},
    24100.0: {"ce_price": 60.0, "pe_price": 140.0, "ce_oi": 900, "pe_oi": 1750},
}
# Prior-snapshot CE/PE prices and OI per strike (distinct from latest).
PRIOR_CHAIN = {
    24000.0: {"ce_price": 110.0, "pe_price": 85.0, "ce_oi": 1000, "pe_oi": 2000},
    24100.0: {"ce_price": 55.0, "pe_price": 150.0, "ce_oi": 800, "pe_oi": 1600},
}
# Latest cumulative traded volume per symbol; SYM_24100_PE is deliberately
# absent so the join yields ``None`` for that leg's volume.
EXPECTED_VOLUME = {
    SYM_24000_CE: 12345.0,
    SYM_24000_PE: 23456.0,
    SYM_24100_CE: 3456.0,
}

# Spot ticks for the underlying: most-recent (later timestamp) must win.
SPOT_OLD = 23990.0
SPOT_LATEST = 24017.75


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
    reason=f"QuestDB not reachable at {QUESTDB_HTTP_URL}; read-layer integration "
    "tests are gated on a running QuestDB (pass-by-skip).",
)


def _ts_literal(micros):
    """A QuestDB timestamp literal from epoch microseconds."""
    return f"cast({int(micros)} as timestamp)"


def _seed_snapshots():
    """Seed the latest + prior ``option_chain_snapshots`` rows (CE & PE legs)."""
    sym = {
        (24000.0, "CE"): SYM_24000_CE, (24000.0, "PE"): SYM_24000_PE,
        (24100.0, "CE"): SYM_24100_CE, (24100.0, "PE"): SYM_24100_PE,
    }
    rows = []
    for micros, chain in ((LATEST_MICROS, LATEST_CHAIN), (PRIOR_MICROS, PRIOR_CHAIN)):
        for strike, q in chain.items():
            rows.append(
                f"('{TEST_UNDERLYING}','{TEST_EXPIRY}',{strike},'CE',"
                f"'{sym[(strike, 'CE')]}',{q['ce_price']},{q['ce_oi']},"
                f"{_ts_literal(micros)})"
            )
            rows.append(
                f"('{TEST_UNDERLYING}','{TEST_EXPIRY}',{strike},'PE',"
                f"'{sym[(strike, 'PE')]}',{q['pe_price']},{q['pe_oi']},"
                f"{_ts_literal(micros)})"
            )
    _exec(
        "INSERT INTO option_chain_snapshots "
        "(underlying, expiry, strike, option_type, symbol, last_price, "
        "open_interest, snapshot_ts) VALUES " + ",".join(rows)
    )


def _seed_option_ticks():
    """Seed ``option_ticks`` so the LATEST volume per symbol is EXPECTED_VOLUME.

    For SYM_24000_CE an older, lower-volume row is also seeded to prove the
    read layer's ``LATEST ON timestamp`` selects the most-recent volume.
    SYM_24100_PE gets no tick at all (its joined volume must be ``None``).
    """
    base = LATEST_MICROS
    rows = [
        # older row for SYM_24000_CE (must be superseded by the latest below)
        f"('{SYM_24000_CE}','{TEST_UNDERLYING}','{TEST_EXPIRY}',24000.0,'CE',"
        f"{_ts_literal(base - 120_000_000)},120.0,100,1500,119.5,120.5)",
        # latest rows (one per symbol present in EXPECTED_VOLUME)
        f"('{SYM_24000_CE}','{TEST_UNDERLYING}','{TEST_EXPIRY}',24000.0,'CE',"
        f"{_ts_literal(base)},120.5,{int(EXPECTED_VOLUME[SYM_24000_CE])},1500,120.0,121.0)",
        f"('{SYM_24000_PE}','{TEST_UNDERLYING}','{TEST_EXPIRY}',24000.0,'PE',"
        f"{_ts_literal(base)},80.25,{int(EXPECTED_VOLUME[SYM_24000_PE])},2200,80.0,80.5)",
        f"('{SYM_24100_CE}','{TEST_UNDERLYING}','{TEST_EXPIRY}',24100.0,'CE',"
        f"{_ts_literal(base)},60.0,{int(EXPECTED_VOLUME[SYM_24100_CE])},900,59.5,60.5)",
    ]
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

    Yields nothing; the tests read back through ``options.read_*``. Teardown drops
    any table this module created, and otherwise drops just the far-future test
    partition, on a best-effort basis so real F1 data is never disturbed.
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


def test_latest_and_prior_snapshot_prices_and_oi(seeded_questdb):
    """R5.1 — latest + immediately-prior snapshots with correct CE/PE price & OI."""
    latest, prior = read_latest_and_prior_snapshot(TEST_UNDERLYING, TEST_EXPIRY)

    assert latest is not None, "expected a latest snapshot for the seeded chain"
    assert prior is not None, "expected an immediately-prior snapshot"

    # Snapshot timestamps are projected micros -> ms.
    assert latest.snapshot_ts == LATEST_MICROS // 1000
    assert prior.snapshot_ts == PRIOR_MICROS // 1000
    assert latest.snapshot_ts > prior.snapshot_ts

    # Strikes come back ascending and distinct.
    latest_by_strike = {q.strike: q for q in latest.strikes}
    prior_by_strike = {q.strike: q for q in prior.strikes}
    assert sorted(latest_by_strike) == [24000.0, 24100.0]
    assert sorted(prior_by_strike) == [24000.0, 24100.0]

    # Latest CE/PE prices and OI per strike.
    for strike, q in LATEST_CHAIN.items():
        sq = latest_by_strike[strike]
        assert sq.ce_price == q["ce_price"]
        assert sq.pe_price == q["pe_price"]
        assert sq.ce_oi == q["ce_oi"]
        assert sq.pe_oi == q["pe_oi"]

    # Prior CE/PE prices and OI per strike (distinct from latest).
    for strike, q in PRIOR_CHAIN.items():
        sq = prior_by_strike[strike]
        assert sq.ce_price == q["ce_price"]
        assert sq.pe_price == q["pe_price"]
        assert sq.ce_oi == q["ce_oi"]
        assert sq.pe_oi == q["pe_oi"]


def test_option_ticks_volume_attaches_to_matching_strikes(seeded_questdb):
    """R5.1 — latest option_ticks volume joins to matching strikes; absent -> None."""
    latest, _ = read_latest_and_prior_snapshot(TEST_UNDERLYING, TEST_EXPIRY)
    assert latest is not None
    by_strike = {q.strike: q for q in latest.strikes}

    # 24000 CE/PE and 24100 CE have ticks -> their LATEST volume attaches.
    assert by_strike[24000.0].ce_volume == EXPECTED_VOLUME[SYM_24000_CE]
    assert by_strike[24000.0].pe_volume == EXPECTED_VOLUME[SYM_24000_PE]
    assert by_strike[24100.0].ce_volume == EXPECTED_VOLUME[SYM_24100_CE]

    # 24100 PE has NO option_ticks row -> volume must be None (never fabricated).
    assert by_strike[24100.0].pe_volume is None


def test_read_spot_returns_most_recent(seeded_questdb):
    """R5.2 — read_spot returns the most-recent live_ticks last_traded_price."""
    assert read_spot(TEST_UNDERLYING) == SPOT_LATEST


def test_read_spot_empty_returns_none(seeded_questdb):
    """R5.2 — read_spot returns None when no live tick exists for the underlying."""
    assert read_spot(EMPTY_UNDERLYING) is None
