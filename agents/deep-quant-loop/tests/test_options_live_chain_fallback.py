"""The live-chain fallback: F&O for underlyings QuestDB does not hold.

Why it exists. `option_chain_selector` subscribes a bounded set of underlyings and
always will — Kite allows 3000 instruments on one WebSocket and the selector already
spends about 1300 of that, nowhere near enough for every F&O-listed stock. So
`option_chain_snapshots` held rows for exactly ten names, and selecting any other
one produced "F&O DATA UNAVAILABLE" permanently: HINDUNILVR had zero rows ever,
while the NFO master listed it with three live expiries.

`read_chain_for_analytics` closes that by reading the chain from the exchange when
QuestDB has none. These tests pin the behaviour that matters:

  * an ingested chain is returned untouched, prior snapshot included — the ten
    configured underlyings must not change behaviour at all;
  * a non-ingested chain is built from the exchange, priced, and bounded the same
    way;
  * nothing is fabricated when either leg fails;
  * the fallback is genuinely off when `KITE_API_URL` is empty.

The aggregator is stubbed at `_kite_get`, so these are hermetic and assert on the
projection rather than on HTTP.
"""

import pytest

import options
from options import ChainSnapshot, StrikeQuote


# Real shapes, from the live route for HINDUNILVR (spot 1990.6, ATM 2000).
LADDER = {
    "underlying": "HINDUNILVR",
    "exchange": "NFO",
    "expiry": "2026-09-29",
    "atm_strike": 2000.0,
    "spot": 1990.6,
    "expiries": ["2026-09-29", "2026-10-27", "2026-11-23"],
    "contracts": [
        {"tradingsymbol": "HINDUNILVR26SEP1950CE", "strike": 1950.0, "option_type": "CE"},
        {"tradingsymbol": "HINDUNILVR26SEP1950PE", "strike": 1950.0, "option_type": "PE"},
        {"tradingsymbol": "HINDUNILVR26SEP2000CE", "strike": 2000.0, "option_type": "CE"},
        {"tradingsymbol": "HINDUNILVR26SEP2000PE", "strike": 2000.0, "option_type": "PE"},
    ],
}

QUOTES = {
    "quotes": [
        {"symbol": "HINDUNILVR26SEP1950CE", "last_price": 71.5, "oi": 1200, "volume": 340},
        {"symbol": "HINDUNILVR26SEP1950PE", "last_price": 28.4, "oi": 900, "volume": 210},
        {"symbol": "HINDUNILVR26SEP2000CE", "last_price": 44.0, "oi": 2500, "volume": 810},
        # 2000PE deliberately absent — an unquoted leg must read as null, not zero.
    ]
}


@pytest.fixture
def kite(monkeypatch):
    """Arm the fallback and stub the aggregator; returns the recorded calls."""
    monkeypatch.setattr(options, "KITE_API_URL", "http://aggregator:8087/api/kite")
    calls = []

    def fake_get(path, params, timeout=10.0):
        calls.append((path, dict(params)))
        if path == "/option_chain":
            return dict(LADDER) if params.get("expiry") else {
                "underlying": "HINDUNILVR",
                "exchange": "NFO",
                "expiries": LADDER["expiries"],
            }
        if path == "/quote":
            return QUOTES
        return None

    monkeypatch.setattr(options, "_kite_get", fake_get)
    return calls


class TestListedExpiries:
    def test_returns_what_the_exchange_lists(self, kite):
        assert options.read_listed_expiries("HINDUNILVR") == LADDER["expiries"]

    def test_empty_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(options, "KITE_API_URL", "http://x")
        monkeypatch.setattr(options, "_kite_get", lambda *a, **k: None)
        assert options.read_listed_expiries("HINDUNILVR") == []

    def test_empty_when_the_fallback_is_switched_off(self, monkeypatch):
        # The documented off switch, and the mechanism the suite itself uses.
        monkeypatch.setattr(options, "KITE_API_URL", "")
        assert options.read_listed_expiries("HINDUNILVR") == []


