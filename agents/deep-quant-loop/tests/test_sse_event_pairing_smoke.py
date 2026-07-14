"""Integration smoke test for SSE tool-call pairing and run termination.

Feature: agent-loop-responsiveness

This module is the optional integration smoke for **task 4.4**: on a mocked /
replayed glass-box SSE stream, it asserts the two liveness invariants the UI
relies on so no tool card is left spinning after a run settles:

  * **Pairing invariant (R4.3):** by the time ``RUN_FINISHED`` is emitted, every
    ``TOOL_CALL_START`` has a corresponding ``TOOL_CALL_END`` for the same tool
    (no orphaned / perpetually-ACTIVE tool card). ``TOOL_CALL_START`` always
    precedes its matching ``TOOL_CALL_END``.
  * **Single-terminal invariant (R4.1):** a settled run ends with exactly one
    ``RUN_FINISHED`` event, and it is the final event of the stream.

No live LLM / backend / graph is invoked. The stream is produced exactly the way
``main.py``'s ``event_generator`` produces it — the pure ordered assembler
``assemble_run_events`` builds the ``(event_name, payload)`` sequence from
lightweight stub node updates, and each event is framed through the real
``format_sse`` into an SSE wire string. The test then **parses that SSE text back**
into events and validates the invariants, so the contract is exercised end to end
through serialization (event names, JSON-object payloads, framing) rather than
against in-memory dicts alone.

The stub message objects (class names containing ``AIMessage`` / ``ToolMessage``)
and the sys.path bootstrap mirror the sibling ``test_stream_ordering_resilience_
debate_properties`` / ``test_stream_events`` modules; ``message_events`` dispatches
on ``type(msg).__name__`` and emits ``TOOL_CALL_START`` from an AIMessage's
``tool_calls`` and ``TOOL_CALL_END`` from the matching ``ToolMessage``.

Validates: Requirements 4.1, 4.3.
"""

import json
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (stream_events.py / graph.py live up one).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from stream_events import (  # noqa: E402
    RUN_STARTED,
    RUN_FINISHED,
    TOOL_CALL_START,
    TOOL_CALL_RESULT,
    TOOL_CALL_END,
    RUN_COMPLETED,
    RUN_PAUSED,
    format_sse,
    assemble_run_events,
)


# ── Lightweight stub messages (mirror the LangChain shape message_events reads) ─
class StubAIMessage:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.type = "ai"
        self.additional_kwargs = {}


class StubToolMessage:
    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


def _tool_call(name):
    """A single well-formed tool call as emitted on an AIMessage."""
    return {"name": name, "args": {"timeframe": "15m"}, "id": f"call_{name}"}


# ── SSE (de)serialization + invariant validator (documents the contract) ──────

def parse_sse_stream(sse_text):
    """Parse an SSE wire string into an ordered ``[(event_name, data_dict), ...]``.

    Mirrors how a browser ``EventSource`` reconstructs events: each ``\n\n``-
    separated block carries an ``event:`` line (the event name) and a ``data:``
    line (a JSON object). Returns them in stream order so the pairing / ordering
    invariants can be checked exactly as the frontend would observe them.
    """
    events = []
    for block in sse_text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        assert name is not None, f"SSE block missing event name: {block!r}"
        assert isinstance(data, dict), f"SSE data must be a JSON object, got {data!r}"
        events.append((name, data))
    return events


