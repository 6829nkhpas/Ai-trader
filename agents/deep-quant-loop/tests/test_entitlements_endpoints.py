"""Compliance blocker P1 — the FastAPI recommendation endpoints refuse, and
refuse BEFORE any research is produced.

``test_entitlements_unit.py`` proves ``entitlements.py`` fails closed. That is
necessary but not sufficient: a gate wired in the wrong place still lets the graph
run, the LLM spend happen, and a regulated recommendation be generated and logged
before the refusal reaches the wire. Gate 0->1 asks whether the *surface* is
reachable, not whether a helper returns False.

So the load-bearing assertion in this file is the tripwire: ``event_generator`` —
the single entry point to the LangGraph run for ``/run``, ``/resume`` and ``/qa``
— is replaced with a function that fails the test if it is ever called. A passing
test therefore means no graph node executed, no tool ran, no LLM was invoked and
no market data was fetched for an unentitled caller.

``VERIFY`` is asserted to still reach the graph. The repackage gates the
recommendation surface; it does not remove features from the unregulated SKU, and
a test suite that only proved things were blocked would not catch over-blocking.
"""

import json
import os
import sys

import pytest

_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import entitlements  # noqa: E402
import main  # noqa: E402
from entitlements import ENTITLEMENT_ERROR_CODE  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


RESEARCH_MODES = ["FIND", "DEBATE", "QA"]


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _enforced_and_unentitled(monkeypatch):
    """Enforcement ON, entitlement backend answering 404 for everyone.

    404 is not a contrived failure — it is exactly what the endpoint returns
    today, because the remote ``/internal/entitlement/{user_id}`` route has not
    been built yet (see the module docstring of ``entitlements.py``). This fixture
    therefore models the real current state of the deployment.
    """
    entitlements.clear_cache()
    monkeypatch.setenv("SKU_ENFORCE", "1")

    class _NotFound:
        status_code = 404

        def json(self):
            return {}

    monkeypatch.setattr(entitlements.httpx, "get", lambda *_a, **_kw: _NotFound())
    yield
    entitlements.clear_cache()


@pytest.fixture
def graph_tripwire(monkeypatch):
    """Replace ``event_generator`` with a detector for "the graph was reached".

    Returns a dict whose ``called`` flag the assertions read. The stub is an async
    generator so that, if a regression *does* reach it, the endpoint still returns
    a valid response and the test fails on the flag rather than on an obscure
    TypeError.
    """
    tripped = {"called": False, "kwargs": None}

    async def _stub(*_args, **kwargs):
        tripped["called"] = True
        tripped["kwargs"] = kwargs
        yield "data: {}\n\n"

    monkeypatch.setattr(main, "event_generator", _stub)
    return tripped


def _sse_payloads(response):
    """Parse the ``data:`` lines of an SSE response body into dicts."""
    out = []
    for line in response.text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        body = line[len("data:") :].strip()
        if not body:
            continue
        try:
            out.append(json.loads(body))
        except json.JSONDecodeError:
            pass
    return out


# ── 1. /run ─────────────────────────────────────────────────────────────────


