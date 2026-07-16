"""run_find.py — TEMP driver: run a REAL Find-Trade FIND run from the CLI.

This invokes the ACTUAL compiled Deep Quant graph (same system prompt, same
tools, same ReAct + heartbeat/watch loop the frontend drives) and pulls REAL
data through the tools from the Rust Tool_Server + QuestDB. It streams every
step — the model's reasoning, each tool call and (a preview of) its real result,
routing, and the final committed decision (or the armed watch). When the run
suspends on a `watch_price_condition`, it can inject ONE synthetic heartbeat
resume so you can watch the heartbeat re-evaluation adapt.

The point: BE the model on live data — see exactly what each tool returns, what
the model decides, and where a trade gets blocked or turns into a HOLD/watch.

PREREQUISITES
  * The backend must be running: the Rust Tool_Server (:8084) + QuestDB, i.e. the
    same stack `start_system.ps1` brings up. Without it, market-data tools return
    honest "unavailable" markers and you'll still see the FLOW (just not live data).
  * LLM_API_KEY / OPENAI_API_KEY must be set — graph.py auto-loads the repo .env,
    so if the terminal runs the normal system this already works. NOTE: this makes
    REAL LLM API calls (one per reasoning turn).

USAGE  (from agents/deep-quant-loop, with the backend up)
    python run_find.py "NIFTY 50" INTRADAY 10m
    python run_find.py RELIANCE SWING 15m --no-resume     # stop at first watch
    python run_find.py "NIFTY 50" INTRADAY 10m --beats 3   # inject up to 3 heartbeats

Delete this file when done — it is a diagnostic driver, not part of the app.
"""

import json
import sys
import uuid

from graph import graph  # the compiled LangGraph agent (real prompt + tools)

try:
    from langgraph.types import Command
except Exception:  # older langgraph
    from langgraph.pregel import Command  # type: ignore


def _preview(obj, n: int = 900) -> str:
    try:
        s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    except Exception:
        s = str(obj)
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n] + f" ... [+{len(s) - n} chars]"


def _print_event(event: dict) -> None:
    """Render one stream_mode='updates' event: reasoning, tool calls, results, decision."""
    for node, data in (event or {}).items():
        if not isinstance(data, dict):
            continue
        for m in data.get("messages") or []:
            role = type(m).__name__
            content = getattr(m, "content", "") or ""
            name = getattr(m, "name", None)
            tool_calls = getattr(m, "tool_calls", None) or []
            for tc in tool_calls:
                print(f"  [{node}] >>> TOOL_CALL {tc.get('name')}  args={_preview(tc.get('args'), 300)}")
            if role == "ToolMessage":
                print(f"  [{node}] <<< TOOL_RESULT {name}: {_preview(content, 500)}")
            elif content:
                print(f"  [{node}] {role}: {_preview(content)}")
        decision = data.get("decision")
        if isinstance(decision, dict):
            defen = decision.get("defensibility") or {}
            print(
                f"  [{node}] *** DECISION  action={decision.get('action')} "
                f"conviction={decision.get('conviction_score')} source={decision.get('source')} "
                f"tier={defen.get('opportunity_tier')} reason={decision.get('reason')}"
            )


def _run(stream_input, config) -> None:
    for event in graph.stream(stream_input, config, stream_mode="updates"):
        _print_event(event)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    symbol = args[0] if len(args) > 0 else "NIFTY 50"
    profile = args[1] if len(args) > 1 else "INTRADAY"
    timeframe = args[2] if len(args) > 2 else "10m"
    no_resume = "--no-resume" in sys.argv
    max_beats = 1
    for a in sys.argv:
        if a.startswith("--beats"):
            try:
                max_beats = int(a.split("=", 1)[1]) if "=" in a else int(sys.argv[sys.argv.index(a) + 1])
            except Exception:
                max_beats = 1

    thread_id = f"cli_{symbol.replace(' ', '_')}_{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id, "profile": profile}}
    state = {
        "messages": [("user", f"Find the best available {symbol} trade right now.")],
        "symbol": symbol,
        "profile": profile,
        "timeframe": timeframe,
        "mode": "FIND",
    }

    print("=" * 80)
    print(f"LIVE FIND RUN  symbol={symbol}  profile={profile}  tf={timeframe}  thread={thread_id}")
    print("=" * 80)

    _run(state, config)

    beats = 0
    while True:
        snap = graph.get_state(config)
        if not snap.next:
            values = snap.values or {}
            d = values.get("decision") or {}
            print("\n" + "=" * 80)
            print(
                f"RUN COMPLETE  ->  action={d.get('action')} conviction={d.get('conviction_score')} "
                f"source={d.get('source')} reason={d.get('reason')}"
            )
            print("=" * 80)
            return
        # Suspended on a watch.
        print("\n" + "-" * 80)
        print(f"SUSPENDED (watch armed)  next={snap.next}")
        print("-" * 80)
        if no_resume or beats >= max_beats:
            print(f"Stopping (beats={beats}/{max_beats}, no_resume={no_resume}). "
                  f"The watch is armed and would wait for a live trigger / heartbeat.")
            return
        beats += 1
        print(f"=== Injecting synthetic HEARTBEAT resume #{beats} to observe adaptation ===")
        resume = Command(resume={"candle": {"note": f"synthetic heartbeat {beats}"}, "trigger_kind": "heartbeat"})
        _run(resume, config)


if __name__ == "__main__":
    main()
