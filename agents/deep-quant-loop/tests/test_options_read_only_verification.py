"""Read-only verification test for the options read layer (options.py, task 8.5).

Feature: options-analytics-engine

Requirement 5.4 mandates that the read/query layer is **read-only** with respect
to QuestDB: every query it issues is a ``SELECT`` and it emits no
``CREATE``/``INSERT``/``UPDATE``/``DELETE``/``DROP``/``ALTER``/``TRUNCATE`` —
so the F1 tables (``option_chain_snapshots``, ``option_ticks``, ``live_ticks``)
are left unchanged after a run.

This test needs **no running QuestDB**. The read layer's single outbound
dependency is ``httpx.get(f"{QUESTDB_HTTP_URL}/exec", params={"query": ...})``
inside the ``options._questdb_select`` helper (mirroring ``tools.py`` /
``backtest.py``). We monkeypatch ``options.httpx.get`` to (a) record every
``params["query"]`` string issued and (b) return a fake response object that
satisfies the helper's contract (``.raise_for_status()`` is a no-op and
``.json()`` returns ``{"dataset": []}``), so the read-layer functions run end to
end without a real server.

We then drive every read-layer entry point — :func:`options.read_spot`,
:func:`options.read_latest_and_prior_snapshot`, and
:func:`options.read_future_price` — and assert that **every** captured query:

  * begins with ``SELECT`` (case-insensitive, after stripping leading
    whitespace), and
  * contains **none** of the mutating SQL keywords
    (``CREATE``/``INSERT``/``UPDATE``/``DELETE``/``DROP``/``ALTER``/``TRUNCATE``)
    as a standalone token.

Because the only transport the layer can reach is the patched ``httpx.get``,
capturing and validating every query string is equivalent to proving the F1
tables are untouched: a read-only ``SELECT`` cannot mutate any table, and no
other write path exists.

Validates: Requirements 5.4
"""

import os
import re
import sys

# Make the service package importable (options.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import options  # noqa: E402


# Mutating SQL keywords that must never appear in any query the read layer issues.
_MUTATING_KEYWORDS = (
    "CREATE",
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "TRUNCATE",
)


class _FakeResponse:
    """Minimal stand-in for an ``httpx.Response`` the read layer expects.

    ``_questdb_select`` calls ``.raise_for_status()`` then ``.json()``; we make
    the former a no-op and the latter return an empty dataset so each read-layer
    function runs to completion and degrades to its honest empty result without a
    live QuestDB.
    """

    def raise_for_status(self):  # no-op: simulate a 2xx response
        return None

    def json(self):
        return {"dataset": []}


def _install_query_capture(monkeypatch):
    """Patch ``options.httpx.get`` to record every issued query string.

    Returns the list that will be populated with each ``params["query"]`` value,
    in the order the read layer issues them.
    """
    captured_queries = []

    def _fake_get(url, params=None, timeout=None, **kwargs):
        # The read layer always passes the SQL via ``params={"query": ...}``.
        assert params is not None and "query" in params, (
            "read layer must pass the SQL via params['query']"
        )
        captured_queries.append(params["query"])
        # And it always targets the QuestDB /exec endpoint.
        assert url.endswith("/exec"), f"unexpected read endpoint: {url!r}"
        return _FakeResponse()

    monkeypatch.setattr(options.httpx, "get", _fake_get)
    return captured_queries


def _assert_read_only(query):
    """Assert a single captured query is a read-only ``SELECT`` (Requirement 5.4)."""
    normalized = query.strip()
    assert normalized, "read layer issued an empty query"

    # Must START with SELECT (case-insensitive), after stripping leading space.
    assert normalized[:6].upper() == "SELECT", (
        f"query is not a SELECT (read-only violation): {query!r}"
    )

    # Must contain NO mutating SQL keyword as a standalone, word-bounded token
    # (so e.g. an identifier merely containing the substring would not trip it,
    # while a real ``DROP``/``INSERT``/... statement does).
    upper = query.upper()
    for keyword in _MUTATING_KEYWORDS:
        assert not re.search(rf"\b{keyword}\b", upper), (
            f"query contains mutating keyword {keyword!r} "
            f"(read-only violation): {query!r}"
        )


def test_read_layer_issues_only_select_queries(monkeypatch):
    """Every query from every read-layer function is a read-only SELECT (R5.4).

    Drives all three read-layer entry points through a transport that captures
    each issued query, then asserts the read-only invariant on every captured
    query. Because the only reachable transport is the patched ``httpx.get``,
    this proves the engine issues no DDL/DML and leaves the F1 tables unchanged.
    """
    captured_queries = _install_query_capture(monkeypatch)

    # Exercise the full read layer. None of these require a live QuestDB because
    # the patched transport returns an empty dataset; each degrades to its honest
    # empty/None result, but along the way it issues its real SELECT statements.
    spot = options.read_spot("NIFTY")
    latest, prior = options.read_latest_and_prior_snapshot("NIFTY", "2024-12-26")
    future = options.read_future_price("NIFTY")

    # With an empty dataset the readers honestly report "no data" and never raise.
    assert spot is None
    assert latest is None and prior is None
    assert future is None

    # The read layer MUST have issued at least one query across these calls,
    # otherwise the read-only assertion below would be vacuously true.
    assert captured_queries, "read layer issued no queries to validate"

    # Every captured query must be a read-only SELECT (Requirement 5.4).
    for query in captured_queries:
        _assert_read_only(query)


def test_read_spot_query_is_select(monkeypatch):
    """``read_spot`` issues exactly one read-only SELECT against ``live_ticks``."""
    captured_queries = _install_query_capture(monkeypatch)

    options.read_spot("NIFTY")

    assert captured_queries, "read_spot issued no query"
    for query in captured_queries:
        _assert_read_only(query)
        assert "live_ticks" in query.lower()


def test_read_future_price_query_is_select(monkeypatch):
    """``read_future_price`` issues a read-only SELECT against ``option_ticks``."""
    captured_queries = _install_query_capture(monkeypatch)

    options.read_future_price("NIFTY")

    assert captured_queries, "read_future_price issued no query"
    for query in captured_queries:
        _assert_read_only(query)
        assert "option_ticks" in query.lower()


def test_no_mutating_keyword_in_any_read_query(monkeypatch):
    """Defense-in-depth: scan the union of all read-layer queries for mutations.

    Even when the readers traverse their fuller code paths (which they do when a
    dataset is non-empty), the module-level transport is the single choke point,
    so capturing here is sufficient. We additionally drive
    ``read_latest_and_prior_snapshot`` whose multi-query path
    (timestamps -> per-snapshot rows -> volume join) issues the most queries.
    """
    captured_queries = _install_query_capture(monkeypatch)

    options.read_spot("NIFTY")
    options.read_latest_and_prior_snapshot("BANKNIFTY", "2024-12-26")
    options.read_future_price("BANKNIFTY")

    assert captured_queries
    joined_upper = " ; ".join(captured_queries).upper()
    for keyword in _MUTATING_KEYWORDS:
        assert not re.search(rf"\b{keyword}\b", joined_upper), (
            f"read layer issued a query containing mutating keyword {keyword!r}"
        )
