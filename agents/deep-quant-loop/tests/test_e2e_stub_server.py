"""The e2e stub server must actually work, or the CI job it backs is theatre.

A stub that fails to install is the worst possible outcome: the Playwright job would either hang
waiting for frames or fall back to a real graph and try to call an LLM with a placeholder key.
Neither failure names itself, so the stub is tested like any other code.
"""

import json
import os

os.environ.setdefault("SESSIONS_DB_PATH", ":memory:")

import pytest
from fastapi.testclient import TestClient

import e2e_stub_server as stub
import main


@pytest.fixture
def client(monkeypatch):
    """A client with the stub installed, and REVERTED afterwards.

    `install_stub` mutates module-level state (`graph_module.compile_with`, `graph_module.graph`,
    `main.set_run_llm_credentials`). Calling it directly left those replacements in place for the rest of
    the pytest session, which broke 33 unrelated tests — every suite that expected a real graph got the
    canned one. `monkeypatch.setattr` is what makes it a fixture rather than a global mutation.
    """
    monkeypatch.setenv("LLM_API_KEY", "e2e-placeholder")
    monkeypatch.setenv("SKU_ENFORCE", "0")
    monkeypatch.setattr(main, "set_run_llm_credentials", lambda *a, **k: None)
    monkeypatch.setattr(main.graph_module, "compile_with", lambda checkpointer=None: stub._StubGraph())
    monkeypatch.setattr(main.graph_module, "graph", stub._StubGraph())
    return TestClient(main.app)


def frames(response):
    out = []
    event = None
    for line in response.text.splitlines():
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            try:
                out.append((event, json.loads(line[len("data:"):].strip())))
            except json.JSONDecodeError:
                out.append((event, {}))
    return out


def test_the_stub_survives_a_lifespan_rebind(monkeypatch):
    """The failure mode that would silently un-stub the server.

    `main` reads `graph_module.graph` at every call site because the lifespan rebinds it at
    startup. A stub installed only on the current instance would be replaced by a real compiled
    graph the moment the app booted — and the job would then try to reach an LLM.

    `install_stub` is called for real here (that is the subject), but its replacements are captured and
    restored, because leaving them in place contaminates every later test in the session.
    """
    original_compile = main.graph_module.compile_with
    original_graph = main.graph_module.graph
    monkeypatch.setattr(main.graph_module, "compile_with", original_compile)
    monkeypatch.setattr(main.graph_module, "graph", original_graph)

    stub.install_stub()
    rebuilt = main.graph_module.compile_with(None)

    assert isinstance(rebuilt, stub._StubGraph), "the lifespan would compile a REAL graph"


def test_a_run_streams_a_complete_analysis(client):
    response = client.post(
        "/run",
        json={
            "thread_id": "e2e-run",
            "message": "analyse RELIANCE",
            "mode": "FIND",
            "symbol": "RELIANCE",
            "timeframe": "10m",
            "profile": "INTRADAY",
            "user_id": "e2e-user",
        },
    )
    parsed = frames(response)
    names = [name for name, _ in parsed]

    # The whole point of the canned script: every frame KIND the client reducer handles is
    # exercised, not just REASONING.
    assert names[0] == "RUN_STARTED"
    assert names[-1] == "RUN_FINISHED"
    assert any(n == "REASONING" for n in names), names
    assert any(n == "DECISION" for n in names), names
    # A terminal that is not `completed` would mean the stub parked the graph, and the Playwright
    # job would wait forever for a result.
    assert parsed[-1][1].get("status") == "completed"


def test_run_frames_are_marked_as_analysis(client):
    response = client.post(
        "/run",
        json={
            "thread_id": "e2e-run-turn",
            "message": "analyse RELIANCE",
            "mode": "FIND",
            "symbol": "RELIANCE",
            "timeframe": "10m",
            "profile": "INTRADAY",
            "user_id": "e2e-user",
        },
    )

    assert all(payload.get("turn") == "run" for _, payload in frames(response))


def test_a_question_streams_its_own_script(client):
    """The Q&A branch must answer, not replay the analysis.

    Detected from the graph INPUT rather than from server state, so two sessions running
    concurrently cannot make each other emit the wrong script — which is exactly the class of bug
    this migration exists to remove.
    """
    response = client.post(
        "/qa",
        json={"thread_id": "e2e-qa", "question": "why is the stop there?", "user_id": "e2e-user"},
    )
    parsed = frames(response)
    text = " ".join(str(p.get("content", "")) for _, p in parsed)

    assert all(payload.get("turn") == "qa" for _, payload in parsed)
    assert "swing low" in text, text
    # The analysis script must not leak into an answer.
    assert "Scanning RELIANCE" not in text


def test_the_databases_are_redirected_away_from_real_state():
    """A CI run must not write into a developer's real database files."""
    for var in (
        "SESSIONS_DB_PATH",
        "LANGGRAPH_CHECKPOINT_DB",
        "COMPLIANCE_DB_PATH",
        "TELEMETRY_DB_PATH",
        "JOURNAL_DB_PATH",
    ):
        value = os.environ.get(var, "")
        assert value, f"{var} was left unset, so the service would use its default path"
