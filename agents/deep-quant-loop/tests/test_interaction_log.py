"""P5 — proof that the interaction log records what was communicated, unaltered.

`docs/business/PLAN_OF_ACTION.md` §4.2 blocker **P5**. Two questions have to be
answerable from this store years after the fact: *what was this client told, and
when*, and *has that record been altered since*. These tests cover both, at two
levels:

  * the store itself — append-only, chained, verbatim content, no purge;
  * the endpoints — every request logged BEFORE the work, including the ones that
    are refused, and a terminal row for every way an interaction can end.

The endpoint tests drive the real FastAPI routes through ``TestClient`` with the
graph stubbed, because the claim being tested is about the ROUTE's behaviour. A
test that called ``interaction_log.record_request`` directly would prove the store
works while leaving the actual gap — an endpoint that forgot to log — undetected.
"""

import os
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import hashchain  # noqa: E402
import interaction_log  # noqa: E402
import main  # noqa: E402


# ── Store-level: the chain ────────────────────────────────────────────────────


def _request(**overrides) -> int:
    fields = dict(
        kind=interaction_log.KIND_RUN,
        thread_id="thread-1",
        user_id="user-1",
        content="analyse NIFTY",
        mode="FIND",
        symbol="NIFTY",
        timeframe="15m",
        profile="INTRADAY",
    )
    fields.update(overrides)
    return interaction_log.record_request(**fields)


def _outcome(**overrides) -> int:
    fields = dict(
        kind=interaction_log.KIND_RUN,
        status="completed",
        thread_id="thread-1",
        user_id="user-1",
    )
    fields.update(overrides)
    return interaction_log.record_outcome(**fields)


def _raw_connect() -> sqlite3.Connection:
    """A connection with the append-only triggers removed — see the P2 suite."""
    conn = sqlite3.connect(hashchain.db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute(f"DROP TRIGGER IF EXISTS {interaction_log.TABLE}_no_update")
    conn.execute(f"DROP TRIGGER IF EXISTS {interaction_log.TABLE}_no_delete")
    conn.commit()
    return conn


def test_empty_initialised_store_verifies():
    interaction_log.ensure_store()
    result = interaction_log.verify_chain()
    assert result.ok is True and result.rows == 0


def test_appended_events_verify():
    _request()
    _outcome()
    result = interaction_log.verify_chain()
    assert result.ok is True, result.reason
    assert result.rows == 2


def test_editing_a_logged_answer_breaks_the_chain():
    """The scenario this store exists for: an answer edited after a complaint."""
    _request()
    target = _outcome(content="Buy NIFTY above 24,500.")
    conn = _raw_connect()
    try:
        conn.execute(
            f"UPDATE {interaction_log.TABLE} SET content=? WHERE id=?",
            ("We advised caution.", target),
        )
        conn.commit()
    finally:
        conn.close()

    result = interaction_log.verify_chain()
    assert result.ok is False
    assert result.broken_at_id == target
    assert "edited" in (result.reason or "")


def test_removing_an_interaction_from_the_middle_is_detected():
    """Deleting an inconvenient turn is the other obvious attack."""
    ids = [_request(thread_id=f"t{i}") for i in range(4)]
    conn = _raw_connect()
    try:
        conn.execute(f"DELETE FROM {interaction_log.TABLE} WHERE id=?", (ids[1],))
        conn.commit()
    finally:
        conn.close()
    result = interaction_log.verify_chain()
    assert result.ok is False
    assert result.broken_at_id == ids[2]


def test_update_and_delete_are_refused_by_the_database():
    _request()
    conn = hashchain.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError) as update_err:
            conn.execute(f"UPDATE {interaction_log.TABLE} SET content='x'")
        assert "append-only" in str(update_err.value)
        with pytest.raises(sqlite3.IntegrityError) as delete_err:
            conn.execute(f"DELETE FROM {interaction_log.TABLE}")
        assert "append-only" in str(delete_err.value)
    finally:
        conn.close()
    assert interaction_log.count() == 1


def test_there_is_no_purge():
    """SEBI's five-year retention floor, enforced by the absence of an API.

    ``journal.py`` has ``purge()`` because it is a measurement store. This one must
    never grow one: a DPDP erasure request cannot reach these rows while the
    statutory retention obligation applies, so a convenience purge would be a
    compliance breach waiting for a caller.
    """
    for forbidden in ("purge", "delete", "update", "clear", "reset", "drop", "erase"):
        assert not hasattr(interaction_log, forbidden), (
            f"interaction_log must not expose {forbidden!r}"
        )


