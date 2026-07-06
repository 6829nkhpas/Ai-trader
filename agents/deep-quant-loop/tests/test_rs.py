"""Property-based tests for relative-strength parameter resolution (rs.py).

Feature: relative-strength-context

These Hypothesis properties exercise the deterministic parameter resolver
(:func:`rs.resolve_rs_config`) across the env-var input space. They complement
example-based unit tests by asserting universal invariants over the resolved
:class:`rs.RSConfig`.
"""

import os
import sys
from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (rs.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import rs  # noqa: E402
from rs import (  # noqa: E402
    DEFAULT_RS_LAGGARD_CUTOFF,
    DEFAULT_RS_LEADER_CUTOFF,
    ENV_RS_LAGGARD_CUTOFF,
    ENV_RS_LEADER_CUTOFF,
    resolve_rs_config,
)

# Every RS_* env var the resolver reads. We clear all of them inside the
# isolation context so only the two cutoffs under test influence the result and
# the remaining parameters fall back to their documented defaults deterministically.
_ALL_RS_ENV_VARS = (
    rs.ENV_RS_LOOKBACK,
    rs.ENV_RS_CORR_WINDOW,
    rs.ENV_RS_LEADER_CUTOFF,
    rs.ENV_RS_LAGGARD_CUTOFF,
    rs.ENV_RS_INDEX_FLAT_BAND,
    rs.ENV_RS_MIN_CANDLES,
    rs.ENV_RS_DEFAULT_BENCHMARK,
    rs.ENV_RS_BENCHMARK_MAP,
)


@contextmanager
def _rs_env(overrides):
    """Isolate ``os.environ`` for the resolver.

    Removes every RS_* var, applies ``overrides``, and restores the prior
    environment exactly on exit (so Hypothesis re-runs never leak state). Used
    instead of the ``monkeypatch`` fixture because Hypothesis re-invokes the test
    body many times within one function-scoped fixture lifetime.
    """
    saved = {name: os.environ.get(name) for name in _ALL_RS_ENV_VARS}
    try:
        for name in _ALL_RS_ENV_VARS:
            os.environ.pop(name, None)
        for name, value in overrides.items():
            os.environ[name] = value
        yield
    finally:
        for name, prior in saved.items():
            if prior is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prior


# In-range cutoff values (-1.0–1.0); repr() of a Python float round-trips exactly
# through float(), so the resolved cutoff equals the generated value when the
# ordering is valid (laggard < leader).
_cutoff = st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False)


# ─────────────────────────────────────────────────────────────────────────────
# Property 31 (1.3): Cutoff ordering is enforced
# ─────────────────────────────────────────────────────────────────────────────

@settings(max_examples=100)
@given(laggard=_cutoff, leader=_cutoff)
def test_property_31_cutoff_ordering_enforced(laggard, leader):
    """Feature: relative-strength-context, Property 31: Cutoff ordering is
    enforced — when the resolved ``laggard_cutoff >= leader_cutoff``, BOTH
    cutoffs revert to their documented defaults together; a strictly valid
    ordering (laggard < leader) is preserved verbatim.

    Validates: Requirements 12.5
    """
    with _rs_env(
        {ENV_RS_LAGGARD_CUTOFF: repr(laggard), ENV_RS_LEADER_CUTOFF: repr(leader)}
    ):
        config = resolve_rs_config()

    if laggard < leader:
        # Strictly valid ordering: both cutoffs preserved exactly as provided.
        assert config.laggard_cutoff == laggard
        assert config.leader_cutoff == leader
        # The preserved ordering still satisfies the strict invariant.
        assert config.laggard_cutoff < config.leader_cutoff
    else:
        # laggard >= leader (including equality): BOTH revert to their documented
        # defaults together, never just one of them.
        assert config.laggard_cutoff == DEFAULT_RS_LAGGARD_CUTOFF
        assert config.leader_cutoff == DEFAULT_RS_LEADER_CUTOFF

    # Whatever branch was taken, the resolved cutoffs always satisfy the strict
    # ordering the resolver guarantees, and the resolver never raised.
    assert config.laggard_cutoff < config.leader_cutoff
