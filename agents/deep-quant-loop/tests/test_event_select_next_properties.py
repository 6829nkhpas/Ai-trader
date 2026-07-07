# Feature: earnings-event-risk-gate, Property 9: Nearest-future selection excludes past and at-reference events
"""Property-based test for nearest-future Scheduled_Event selection (events.py, task 2.11).

Feature: earnings-event-risk-gate

This module implements design **Property 9: Nearest-future selection excludes
past and at-reference events**:

    Given an iterable of candidate event timestamps (epoch ms) and a reference
    "now" timestamp, ``select_next_event`` discards every candidate at or before
    ``reference_ms`` (past / not upcoming, Requirement 1.6) and returns the
    earliest of the remaining strictly-future candidates, or ``None`` when none
    remain (Requirement 1.5). Non-finite / non-numeric candidates (and a
    non-finite / non-numeric ``reference_ms`` or a non-iterable ``candidate_ms``)
    are ignored rather than raising. The function is pure and never raises.

Validates: Requirements 1.5, 1.6.

Strategy: generate candidate lists that deliberately mix strictly-future,
at-reference, past, and "garbage" (non-finite / non-numeric) entries, plus a
finite ``reference_ms``. The expected earliest strictly-future finite candidate
(or ``None``) is computed independently and asserted equal to the function's
result. Additional cases assert ``None`` when every candidate is
past / at-reference / garbage / empty, and that a non-finite reference or a
non-iterable candidate collection yields ``None`` without raising.

The sys.path / import bootstrap mirrors
``tests/test_event_determinism_properties.py``.
"""