def validate_tool_pairing_and_termination(events):
    """Validate the pairing + single-terminal invariants over an event list.

    Raises ``AssertionError`` describing the first violation; returns silently
    when the stream is well formed. Factored out so the test documents the exact
    invariant and can reuse it across several replayed streams:

      * every ``TOOL_CALL_START`` is matched by a later ``TOOL_CALL_END`` for the
        same tool (FIFO per tool name), with no ``TOOL_CALL_END`` preceding an
        open ``TOOL_CALL_START`` and no START left open at ``RUN_FINISHED`` (R4.3);
      * exactly one ``RUN_FINISHED``, emitted as the final event (R4.1).
    """
    names = [n for n, _ in events]

    # ── Single-terminal invariant (R4.1). ────────────────────────────────────
    assert names.count(RUN_FINISHED) == 1, (
        f"expected exactly one RUN_FINISHED, got {names.count(RUN_FINISHED)}"
    )
    assert names[-1] == RUN_FINISHED, f"RUN_FINISHED must be the final event, got {names[-1]!r}"

    # ── Pairing invariant (R4.3): FIFO match START->END per tool name. ────────
    open_starts = {}  # tool name -> count of unmatched TOOL_CALL_START
    for name, payload in events:
        if name == RUN_FINISHED:
            break
        if name == TOOL_CALL_START:
            tool = payload.get("tool")
            open_starts[tool] = open_starts.get(tool, 0) + 1
        elif name == TOOL_CALL_END:
            tool = payload.get("tool")
            assert open_starts.get(tool, 0) > 0, (
                f"TOOL_CALL_END for {tool!r} with no matching open TOOL_CALL_START"
            )
            open_starts[tool] -= 1

    orphaned = {t: c for t, c in open_starts.items() if c > 0}
    assert not orphaned, (
        f"orphaned TOOL_CALL_START (no TOOL_CALL_END before RUN_FINISHED): {orphaned}"
    )


def _replay_sse(thread_id, node_updates, outcome):
    """Assemble a run, frame it through the real ``format_sse``, and parse it back.

    This is the mocked/replayed SSE stream: the same ordered assembler and the
    same framing ``main.py`` uses, with no live graph/LLM.
    """
    assembled = assemble_run_events(thread_id, node_updates, outcome)
    sse_text = "".join(format_sse(name, payload) for name, payload in assembled)
    return parse_sse_stream(sse_text)


# ── Representative, well-formed run ───────────────────────────────────────────

