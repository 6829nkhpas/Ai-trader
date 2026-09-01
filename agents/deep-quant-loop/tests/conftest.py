"""Shared test-isolation for the deep-quant-loop suite.

Several journal-backed property-test modules mutate process-global ``journal``
attributes rather than going through the function-scoped ``monkeypatch``
fixture:

  * ``test_debate_aggregation_properties`` replaces ``journal.score_open_trades``
    with a no-op stub at *module import time* (to keep its aggregation tests
    hermetic while Hypothesis iterates), and
  * the ``*_aggregation_properties`` modules assign ``journal.LOW_SAMPLE_THRESHOLD``
    inside ``@given`` bodies and only restore it at ``atexit``.

Because those mutations hit the module global (not a restored monkeypatch),
they leaked across modules in a full-suite collection: once the debate module
was imported, ``score_open_trades`` stayed stubbed to ``0`` for the whole
process, so unrelated journal tests (e.g. trade-management scoring) observed
``resolved == 0``; likewise a foreign ``LOW_SAMPLE_THRESHOLD`` bled into other
grouping tests.

This conftest captures the PRISTINE values once — before pytest imports any
test module — and an autouse fixture resets those two leak-prone globals around
every test. Tests remain free to set them inside their own body; the fixture
only guarantees each test starts and ends from the pristine value, so no
module's mutation can leak into another.

A second autouse fixture redirects the compliance store (``COMPLIANCE_DB_PATH``,
read per call by ``hashchain.db_path``) into a per-test temporary directory. That
one is not about leakage between tests — it is about the suite never appending to
the real, append-only recommendation record. ``_finalize_decision`` writes there
now (compliance blocker P2), and several existing property tests drive it with
hundreds of synthetic decisions; those rows must not end up in an artefact whose
whole purpose is that rows cannot be removed from it.

Finally, a placeholder LLM credential is exported at module scope so the suite can
be collected in an environment that has none — see the comment on
``_PLACEHOLDER_LLM_KEY`` below.
"""

import os
import sys

import pytest

# tests/ sits directly under the service dir; put the service dir on the path so
# ``import journal`` resolves exactly as every test module expects.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

# ── Collection-time LLM credential ───────────────────────────────────────────
# ``graph.py`` builds its ChatOpenAI client at MODULE scope (``llm = ChatOpenAI(...)``)
# and the OpenAI client refuses to construct without a credential. So every test
# module that imports ``graph`` — most of this suite — fails at COLLECTION with
# ``openai.OpenAIError: Missing credentials`` on any machine that has no key:
# clean CI, or a contributor who has not written a repo-root ``.env``. That is a
# harness gap, not a code defect; these tests stub the graph and never call an LLM.
#
# Three deliberate choices:
#   * MODULE scope, not a fixture. Fixtures run after pytest imports the test
#     module, and the import itself is what raises.
#   * ``LLM_API_KEY`` — the variable ``graph.py`` actually reads (``:978``).
#     Exporting ``OPENAI_API_KEY`` instead does NOT work: ``_env_nonempty``
#     defaults to ``""`` and ``graph`` passes that empty string explicitly, which
#     the OpenAI client treats as a supplied-but-invalid credential and rejects
#     WITHOUT consulting the environment (verified: ``openai_api_key=""`` raises
#     where ``None`` succeeds). Making ``graph`` pass ``None`` would fix the import
#     by enabling exactly the ambient-``OPENAI_API_KEY`` pickup that ``graph.py:1018``
#     documents as unwanted, so the harness sets the real variable instead.
#   * A deliberately invalid value, via ``setdefault`` so an exported key still
#     wins. If a test ever does reach the network this 401s loudly and names
#     itself in the log rather than silently spending someone's quota.
#
# Pinning it also makes the credential MODE deterministic — shared-key rather than
# per-user — which is what a developer with a ``.env`` already got, so CI now
# matches the locally verified baseline instead of a third, untested configuration.
# ``test_interaction_log.py``'s ``client`` fixture documents the same hazard and
# reaches for the same remedy; it and the one per-user-mode test override this
# session default explicitly, and still do.
_PLACEHOLDER_LLM_KEY = "pytest-placeholder-not-a-real-key"
os.environ.setdefault("LLM_API_KEY", _PLACEHOLDER_LLM_KEY)

import journal  # noqa: E402

# Captured at conftest import time — BEFORE pytest collects (imports) any test
# module — so these are the genuine, unpolluted journal globals.
_PRISTINE_SCORE_OPEN_TRADES = journal.score_open_trades
_PRISTINE_LOW_SAMPLE_THRESHOLD = journal.LOW_SAMPLE_THRESHOLD


@pytest.fixture(autouse=True)
def _isolate_journal_globals():
    """Reset the leak-prone journal globals to their pristine values around
    every test, neutralising cross-module import-time / ``@given`` mutations."""
    journal.score_open_trades = _PRISTINE_SCORE_OPEN_TRADES
    journal.LOW_SAMPLE_THRESHOLD = _PRISTINE_LOW_SAMPLE_THRESHOLD
    try:
        yield
    finally:
        journal.score_open_trades = _PRISTINE_SCORE_OPEN_TRADES
        journal.LOW_SAMPLE_THRESHOLD = _PRISTINE_LOW_SAMPLE_THRESHOLD


@pytest.fixture(autouse=True)
def _isolate_compliance_store(tmp_path_factory, monkeypatch):
    """Point the P2/P5 compliance store at a throwaway file for every test.

    Set via the environment rather than by patching a module global because
    ``hashchain.db_path()`` deliberately reads ``COMPLIANCE_DB_PATH`` on every
    call — so this holds for any module that reaches the store, including code
    imported before this fixture runs.

    A test that wants its own path simply sets the variable again; last writer
    wins and ``monkeypatch`` restores the original afterwards.
    """
    store = tmp_path_factory.mktemp("compliance") / "compliance.db"
    monkeypatch.setenv("COMPLIANCE_DB_PATH", str(store))
    yield


@pytest.fixture(autouse=True)
def _disable_live_chain_fallback(monkeypatch):
    """Keep the options suite off the network.

    `options.read_chain_for_analytics` falls back to reading a chain from the
    exchange (through the aggregator's Kite proxy) when QuestDB holds none — which
    is what makes an underlying outside the ingested ten usable at all. Every
    pre-existing options test asserts on the QuestDB path and expects
    "no snapshot" to mean "unavailable", so leaving the fallback armed would both
    change those contracts and put an HTTP attempt in each one.

    An empty `KITE_API_URL` is the documented off switch (see `options._kite_get`),
    so this uses the real mechanism rather than stubbing internals. The tests that
    exercise the fallback set it themselves.
    """
    import options  # noqa: PLC0415 — imported here so conftest stays import-light

    monkeypatch.setattr(options, "KITE_API_URL", "", raising=False)
    yield
