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
"""

import os
import sys

import pytest

# tests/ sits directly under the service dir; put the service dir on the path so
# ``import journal`` resolves exactly as every test module expects.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

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
