"""Property-based test for the passthrough observation tee (telemetry.py, task 9.3).

Feature: session-telemetry

This module implements design **Property 20: The observation tee is an exact
passthrough (non-invasive)**:

    For any sequence of source SSE frames, the frames yielded by ``observe_stream``
    equal the source frames exactly — same frames, same order, none added,
    removed, reordered, or altered — so telemetry never emits, blocks, or alters a
    trade decision or the run's control flow.

Validates: Requirements 2.5, 6.1, 10.2.

The sys.path / import pattern mirrors the other deep-quant-loop telemetry property
tests (e.g. ``tests/test_telemetry_funnel_ordering_properties.py``). The test
builds arbitrary lists of SSE frame strings via Hypothesis (realistic
``event: NAME\\ndata: {json}\\n\\n`` frames mixed with arbitrary text), drives them
through ``observe_stream`` over an async source generator, and asserts the
collected output list equals the input list EXACTLY.

To keep the property deterministic and free of I/O, ``telemetry.get_session_writer``
is patched to return a no-op writer whose ``enqueue`` does nothing — so the real
background writer / SQLite store is never touched and the test verifies ONLY the
passthrough behavior of the tee.
"""

import asyncio
import json
import os
import sys
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (telemetry.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import telemetry  # noqa: E402
from telemetry import (  # noqa: E402
    ENTRY_KIND_RESUME,
    ENTRY_KIND_RUN,
    EVENT_DECISION,
    EVENT_ERROR,
    EVENT_REASONING,
    EVENT_RUN_FINISHED,
    EVENT_RUN_STARTED,
    EVENT_TOOL_CALL_START,
    TRIGGER_INVALIDATION,
    TRIGGER_TARGET,
    RunEntry,
    observe_stream,
)


class _NoOpWriter:
    """A controlled writer whose ``enqueue`` is a no-op (no queue, no I/O).

    Standing in for the real background :class:`telemetry.SessionWriter` so the tee
    performs zero SQLite I/O and the property is deterministic. ``enqueue`` accepts
    any observation and returns ``True`` (as the real, non-blocking producer would)
    without doing anything.
    """

    def enqueue(self, observation):  # noqa: D401 - trivial no-op
        return True


# ── SSE frame strategies ──────────────────────────────────────────────────────
# Realistic SSE frames matching ``stream_events.format_sse`` shape, plus arbitrary
# text frames. The tee MUST re-yield ALL of them verbatim regardless of shape.

_EVENT_NAMES = [
    EVENT_RUN_STARTED,
    EVENT_RUN_FINISHED,
    EVENT_REASONING,
    EVENT_TOOL_CALL_START,
    EVENT_DECISION,
    EVENT_ERROR,
    "TOOL_CALL_END",
    "VERIFICATION_STEP",
]


@st.composite
def _realistic_sse_frame(draw):
    """A well-formed ``event: NAME\\ndata: {json}\\n\\n`` frame."""
    name = draw(st.sampled_from(_EVENT_NAMES))
    payload = draw(
        st.dictionaries(
            keys=st.sampled_from(["tool", "action", "reason", "ts", "text"]),
            values=st.one_of(
                st.text(max_size=12),
                st.integers(min_value=-1000, max_value=1000),
                st.booleans(),
            ),
            max_size=4,
        )
    )
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n"


# A frame is either a realistic SSE frame or arbitrary text (which the tee must
# still pass through unchanged — the parse is best-effort and never gates yield).
_sse_frame = st.one_of(
    _realistic_sse_frame(),
    st.text(max_size=40),
    st.just(""),
    st.just("data: not-json\n\n"),
)

_frame_list = st.lists(_sse_frame, max_size=30)

_entries = st.one_of(
    st.builds(
        RunEntry,
        kind=st.just(ENTRY_KIND_RUN),
        symbol=st.one_of(st.none(), st.sampled_from(["RELIANCE", "TCS"])),
        timeframe=st.one_of(st.none(), st.sampled_from(["5m", "15m", "1h"])),
        mode=st.one_of(st.none(), st.sampled_from(["FIND", "MANAGE"])),
    ),
    st.builds(
        RunEntry,
        kind=st.just(ENTRY_KIND_RESUME),
        symbol=st.one_of(st.none(), st.sampled_from(["RELIANCE", "TCS"])),
        timeframe=st.one_of(st.none(), st.sampled_from(["5m", "15m"])),
        mode=st.one_of(st.none(), st.sampled_from(["FIND", "MANAGE"])),
        trigger_kind=st.one_of(
            st.none(), st.sampled_from([TRIGGER_TARGET, TRIGGER_INVALIDATION])
        ),
    ),
)


async def _asrc(frames):
    """An async source generator that yields the given frames in order."""
    for frame in frames:
        yield frame


async def _collect(thread_id, entry, frames):
    """Drive ``observe_stream`` over ``frames`` and collect every yielded frame."""
    out = []
    async for f in observe_stream(thread_id, entry, _asrc(frames)):
        out.append(f)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Property 20 (task 9.3): The observation tee is an exact passthrough (non-invasive)
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 20: The observation tee is an exact passthrough (non-invasive)
@settings(max_examples=100, deadline=None)
@given(frames=_frame_list, entry=_entries, thread_id=st.text(min_size=1, max_size=12))
def test_property_20_observation_tee_is_exact_passthrough(frames, entry, thread_id):
    """Feature: session-telemetry, Property 20: The observation tee is an exact
    passthrough (non-invasive) — for any sequence of source SSE frames, the frames
    yielded by ``observe_stream`` equal the source frames EXACTLY (same frames,
    same order, same count; none added, removed, reordered, or altered).

    Validates: Requirements 2.5, 6.1, 10.2
    """
    # Patch the writer to a controlled no-op so the tee does zero I/O and the
    # property isolates passthrough behavior (no dependence on the real writer/DB).
    with mock.patch.object(telemetry, "get_session_writer", return_value=_NoOpWriter()):
        collected = asyncio.run(_collect(thread_id, entry, frames))

    # ── Exact passthrough: same length, same content, same order ────────────────
    assert collected == frames
    assert len(collected) == len(frames)
    # Element-wise identity (same object references — the tee never copies/alters).
    assert all(a is b for a, b in zip(collected, frames))
