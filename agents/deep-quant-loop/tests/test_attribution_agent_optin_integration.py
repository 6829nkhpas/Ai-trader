"""Integration tests: agent opt-in Weight_Map consultation (graph.py, task 11.2).

Feature: feature-attribution-pruning

These tests exercise the agent-side opt-in seam where the Feature_Attribution
Weight_Map is (optionally) fed back into a committed decision's conviction:
``graph._apply_weight_map_to_conviction(decision, symbol)``, which
``graph._finalize_decision`` calls AFTER ``build_defensibility_record`` and
BEFORE ``journal.record_decision``.

What is verified (per the task and the requirements it cites):

  * Flag OFF (unset / explicit false) — the default — the helper returns before
    touching the decision in ANY way: the committed decision is byte-for-byte
    unchanged, no ``weight_map_applied`` key is added, and the Weight_Map is
    NEVER consulted (``attribution.weight_map_from_journal`` is not called). This
    is the "zero effect on the running agent" guarantee (R6.2, R6.3, R9.4).

  * Flag ON — the helper consults a (monkeypatched) known Weight_Map, scales the
    decision's ``conviction_score`` by the sample-weighted-neutral mean of the
    present fingerprint dimensions' weights, and records ``weight_map_applied:
    True`` with the resolved per-dimension weights (and the before/after
    conviction) in the defensibility record so the committed decision stays
    auditable (R6.5).

  * Risk-rejected stays rejected (R6.4) — the Weight_Map only ever scales
    ``conviction_score``; it never touches the decision's ``action`` or its
    execution levels (entry / stop_loss / take_profit). A validator-rejected
    declare_trade never reaches ``_finalize_decision`` at all (it leaves the
    decision unset and the bounded loop continues), so the strongest faithful
    guarantee at this seam is that the helper cannot flip a HOLD/rejected
    decision's action or relax its levels — the only thing the weight can do is
    attenuate conviction.

The real LLM / Rust server is never invoked. The conviction scaling is driven by
a monkeypatched ``attribution.weight_map_from_journal`` returning a KNOWN map and
``ATTRIBUTION_WEIGHT_MAP_ENABLED`` toggled via ``monkeypatch.setenv`` — the
config is resolved from the environment at call time (``resolve_attribution_config``
reads ``os.getenv`` each call), so setting the env var before invoking the helper
is sufficient.

The sys.path / import pattern mirrors the sibling graph tests: the service
directory (one level up) is prepended to ``sys.path`` so ``graph`` is importable
when pytest is run from anywhere.
"""

import copy
import os
import sys

import pytest

# Make the service package importable (graph.py / attribution.py / journal.py
# live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import attribution  # noqa: E402
import journal  # noqa: E402
from graph import _apply_weight_map_to_conviction  # noqa: E402

ENV_FLAG = "ATTRIBUTION_WEIGHT_MAP_ENABLED"

# The committed-decision fields the Weight_Map must NEVER touch: the action and
# the three execution levels. The weight only scales conviction_score (R6.4).
_EXECUTION_LEVEL_FIELDS = ("entry", "stop_loss", "take_profit")


def _committed_decision(*, action="BUY", conviction=80):
    """Build a committed decision dict with a defensibility record already attached.

    Mirrors the shape ``_finalize_decision`` hands the helper: the defensibility
    record exists (the helper requires a dict ``defensibility`` to run), the
    action/levels are present, and ``conviction_score`` is a plain int in [0,100]
    exactly as a committed ``declare_trade`` carries. The defensibility record is
    intentionally minimal — ``derive_setup_tags`` reads it defensively and
    defaults every absent dimension (so the fingerprint is well-formed regardless).
    """
    decision = {
        "action": action,
        "source": "declare_trade",
        "conviction_score": conviction,
        "defensibility": {"action": action},
    }
    for field in _EXECUTION_LEVEL_FIELDS:
        decision[field] = 100.0
    return decision


def _present_dimensions(decision):
    """The fingerprint dimensions the helper will derive for ``decision``.

    Re-derives via the SAME pure pipeline the helper uses
    (``journal.derive_setup_tags`` -> ``journal.setup_key_from_tags`` ->
    ``attribution.parse_setup_key``) so the expected weights/mean computed in the
    tests track the implementation exactly rather than hard-coding the dimension
    set.
    """
    tags = journal.derive_setup_tags(decision)
    key = journal.setup_key_from_tags(tags)
    return attribution.parse_setup_key(key)


def _expected_scaled(conviction, applied):
    """Replicate the helper's clamp-and-round scaling for the expected value."""
    mean_weight = sum(applied.values()) / len(applied)
    scaled = int(round(conviction * mean_weight))
    return 0 if scaled < 0 else (100 if scaled > 100 else scaled)


