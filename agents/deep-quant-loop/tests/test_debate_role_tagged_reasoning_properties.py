"""Property test for distinct role-tagged debate reasoning events.

# Feature: multi-agent-debate, Property 21: Role reasoning is surfaced with distinct role tags

Validates: Requirements 8.1

Property 21 statement: For ANY assistant (AI) message carrying a role tag
(bull/bear/judge), the REASONING event emitted by ``message_events`` carries
that distinct ``role`` tag; messages with no role tag emit a REASONING payload
with no ``role`` key (the single-agent payload shape is unchanged). The three
debate roles are thus distinguishable from one another.

These tests target the pure stream helpers in ``stream_events.py``
(``build_reasoning_event`` and ``message_events``) directly — the LLM and the
graph are never invoked. Real ``langchain_core.messages.AIMessage`` objects are
constructed so the test exercises the same ``additional_kwargs["role"]`` channel
the debate role nodes (tasks 7.1 / 8.1 / 15.1) use to tag bull/bear/judge
messages.
"""

import os
import sys

from hypothesis import given, settings, strategies as st
from langchain_core.messages import AIMessage

# Make the service package importable (stream_events.py lives one dir up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from stream_events import (  # noqa: E402
    REASONING,
    build_reasoning_event,
    message_events,
)

# Min 100 iterations per the spec (max_examples floor).
SETTINGS = settings(max_examples=100)

DEBATE_ROLES = ("bull", "bear", "judge")


def _reasoning_payload(msg):
    """Return the single REASONING payload emitted for ``msg`` (or ``None``)."""
    for name, payload in message_events(msg):
        if name == REASONING:
            return payload
    return None


# Non-empty natural-language content so a REASONING event is always produced
# (build_reasoning_event returns None when the stripped content is empty). We
# avoid tool-call markup tokens by drawing from plain printable ASCII (excluding
# the angle-bracket / vertical-bar characters used by the markup tokens) and
# rejecting blank-after-strip values.
content_strategy = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126, blacklist_characters="<>|"),
    min_size=1,
    max_size=200,
).filter(lambda s: s.strip() != "")

# Role tags: the three debate roles, plus None/absent and arbitrary strings.
role_strategy = st.one_of(
    st.sampled_from(DEBATE_ROLES),
    st.none(),
    st.text(max_size=20),
)


@given(content=content_strategy, role=st.sampled_from(DEBATE_ROLES))
@SETTINGS
def test_debate_role_tag_is_surfaced(content, role):
    """A bull/bear/judge-tagged AIMessage surfaces that exact role on REASONING."""
    msg = AIMessage(content=content, additional_kwargs={"role": role})
    payload = _reasoning_payload(msg)
    assert payload is not None
    assert payload["role"] == role


@given(content=content_strategy)
@settings(max_examples=100)
def test_no_role_tag_omits_role_key(content):
    """An untagged AIMessage emits a REASONING payload with no ``role`` key.

    Covers both an absent ``role`` key and an explicit ``role=None``, plus
    blank/whitespace role tags which are treated as no tag (single-agent shape).
    """
    # Absent role key entirely.
    msg_absent = AIMessage(content=content)
    payload_absent = _reasoning_payload(msg_absent)
    assert payload_absent is not None
    assert "role" not in payload_absent

    # Explicit None role.
    msg_none = AIMessage(content=content, additional_kwargs={"role": None})
    payload_none = _reasoning_payload(msg_none)
    assert payload_none is not None
    assert "role" not in payload_none

    # Whitespace-only role tag → treated as no tag.
    msg_blank = AIMessage(content=content, additional_kwargs={"role": "   "})
    payload_blank = _reasoning_payload(msg_blank)
    assert payload_blank is not None
    assert "role" not in payload_blank


@given(content=content_strategy, role=role_strategy)
@SETTINGS
def test_role_surfacing_total_over_arbitrary_tags(content, role):
    """For ANY role value the payload carries it iff it is a non-empty string.

    Non-empty (post-strip) string role tags — including arbitrary strings beyond
    the three debate roles — are surfaced verbatim (stripped); None/absent and
    blank tags omit the ``role`` key, preserving the single-agent shape.
    """
    kwargs = {} if role is None else {"role": role}
    msg = AIMessage(content=content, additional_kwargs=kwargs)
    payload = _reasoning_payload(msg)
    assert payload is not None
    if isinstance(role, str) and role.strip():
        assert payload["role"] == role.strip()
    else:
        assert "role" not in payload


@given(content_bull=content_strategy, content_bear=content_strategy)
@SETTINGS
def test_bull_and_bear_yield_distinct_role_values(content_bull, content_bear):
    """A bull-tagged and a bear-tagged message produce different role values.

    This is the distinctness guarantee: the roles are distinguishable in the
    emitted stream regardless of the (independent) message content.
    """
    bull_msg = AIMessage(content=content_bull, additional_kwargs={"role": "bull"})
    bear_msg = AIMessage(content=content_bear, additional_kwargs={"role": "bear"})
    bull_payload = _reasoning_payload(bull_msg)
    bear_payload = _reasoning_payload(bear_msg)
    assert bull_payload is not None and bear_payload is not None
    assert bull_payload["role"] == "bull"
    assert bear_payload["role"] == "bear"
    assert bull_payload["role"] != bear_payload["role"]


@given(
    content=content_strategy,
    roles=st.lists(st.sampled_from(DEBATE_ROLES), min_size=2, max_size=3, unique=True),
)
@SETTINGS
def test_distinct_roles_remain_distinct(content, roles):
    """Distinct role tags yield distinct surfaced role values (pairwise)."""
    payloads = []
    for role in roles:
        msg = AIMessage(content=content, additional_kwargs={"role": role})
        payload = _reasoning_payload(msg)
        assert payload is not None
        payloads.append(payload["role"])
    assert len(set(payloads)) == len(roles)


# ── Direct build_reasoning_event role-surfacing tests ────────────────────────

@given(content=content_strategy, role=st.sampled_from(DEBATE_ROLES))
@SETTINGS
def test_build_reasoning_event_surfaces_role(content, role):
    """build_reasoning_event surfaces a non-empty role tag on its payload."""
    payload = build_reasoning_event(content, role)
    assert payload is not None
    assert payload["content"]  # natural-language reasoning retained
    assert payload["role"] == role


@given(content=content_strategy)
@SETTINGS
def test_build_reasoning_event_omits_role_when_absent(content):
    """build_reasoning_event omits the role key when no role tag is supplied."""
    # Default (no role argument).
    payload_default = build_reasoning_event(content)
    assert payload_default is not None
    assert "role" not in payload_default

    # Explicit None role.
    payload_none = build_reasoning_event(content, None)
    assert payload_none is not None
    assert "role" not in payload_none

    # Whitespace-only role → omitted.
    payload_blank = build_reasoning_event(content, "   ")
    assert payload_blank is not None
    assert "role" not in payload_blank


@given(content=content_strategy, role=role_strategy)
@SETTINGS
def test_build_reasoning_event_role_surfacing_total(content, role):
    """build_reasoning_event surfaces role iff it is a non-empty (post-strip) string."""
    payload = build_reasoning_event(content, role)
    assert payload is not None
    if isinstance(role, str) and role.strip():
        assert payload["role"] == role.strip()
    else:
        assert "role" not in payload
