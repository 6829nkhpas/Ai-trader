"""run_verify.py — TEMP driver: run a REAL "Verify My Trade" (VERIFY / Co-Pilot) run.

Mirrors run_find.py but drives VERIFY mode: the user proposes a trade
(side/entry/stop/target) and the agent verifies it with the RISK_MANAGER_PROMPT,
the Bear devil's-advocate, and the same live tools — then commits a verdict via
declare_trade (approve as BUY/SELL, or reject -> HOLD). Streams every step so we
can watch the verification as the model and find where it stalls or misjudges.

PREREQ: backend up (Tool_Server :8084 + QuestDB :9000), LLM key in .env.
Makes REAL LLM calls.

USAGE (from agents/deep-quant-loop):
    python run_verify.py                # default sample BUY on NIFTY 50 INTRADAY
    python run_verify.py BUY "NIFTY 50" 24110 24075 24160 INTRADAY 10m
    args: SIDE SYMBOL ENTRY STOP TARGET [PROFILE] [TIMEFRAME]

Delete when done — diagnostic driver only.
"""

import json
import sys
import uuid

from graph import graph

try:
    from langgraph.types import Command  # noqa: F401 (parity with run_find; unused here)
except Exception:  # pragma: no cover
    pass


def _preview(obj, n: int = 900) -> str:
    try:
        s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    except Exception:
        s = str(obj)
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n] + f" ... [+{len(s) - n} chars]"


def _print_event(event: dict) -> None:
    for node, data in (event or {}).items():
        if not isinstance(data, dict):
            continue
        for m in data.get("messages") or []:
            role = type(m).__name__
            content = getattr(m, "content", "") or ""
            name = getattr(m, "name", None)
            for tc in getattr(m, "tool_calls", None) or []:
                print(f"  [{node}] >>> TOOL_CALL {tc.get('name')}  args={_preview(tc.get('args'), 300)}")
            if role == "ToolMessage":
                print(f"  [{node}] <<< TOOL_RESULT {name}: {_preview(content, 500)}")
            elif content:
                print(f"  [{node}] {role}: {_preview(content)}")
        decision = data.get("decision")
        if isinstance(decision, dict):
            defen = decision.get("defensibility") or {}
            checks = defen.get("validator_checks")
            print(
                f"  [{node}] *** VERDICT  action={decision.get('action')} "
                f"conviction={decision.get('conviction_score')} source={decision.get('source')} "
                f"tier={defen.get('opportunity_tier')} validator_checks={_preview(checks, 400)}"
            )


def main() -> None:
    side = (sys.argv[1] if len(sys.argv) > 1 else "BUY").upper()
    symbol = sys.argv[2] if len(sys.argv) > 2 else "NIFTY 50"
    entry = float(sys.argv[3]) if len(sys.argv) > 3 else 24110.0
    stop = float(sys.argv[4]) if len(sys.argv) > 4 else 24075.0
    target = float(sys.argv[5]) if len(sys.argv) > 5 else 24160.0
    profile = sys.argv[6] if len(sys.argv) > 6 else "INTRADAY"
    timeframe = sys.argv[7] if len(sys.argv) > 7 else "10m"

    notes = (
        "Reclaim of the 24100 support/OI shelf on the 10m; expecting continuation "
        "toward the 24150 max-pain magnet. Stop below the S3/support shelf."
    )

    thread_id = f"verify_{symbol.replace(' ', '_')}_{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id, "profile": profile}}
    state = {
        "messages": [("user", f"Verify my {side} trade on {symbol}.")],
        "symbol": symbol,
        "profile": profile,
        "timeframe": timeframe,
        "mode": "VERIFY",
        "manual_trade": {
            "side": side,
            "entry": entry,
            "stop_loss": stop,
            "take_profit": target,
            "user_analysis": notes,
        },
    }

    risk = abs(entry - stop)
    reward = abs(target - entry)
    print("=" * 80)
    print(f"VERIFY RUN  {side} {symbol}  entry={entry} stop={stop} target={target}")
    print(f"  proposed R:R = {reward / risk:.2f}  (risk {risk:.1f} / reward {reward:.1f})  profile={profile} tf={timeframe}")
    print(f"  thread={thread_id}")
    print("=" * 80)

    for event in graph.stream(state, config, stream_mode="updates"):
        _print_event(event)

    snap = graph.get_state(config)
    d = (snap.values or {}).get("decision") or {}
    print("\n" + "=" * 80)
    print(
        f"VERIFY COMPLETE  ->  verdict action={d.get('action')} "
        f"conviction={d.get('conviction_score')} source={d.get('source')}"
    )
    print("=" * 80)


if __name__ == "__main__":
    main()