def _representative_node_updates():
    """A representative run: reasoning, several START/RESULT/END tool round-trips
    (including the same tool invoked twice), then a committed decision.

    Deliberately well formed — every issued tool call is followed by its result +
    end — so the smoke asserts the happy-path contract the UI depends on.
    """
    return [
        {"messages": [StubAIMessage(content="Reading the tape before acting.")]},
        # get_candles round-trip.
        {"messages": [StubAIMessage(content="", tool_calls=[_tool_call("get_candles")])]},
        {"messages": [StubToolMessage(content='{"candles": [1, 2, 3]}', name="get_candles")]},
        # get_support_resistance round-trip.
        {"messages": [StubAIMessage(content="", tool_calls=[_tool_call("get_support_resistance")])]},
        {"messages": [StubToolMessage(content='{"levels": {"support": 100}}', name="get_support_resistance")]},
        # get_options_chain_analysis round-trip (index confirmation).
        {"messages": [StubAIMessage(content="", tool_calls=[_tool_call("get_options_chain_analysis")])]},
        {"messages": [StubToolMessage(content='{"pcr_oi": 0.9}', name="get_options_chain_analysis")]},
        # The same tool invoked a second time — pairing must still be FIFO-clean.
        {"messages": [StubAIMessage(content="", tool_calls=[_tool_call("get_candles")])]},
        {"messages": [StubToolMessage(content='{"candles": [4, 5, 6]}', name="get_candles")]},
        # A committed decision closes the analysis.
        {"decision": {"action": "BUY", "conviction_score": 72, "setup_validation": "levels reclaimed"}},
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Smoke tests
# ─────────────────────────────────────────────────────────────────────────────

def test_representative_stream_pairs_tools_and_finishes_once():
    """Validates: Requirements 4.1, 4.3

    On a representative replayed SSE stream, every TOOL_CALL_START is matched by a
    TOOL_CALL_END for the same tool, and the run ends with exactly one
    RUN_FINISHED as the final event.
    """
    events = _replay_sse("thread-abc", _representative_node_updates(), RUN_COMPLETED)
    names = [n for n, _ in events]

    # Sanity: the stream actually exercised tool cards and both terminals.
    assert names[0] == RUN_STARTED
    starts = [p["tool"] for n, p in events if n == TOOL_CALL_START]
    ends = [p["tool"] for n, p in events if n == TOOL_CALL_END]
    assert len(starts) == 4 and len(ends) == 4, (starts, ends)
    # Every RESULT sits between its START and END for the tool.
    assert sum(1 for n, _ in events if n == TOOL_CALL_RESULT) == 4

    # The core invariants.
    validate_tool_pairing_and_termination(events)
    # Multiset of started tools equals multiset of ended tools (no orphan/extra).
    assert sorted(starts) == sorted(ends)


def test_paused_run_also_pairs_and_finishes_once():
    """Validates: Requirements 4.1, 4.3

    A paused run (heartbeat wait) still emits a single terminal RUN_FINISHED and
    leaves no tool card open.
    """
    events = _replay_sse("thread-paused", _representative_node_updates(), RUN_PAUSED)
    assert events[-1] == (RUN_FINISHED, events[-1][1])
    assert events[-1][1].get("status") == "paused"
    validate_tool_pairing_and_termination(events)


def test_validator_detects_orphaned_tool_start():
    """The validator itself catches an orphaned TOOL_CALL_START (a spinning card).

    This documents the invariant negatively: a stream whose START has no matching
    END before RUN_FINISHED must fail validation, proving the smoke would catch a
    regression that leaves a tool card ACTIVE.
    """
    orphaned = [
        (RUN_STARTED, {"thread_id": "t"}),
        (TOOL_CALL_START, {"tool": "get_candles", "args": {}}),
        # ... no TOOL_CALL_END for get_candles ...
        (RUN_FINISHED, {"thread_id": "t", "status": "completed"}),
    ]
    try:
        validate_tool_pairing_and_termination(orphaned)
    except AssertionError:
        pass
    else:  # pragma: no cover - guards against a silent invariant regression
        raise AssertionError("validator failed to flag an orphaned TOOL_CALL_START")


# ── Property strengthening over many synthetic well-formed streams ────────────

_TOOL_NAMES = [
    "get_candles",
    "get_multi_tf_trend",
    "get_support_resistance",
    "get_options_chain_analysis",
    "get_news_context",
]


def _round_trip_updates(tool):
    """A well-formed START->RESULT->END round-trip for ``tool`` (two node updates)."""
    return [
        {"messages": [StubAIMessage(content="", tool_calls=[_tool_call(tool)])]},
        {"messages": [StubToolMessage(content='{"ok": true}', name=tool)]},
    ]


@given(
    tools=st.lists(st.sampled_from(_TOOL_NAMES), min_size=0, max_size=8),
    outcome=st.sampled_from([RUN_COMPLETED, RUN_PAUSED]),
    thread_id=st.text(
        alphabet=st.characters(min_codepoint=48, max_codepoint=122), min_size=1, max_size=10
    ),
)
@settings(max_examples=75, deadline=None)
def test_property_wellformed_streams_always_pair_and_finish_once(tools, outcome, thread_id):
    """Validates: Requirements 4.1, 4.3

    For ANY sequence of well-formed tool round-trips (each START followed by its
    RESULT + END) replayed as an SSE stream, the pairing invariant holds and the
    run ends with exactly one RUN_FINISHED.
    """
    node_updates = [{"messages": [StubAIMessage(content="thinking")]}]
    for tool in tools:
        node_updates.extend(_round_trip_updates(tool))
    node_updates.append(
        {"decision": {"action": "HOLD", "conviction_score": 10, "setup_validation": "no edge"}}
    )

    events = _replay_sse(thread_id, node_updates, outcome)
    validate_tool_pairing_and_termination(events)

    starts = sorted(p["tool"] for n, p in events if n == TOOL_CALL_START)
    ends = sorted(p["tool"] for n, p in events if n == TOOL_CALL_END)
    assert starts == sorted(tools)
    assert starts == ends
