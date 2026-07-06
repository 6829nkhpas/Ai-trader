"""Property-based test for debate configuration resolution (debate.py, task 1.2).

Feature: multi-agent-debate

This module implements design **Property 15: Debate configuration resolution is
total and bounded**:

    For arbitrary environment values (unset / empty / whitespace / non-numeric /
    out-of-range / valid) for every debate-tuning variable,
    ``resolve_debate_config()`` never raises and always returns a
    ``DebateConfig`` whose every field satisfies the documented invariants.

Validates: Requirements 6.1, 6.3, 6.4, 6.5.

The strategy fuzzes each of the six debate environment variables
(``DEBATE_ROUNDS``, ``DEBATE_MAX_TURNS``, ``DEBATE_JUDGE_MAX_TOOL_CALLS``,
``DEBATE_BULL_MODEL``, ``DEBATE_BEAR_MODEL``, ``DEBATE_JUDGE_MODEL``) plus the
system-default ``LLM_MODEL`` and the explicit ``default_model`` argument across
the full space of degraded inputs. For every combination it asserts the resolved
config:

  * ``rounds`` in ``[1, MAX_ROUNDS]`` (R6.1, R6.5),
  * ``max_turns`` large enough to run ``rounds`` (>= the derived turn budget) and
    ``<= MAX_TURNS_CAP`` (R6.2/R6.5 totality and bounding),
  * ``judge_max_tool_calls`` in ``[0, JUDGE_MAX_TOOL_CALLS_CAP]`` (R6.5),
  * each per-role model is a non-empty string (R6.3, R6.4),

and that the call itself never raises regardless of the raw environment (R6.5).

The environment is mutated in-process and restored from a snapshot in a
``finally`` block so the fuzzing leaves no residue for sibling tests. The
sys.path / import pattern mirrors the sibling ``test_session_*`` modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (debate.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from debate import (  # noqa: E402
    ENV_DEBATE_BEAR_MODEL,
    ENV_DEBATE_BULL_MODEL,
    ENV_DEBATE_JUDGE_MAX_TOOL_CALLS,
    ENV_DEBATE_JUDGE_MODEL,
    ENV_DEBATE_MAX_TURNS,
    ENV_DEBATE_ROUNDS,
    ENV_SYSTEM_MODEL,
    JUDGE_TURNS,
    JUDGE_MAX_TOOL_CALLS_CAP,
    MAX_ROUNDS,
    MAX_TURNS_CAP,
    TURNS_PER_ROUND,
    DebateConfig,
    resolve_debate_config,
)

# Every environment variable the resolver consults; snapshotted and restored so
# the fuzzed values never leak into sibling tests.
_MANAGED_ENV = (
    ENV_DEBATE_ROUNDS,
    ENV_DEBATE_MAX_TURNS,
    ENV_DEBATE_JUDGE_MAX_TOOL_CALLS,
    ENV_DEBATE_BULL_MODEL,
    ENV_DEBATE_BEAR_MODEL,
    ENV_DEBATE_JUDGE_MODEL,
    ENV_SYSTEM_MODEL,
)


def _int_env_values():
    """Raw values for an integer-typed env var across the degraded input space.

    ``None`` models an unset variable; the remaining branches cover empty /
    whitespace / non-numeric / out-of-range / in-range valid values so the
    resolver's clamp-or-default behaviour is exercised everywhere.
    """
    return st.one_of(
        st.none(),                                   # unset
        st.just(""),                                 # empty
        st.sampled_from(["   ", "\t", "\n  "]),       # whitespace
        st.sampled_from(["abc", "1.5", "0x10", "NaN", "1e3", "--3", "3,5"]),  # non-numeric
        st.integers(min_value=-10_000, max_value=10_000).map(str),  # any int (in/out of range)
        # Padded valid-ish ints to exercise the strip-then-parse path.
        st.integers(min_value=-50, max_value=50).map(lambda n: f"  {n}  "),
    )


def _model_env_values():
    """Raw values for a per-role model env var across the degraded input space."""
    return st.one_of(
        st.none(),                              # unset
        st.just(""),                            # empty
        st.sampled_from(["   ", "\t", "\n"]),    # whitespace -> treated as unset
        st.text(min_size=1, max_size=40),       # arbitrary text (may be blank/padded)
        st.sampled_from(["gpt-4o", "gemini-2.5-pro", "claude-3-5-sonnet"]),  # realistic
    )


@st.composite
def _env_and_default(draw):
    """An arbitrary environment mapping plus an arbitrary ``default_model`` arg."""
    env = {
        ENV_DEBATE_ROUNDS: draw(_int_env_values()),
        ENV_DEBATE_MAX_TURNS: draw(_int_env_values()),
        ENV_DEBATE_JUDGE_MAX_TOOL_CALLS: draw(_int_env_values()),
        ENV_DEBATE_BULL_MODEL: draw(_model_env_values()),
        ENV_DEBATE_BEAR_MODEL: draw(_model_env_values()),
        ENV_DEBATE_JUDGE_MODEL: draw(_model_env_values()),
        ENV_SYSTEM_MODEL: draw(_model_env_values()),
    }
    default_model = draw(
        st.one_of(
            st.none(),
            st.just(""),
            st.sampled_from(["   ", "\t"]),
            st.text(min_size=1, max_size=40),
            st.sampled_from(["gpt-4o-mini", "gemini-2.5-flash"]),
        )
    )
    return env, default_model


def _is_non_empty_str(value) -> bool:
    return isinstance(value, str) and value.strip() != ""


# ─────────────────────────────────────────────────────────────────────────────
# Property 15: Debate configuration resolution is total and bounded
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 15: Debate configuration resolution is total and bounded
@settings(max_examples=100, deadline=None)
@given(case=_env_and_default())
def test_property_15_debate_config_resolution_is_total_and_bounded(case):
    """Validates: Requirements 6.1, 6.3, 6.4, 6.5

    For arbitrary (possibly invalid) environment values and ``default_model``,
    ``resolve_debate_config()`` never raises and returns a ``DebateConfig`` whose
    fields are all within their documented bounds with non-empty role models.
    """
    env_values, default_model = case

    snapshot = {name: os.environ.get(name) for name in _MANAGED_ENV}
    try:
        for name in _MANAGED_ENV:
            raw = env_values.get(name)
            if raw is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = raw

        # ── Totality: the call must never raise (R6.5). ───────────────────────
        config = resolve_debate_config(default_model)

        assert isinstance(config, DebateConfig)

        # ── rounds in [1, MAX_ROUNDS] (R6.1, R6.5). ──────────────────────────
        assert isinstance(config.rounds, int) and not isinstance(config.rounds, bool)
        assert 1 <= config.rounds <= MAX_ROUNDS, (
            f"rounds {config.rounds} out of [1, {MAX_ROUNDS}]"
        )

        # ── max_turns large enough for rounds and <= MAX_TURNS_CAP (R6.5). ───
        derived = config.rounds * TURNS_PER_ROUND + JUDGE_TURNS
        assert isinstance(config.max_turns, int) and not isinstance(config.max_turns, bool)
        assert config.max_turns >= derived, (
            f"max_turns {config.max_turns} too small to run {config.rounds} "
            f"rounds (need >= {derived})"
        )
        assert config.max_turns <= MAX_TURNS_CAP, (
            f"max_turns {config.max_turns} exceeds cap {MAX_TURNS_CAP}"
        )

        # ── judge_max_tool_calls in [0, JUDGE_MAX_TOOL_CALLS_CAP] (R6.5). ─────
        assert isinstance(config.judge_max_tool_calls, int) and not isinstance(
            config.judge_max_tool_calls, bool
        )
        assert 0 <= config.judge_max_tool_calls <= JUDGE_MAX_TOOL_CALLS_CAP, (
            f"judge_max_tool_calls {config.judge_max_tool_calls} out of "
            f"[0, {JUDGE_MAX_TOOL_CALLS_CAP}]"
        )

        # ── Every role model is a non-empty string (R6.3, R6.4). ─────────────
        for role, value in (
            ("bull_model", config.bull_model),
            ("bear_model", config.bear_model),
            ("judge_model", config.judge_model),
        ):
            assert _is_non_empty_str(value), f"{role} must be a non-empty string, got {value!r}"
    finally:
        # Restore the environment exactly as it was before the fuzzing.
        for name, original in snapshot.items():
            if original is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original