import copy
import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (events.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import events  # noqa: E402
from events import EventConfig, HOLDING_HORIZONS, select_next_event  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

def _is_finite_number(v) -> bool:
    """Mirror ``events._is_finite_number``: a finite real number, bool excluded."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _expected_next(candidate_ms, reference_ms):
    """Independently compute the earliest strictly-future finite candidate.

    Ignores non-finite / non-numeric candidates and any candidate at or before
    ``reference_ms``. Returns ``float`` (matching the function's cast) or ``None``.
    """
    if not _is_finite_number(reference_ms):
        return None
    try:
        iterator = iter(candidate_ms)
    except TypeError:
        return None
    best = None
    for c in iterator:
        if not _is_finite_number(c):
            continue
        if c <= reference_ms:
            continue
        if best is None or c < best:
            best = float(c)
    return best


# ── Input strategies ──────────────────────────────────────────────────────────

# A representative span of finite epoch-millisecond timestamps used as the
# reference "now". Kept well inside the representable datetime range.
_finite_ms = st.one_of(
    st.integers(min_value=1_500_000_000_000, max_value=1_800_000_000_000),  # ~2017..2027
    st.floats(
        min_value=0.0,
        max_value=4_102_444_800_000.0,
        allow_nan=False,
        allow_infinity=False,
    ),
)

# "Garbage" candidate entries the selector must ignore rather than trip over:
# non-numeric strings, None, NaN, ±inf, and booleans (bool is excluded by
# _is_finite_number even though it is an int subclass).
_garbage_candidate = st.one_of(
    st.none(),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
    st.booleans(),
    st.text(alphabet="abc0123:-/", min_size=0, max_size=6),
    st.tuples(st.integers()),
)

# A single finite candidate spanning a wide range so the mix contains a blend of
# past, at-reference, and future entries relative to any drawn reference.
_finite_candidate = st.one_of(
    st.integers(min_value=0, max_value=4_102_444_800_000),
    st.floats(min_value=0.0, max_value=4_102_444_800_000.0,
              allow_nan=False, allow_infinity=False),
)

# A mixed candidate: finite (past/future/at-ref) or garbage.
_mixed_candidate = st.one_of(_finite_candidate, _finite_candidate, _garbage_candidate)

_candidate_list = st.lists(_mixed_candidate, min_size=0, max_size=12)


@st.composite
def _event_configs(draw):
    """Structurally-valid resolved configs; selection ignores config content."""
    imminent = draw(st.integers(min_value=0, max_value=30))
    through = draw(st.integers(min_value=0, max_value=imminent))
    return EventConfig(
        enabled=draw(st.booleans()),
        timezone=draw(st.sampled_from(["Asia/Kolkata", "UTC", "America/New_York"])),
        default_holding_horizon=draw(st.sampled_from(sorted(HOLDING_HORIZONS))),
        imminent_window_days=imminent,
        through_event_window_days=through,
        source_timeout_s=draw(
            st.floats(min_value=0.001, max_value=120.0, allow_nan=False, allow_infinity=False)
        ),
        calendar_api_url=draw(st.one_of(st.none(), st.sampled_from(["https://x.test/cal"]))),
        calendar_file_path=draw(st.one_of(st.none(), st.sampled_from(["/tmp/cal.json"]))),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 9 (task 2.11): Nearest-future selection excludes past/at-reference
# ─────────────────────────────────────────────────────────────────────────────

# Feature: earnings-event-risk-gate, Property 9: Nearest-future selection excludes past and at-reference events
@settings(max_examples=25, deadline=None)
@given(
    candidate_ms=_candidate_list,
    reference_ms=_finite_ms,
    config=_event_configs(),
)
def test_property_9_selects_earliest_strictly_future_candidate(
    candidate_ms, reference_ms, config
):
    """Validates: Requirements 1.5, 1.6

    For an arbitrary mix of past, at-reference, future, and garbage candidates,
    ``select_next_event`` returns exactly the earliest strictly-future finite
    candidate (independently computed) or ``None`` when none exist. It never
    raises, and any returned value is strictly greater than ``reference_ms``.
    """
    result = select_next_event(candidate_ms, reference_ms, config)
    expected = _expected_next(candidate_ms, reference_ms)

    assert result == expected

    if result is not None:
        # The chosen event is genuinely in the future (R1.6) ...
        assert result > reference_ms
        # ... and is no later than any other valid future candidate (R1.5).
        for c in candidate_ms:
            if _is_finite_number(c) and c > reference_ms:
                assert result <= c


# Feature: earnings-event-risk-gate, Property 9: Nearest-future selection excludes past and at-reference events
@settings(max_examples=25, deadline=None)
@given(
    reference_ms=_finite_ms,
    config=_event_configs(),
    n_past=st.integers(min_value=0, max_value=6),
    n_garbage=st.integers(min_value=0, max_value=6),
)
def test_property_9_none_when_no_future_candidate(reference_ms, config, n_past, n_garbage):
    """Validates: Requirements 1.5, 1.6

    When every candidate is at or before the reference (past / at-reference) or
    is garbage — and when the candidate list is empty — the selection is
    ``None``: there is no upcoming event to return, and no fabrication.
    """
    # Past / at-reference finite candidates (all <= reference_ms).
    past = [reference_ms] * min(n_past, 1)  # include the at-reference boundary
    # Additional strictly-past candidates.
    for _ in range(max(n_past - 1, 0)):
        past.append(reference_ms - 1)
    garbage = [None, float("nan"), float("inf"), "notanumber", True][:n_garbage]

    candidates = past + garbage
    assert select_next_event(candidates, reference_ms, config) is None
    # Empty candidate list -> None.
    assert select_next_event([], reference_ms, config) is None


# Feature: earnings-event-risk-gate, Property 9: Nearest-future selection excludes past and at-reference events
@settings(max_examples=25, deadline=None)
@given(
    candidate_ms=_candidate_list,
    config=_event_configs(),
    bad_reference=st.sampled_from([None, float("nan"), float("inf"), float("-inf"), "x", True]),
)
def test_property_9_invalid_reference_or_noniterable_returns_none(
    candidate_ms, config, bad_reference
):
    """Validates: Requirements 1.5, 1.6

    A non-finite / non-numeric ``reference_ms`` yields ``None`` without raising
    (there is no valid "now" to compare against), and a non-iterable
    ``candidate_ms`` likewise yields ``None`` rather than raising.
    """
    # Invalid reference -> None regardless of candidates.
    assert select_next_event(candidate_ms, bad_reference, config) is None
    # Non-iterable candidate collection -> None (never raises), given a valid ref.
    assert select_next_event(12345, 1_600_000_000_000, config) is None


# Feature: earnings-event-risk-gate, Property 9: Nearest-future selection excludes past and at-reference events
@settings(max_examples=25, deadline=None)
@given(
    candidate_ms=_candidate_list,
    reference_ms=_finite_ms,
    config=_event_configs(),
)
def test_property_9_does_not_mutate_candidates(candidate_ms, reference_ms, config):
    """Validates: Requirements 1.5, 1.6

    Selection is pure: it must not mutate the candidate collection it is handed.
    """
    before = copy.deepcopy(candidate_ms)
    select_next_event(candidate_ms, reference_ms, config)

    def _same(after, orig):
        if isinstance(after, float) and isinstance(orig, float) \
                and math.isnan(after) and math.isnan(orig):
            return True
        return after == orig and type(after) is type(orig)

    assert len(candidate_ms) == len(before)
    assert all(_same(a, b) for a, b in zip(candidate_ms, before))