# ── Store-level: content fidelity ─────────────────────────────────────────────


def test_content_is_stored_verbatim_not_hashed():
    """The question an inspection asks is "what did you tell them?"."""
    answer = "The 15m structure is bullish above 24,500. This is not advice."
    _outcome(content=answer)
    assert interaction_log.latest(limit=1)[0]["content"] == answer


def test_unicode_content_round_trips():
    """₹ and Devanagari must survive; canonical JSON keeps non-ASCII readable."""
    text = "₹5,00,000 का सवाल"
    _request(content=text)
    assert interaction_log.latest(limit=1)[0]["content"] == text
    assert interaction_log.verify_chain().ok is True


def test_oversized_content_is_truncated_visibly_not_silently():
    """A row that says it was truncated is honest; one that loses its tail is not."""
    huge = "x" * (interaction_log.MAX_CONTENT_CHARS + 500)
    _request(content=huge)
    stored = interaction_log.latest(limit=1)[0]["content"]
    assert len(stored) < len(huge)
    assert "[truncated 500 characters]" in stored
    assert interaction_log.verify_chain().ok is True


def test_blank_content_is_null_rather_than_a_space():
    _request(content="   ")
    assert interaction_log.latest(limit=1)[0]["content"] is None


def test_many_turns_on_one_thread_are_all_kept_in_order():
    """A thread has many interactions; collapsing them would destroy the sequence."""
    for turn in range(3):
        _request(kind=interaction_log.KIND_QA, content=f"question {turn}")
        _outcome(kind=interaction_log.KIND_QA, content=f"answer {turn}")
    rows = interaction_log.for_thread("thread-1")
    assert len(rows) == 6
    assert [row["content"] for row in rows] == [
        "question 0", "answer 0", "question 1", "answer 1", "question 2", "answer 2",
    ]
    assert interaction_log.verify_chain().ok is True


def test_for_user_returns_only_that_users_events():
    _request(user_id="user-A", thread_id="tA")
    _request(user_id="user-B", thread_id="tB")
    rows = interaction_log.for_user("user-A")
    assert len(rows) == 1
    assert rows[0]["thread_id"] == "tA"


