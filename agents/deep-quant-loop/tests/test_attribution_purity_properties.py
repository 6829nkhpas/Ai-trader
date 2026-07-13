"""Property-based test for attribution purity / no input mutation (task 6.3).

Feature: feature-attribution-pruning

This module implements design **Property 2: Purity and no input mutation**:

    For any list of trade rows and any configuration, running the full pipeline
    (build_attribution_report and derive_weight_map) leaves the input rows and
    the configuration byte-for-byte unchanged, and produces no observable side
    effect.

Validates: Requirements 1.5, 2.3, 3.6, 8.2, 8.3.

The pure core reads only the rows and config it is handed: it holds no ambient
state and must NEVER mutate its inputs. We exercise this end to end by taking a
``copy.deepcopy`` snapshot of the input ``rows`` (a list of mutable nested dicts,
so any in-place write WOULD be detectable) before invoking the pipeline, running
``build_attribution_report(rows, config)`` followed by
``derive_weight_map(report, config)``, and then asserting ``rows == snapshot``
(deep-equal). ``AttributionConfig`` is a frozen dataclass — it cannot be mutated
in place — but we confirm it equals its pre-call snapshot regardless.

The sys.path / import pattern and the ``@composite`` journal generator mirror
``tests/test_attribution_determinism_properties.py``.
"""