class TestRunEndpoint:
    @pytest.mark.parametrize("mode", RESEARCH_MODES)
    def test_research_modes_never_reach_the_graph(self, client, graph_tripwire, mode):
        response = client.post(
            "/run",
            json={
                "thread_id": "t-p1-1",
                "message": "analyse RELIANCE",
                "mode": mode,
                "symbol": "RELIANCE",
                "user_id": "user-unentitled",
            },
        )
        assert response.status_code == 200  # SSE transport is fine; content refuses
        assert graph_tripwire["called"] is False, (
            f"{mode} reached event_generator despite no RESEARCH entitlement — "
            "the recommendation was generated before the refusal was streamed"
        )

    @pytest.mark.parametrize("mode", RESEARCH_MODES)
    def test_refusal_carries_the_machine_readable_code(
        self, client, graph_tripwire, mode
    ):
        response = client.post(
            "/run",
            json={
                "thread_id": "t-p1-2",
                "message": "analyse RELIANCE",
                "mode": mode,
                "user_id": "user-unentitled",
            },
        )
        payloads = _sse_payloads(response)
        assert payloads, "refusal produced no SSE frame at all"
        assert any(p.get("code") == ENTITLEMENT_ERROR_CODE for p in payloads)
        assert any(p.get("entitlement_required") is True for p in payloads)

    def test_refusal_emits_no_decision_frame(self, client, graph_tripwire):
        # A DECISION frame is the recommendation itself. It must be absent.
        response = client.post(
            "/run",
            json={
                "thread_id": "t-p1-3",
                "message": "analyse RELIANCE",
                "mode": "FIND",
                "user_id": "user-unentitled",
            },
        )
        assert "DECISION" not in response.text
        for payload in _sse_payloads(response):
            assert "decision" not in payload
            for key in ("entry", "stop_loss", "take_profit", "conviction_score"):
                assert key not in payload

    def test_missing_user_id_is_refused(self, client, graph_tripwire):
        # An anonymous caller POSTing directly at the service is the exact threat
        # the UI-side gate cannot address.
        response = client.post(
            "/run",
            json={"thread_id": "t-p1-4", "message": "analyse RELIANCE", "mode": "FIND"},
        )
        assert response.status_code == 200
        assert graph_tripwire["called"] is False

    def test_omitted_mode_defaults_to_find_and_is_refused(self, client, graph_tripwire):
        # RunRequest defaults mode to "FIND", so an omitted mode must NOT slip
        # through as unclassified.
        response = client.post(
            "/run",
            json={
                "thread_id": "t-p1-5",
                "message": "analyse RELIANCE",
                "user_id": "user-unentitled",
            },
        )
        assert response.status_code == 200
        assert graph_tripwire["called"] is False

    @pytest.mark.parametrize("mode", ["find", " Find ", "fInD"])
    def test_case_and_whitespace_variants_are_refused(
        self, client, graph_tripwire, mode
    ):
        response = client.post(
            "/run",
            json={
                "thread_id": "t-p1-6",
                "message": "analyse RELIANCE",
                "mode": mode,
                "user_id": "user-unentitled",
            },
        )
        assert graph_tripwire["called"] is False

    @pytest.mark.parametrize("mode", ["SCAN", "ADVISE", "FIND_V2", "", "   "])
    def test_unknown_modes_are_refused_rather_than_waved_through(
        self, client, graph_tripwire, mode
    ):
        # Gated by default: a mode added to graph.py later must not ship ungated.
        response = client.post(
            "/run",
            json={
                "thread_id": "t-p1-7",
                "message": "analyse RELIANCE",
                "mode": mode,
                "user_id": "user-unentitled",
            },
        )
        assert graph_tripwire["called"] is False

    def test_verify_still_reaches_the_graph(self, client, graph_tripwire):
        # The unregulated SKU keeps its feature. If this ever fails, the gate has
        # become over-broad and we have weakened the product instead of
        # repackaging it.
        response = client.post(
            "/run",
            json={
                "thread_id": "t-p1-8",
                "message": "validate my trade",
                "mode": "VERIFY",
                "symbol": "RELIANCE",
                "user_id": "user-unentitled",
                "manual_trade": {
                    "side": "LONG",
                    "entry": 1400,
                    "stopLoss": 1380,
                    "takeProfit": 1450,
                    "userAnalysis": "range breakout",
                },
            },
        )
        assert response.status_code == 200
        assert graph_tripwire["called"] is True, (
            "VERIFY was blocked — validating the user's own numbers is not "
            "regulated research and must work on TERMINAL"
        )

    def test_entitled_user_reaches_the_graph(self, client, graph_tripwire, monkeypatch):
        # Proves the refusals above are not vacuous: with a grant, FIND runs.
        class _Granted:
            status_code = 200

            def json(self):
                return {"success": True, "data": {"canAccessResearch": True}}

        entitlements.clear_cache()
        monkeypatch.setattr(entitlements.httpx, "get", lambda *_a, **_kw: _Granted())

        response = client.post(
            "/run",
            json={
                "thread_id": "t-p1-9",
                "message": "analyse RELIANCE",
                "mode": "FIND",
                "user_id": "user-entitled",
            },
        )
        assert response.status_code == 200
        assert graph_tripwire["called"] is True


