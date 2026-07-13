"""Unit tests for ``classify_symbol_class`` (task 6.1).

Feature: index-options-intraday-context

These focused unit tests exercise design **Property 1: Symbol-class resolution
is total and correct**:

    For any input (including missing/empty/non-string), ``classify_symbol_class``
    returns exactly ``"index"`` or ``"equity"``, returns ``"index"`` iff the
    upper-cased symbol is in ``INDEX_UNDERLYINGS``, and never raises.

    Validates: Requirements 1.1, 1.2, 1.3

The resolver lives in ``tools.py`` beside ``INDEX_UNDERLYINGS`` (the single
source of truth for the index set). The ``sys.path`` / import pattern mirrors the
sibling ``test_*`` modules in this directory (``tests/`` sits directly under the
service dir).
"""

import os
import sys

import pytest

# Make the service package importable (tools.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from tools import INDEX_UNDERLYINGS, classify_symbol_class  # noqa: E402


# ── Requirement 1.1: index members (case-insensitive) → "index" ──────────────
@pytest.mark.parametrize(
    "symbol",
    [
        "NIFTY 50",
        "nifty 50",
        "Nifty 50",
        "NIFTY",
        "nifty",
        "BANKNIFTY",
        "banknifty",
        "BankNifty",
        "  NIFTY 50  ",  # surrounding whitespace is stripped
        "  banknifty ",
    ],
)
def test_index_members_mixed_case_classify_as_index(symbol):
    """Validates: Requirements 1.1

    A symbol matching a known index underlying (case-insensitive, whitespace
    tolerant) resolves to ``"index"``.
    """
    assert classify_symbol_class(symbol) == "index"


# ── Requirement 1.2: a non-index symbol → "equity" ───────────────────────────
@pytest.mark.parametrize(
    "symbol",
    [
        "RELIANCE",
        "reliance",
        "TCS",
        "INFY",
        "NIFTYBEES",   # superficially similar but not in the set
        "BANK NIFTY",  # space variant not in the set
        "NIFTY50",     # no space, not in the set
    ],
)
def test_stock_symbol_classifies_as_equity(symbol):
    """Validates: Requirements 1.2

    A symbol that does not match a known index underlying resolves to
    ``"equity"``.
    """
    assert classify_symbol_class(symbol) == "equity"


# ── Requirement 1.3: missing/empty/non-string → "equity", never raises ───────
@pytest.mark.parametrize(
    "symbol",
    [
        None,
        "",
        "   ",
        123,
        12.5,
        [],
        {},
        ("NIFTY",),
        True,
        object(),
    ],
)
def test_missing_empty_or_non_string_defaults_to_equity_without_raising(symbol):
    """Validates: Requirements 1.3

    A missing, empty, whitespace-only, or non-string symbol defaults to
    ``"equity"`` and the call never raises.
    """
    try:
        result = classify_symbol_class(symbol)
    except Exception as exc:  # pragma: no cover - failure path
        pytest.fail(f"classify_symbol_class raised on {symbol!r}: {exc!r}")
    assert result == "equity"


# ── Property 1: total + correct — result is always one of the two labels ──────
@pytest.mark.parametrize(
    "symbol",
    [
        "NIFTY 50", "nifty", "BANKNIFTY", "RELIANCE", "", "   ",
        None, 123, [], {}, object(),
    ],
)
def test_result_is_always_index_or_equity(symbol):
    """Validates: Requirements 1.1, 1.2, 1.3

    Property 1 (totality): the function returns exactly one of the two valid
    labels for every input.
    """
    assert classify_symbol_class(symbol) in {"index", "equity"}


def test_index_iff_upper_cased_symbol_in_index_underlyings():
    """Validates: Requirements 1.1, 1.2, 1.3

    Property 1 (correctness): a string resolves to ``"index"`` exactly when its
    stripped, upper-cased form is a member of ``INDEX_UNDERLYINGS`` — the single
    source of truth.
    """
    for member in INDEX_UNDERLYINGS:
        assert classify_symbol_class(member) == "index"
        assert classify_symbol_class(member.lower()) == "index"
        assert classify_symbol_class(f"  {member.lower()}  ") == "index"