import copy
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (attribution.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from attribution import (  # noqa: E402
    AttributionConfig,
    build_attribution_report,
    derive_weight_map,
)


# ── Random-but-valid AttributionConfig (covers "any configuration") ───────────
# Each field is drawn within its documented range so the purity property is
# exercised across the whole configuration space, not just a single fixed config.
@st.composite
def _config(draw):
    """A random AttributionConfig with every field inside its documented range."""
    return AttributionConfig(
        min_sample_dimension=draw(st.integers(min_value=1, max_value=200)),
        min_sample_value=draw(st.integers(min_value=1, max_value=100)),
        contribution_threshold=draw(
            st.floats(
                min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False
            )
        ),
        global_min_scored=draw(st.integers(min_value=1, max_value=500)),
        down_weight_factor=draw(
            st.floats(
                min_value=0.0,
                max_value=1.0,
                exclude_min=True,  # (0.0, 1.0]
                allow_nan=False,
                allow_infinity=False,
            )
        ),
        weight_map_enabled=draw(st.booleans()),
    )


# ── Shared journal generators (local to this file) ────────────────────────────
# The real fingerprint dimensions and a small pool of values, so generated keys
# look like the journal's low-cardinality fingerprints and collide across rows
# (exercising real per-value aggregation rather than all-singleton values).
_DIMENSIONS = [
    "dir", "macro", "pred", "va", "regime",
    "rs", "fc", "tm", "sess", "db", "opt",
]
_VALUES = [
    "BUY", "SELL", "aligned", "below", "above",
    "trend-favorable", "leader-aligned", "strong", "weak", "morning",
    "unknown", "",
]

# A finite, usable R-multiple (a *scored* row must carry one of these).
_finite_r = st.floats(
    min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False
)

# A non-finite / unusable R-multiple: None, NaN, or ±inf. A win/loss row carrying
# one of these is NOT a Scored_Trade.
_nonfinite_r = st.one_of(
    st.none(),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
)


@st.composite
def _setup_key(draw):
    """A random ``setup_key``: a structured dimension:value fingerprint, or one of
    a set of malformed / empty keys (robustness coverage)."""
    kind = draw(st.integers(min_value=0, max_value=3))
    if kind == 0:
        # Malformed / empty / degenerate keys the parser must tolerate.
        return draw(st.sampled_from(
            ["", "   ", "|", "||", "a||b", ":", ":trend", "regime", "regime:",
             "regime:unknown", "fc:aligned:strong", "x:|y:unknown|z"]
        ))
    if kind == 1:
        # Wholly arbitrary text.
        return draw(st.text(max_size=40))
    # Structured: a random non-empty subset of dimensions, each with a random
    # value. dict() collapses duplicate dimensions deterministically.
    spec = draw(st.dictionaries(
        keys=st.sampled_from(_DIMENSIONS),
        values=st.sampled_from(_VALUES),
        min_size=1,
        max_size=6,
    ))
    return "|".join(f"{d}:{v}" for d, v in spec.items())


_source = st.sampled_from(["backtest", "live", "LIVE", "Backtest", None, "", "paper"])


@st.composite
def _scored_row(draw):
    """A guaranteed Scored_Trade: win/loss status with a finite ``r_multiple``.

    Carries a nested mutable dict (``meta``) in addition to the flat fields so an
    in-place write at ANY depth would be detectable by the deep-equal assertion.
    """
    return {
        "setup_key": draw(_setup_key()),
        "status": draw(st.sampled_from(["win", "loss", "WIN", "Loss"])),
        "r_multiple": draw(_finite_r),
        "source": draw(_source),
        "symbol": draw(st.sampled_from(["RELIANCE", "TCS", "INFY", None])),
        "meta": {"tags": draw(st.lists(st.sampled_from(_VALUES), max_size=3))},
    }


@st.composite
def _non_scored_row(draw):
    """A guaranteed NON-scored row (non-resolving status, or unusable r_multiple)."""
    setup_key = draw(_setup_key())
    source = draw(_source)
    symbol = draw(st.sampled_from(["RELIANCE", "TCS", "INFY", None]))
    meta = {"tags": draw(st.lists(st.sampled_from(_VALUES), max_size=3))}
    if draw(st.booleans()):
        return {
            "setup_key": setup_key,
            "status": draw(st.sampled_from(["open", "expired", "hold", "OPEN", "", "pending"])),
            "r_multiple": draw(st.one_of(_finite_r, _nonfinite_r)),
            "source": source,
            "symbol": symbol,
            "meta": meta,
        }
    return {
        "setup_key": setup_key,
        "status": draw(st.sampled_from(["win", "loss", "WIN", "Loss"])),
        "r_multiple": draw(_nonfinite_r),
        "source": source,
        "symbol": symbol,
        "meta": meta,
    }


@st.composite
def _journal_row(draw):
    """An arbitrary trade row: scored OR non-scored, full range of keys/statuses."""
    if draw(st.booleans()):
        return draw(_scored_row())
    return draw(_non_scored_row())


@st.composite
def _journal(draw, min_size=0, max_size=40):
    """A random journal: a list of arbitrary, mutable trade rows."""
    return draw(st.lists(_journal_row(), min_size=min_size, max_size=max_size))


# ─────────────────────────────────────────────────────────────────────────────
# Property 2 (task 6.3): Purity and no input mutation
# ─────────────────────────────────────────────────────────────────────────────

# Feature: feature-attribution-pruning, Property 2: For any list of trade rows and any configuration, running the full pipeline (build_attribution_report and derive_weight_map) leaves the input rows and the configuration byte-for-byte unchanged, and produces no observable side effect.
@settings(max_examples=100, deadline=None)
@given(rows=_journal(), config=_config())
def test_property_2_purity_no_input_mutation(rows, config):
    """Feature: feature-attribution-pruning, Property 2: running the full pipeline
    leaves the input rows and the configuration unchanged.

    Take a ``deepcopy`` snapshot of ``rows`` (a list of mutable nested dicts)
    before the pipeline runs, then invoke ``build_attribution_report`` followed
    by ``derive_weight_map``. Afterward the input list and every nested dict must
    be deep-equal to the snapshot — the pure core reads its inputs and must never
    write them. ``AttributionConfig`` is frozen (cannot be mutated in place), but
    we confirm it still equals its pre-call snapshot.

    Validates: Requirements 1.5, 2.3, 3.6, 8.2, 8.3
    """
    rows_snapshot = copy.deepcopy(rows)
    config_snapshot = copy.deepcopy(config)

    report = build_attribution_report(rows, config)
    weight_map = derive_weight_map(report, config)

    # The pipeline must produce results without touching the inputs.
    assert isinstance(report, dict)
    assert isinstance(weight_map, dict)

    # No mutation of the input list or any of its nested dicts (deep-equal).
    assert rows == rows_snapshot

    # The frozen config is unchanged (it cannot be mutated, but confirm).
    assert config == config_snapshot
