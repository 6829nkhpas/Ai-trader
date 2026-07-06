"""Property-based tests for `extract_tool_calls` (graph.py, task 2.1).

Feature: deep-quant-analysis-hardening

These tests exercise the five correctness properties for tool-call extraction
(design Properties 1-5, Requirements 1.1-1.5) using Hypothesis with
``max_examples=100``. The implementation under test is
``graph.extract_tool_calls(response) -> ToolCallExtraction`` together with the
``ExtractedCall`` dataclass.

Generators deliberately include boundary inputs: zero-width and other unicode
characters embedded in the markup/args, empty arg objects, and varying call
counts.
"""

import json
import os
import sys

from hypothesis import given, settings, strategies as st

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import extract_tool_calls, REGISTERED_TOOL_NAMES  # noqa: E402


# ── Custom-token markup literals (must match graph.py regexes exactly) ───────
BEGIN = "<｜tool▁call▁begin｜>"
SEP = "<｜tool▁sep｜>"
END = "<｜tool▁call▁end｜>"

# Zero-width / format characters used as boundary inputs in the markup.
ZERO_WIDTH = ["\u200b", "\u200c", "\u200d", "\ufeff"]

# Characters that must never appear inside generated text, because they would
# either inject markup tokens or break the name/JSON scanner.
_FORBIDDEN = "<>｜▁`"


# ── Shared strategies ────────────────────────────────────────────────────────

# Text that is safe to embed inside JSON strings without colliding with the
# custom-token markup. Excludes surrogates, control chars, and markup glyphs.
safe_text = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs", "Cc"),
        blacklist_characters=_FORBIDDEN,
    ),
    max_size=10,
)

# A value-text strategy that sometimes splices a zero-width/unicode character
# into the string so round-tripping is exercised against those boundary chars.
_zw_or_empty = st.sampled_from(ZERO_WIDTH + [""])
value_text = st.builds(lambda a, z, b: a + z + b, safe_text, _zw_or_empty, safe_text)

# JSON-object args: keys are non-empty strings, values are JSON scalars that
# survive a json.dumps -> json.loads round trip exactly.
json_scalar = st.one_of(
    value_text,
    st.integers(min_value=-1_000_000_000, max_value=1_000_000_000),
    st.booleans(),
    st.none(),
)
args_strategy = st.dictionaries(
    keys=safe_text.filter(lambda s: len(s) > 0),
    values=json_scalar,
    max_size=5,
)

registered_name = st.sampled_from(sorted(REGISTERED_TOOL_NAMES))

# A tool name that is NOT registered. Letters/underscore only so it forms a
# single name token under the `[^\s`{]+` scanner.
unregistered_name = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu"), max_codepoint=122),
    min_size=3,
    max_size=12,
).filter(lambda s: s not in REGISTERED_TOOL_NAMES)

# Optional zero-width/whitespace padding placed *around* call blocks (never
# between the separator token and the tool name, which would corrupt the name).
pad = st.text(alphabet=st.sampled_from(ZERO_WIDTH + [" ", "\n", "\t"]), max_size=4)


class StubResponse:
    """Minimal stand-in for a LangChain model response.

    ``extract_tool_calls`` only accesses ``.tool_calls`` and ``.content``.
    """

    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


def _render_block(name, raw_args):
    """Render a single custom-token tool-call block."""
    return f"{BEGIN}{SEP}{name} {raw_args}{END}"


# ── Property 1 ───────────────────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 1: Native tool calls bypass
# text extraction
@settings(max_examples=100)
@given(
    native=st.lists(
        st.tuples(registered_name, args_strategy),
        min_size=1,
        max_size=4,
    ),
    markup_name=registered_name,
    markup_args=args_strategy,
)
def test_property_1_native_tool_calls_bypass_text_extraction(native, markup_name, markup_args):
    """Validates: Requirements 1.1

    When ``response.tool_calls`` is non-empty, the extractor returns exactly
    those native calls (status ``ok``), reports ``used_text_extraction == False``,
    and ignores any custom-token markup present in ``response.content``.
    """
    native_calls = [
        {"name": name, "args": args, "id": f"native_{i}"}
        for i, (name, args) in enumerate(native)
    ]
    # Content carries decoy markup that MUST be ignored on the native path.
    decoy = _render_block(markup_name, json.dumps(markup_args))
    response = StubResponse(content=decoy, tool_calls=native_calls)

    result = extract_tool_calls(response)

    assert result.used_text_extraction is False
    # Exactly the native calls are returned, in order, all status ok.
    assert len(result.calls) == len(native_calls)
    assert [c.name for c in result.calls] == [c["name"] for c in native_calls]
    assert all(c.status == "ok" for c in result.calls)
    # The native args are preserved; the decoy markup never influences output.
    assert [c.args for c in result.calls] == [c["args"] for c in native_calls]