# ── Endpoint-level ────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    """A TestClient with the graph stubbed out.

    The graph is replaced because these tests assert on LOGGING, not on analysis:
    a real run would need an LLM key, live market data and minutes of wall clock.
    The stub still exercises the real endpoint bodies, the real
    ``event_generator``/``_run_events`` terminal branches and the real store.

    ``LLM_API_KEY`` is pinned rather than inherited. Importing ``main`` pulls the
    repo ``.env`` in through ``graph.py``, so on a developer machine the service
    silently starts in SHARED-KEY mode and on CI in PER-USER mode — the same test
    would then exercise different branches depending on whose checkout it ran in.
    Setting it here fixes the mode at shared-key; the one test that needs the
    per-user branch deletes it explicitly.
    """
    monkeypatch.setenv("SKU_ENFORCE", "0")
    monkeypatch.setenv("LLM_API_KEY", "test-shared-key")
    monkeypatch.setattr(main, "resolve_openrouter_key", lambda user_id: "test-key")
    monkeypatch.setattr(main, "set_run_llm_credentials", lambda *a, **k: None)

    class StubState:
        next = ()
        values = {"messages": []}

    async def stub_astream(*args, **kwargs):
        if False:  # pragma: no cover - an empty async generator
            yield {}

    # Patched on `main.graph_module.graph`, not on `main.graph`.
    #
    # `main` used to bind the compiled graph directly (`from graph import graph`). It now
    # does `import graph as graph_module` and reads `graph_module.graph` at every call
    # site, because the durable checkpointer can only be built inside the running event
    # loop and so the FastAPI lifespan REBINDS that attribute at startup — a
    # once-bound name would keep pointing at the MemorySaver-backed graph.
    #
    # Patching the same object main actually calls is what keeps this a test of the real
    # endpoint bodies. The assertions below are untouched.
    monkeypatch.setattr(main.graph_module.graph, "astream", stub_astream)
    monkeypatch.setattr(main.graph_module.graph, "get_state", lambda config: StubState())
    return TestClient(main.app)


def _drain(response) -> None:
    """Consume an SSE response so the generator reaches its terminal branch."""
    for _ in response.iter_lines():
        pass


def test_run_endpoint_logs_the_request_and_the_outcome(client):
    response = client.post(
        "/run",
        json={
            "thread_id": "t-run",
            "message": "analyse NIFTY",
            "mode": "FIND",
            "symbol": "NIFTY",
            "timeframe": "15m",
            "profile": "INTRADAY",
            "user_id": "user-9",
        },
    )
    _drain(response)

    rows = interaction_log.for_thread("t-run")
    events = [(row["event"], row["kind"], row["status"]) for row in rows]
    assert (interaction_log.EVENT_REQUEST, "run", None) in events
    request_row = next(r for r in rows if r["event"] == interaction_log.EVENT_REQUEST)
    assert request_row["content"] == "analyse NIFTY"
    assert request_row["user_id"] == "user-9"
    assert request_row["symbol"] == "NIFTY"
    assert request_row["mode"] == "FIND"
    assert request_row["profile"] == "INTRADAY"
    # Exactly one terminal row — not two, despite the generator's `finally` also
    # being able to record one.
    outcomes = [r for r in rows if r["event"] == interaction_log.EVENT_OUTCOME]
    assert len(outcomes) == 1, f"expected one terminal row, got {len(outcomes)}"
    assert interaction_log.verify_chain().ok is True


def test_qa_endpoint_logs_the_question(client):
    response = client.post(
        "/qa",
        json={"thread_id": "t-qa", "question": "why is the stop there?", "user_id": "user-9"},
    )
    _drain(response)
    rows = interaction_log.for_thread("t-qa")
    request_row = next(r for r in rows if r["event"] == interaction_log.EVENT_REQUEST)
    assert request_row["kind"] == interaction_log.KIND_QA
    assert request_row["content"] == "why is the stop there?"
    assert request_row["mode"] == "QA"


def test_qa_endpoint_logs_the_answer_that_was_sent(client, monkeypatch):
    """The half of the record that matters most: what the client was told."""

    class StubMessage:
        content = "The stop sits below the 15m swing low at 24,400."
        additional_kwargs: dict = {}

    class StubState:
        next = ()
        values = {"messages": [StubMessage()]}

    monkeypatch.setattr(main.graph_module.graph, "get_state", lambda config: StubState())
    _drain(client.post("/qa", json={"thread_id": "t-ans", "question": "why?", "user_id": "u"}))

    outcome = next(
        r for r in interaction_log.for_thread("t-ans")
        if r["event"] == interaction_log.EVENT_OUTCOME
    )
    assert outcome["content"] == StubMessage.content
    assert outcome["status"] == "completed"


def test_qa_refusal_records_the_personalisation_category(client, monkeypatch):
    """A P8a refusal is logged as evidence, not merely as a completed turn.

    ``qa_node`` stamps the matched category on the refusal message without calling
    the model (compliance blocker P8a). Carrying it into the log is what lets
    someone show the RA/IA boundary being enforced turn by turn.
    """

    class StubMessage:
        content = "I can't tailor this to your personal circumstances."
        additional_kwargs = {"_personalisation_refusal": "capital"}

    class StubState:
        next = ()
        values = {"messages": [StubMessage()]}

    monkeypatch.setattr(main.graph_module.graph, "get_state", lambda config: StubState())
    _drain(client.post(
        "/qa",
        json={"thread_id": "t-ref", "question": "how much of my 5 lakh?", "user_id": "u"},
    ))

    outcome = next(
        r for r in interaction_log.for_thread("t-ref")
        if r["event"] == interaction_log.EVENT_OUTCOME
    )
    assert outcome["refusal_category"] == "capital"
    # The question that triggered it is on the record too, so the pair reads as a
    # complete interaction.
    request_row = next(
        r for r in interaction_log.for_thread("t-ref")
        if r["event"] == interaction_log.EVENT_REQUEST
    )
    assert "5 lakh" in (request_row["content"] or "")


def test_cancel_endpoint_logs_the_request(client):
    client.post("/cancel", json={"thread_id": "t-cancel"})
    rows = interaction_log.for_thread("t-cancel")
    assert len(rows) == 1
    assert rows[0]["kind"] == interaction_log.KIND_CANCEL
    assert rows[0]["event"] == interaction_log.EVENT_REQUEST


def test_resume_endpoint_logs_the_trigger(client, monkeypatch):
    class PausedState:
        next = ("watch_price_condition",)
        values = {"messages": []}

    monkeypatch.setattr(main.graph_module.graph, "get_state", lambda config: PausedState())
    response = client.post(
        "/resume",
        json={
            "thread_id": "t-resume",
            "triggered_candle": {"close": 24550.0},
            "trigger_kind": "target",
            "user_id": "user-9",
        },
    )
    _drain(response)
    rows = interaction_log.for_thread("t-resume")
    request_row = next(r for r in rows if r["event"] == interaction_log.EVENT_REQUEST)
    assert request_row["kind"] == interaction_log.KIND_RESUME
    assert "target" in (request_row["content"] or "")


def test_a_refused_request_is_logged_with_both_rows(client, monkeypatch):
    """The evidence Gate 0→1 asks for: the gate refusing an unlicensed caller.

    A log that only recorded permitted traffic could not distinguish "no
    unentitled user ever asked" from "we never wrote it down".
    """
    monkeypatch.setenv("SKU_ENFORCE", "1")
    response = client.post(
        "/run",
        json={"thread_id": "t-refused", "message": "analyse NIFTY", "mode": "FIND",
              "user_id": "unentitled-user"},
    )
    _drain(response)

    rows = interaction_log.for_thread("t-refused")
    assert any(r["event"] == interaction_log.EVENT_REQUEST for r in rows), (
        "the refused request itself must be on the record"
    )
    outcome = next(r for r in rows if r["event"] == interaction_log.EVENT_OUTCOME)
    assert outcome["status"] == "refused_entitlement"
    assert outcome["user_id"] == "unentitled-user"
    assert interaction_log.verify_chain().ok is True


def test_an_errored_run_still_gets_a_terminal_row(client, monkeypatch):
    """A failed interaction is still an interaction."""

    async def exploding_astream(*args, **kwargs):
        raise RuntimeError("provider timeout")
        yield {}  # pragma: no cover

    monkeypatch.setattr(main.graph_module.graph, "astream", exploding_astream)
    _drain(client.post(
        "/run",
        json={"thread_id": "t-err", "message": "analyse NIFTY", "mode": "FIND", "user_id": "u"},
    ))

    outcome = next(
        r for r in interaction_log.for_thread("t-err")
        if r["event"] == interaction_log.EVENT_OUTCOME
    )
    assert outcome["status"] == "error"
    assert "provider timeout" in (outcome["detail"] or "")


def test_a_run_in_per_user_mode_without_a_user_id_is_logged_as_an_auth_error(
    client, monkeypatch
):
    """The record shows the attempt AND why it produced nothing.

    Deleting ``LLM_API_KEY`` selects PER-USER credential mode, which is the only
    mode in which a missing ``user_id`` is an error — in shared-key beta mode the
    run legitimately proceeds without one. Asserting this without pinning the mode
    would make the test depend on the developer's ``.env``.
    """
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    _drain(client.post(
        "/run",
        json={"thread_id": "t-nouser", "message": "analyse NIFTY", "mode": "FIND"},
    ))
    outcome = next(
        r for r in interaction_log.for_thread("t-nouser")
        if r["event"] == interaction_log.EVENT_OUTCOME
    )
    assert outcome["status"] == "auth_error"
    # The request row still exists, so the log shows an attempt was made rather
    # than nothing having happened.
    assert any(
        r["event"] == interaction_log.EVENT_REQUEST
        for r in interaction_log.for_thread("t-nouser")
    )


def test_a_logging_failure_does_not_break_the_endpoint(client, monkeypatch, capsys):
    """Deliberate posture: an unwritable audit log degrades, loudly.

    The store raises (a dropped row is the defect it prevents) and the endpoint
    swallows it with a WARN — trading a compliance gap for an outage would be the
    worse failure, and the WARN is what tells an operator the gap occurred.
    """
    def boom(**kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(interaction_log, "record_request", boom)
    response = client.post(
        "/run",
        json={"thread_id": "t-boom", "message": "analyse NIFTY", "mode": "FIND", "user_id": "u"},
    )
    _drain(response)
    assert response.status_code == 200
    assert "interaction_log" in capsys.readouterr().out


def test_the_outcome_recorder_is_one_shot():
    """Two contradictory terminal rows for one interaction cannot be corrected.

    In an append-only store a duplicate outcome is permanent, so the guard that
    prevents it is load-bearing rather than tidy.
    """
    recorder = main._InteractionOutcome("run", "t-once", user_id="u")
    recorder.record("completed")
    recorder.record("disconnected")
    recorder.record("error")
    outcomes = [
        r for r in interaction_log.for_thread("t-once")
        if r["event"] == interaction_log.EVENT_OUTCOME
    ]
    assert len(outcomes) == 1
    assert outcomes[0]["status"] == "completed"


def test_final_answer_reader_is_total():
    """It runs in a terminal stream branch, so no input may make it raise."""
    class Weird:
        pass

    for state in (None, Weird(), object()):
        assert main._final_answer_and_refusal(state) == (None, None)
