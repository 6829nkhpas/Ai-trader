"""Route-level identity wiring — the body ``user_id`` must stop being authoritative.

Two things are proven here, and they matter in opposite directions:

1. **Phase 1 changes nothing observable.** With ``DEEP_QUANT_REQUIRE_IDENTITY`` off
   and no assertion header, every route behaves exactly as before: the body
   ``user_id`` is what reaches the interaction log, the entitlement gate and the LLM
   key resolution. Anything else would be a regression shipped for the sake of a
   future phase.
2. **The boundary is real when enforced.** With the flag on, an unauthenticated
   caller gets 401 and the graph is never entered, and a body ``user_id`` buys
   nothing at all.

``main.event_generator`` is used as a tripwire rather than mocking deeper: it is the
first thing that touches the graph, so "the tripwire was not called" is a precise
statement that no analysis, no LLM call and no market-data fetch happened.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import internal_identity as ident
import main

SECRET = "d" * 64
SERVICE_SECRET = "e" * 64


@pytest.fixture(autouse=True)
def _identity_env(monkeypatch):
    monkeypatch.setenv(ident.ENV_IDENTITY_SECRET, SECRET)
    monkeypatch.setenv(ident.ENV_SERVICE_SECRET, SERVICE_SECRET)
    monkeypatch.delenv(ident.ENV_REQUIRE_IDENTITY, raising=False)
    # SKU enforcement is a separate gate with its own default-off switch; keep it off
    # so a refusal here can only come from the identity boundary under test.
    monkeypatch.delenv("SKU_ENFORCE", raising=False)
    ident._warned_unenforced = False
    yield
    ident._warned_unenforced = False


@pytest.fixture
def seen(monkeypatch):
    """Replace ``event_generator`` with a tripwire recording the user_id it received."""
    calls: list = []

    async def fake_generator(thread_id, graph_input=None, resume_command=None, user_id=None, kind="run", **_kwargs):
        calls.append({"thread_id": thread_id, "user_id": user_id, "kind": kind})
        yield "event: RUN_FINISHED\ndata: {}\n\n"

    monkeypatch.setattr(main, "event_generator", fake_generator)
    return calls


@pytest.fixture
def client():
    return TestClient(main.app)


def _run_body(**over):
    body = {"thread_id": "thread_T_1", "message": "analyse", "mode": "FIND", "user_id": "body_user"}
    body.update(over)
    return body


def _qa_body(**over):
    body = {"thread_id": "thread_T_1", "question": "why that stop?", "user_id": "body_user"}
    body.update(over)
    return body


def _identity(sub: str) -> dict:
    return {ident.HEADER_IDENTITY: ident.sign_identity(sub)}


# ── Unenforced: today's behaviour, preserved ─────────────────────────────────


def test_run_unenforced_uses_the_body_user_id(client, seen):
    res = client.post("/run", json=_run_body())
    assert res.status_code == 200
    assert seen and seen[0]["user_id"] == "body_user"


def test_qa_unenforced_uses_the_body_user_id(client, seen):
    res = client.post("/qa", json=_qa_body())
    assert res.status_code == 200
    assert seen and seen[0]["user_id"] == "body_user"


def test_run_unenforced_with_no_user_id_at_all_still_reaches_the_graph(client, seen):
    """A body with no user_id must behave exactly as before.

    The run then fails its own key-resolution check inside `_run_events` with a clean
    ERROR frame — that is pre-existing behaviour and not this boundary's business.
    """
    res = client.post("/run", json=_run_body(user_id=None))
    assert res.status_code == 200
    assert seen and seen[0]["user_id"] is None


def test_resume_unenforced_is_not_refused(client):
    """The headless watcher has no credential yet and MUST keep working.

    A 400 here is correct and expected (the thread is not paused). The assertion is
    only that it is not a 401 — the auth boundary let it through.
    """
    res = client.post("/resume", json={"thread_id": "thread_unknown", "triggered_candle": {}})
    assert res.status_code != 401


# ── Unenforced but an assertion is present: the real path is exercised ───────


def test_a_valid_assertion_wins_over_the_body_even_unenforced(client, seen):
    """Exercise the verified path before enforcing it.

    If the header only started being read at the moment the flag flipped, the flip
    itself would be the first test of it.
    """
    res = client.post("/run", json=_run_body(), headers=_identity("header_user"))
    assert res.status_code == 200
    assert seen and seen[0]["user_id"] == "header_user"


def test_an_unverifiable_assertion_is_ignored_and_logged(client, seen, capsys):
    """A secret mismatch must be visible BEFORE production depends on it."""
    res = client.post("/run", json=_run_body(), headers={ident.HEADER_IDENTITY: "bogus.mac"})
    assert res.status_code == 200
    assert seen and seen[0]["user_id"] == "body_user"
    assert "unverifiable assertion" in capsys.readouterr().out


# ── Enforced ─────────────────────────────────────────────────────────────────


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setenv(ident.ENV_REQUIRE_IDENTITY, "1")


def test_run_enforced_without_an_assertion_is_401_and_never_reaches_the_graph(client, seen, enforced):
    res = client.post("/run", json=_run_body())
    assert res.status_code == 401
    assert res.json()["detail"] == "authentication required"
    assert seen == [], "no analysis may run for an unauthenticated caller"


def test_qa_enforced_without_an_assertion_is_401(client, seen, enforced):
    res = client.post("/qa", json=_qa_body())
    assert res.status_code == 401
    assert seen == []


def test_run_enforced_ignores_a_forged_body_user_id(client, seen, enforced):
    """The whole point of the migration.

    Claiming to be someone else in the body must buy exactly nothing.
    """
    res = client.post("/run", json=_run_body(user_id="administrator"))
    assert res.status_code == 401
    assert seen == []


def test_run_enforced_accepts_a_valid_assertion_and_ignores_the_body(client, seen, enforced):
    res = client.post("/run", json=_run_body(user_id="administrator"), headers=_identity("real_user"))
    assert res.status_code == 200
    assert seen and seen[0]["user_id"] == "real_user"


def test_qa_enforced_accepts_a_valid_assertion(client, seen, enforced):
    res = client.post("/qa", json=_qa_body(), headers=_identity("real_user"))
    assert res.status_code == 200
    assert seen and seen[0]["user_id"] == "real_user"


def test_run_enforced_rejects_a_service_credential(client, seen, enforced):
    """A service assertion must not authenticate a user surface.

    The two secrets are separate precisely so a compromised watcher cannot become a
    user.
    """
    headers = {ident.HEADER_IDENTITY: ident.sign_service("tool-server")}
    res = client.post("/run", json=_run_body(), headers=headers)
    assert res.status_code == 401
    assert seen == []


def test_resume_enforced_requires_the_service_credential(client, enforced):
    res = client.post("/resume", json={"thread_id": "thread_x", "triggered_candle": {}})
    assert res.status_code == 401


def test_resume_enforced_accepts_the_service_credential(client, enforced):
    """The watcher gets through; the 400 that follows is the unpaused-thread answer."""
    headers = {ident.HEADER_SERVICE: ident.sign_service("tool-server")}
    res = client.post("/resume", json={"thread_id": "thread_x", "triggered_candle": {}}, headers=headers)
    assert res.status_code != 401


def test_resume_enforced_rejects_a_user_assertion(client, enforced):
    """A user must not be able to drive the watcher's resume path."""
    headers = {ident.HEADER_SERVICE: ident.sign_identity("some_user")}
    res = client.post("/resume", json={"thread_id": "thread_x", "triggered_candle": {}}, headers=headers)
    assert res.status_code == 401


