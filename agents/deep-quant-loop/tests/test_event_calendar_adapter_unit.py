"""Unit tests for the NSE Event_Source adapter (``event_calendar`` + its route).

Feature: earnings-event-risk-gate — wiring an actual Event_Source.

`get_event_risk` was returning "no event source configured" on every run, so
earnings proximity was never assessed and a multi-session position could be held
straight through a results date. `event_calendar.py` adapts NSE's free corporate
event calendar into the generic ``{symbol, date}`` records `tools.py` already
parses.

Three properties carry real risk and get the most attention here:

  * **Locale independence.** The date conversion must NOT go through
    ``strptime("%d-%b-%Y")``, whose month abbreviations resolve through the process
    locale. Developed on a Windows host, run in a Linux container: a container
    with a non-English locale would drop every date, the endpoint would answer
    ``[]``, and the agent would conclude the calendar is clear.
  * **Symbol scoping.** `tools.py::_collect_api_dates` harvests the ``date`` of
    EVERY record in a list body, ignoring its symbol argument. So returning the
    whole calendar would make every other company's board meeting a candidate
    event for the queried symbol.
  * **Failure is not "clear".** A 200 with ``[]`` means "we looked, the diary is
    clear"; non-2xx means "we are blind". The agent maps them to different
    Unavailable_Markers, so they must never be interchanged.

Hermetic: no network. The upstream fetch is monkeypatched; the pure helpers need
nothing.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import event_calendar as ec  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_adapter_state(monkeypatch):
    """Every test starts from an empty cache and default configuration."""
    for var in (
        ec.ENV_SOURCE_URL,
        ec.ENV_PRIME_URL,
        ec.ENV_TTL_SECONDS,
        ec.ENV_TIMEOUT_SECONDS,
        ec.ENV_STALE_GRACE_SECONDS,
        ec.ENV_PURPOSES,
    ):
        monkeypatch.delenv(var, raising=False)
    ec.reset_cache()
    yield
    ec.reset_cache()


# ── Date conversion (R: never fabricate, never depend on locale) ──────────────


class TestDateConversion:
    def test_converts_nse_dd_mon_yyyy_to_iso(self):
        # The exact format NSE serves, verified against the live endpoint.
        assert ec.to_iso_date("01-Sep-2026") == "2026-09-01"
        assert ec.to_iso_date("15-Jan-2027") == "2027-01-15"
        assert ec.to_iso_date("31-Dec-2026") == "2026-12-31"

    def test_accepts_every_month_abbreviation_without_the_locale(self):
        # The whole point of the explicit month table. If this ever regresses to
        # strptime("%d-%b-%Y") it still passes on an English host and fails in a
        # container with a different locale — so assert the mapping directly.
        expected = {
            "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
            "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
            "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
        }
        for mon, num in expected.items():
            assert ec.to_iso_date(f"10-{mon}-2026") == f"2026-{num}-10"

    def test_is_case_insensitive_about_the_month(self):
        for spelling in ("SEP", "sep", "Sep", "sEp"):
            assert ec.to_iso_date(f"01-{spelling}-2026") == "2026-09-01"

    def test_passes_iso_through_so_the_source_can_be_repointed(self):
        # A paid feed emitting ISO must work without touching this module.
        assert ec.to_iso_date("2026-09-01") == "2026-09-01"
        assert ec.to_iso_date("2026-09-01T10:30:00") == "2026-09-01"

    def test_rejects_an_impossible_calendar_date(self):
        # 31-Feb parses arithmetically but is not a date; dropping it here makes
        # the reason visible instead of failing silently downstream.
        assert ec.to_iso_date("31-Feb-2026") is None
        assert ec.to_iso_date("00-Sep-2026") is None
        assert ec.to_iso_date("01-Xyz-2026") is None

    @pytest.mark.parametrize("bad", ["", "   ", "garbage", "01-09", None, 42, [], {}, True])
    def test_returns_none_rather_than_guessing(self, bad):
        assert ec.to_iso_date(bad) is None


# ── Purpose filter ───────────────────────────────────────────────────────────


class TestPurposeFilter:
    def test_defaults_to_results_only(self):
        # Measured against live NSE: 1 of 35 rows was "Financial Results"; the
        # rest were AGMs, fund raising, dividends and buybacks. The gate is
        # documented as assessing earnings/results proximity.
        purposes = ec.resolve_purposes()
        assert purposes == ("result",)
        assert ec.purpose_matches("Financial Results", purposes) is True
        assert ec.purpose_matches("Quarterly Results/Dividend", purposes) is True
        assert ec.purpose_matches("Fund Raising", purposes) is False
        assert ec.purpose_matches("Buyback", purposes) is False
        assert ec.purpose_matches("Other business matters", purposes) is False

    def test_star_opts_out_of_filtering(self, monkeypatch):
        monkeypatch.setenv(ec.ENV_PURPOSES, "*")
        purposes = ec.resolve_purposes()
        assert purposes == ()
        assert ec.purpose_matches("Fund Raising", purposes) is True
        assert ec.purpose_matches("anything at all", purposes) is True

    def test_operator_can_widen_to_a_named_list(self, monkeypatch):
        monkeypatch.setenv(ec.ENV_PURPOSES, "result, dividend ,buyback")
        purposes = ec.resolve_purposes()
        assert purposes == ("result", "dividend", "buyback")
        assert ec.purpose_matches("Dividend", purposes) is True
        assert ec.purpose_matches("Fund Raising", purposes) is False

    def test_an_unclassifiable_purpose_is_not_assumed_to_be_earnings(self):
        assert ec.purpose_matches(None, ("result",)) is False
        assert ec.purpose_matches(123, ("result",)) is False


# ── Projection ───────────────────────────────────────────────────────────────


NSE_SAMPLE = [
    {"symbol": "TECHNOCRAF", "company": "Technocraft", "purpose": "Financial Results", "date": "02-Sep-2026"},
    {"symbol": "AVONMORE", "company": "Avonmore", "purpose": "Fund Raising", "date": "01-Sep-2026"},
    {"symbol": "INFY", "company": "Infosys", "purpose": "Quarterly Results", "date": "10-Oct-2026"},
    {"symbol": "BROKEN", "company": "No date", "purpose": "Financial Results", "date": "not-a-date"},
    {"symbol": "", "company": "No symbol", "purpose": "Financial Results", "date": "05-Sep-2026"},
    "not even a record",
]


class TestNormaliseRows:
    def test_keeps_only_usable_qualifying_rows(self):
        rows = ec.normalise_rows(NSE_SAMPLE, ("result",))
        assert [r["symbol"] for r in rows] == ["TECHNOCRAF", "INFY"]
        assert rows[0]["date"] == "2026-09-02"
        assert rows[1]["date"] == "2026-10-10"

    def test_drops_a_row_whose_date_cannot_be_converted(self):
        # Never fabricate: an unparseable date means no event, not today's date.
        rows = ec.normalise_rows(NSE_SAMPLE, ("result",))
        assert all(r["symbol"] != "BROKEN" for r in rows)

    def test_uppercases_the_symbol_for_matching(self):
        rows = ec.normalise_rows(
            [{"symbol": " reliance ", "purpose": "Financial Results", "date": "01-Sep-2026"}],
            ("result",),
        )
        assert rows[0]["symbol"] == "RELIANCE"

    @pytest.mark.parametrize("junk", [None, {}, "string", 42, [None, 1, "x"]])
    def test_is_total_against_a_shape_change_upstream(self, junk):
        # A payload change must yield fewer rows, never an exception.
        assert ec.normalise_rows(junk, ("result",)) == []


# ── Symbol scoping — the load-bearing filter ─────────────────────────────────


class TestSymbolScoping:
    def test_returns_no_other_symbols_rows(self):
        """The property that stops one company's meeting becoming another's event.

        `_collect_api_dates` harvests every record's date from a list body, so a
        full-calendar response would attribute all 35 dates to whichever symbol
        was asked about.
        """
        rows = ec.normalise_rows(NSE_SAMPLE, ())
        got = ec.rows_for_symbol(rows, "TECHNOCRAF")
        assert len(got) == 1
        assert {r["symbol"] for r in got} == {"TECHNOCRAF"}
        # And nothing belonging to anyone else leaked in.
        assert all(r["symbol"] == "TECHNOCRAF" for r in got)

    def test_matches_case_insensitively(self):
        rows = ec.normalise_rows(NSE_SAMPLE, ())
        for spelling in ("infy", "INFY", "Infy", "  infy  "):
            assert len(ec.rows_for_symbol(rows, spelling)) == 1

    def test_never_matches_on_a_substring(self):
        # "INFY" must not pick up "INFYTECH", nor "INF" pick up "INFY".
        rows = ec.normalise_rows(
            [
                {"symbol": "INFY", "purpose": "Financial Results", "date": "01-Sep-2026"},
                {"symbol": "INFYTECH", "purpose": "Financial Results", "date": "02-Sep-2026"},
            ],
            ("result",),
        )
        assert [r["symbol"] for r in ec.rows_for_symbol(rows, "INFY")] == ["INFY"]
        assert ec.rows_for_symbol(rows, "INF") == []

    def test_an_unknown_symbol_yields_a_clear_calendar(self):
        rows = ec.normalise_rows(NSE_SAMPLE, ())
        assert ec.rows_for_symbol(rows, "NOSUCHCO") == []

    @pytest.mark.parametrize("empty", ["", "   ", None, 42])
    def test_an_empty_symbol_never_returns_the_whole_calendar(self, empty):
        rows = ec.normalise_rows(NSE_SAMPLE, ())
        assert ec.rows_for_symbol(rows, empty) == []


# ── Caching + failure posture ────────────────────────────────────────────────


class TestCachingAndFailure:
    def test_a_second_call_within_the_ttl_does_not_refetch(self, monkeypatch):
        calls = []

        def fake_fetch(source_url, prime_url, timeout):
            calls.append(1)
            return NSE_SAMPLE

        monkeypatch.setattr(ec, "_fetch_upstream", fake_fetch)
        first, stale1 = ec.get_calendar()
        second, stale2 = ec.get_calendar()
        assert len(calls) == 1, "NSE throttles; the calendar must be cached"
        assert first == second
        assert stale1 is False and stale2 is False

    def test_changing_the_purpose_filter_invalidates_the_cache(self, monkeypatch):
        monkeypatch.setattr(ec, "_fetch_upstream", lambda *a, **k: NSE_SAMPLE)
        results_only, _ = ec.get_calendar()
        monkeypatch.setenv(ec.ENV_PURPOSES, "*")
        widened, _ = ec.get_calendar()
        # Serving the old rows would silently ignore the new setting.
        assert len(widened) > len(results_only)

    def test_serves_a_stale_calendar_when_upstream_starts_failing(self, monkeypatch):
        monkeypatch.setattr(ec, "_fetch_upstream", lambda *a, **k: NSE_SAMPLE)
        good, _ = ec.get_calendar()
        assert good

        def boom(*a, **k):
            raise RuntimeError("upstream down")

        monkeypatch.setattr(ec, "_fetch_upstream", boom)
        monkeypatch.setenv(ec.ENV_TTL_SECONDS, "0.0001")  # force a refresh attempt
        rows, stale = ec.get_calendar()
        assert rows == good
        assert stale is True, "a served-from-cache answer must announce itself"

    def test_raises_rather_than_reporting_a_clear_calendar(self, monkeypatch):
        """With nothing cached and upstream down, fail — never return [].

        This is the difference between "we looked, nothing scheduled" and "we are
        blind". Returning [] here would let the gate clear a trade into an
        unknown results date.
        """

        def boom(*a, **k):
            raise RuntimeError("upstream down")

        monkeypatch.setattr(ec, "_fetch_upstream", boom)
        with pytest.raises(ec.EventCalendarUnavailable):
            ec.get_calendar()

    def test_stops_serving_stale_past_the_grace_window(self, monkeypatch):
        monkeypatch.setattr(ec, "_fetch_upstream", lambda *a, **k: NSE_SAMPLE)
        ec.get_calendar()

        def boom(*a, **k):
            raise RuntimeError("upstream down")

        monkeypatch.setattr(ec, "_fetch_upstream", boom)
        monkeypatch.setenv(ec.ENV_TTL_SECONDS, "0.0001")
        monkeypatch.setenv(ec.ENV_STALE_GRACE_SECONDS, "0.0001")
        # A revised event date is worse than an honest "blind", so past the grace
        # window the endpoint must fail loudly instead of answering from memory.
        with pytest.raises(ec.EventCalendarUnavailable):
            ec.get_calendar()

    def test_a_malformed_env_value_falls_back_to_the_default(self, monkeypatch):
        # A typo in an env var must not take the endpoint down.
        monkeypatch.setenv(ec.ENV_TTL_SECONDS, "not-a-number")
        monkeypatch.setattr(ec, "_fetch_upstream", lambda *a, **k: NSE_SAMPLE)
        rows, _ = ec.get_calendar()
        assert rows  # resolved to DEFAULT_TTL_SECONDS instead of raising


# ── The route's status-code contract ─────────────────────────────────────────


class TestRouteContract:
    """The endpoint's status code is what the agent turns into its marker.

    `main` is imported lazily and per-test: it pulls in the whole graph/LLM layer,
    which is far heavier than the rest of this module needs.

    A missing third-party dependency SKIPS (that is an environment fault, and the
    40 pure tests above still cover the logic); anything else propagates and fails,
    so a genuine break in our own code is never hidden by a skip.
    """

    def _client(self):
        os.environ.setdefault("OPENAI_API_KEY", "test-key")
        try:
            from fastapi.testclient import TestClient

            import main
        except (ImportError, ModuleNotFoundError) as exc:
            pytest.skip(f"service layer unavailable in this environment: {exc}")

        return TestClient(main.app, raise_server_exceptions=False)

    def test_returns_the_symbols_rows_on_success(self, monkeypatch):
        monkeypatch.setattr(ec, "_fetch_upstream", lambda *a, **k: NSE_SAMPLE)
        res = self._client().get("/events/calendar", params={"symbol": "TECHNOCRAF"})
        assert res.status_code == 200
        body = res.json()
        assert [r["symbol"] for r in body] == ["TECHNOCRAF"]
        assert body[0]["date"] == "2026-09-02"

    def test_a_clear_calendar_is_200_with_an_empty_list(self, monkeypatch):
        monkeypatch.setattr(ec, "_fetch_upstream", lambda *a, **k: NSE_SAMPLE)
        res = self._client().get("/events/calendar", params={"symbol": "RELIANCE"})
        # 200 [] is a POSITIVE statement: we read the calendar, nothing is filed.
        assert res.status_code == 200
        assert res.json() == []

    def test_an_upstream_failure_is_5xx_and_never_an_empty_list(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("upstream down")

        monkeypatch.setattr(ec, "_fetch_upstream", boom)
        res = self._client().get("/events/calendar", params={"symbol": "TECHNOCRAF"})
        assert res.status_code == 503
        # The agent reads a non-2xx as "retrieval failed". A 200 [] here would be
        # read as "no upcoming event" — a clear diary for a company that may
        # report tomorrow.
        assert res.json() != []

    def test_a_missing_symbol_is_a_caller_error_not_a_clear_calendar(self):
        res = self._client().get("/events/calendar")
        assert res.status_code == 422
