"""Evaluation_Harness — offline replay of the deterministic deep-quant layer.

Feature: deep-quant-analysis-hardening (task 17.1)

This package is a standalone, offline replay tool that lives alongside the
Python LangGraph service (``agents/deep-quant-loop/``). It feeds a historical
candle series through the deterministic analysis layer — the SR_Engine,
Signal_Engine (conviction), Predictive_Engine (OLS), and Trade_Validator —
**without ever invoking the live LLM** (design Component 13, AD-4).

Language choice (documented per the task): the harness is implemented in
Python. The deterministic cores it measures are authored in Rust, but Python
is the simplest runtime to wire here because:

  * it sits next to the existing Python service and its test suite, and
  * it can directly reuse the already-written ``validator.py`` mirror of the
    Rust Trade_Validator (task 5.2), guaranteeing the validator pass-rate metric
    is computed by the exact same rules the production agent uses.

The SR_Engine, Signal_Engine, and Predictive_Engine pure functions are mirrored
in :mod:`eval.engines` so the replay reproduces the Rust computations
deterministically (identical inputs → identical metrics, R15.5).

Public API:

  * :class:`eval.harness.EvalReport`     — the summary report (R15.4)
  * :func:`eval.harness.produce_eval_report` — pure report producer (R15.1–15.4)
  * :func:`eval.harness.produce_eval_report_checked` — guarded producer with the
    determinism double-run guard (R15.5)
  * :class:`eval.harness.NonDeterminismError` — raised when two identical replays
    disagree (R15.5)
  * :class:`eval.harness.Candle`         — the historical-candle input type
"""

from .harness import (
    Candle,
    EvalReport,
    NonDeterminismError,
    produce_eval_report,
    produce_eval_report_checked,
)

__all__ = [
    "Candle",
    "EvalReport",
    "NonDeterminismError",
    "produce_eval_report",
    "produce_eval_report_checked",
]