class TestBuildLiveChainSnapshot:
    def test_projects_the_priced_ladder(self, kite):
        built = options.build_live_chain_snapshot("HINDUNILVR", "2026-09-29")
        assert built is not None
        snapshot, spot = built

        assert spot == 1990.6
        assert snapshot.underlying == "HINDUNILVR"
        assert snapshot.expiry == "2026-09-29"
        # Ascending, one entry per distinct strike — the shape every analytic assumes.
        assert [q.strike for q in snapshot.strikes] == [1950.0, 2000.0]

        low, atm = snapshot.strikes
        assert (low.ce_price, low.ce_oi, low.ce_volume) == (71.5, 1200.0, 340.0)
        assert (low.pe_price, low.pe_oi, low.pe_volume) == (28.4, 900.0, 210.0)
        assert (atm.ce_price, atm.ce_oi) == (44.0, 2500.0)
        # The unquoted leg: null, never a fabricated zero.
        assert atm.pe_price is None and atm.pe_oi is None and atm.pe_volume is None

    def test_prices_the_whole_ladder_in_one_call(self, kite):
        options.build_live_chain_snapshot("HINDUNILVR", "2026-09-29")
        quote_calls = [c for c in kite if c[0] == "/quote"]
        assert len(quote_calls) == 1, "the band must be priced in one request, not per leg"

        keys = quote_calls[0][1]["i"]
        # A LIST, so httpx emits repeated `i=` params. A comma-joined string reaches
        # Kite as one instrument named "NFO:A,NFO:B,…" and every leg prices as null —
        # which is exactly how this first shipped.
        assert isinstance(keys, list)
        # Exchange-prefixed, so BFO names (SENSEX/BANKEX) resolve too.
        assert keys == [
            "NFO:HINDUNILVR26SEP1950CE",
            "NFO:HINDUNILVR26SEP1950PE",
            "NFO:HINDUNILVR26SEP2000CE",
            "NFO:HINDUNILVR26SEP2000PE",
        ]

    @pytest.mark.parametrize(
        "broken",
        [
            {"contracts": []},
            {"contracts": None},
            {"spot": None},
            {"spot": 0},
            {"exchange": None},
        ],
    )
    def test_no_snapshot_from_an_unusable_ladder(self, monkeypatch, broken):
        # Spot in particular: without it there is no defensible ATM, and the
        # spot-relative analytics must not run against a guessed centre.
        monkeypatch.setattr(options, "KITE_API_URL", "http://x")
        payload = {**LADDER, **broken}
        monkeypatch.setattr(
            options, "_kite_get",
            lambda path, params, timeout=10.0: payload if path == "/option_chain" else QUOTES,
        )
        assert options.build_live_chain_snapshot("HINDUNILVR", "2026-09-29") is None

    def test_survives_prices_being_unavailable(self, monkeypatch):
        # The ladder is real even if quotes fail, so the strikes stand with null
        # prices rather than the whole chain vanishing.
        monkeypatch.setattr(options, "KITE_API_URL", "http://x")
        monkeypatch.setattr(
            options, "_kite_get",
            lambda path, params, timeout=10.0: dict(LADDER) if path == "/option_chain" else None,
        )
        built = options.build_live_chain_snapshot("HINDUNILVR", "2026-09-29")
        assert built is not None
        snapshot, _ = built
        assert [q.strike for q in snapshot.strikes] == [1950.0, 2000.0]
        assert all(q.ce_price is None and q.pe_price is None for q in snapshot.strikes)

    def test_never_raises(self, monkeypatch):
        monkeypatch.setattr(options, "KITE_API_URL", "http://x")
        monkeypatch.setattr(
            options, "_kite_get",
            lambda *a, **k: {"contracts": [{"strike": "oops", "option_type": 7}], "spot": 1.0, "exchange": "NFO"},
        )
        assert options.build_live_chain_snapshot("HINDUNILVR", "2026-09-29") is None


class TestReadChainForAnalytics:
    def test_an_ingested_chain_is_returned_untouched(self, kite, monkeypatch):
        # The ten configured underlyings must be completely unaffected: QuestDB
        # first, prior snapshot preserved, and no exchange call at all.
        latest = ChainSnapshot(
            underlying="NIFTY", expiry="2026-09-29", snapshot_ts=1_700_000_000_000,
            strikes=(StrikeQuote(24000.0, 10.0, 11.0, 1.0, 2.0, 3.0, 4.0),),
        )
        prior = ChainSnapshot(
            underlying="NIFTY", expiry="2026-09-29", snapshot_ts=1_699_999_940_000,
            strikes=(StrikeQuote(24000.0, 9.0, 12.0, 1.0, 2.0, 3.0, 4.0),),
        )
        monkeypatch.setattr(
            options, "read_latest_and_prior_snapshot", lambda u, e: (latest, prior)
        )

        got = options.read_chain_for_analytics("NIFTY", "2026-09-29")

        assert got == (latest, prior, None)
        assert kite == [], "an ingested chain must not reach for the exchange"

    def test_falls_back_when_questdb_has_nothing(self, kite, monkeypatch):
        monkeypatch.setattr(
            options, "read_latest_and_prior_snapshot", lambda u, e: (None, None)
        )

        latest, prior, live_spot = options.read_chain_for_analytics(
            "HINDUNILVR", "2026-09-29"
        )

        assert latest is not None and latest.underlying == "HINDUNILVR"
        # No prior exists for a chain nothing stores, so per-strike OI buildup
        # degrades to neutral rather than being invented.
        assert prior is None
        # Spot rides along, because `live_ticks` has no tick for an underlying
        # nothing subscribes — without it the analytics would degrade on spot.
        assert live_spot == 1990.6

    def test_unavailable_when_neither_source_has_it(self, monkeypatch):
        monkeypatch.setattr(
            options, "read_latest_and_prior_snapshot", lambda u, e: (None, None)
        )
        monkeypatch.setattr(options, "KITE_API_URL", "http://x")
        monkeypatch.setattr(options, "_kite_get", lambda *a, **k: None)

        assert options.read_chain_for_analytics("NOSUCH", "2026-09-29") == (None, None, None)