# ── Property 2 ───────────────────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 2: Custom-token tool calls
# round-trip through extraction
@settings(max_examples=100)
@given(name=registered_name, args=args_strategy, prefix=pad, suffix=pad)
def test_property_2_custom_token_round_trip(name, args, prefix, suffix):
    """Validates: Requirements 1.2

    A registered-tool call rendered as in-content custom-token markup with a
    JSON-object args fragment is recovered as an ``ok`` ExtractedCall whose
    parsed args equal the original object.
    """
    raw_args = json.dumps(args)  # ensure_ascii escapes any zero-width chars
    content = prefix + _render_block(name, raw_args) + suffix
    response = StubResponse(content=content, tool_calls=[])

    result = extract_tool_calls(response)

    assert result.used_text_extraction is True
    assert len(result.calls) == 1
    call = result.calls[0]
    assert call.name == name
    assert call.status == "ok"
    assert call.args == args  # round-trips exactly


# ── Property 3 ───────────────────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 3: Malformed tool-call args
# become parse-failures without dropping or terminating
@settings(max_examples=100)
@given(
    name=registered_name,
    key=st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu"), max_codepoint=122),
        min_size=1,
        max_size=8,
    ),
    template=st.sampled_from(['{{"{k}": }}', '{{"{k}" "{k}"}}', '{{{k}: 1}}', '{{"{k}":\u200b}}']),
)
def test_property_3_malformed_args_become_parse_failures(name, key, template):
    """Validates: Requirements 1.3

    A registered tool whose args fragment is not valid JSON is recorded with
    status ``parse_failure``, is excluded from the executable (``ok``) set, and
    is preserved (not dropped). Extraction returns normally — it never raises.
    """
    raw_args = template.format(k=key)
    # Sanity: the fragment really is invalid JSON (even after zero-width cleanup).
    try:
        parsed = json.loads(raw_args)
        assume_invalid = isinstance(parsed, dict)
    except Exception:
        assume_invalid = False
    # Skip the rare case where a generated fragment is actually valid JSON.
    if assume_invalid:
        return

    content = _render_block(name, raw_args)
    response = StubResponse(content=content, tool_calls=[])

    result = extract_tool_calls(response)  # must not raise

    assert result.used_text_extraction is True
    assert len(result.calls) == 1
    call = result.calls[0]
    assert call.status == "parse_failure"
    assert call.args is None
    # Excluded from the executable set, but preserved in the result.
    ok_calls = [c for c in result.calls if c.status == "ok"]
    assert ok_calls == []
    assert call in result.calls


# ── Property 4 ───────────────────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 4: Unregistered tool names
# are flagged invalid
@settings(max_examples=100)
@given(name=unregistered_name, args=args_strategy)
def test_property_4_unregistered_tool_names_flagged_invalid(name, args):
    """Validates: Requirements 1.4

    A tool name discovered in markup that is not in ``REGISTERED_TOOL_NAMES`` is
    recorded with status ``invalid_tool``.
    """
    content = _render_block(name, json.dumps(args))
    response = StubResponse(content=content, tool_calls=[])

    result = extract_tool_calls(response)

    assert result.used_text_extraction is True
    assert len(result.calls) == 1
    call = result.calls[0]
    assert call.name == name
    assert call.status == "invalid_tool"


# ── Property 5 ───────────────────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 5: Extraction preserves every
# call in order
@settings(max_examples=100)
@given(
    calls=st.lists(
        st.tuples(registered_name, args_strategy),
        min_size=1,
        max_size=6,
    ),
    sep_pad=pad,
)
def test_property_5_extraction_preserves_every_call_in_order(calls, sep_pad):
    """Validates: Requirements 1.5

    For N tool calls rendered as markup, the extraction result contains exactly
    N entries in their original source order, with none dropped.
    """
    blocks = [_render_block(name, json.dumps(args)) for name, args in calls]
    content = sep_pad.join(blocks)
    response = StubResponse(content=content, tool_calls=[])

    result = extract_tool_calls(response)

    assert result.used_text_extraction is True
    # Same count, same order.
    assert len(result.calls) == len(calls)
    assert [c.name for c in result.calls] == [name for name, _ in calls]
    # Every registered call with valid JSON args parses to ``ok``.
    assert all(c.status == "ok" for c in result.calls)
    assert [c.args for c in result.calls] == [args for _, args in calls]
