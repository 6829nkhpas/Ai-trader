"""Prompt and model version fingerprints — what makes a published output replayable.

`docs/business/PLAN_OF_ACTION.md` §4.2 (P2) requires every recommendation record
to name the model AND the prompt that produced it, and
`docs/business/INVESTOR_BRIEF.md` §5 offers that replayability as the mitigation
against stricter AI/ML rules arriving later. "GPT-4o said so" is not a record: the
same model with a different system prompt is a different analyst.

Two different questions get two different hashes, and conflating them is the
mistake this module exists to avoid:

  * ``prompt_hash(text)`` — the fingerprint of the **exact prompt one run used**,
    after timeframe, profile, R:R floor and index addenda were interpolated. This
    is what a recommendation row stores, because it is what the model actually
    saw. Two runs on different timeframes legitimately differ here.
  * ``prompt_set_hash()`` — the fingerprint of the **prompt library** the
    deployment was running: the base constants before any interpolation. This is
    the version register entry for `docs/compliance/AI_MODEL_GOVERNANCE.md`, and
    it changes only when someone edits a prompt.

Both are SHA-256 over line-ending-normalised UTF-8. Normalisation is not cosmetic:
this repository is CRLF on disk, so an un-normalised hash would differ between a
Windows working copy and a Linux container for the same prompt text.

The prompt constants live in ``graph.py``, which imports this module — so they are
read back out of ``sys.modules`` at call time rather than imported at module
scope. That avoids a circular import without duplicating the prompts, and it is
why ``prompt_set_hash`` reports which constants it actually found: a silently
partial fingerprint would be worse than a missing one.
"""

from __future__ import annotations

import hashlib
import os
import sys
from typing import Dict, Optional, Tuple

# The prompt constants that together define "the analyst". Ordered, because the
# set hash is taken over this sequence — adding a name to the middle of the list
# would otherwise change the hash of an unchanged prompt library.
PROMPT_CONSTANTS: Tuple[str, ...] = (
    "DEEP_QUANT_SYSTEM_PROMPT",
    "DEEP_QUANT_FNO_PROMPT",
    "RISK_MANAGER_PROMPT",
    "INDEX_OPTIONS_ADDENDUM",
)

# Additional prompt text that is composed rather than declared. Kept separate
# because these are module attributes of other modules.
_EXTERNAL_PROMPT_SOURCES: Tuple[Tuple[str, str, str], ...] = (
    # (label, module name, attribute) — the personalisation rule is part of the
    # published behaviour of the Q&A surface (compliance blocker P8a), so a change
    # to it is a change to the analyst.
    ("personalisation.QA_PROMPT_RULE", "personalisation", "QA_PROMPT_RULE"),
)

_UNAVAILABLE = "<unavailable>"


def _normalise(text: str) -> str:
    """Fold CRLF/CR to LF and strip a trailing newline.

    A prompt edited on Windows and one edited on Linux are the same prompt; a
    hash that disagrees would make the version register useless exactly when it
    matters (comparing what production ran against what the repository says).
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def prompt_hash(text: Optional[str]) -> str:
    """SHA-256 of one composed prompt. ``""`` and ``None`` hash distinctly.

    A missing prompt returns the sentinel rather than the hash of the empty
    string, so a record cannot claim "this prompt produced it" when the prompt was
    never captured. Total: never raises, because the caller is a compliance write
    on the decision path.
    """
    if text is None:
        return _UNAVAILABLE
    try:
        return hashlib.sha256(_normalise(str(text)).encode("utf-8")).hexdigest()
    except Exception:  # noqa: BLE001 - a hash failure must not abort a commit
        return _UNAVAILABLE


def collect_prompt_sources() -> Dict[str, str]:
    """The prompt library as ``{name: text}``, for whatever is importable now.

    Reads ``sys.modules`` rather than importing, so calling this from inside
    ``graph.py``'s own import cycle is safe and a partially-initialised module
    yields ``<unavailable>`` for its constants instead of an ImportError.
    """
    sources: Dict[str, str] = {}
    graph_module = sys.modules.get("graph")
    for name in PROMPT_CONSTANTS:
        value = getattr(graph_module, name, None) if graph_module is not None else None
        sources[name] = str(value) if isinstance(value, str) else _UNAVAILABLE
    for label, module_name, attribute in _EXTERNAL_PROMPT_SOURCES:
        module = sys.modules.get(module_name)
        value = getattr(module, attribute, None) if module is not None else None
        sources[label] = str(value) if isinstance(value, str) else _UNAVAILABLE
    return sources


def prompt_set_hash() -> str:
    """Fingerprint of the whole prompt library. Stable across runs and platforms.

    Each constant is hashed individually and the per-constant digests are then
    hashed together with their names. That is one step more than hashing the
    concatenation, and it buys a real property: no edit that merely moves text
    from the end of one prompt to the start of the next can leave the fingerprint
    unchanged.
    """
    sources = collect_prompt_sources()
    material = "\n".join(f"{name}={prompt_hash(sources[name])}" for name in sorted(sources))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def prompt_version_report() -> Dict[str, object]:
    """Per-constant digests plus the set hash, for the governance register.

    ``missing`` is reported explicitly: a register entry that quietly omitted an
    unavailable prompt would look complete while describing a different analyst
    than the one that ran.
    """
    sources = collect_prompt_sources()
    per_constant = {name: prompt_hash(text) for name, text in sources.items()}
    return {
        "prompt_set_hash": prompt_set_hash(),
        "prompts": per_constant,
        "missing": sorted(name for name, text in sources.items() if text == _UNAVAILABLE),
    }


def model_id(override: Optional[str] = None) -> str:
    """The model id to record for a run: the run's override, else the default.

    ``override`` is ``state["model"]`` — the per-run model chosen in the UI
    composer. The deployment default comes from ``LLM_MODEL``, read here rather
    than imported from ``graph`` so this stays usable before ``graph`` is loaded
    (and so a test can set it with ``monkeypatch.setenv``).

    Falls back to the string ``"unknown"`` rather than ``None``: a record with no
    model named is not a record, and an explicit "unknown" is auditable.
    """
    if isinstance(override, str) and override.strip():
        return override.strip()
    env_model = os.getenv("LLM_MODEL")
    if env_model and env_model.strip():
        return env_model.strip()
    # Mirrors graph.OPENROUTER_DEFAULT_MODEL. Duplicated deliberately — importing
    # graph here would re-create the cycle this module is built to avoid — and
    # pinned by a test so the two cannot drift silently.
    return "openai/gpt-4o"