# ── Untouched surfaces ───────────────────────────────────────────────────────


def test_cancel_now_requires_identity_when_enforced(client, enforced):
    """The gap this test used to PIN is closed.

    In Phase 1 this asserted `/cancel` returned 200 for any caller, recording as a known
    gap that anyone who knew a thread id could stop somebody else's run. T4.2 closed it,
    so the assertion is inverted rather than deleted — that is the record of the change.
    """
    res = client.post("/cancel", json={"thread_id": "thread_x"})
    assert res.status_code == 401


def test_cancel_unenforced_still_works_for_an_unknown_thread(client):
    """The legacy path stays open while enforcement is off.

    A thread with no run row predates the session store and has no recorded owner, so
    there is nothing to check against. Refusing it would break every in-flight watch
    across a deploy.
    """
    res = client.post("/cancel", json={"thread_id": "thread_legacy"})
    assert res.status_code == 200
    assert res.json()["status"] == "cancelling"


def test_cancel_requires_an_identifier(client):
    res = client.post("/cancel", json={})
    assert res.status_code == 422


def test_options_snapshot_needs_no_identity(client, enforced):
    """The F&O snapshot is not user data and is not gated in the UI either.

    Gating it here would break the options workspace for everyone the moment
    enforcement was turned on.
    """
    res = client.get("/options/snapshot", params={"underlying": "NIFTY"})
    assert res.status_code != 401