# ── 2. /qa ──────────────────────────────────────────────────────────────────


class TestQaEndpoint:
    def test_qa_never_reaches_the_graph_unentitled(self, client, graph_tripwire):
        response = client.post(
            "/qa",
            json={
                "thread_id": "t-p1-qa-1",
                "question": "what is the target on this setup",
                "user_id": "user-unentitled",
            },
        )
        assert response.status_code == 200
        assert graph_tripwire["called"] is False

    def test_qa_refusal_carries_the_code(self, client, graph_tripwire):
        response = client.post(
            "/qa",
            json={
                "thread_id": "t-p1-qa-2",
                "question": "should I hold overnight",
                "user_id": "user-unentitled",
            },
        )
        payloads = _sse_payloads(response)
        assert any(p.get("code") == ENTITLEMENT_ERROR_CODE for p in payloads)

    def test_qa_refused_without_a_user_id(self, client, graph_tripwire):
        response = client.post(
            "/qa",
            json={"thread_id": "t-p1-qa-3", "question": "what is the target"},
        )
        assert graph_tripwire["called"] is False

    def test_qa_reaches_the_graph_when_entitled(
        self, client, graph_tripwire, monkeypatch
    ):
        class _Granted:
            status_code = 200

            def json(self):
                return {"success": True, "data": {"sku": "RESEARCH"}}

        entitlements.clear_cache()
        monkeypatch.setattr(entitlements.httpx, "get", lambda *_a, **_kw: _Granted())

        response = client.post(
            "/qa",
            json={
                "thread_id": "t-p1-qa-4",
                "question": "why is the stop there",
                "user_id": "user-entitled",
            },
        )
        assert graph_tripwire["called"] is True


# ── 3. /resume ──────────────────────────────────────────────────────────────


class TestResumeEndpoint:
    def test_resume_never_reaches_the_graph_unentitled(self, client, graph_tripwire):
        # A resume continues a paused analysis run. Only the analysis modes can
        # arm a watch, so a resume is always continuing RESEARCH work — it must
        # not be a side door back into the graph.
        response = client.post(
            "/resume",
            json={
                "thread_id": "t-p1-resume-1",
                "triggered_candle": {
                    "time": 0,
                    "open": 1,
                    "high": 1,
                    "low": 1,
                    "close": 1,
                },
                "user_id": "user-unentitled",
            },
        )
        assert graph_tripwire["called"] is False

    def test_resume_refusal_carries_the_code(self, client, graph_tripwire):
        response = client.post(
            "/resume",
            json={
                "thread_id": "t-p1-resume-2",
                "triggered_candle": {},
                "user_id": "user-unentitled",
            },
        )
        payloads = _sse_payloads(response)
        assert any(p.get("code") == ENTITLEMENT_ERROR_CODE for p in payloads)

    def test_resume_refused_before_the_paused_state_lookup(
        self, client, graph_tripwire
    ):
        # The gate sits ahead of `graph.get_state`, so an unentitled caller
        # cannot probe which thread_ids exist or are paused: the refusal is
        # indistinguishable regardless of thread state, and never a 400.
        response = client.post(
            "/resume",
            json={
                "thread_id": "definitely-not-a-real-thread",
                "triggered_candle": {},
                "user_id": "user-unentitled",
            },
        )
        assert response.status_code == 200
        assert graph_tripwire["called"] is False


# ── 4. Enforcement off — dev must be unaffected ──────────────────────────────


class TestEnforcementDisabled:
    def test_run_works_normally_when_enforcement_is_off(
        self, client, graph_tripwire, monkeypatch
    ):
        monkeypatch.setenv("SKU_ENFORCE", "0")
        response = client.post(
            "/run",
            json={
                "thread_id": "t-p1-off",
                "message": "analyse RELIANCE",
                "mode": "FIND",
                "user_id": None,
            },
        )
        assert response.status_code == 200
        assert graph_tripwire["called"] is True