# ─────────────────────────────────────────────────────────────────────────────
# Flag OFF: the decision path is identical to baseline; the map is never consulted
# ─────────────────────────────────────────────────────────────────────────────

def test_flag_unset_is_byte_for_byte_noop_and_never_consults_map(monkeypatch):
    """Validates: Requirements 6.2, 6.3, 9.4

    With ``ATTRIBUTION_WEIGHT_MAP_ENABLED`` unset (the default), the helper
    returns before touching the decision: it is byte-for-byte unchanged, no
    ``weight_map_applied`` key is added, and ``attribution.weight_map_from_journal``
    is NEVER called (the map is not consulted at all).
    """
    monkeypatch.delenv(ENV_FLAG, raising=False)

    # Spy that fails the test if the Weight_Map is ever consulted while OFF.
    def _must_not_be_called(*args, **kwargs):
        raise AssertionError(
            "weight_map_from_journal must NOT be consulted when the opt-in flag "
            "is disabled (the default)"
        )

    monkeypatch.setattr(attribution, "weight_map_from_journal", _must_not_be_called)

    decision = _committed_decision(action="BUY", conviction=80)
    snapshot = copy.deepcopy(decision)

    _apply_weight_map_to_conviction(decision, symbol="RELIANCE")

    # Byte-for-byte unchanged — nothing added, removed, or rescaled.
    assert decision == snapshot
    assert "weight_map_applied" not in decision["defensibility"]
    assert "weight_map" not in decision["defensibility"]
    assert decision["conviction_score"] == snapshot["conviction_score"]


def test_flag_explicit_false_is_noop_and_never_consults_map(monkeypatch):
    """Validates: Requirements 6.2, 6.3, 9.4

    An explicit falsy spelling of the flag behaves exactly like unset: a
    byte-for-byte no-op that never consults the Weight_Map.
    """
    monkeypatch.setenv(ENV_FLAG, "false")

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("Weight_Map consulted despite the flag being false")

    monkeypatch.setattr(attribution, "weight_map_from_journal", _must_not_be_called)

    decision = _committed_decision(action="SELL", conviction=55)
    snapshot = copy.deepcopy(decision)

    _apply_weight_map_to_conviction(decision, symbol="INFY")

    assert decision == snapshot
    assert "weight_map_applied" not in decision["defensibility"]


# ─────────────────────────────────────────────────────────────────────────────
# Flag ON: conviction is scaled by the weights and the application is recorded
# ─────────────────────────────────────────────────────────────────────────────

def test_flag_on_scales_conviction_by_mean_weight_and_records_application(monkeypatch):
    """Validates: Requirements 6.5

    With the flag enabled and a KNOWN Weight_Map, the helper scales
    ``conviction_score`` by the (sample-weighted-neutral) mean of the present
    dimensions' weights and records ``weight_map_applied: True`` with the
    resolved per-dimension weights and the before/after conviction in the
    defensibility record.
    """
    monkeypatch.setenv(ENV_FLAG, "true")

    conviction = 80
    decision = _committed_decision(action="BUY", conviction=conviction)

    # Build a known Weight_Map covering exactly the dimensions this decision
    # presents, each at 0.5 -> mean weight 0.5 -> conviction halved.
    present = _present_dimensions(decision)
    weight_map = {dim: 0.5 for dim in present}

    monkeypatch.setattr(
        attribution, "weight_map_from_journal", lambda symbol=None: dict(weight_map)
    )

    applied_expected = {dim: float(weight_map.get(dim, 1.0)) for dim in present}
    scaled_expected = _expected_scaled(conviction, applied_expected)
    # Sanity: a uniform 0.5 map halves the conviction.
    assert scaled_expected == int(round(conviction * 0.5))

    _apply_weight_map_to_conviction(decision, symbol="RELIANCE")

    record = decision["defensibility"]
    assert record["weight_map_applied"] is True
    assert record["weight_map"] == applied_expected
    assert record["conviction_before_weight_map"] == conviction
    assert record["conviction_after_weight_map"] == scaled_expected
    assert decision["conviction_score"] == scaled_expected
    # A (0,1] weight can only attenuate, never amplify.
    assert decision["conviction_score"] <= conviction


def test_flag_on_mixed_weights_uses_mean_of_present_dimensions(monkeypatch):
    """Validates: Requirements 6.5

    A non-uniform Weight_Map (some dimensions down-weighted, some at full weight,
    some absent -> defaulting to 1.0) scales conviction by the mean of the
    per-present-dimension weights exactly as the helper computes it.
    """
    monkeypatch.setenv(ENV_FLAG, "true")

    conviction = 90
    decision = _committed_decision(action="BUY", conviction=conviction)
    present = sorted(_present_dimensions(decision))

    # Down-weight the first two present dimensions to 0.5, leave a third out of
    # the map entirely (the helper defaults a missing dimension to 1.0), and give
    # the rest full weight 1.0 — a genuinely mixed map.
    weight_map = {}
    for i, dim in enumerate(present):
        if i < 2:
            weight_map[dim] = 0.5
        elif i == 2:
            continue  # omitted -> defaults to 1.0 inside the helper
        else:
            weight_map[dim] = 1.0

    monkeypatch.setattr(
        attribution, "weight_map_from_journal", lambda symbol=None: dict(weight_map)
    )

    applied_expected = {dim: float(weight_map.get(dim, 1.0)) for dim in present}
    scaled_expected = _expected_scaled(conviction, applied_expected)

    _apply_weight_map_to_conviction(decision, symbol="TCS")

    record = decision["defensibility"]
    assert record["weight_map_applied"] is True
    assert record["weight_map"] == applied_expected
    assert decision["conviction_score"] == scaled_expected
    assert decision["conviction_score"] <= conviction


# ─────────────────────────────────────────────────────────────────────────────
# Risk-rejected stays rejected: the weight only scales conviction (R6.4)
# ─────────────────────────────────────────────────────────────────────────────

def test_flag_on_never_touches_action_or_execution_levels(monkeypatch):
    """Validates: Requirements 6.4

    Even with the flag ON and an aggressive (down-weighting) Weight_Map, the
    helper only ever rescales ``conviction_score``: it never changes the
    decision's ``action`` nor its execution levels (entry / stop_loss /
    take_profit). The Trade_Validator already ran and is independent of the
    conviction score, so a rejected/HOLD decision cannot be flipped to a
    committed trade by the Weight_Map, and the hard risk rules are never relaxed.
    """
    monkeypatch.setenv(ENV_FLAG, "true")

    # Use a HOLD decision: it stands in for the post-validator state. The weight
    # must not flip its action to BUY/SELL nor invent/relax execution levels.
    decision = _committed_decision(action="HOLD", conviction=70)
    present = _present_dimensions(decision)
    # An aggressive 0.25 weight on every dimension — the maximum attenuation here.
    monkeypatch.setattr(
        attribution,
        "weight_map_from_journal",
        lambda symbol=None: {dim: 0.25 for dim in present},
    )

    action_before = decision["action"]
    levels_before = {f: decision[f] for f in _EXECUTION_LEVEL_FIELDS}

    _apply_weight_map_to_conviction(decision, symbol="HDFCBANK")

    # The action is never touched — a HOLD/rejected decision stays as it was.
    assert decision["action"] == action_before == "HOLD"
    # Execution levels are never touched — the weight cannot relax a hard rule.
    for field in _EXECUTION_LEVEL_FIELDS:
        assert decision[field] == levels_before[field]
    # The ONLY mutation is the (attenuated) conviction + the audit fields.
    assert decision["conviction_score"] <= 70
    assert decision["defensibility"]["weight_map_applied"] is True


def test_flag_on_with_empty_weight_map_records_application_without_scaling(monkeypatch):
    """Validates: Requirements 6.4, 6.5

    When the journal yields an empty Weight_Map (e.g. insufficient data), every
    present dimension defaults to weight 1.0, so the application is recorded for
    auditability but the conviction is unchanged (mean weight 1.0) — and the
    action/levels are still untouched.
    """
    monkeypatch.setenv(ENV_FLAG, "true")

    conviction = 64
    decision = _committed_decision(action="BUY", conviction=conviction)
    action_before = decision["action"]
    levels_before = {f: decision[f] for f in _EXECUTION_LEVEL_FIELDS}

    monkeypatch.setattr(attribution, "weight_map_from_journal", lambda symbol=None: {})

    _apply_weight_map_to_conviction(decision, symbol="SBIN")

    record = decision["defensibility"]
    # The consultation is recorded even when neutral (R6.5).
    assert record["weight_map_applied"] is True
    # Every present dimension defaulted to 1.0 -> mean 1.0 -> no change.
    assert all(w == 1.0 for w in record["weight_map"].values())
    assert decision["conviction_score"] == conviction
    # Action / levels untouched (R6.4).
    assert decision["action"] == action_before
    for field in _EXECUTION_LEVEL_FIELDS:
        assert decision[field] == levels_before[field]


if __name__ == "__main__":  # pragma: no cover - convenience for ad-hoc runs
    raise SystemExit(pytest.main([__file__, "-v"]))
