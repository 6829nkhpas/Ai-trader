"""P2 — proof that the recommendation record is append-only and tamper-evident.

`docs/business/PLAN_OF_ACTION.md` §4.2 blocker **P2** requires a record a SEBI
Research Analyst can produce years later AND show to be unaltered. "We have a
table" does not establish that; these tests do, by attacking the store the way an
inspection would assume someone had:

  * an honest append chain verifies (and keeps verifying as it grows),
  * an edited row is detected, and the row that was edited is NAMED,
  * an inserted, removed or reordered row is detected as a linkage break, not
    merely as a bad hash,
  * UPDATE and DELETE are refused by the database itself, not by convention,
  * one recommendation per thread, so a LangGraph replay cannot make one
    recommendation look like two,
  * ``_finalize_decision`` — the single commit chokepoint — actually writes.

Every test that must defeat the append-only triggers does so explicitly (by
dropping them first). That is the honest way to test tamper detection: if a test
could not corrupt the file, it would be proving nothing about the chain, and the
drop-then-edit sequence is exactly the attack the chain exists to make visible.
"""

import json
import os
import sqlite3
import sys

import pytest

_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
import hashchain  # noqa: E402
import prompt_version  # noqa: E402
import reco_store  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────


def _append(**overrides) -> int:
    """Append one recommendation with sane defaults. Returns its row id."""
    payload = dict(
        action="BUY",
        symbol="NIFTY",
        timeframe="15m",
        mode="FIND",
        profile="INTRADAY",
        entry=24500.0,
        stop_loss=24400.0,
        take_profit=24700.0,
        conviction=72,
        risk_reward=2.0,
        rationale={"summary": "test", "risk_reward": 2.0},
        tool_inputs={"get_consensus_report": {"atr_14": 55.0}},
        model_id="openai/gpt-4o",
        prompt_hash="a" * 64,
        prompt_set_hash="b" * 64,
    )
    payload.update(overrides)
    row_id = reco_store.record(**payload)
    assert row_id is not None
    return row_id


def _raw_connect() -> sqlite3.Connection:
    """A connection with the append-only triggers REMOVED.

    Used only by the tamper tests. Dropping the triggers is the privileged act a
    determined operator would perform, and the point of the hash chain is that it
    survives exactly that.
    """
    conn = sqlite3.connect(hashchain.db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute(f"DROP TRIGGER IF EXISTS {reco_store.TABLE}_no_update")
    conn.execute(f"DROP TRIGGER IF EXISTS {reco_store.TABLE}_no_delete")
    conn.commit()
    return conn


class StubToolMessage:
    """Minimal stand-in for a LangChain ToolMessage (see the parity tests)."""

    def __init__(self, name, payload):
        self.name = name
        self.content = payload if isinstance(payload, str) else json.dumps(payload)
        self.type = "tool"


# ── The chain verifies ────────────────────────────────────────────────────────


def test_empty_store_verifies():
    """An initialised store with no rows is a valid chain, not an error.

    An audit run before the first recommendation must answer "intact, 0 rows"
    rather than raising — otherwise "the verifier crashed" and "the record was
    tampered with" look the same to whoever is reading the output. Note the
    deliberate contrast with ``test_verification_of_a_missing_table_...``: an empty
    table is "nothing published yet", a MISSING table is "something happened to
    this file", and the two must not report the same thing.
    """
    reco_store.ensure_store()
    result = reco_store.verify_chain()
    assert result.ok is True
    assert result.rows == 0
    assert result.broken_at_id is None


def test_ensure_store_installs_the_triggers_before_the_first_row():
    """The guarantee must not begin at the first write.

    If the triggers only appeared once a recommendation had been published, the
    first one would sit in an unprotected table for however long that window is.
    """
    reco_store.ensure_store()
    conn = hashchain.connect()
    try:
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert f"{reco_store.TABLE}_no_update" in names
    assert f"{reco_store.TABLE}_no_delete" in names


def test_appended_chain_verifies_and_counts():
    for index in range(5):
        _append(thread_id=f"thread-{index}", conviction=50 + index)
    result = reco_store.verify_chain()
    assert result.ok is True, result.reason
    assert result.rows == 5
    assert reco_store.count() == 5


def test_first_row_links_to_genesis_and_each_row_links_to_the_previous():
    """The linkage is checked, not assumed.

    A store whose first row claimed some other ``prev_hash`` would verify under a
    naive "recompute each row's own hash" check while actually hiding a removed
    first row.
    """
    _append(thread_id="t1")
    _append(thread_id="t2")
    rows = reco_store.latest(limit=10)
    rows.reverse()  # oldest first
    assert rows[0]["prev_hash"] == hashchain.GENESIS_HASH
    assert rows[1]["prev_hash"] == rows[0]["row_hash"]


def test_recorded_row_carries_what_the_regulation_asks_for():
    """The columns that answer the regulator's questions are actually populated."""
    _append(thread_id="t1", horizon="intraday")
    row = reco_store.latest(limit=1)[0]
    assert row["action"] == "BUY"
    assert row["symbol"] == "NIFTY"
    assert row["entry"] == 24500.0
    assert row["horizon"] == "intraday"
    assert row["model_id"] == "openai/gpt-4o"
    assert row["prompt_hash"] == "a" * 64
    assert row["prompt_set_hash"] == "b" * 64
    # The rationale is stored as canonical JSON, so it round-trips.
    assert json.loads(row["rationale_json"])["summary"] == "test"
    assert json.loads(row["tool_inputs_json"])["get_consensus_report"]["atr_14"] == 55.0
    # Null until P8b (the NISM-certified sign-off) exists. A placeholder name here
    # would be a false statement in a regulatory record.
    assert row["analyst_of_record"] is None


def test_analyst_of_record_is_recorded_when_configured(monkeypatch):
    monkeypatch.setenv("ANALYST_OF_RECORD", "  A. Analyst (INH000000000)  ")
    _append(thread_id="t1")
    assert reco_store.latest(limit=1)[0]["analyst_of_record"] == "A. Analyst (INH000000000)"


def test_whitespace_analyst_of_record_is_treated_as_unset(monkeypatch):
    """A record must not be signed by ``"   "``."""
    monkeypatch.setenv("ANALYST_OF_RECORD", "   ")
    _append(thread_id="t1")
    assert reco_store.latest(limit=1)[0]["analyst_of_record"] is None


# ── Tampering is detected ─────────────────────────────────────────────────────


def test_editing_a_row_breaks_the_chain_and_names_the_row():
    ids = [_append(thread_id=f"t{i}") for i in range(4)]
    target = ids[2]
    conn = _raw_connect()
    try:
        # The most tempting edit there is: quietly improve a recommendation's
        # entry price after the fact.
        conn.execute(
            f"UPDATE {reco_store.TABLE} SET entry = 24000.0 WHERE id = ?", (target,)
        )
        conn.commit()
    finally:
        conn.close()

    result = reco_store.verify_chain()
    assert result.ok is False
    assert result.broken_at_id == target, "verification must name the first bad row"
    assert "edited" in (result.reason or "")


def test_editing_the_rationale_alone_is_detected():
    """The evidence is hashed too, not just the levels.

    A record whose numbers are intact but whose stated basis was rewritten is
    exactly as misleading as one with edited numbers.
    """
    target = _append(thread_id="t1")
    conn = _raw_connect()
    try:
        conn.execute(
            f"UPDATE {reco_store.TABLE} SET rationale_json = ? WHERE id = ?",
            (json.dumps({"summary": "we always said this"}), target),
        )
        conn.commit()
    finally:
        conn.close()
    assert reco_store.verify_chain().ok is False


def test_deleting_a_row_breaks_the_chain_as_a_linkage_failure():
    ids = [_append(thread_id=f"t{i}") for i in range(4)]
    conn = _raw_connect()
    try:
        conn.execute(f"DELETE FROM {reco_store.TABLE} WHERE id = ?", (ids[1],))
        conn.commit()
    finally:
        conn.close()

    result = reco_store.verify_chain()
    assert result.ok is False
    # The row AFTER the hole is where the linkage first fails.
    assert result.broken_at_id == ids[2]
    assert "inserted, removed or reordered" in (result.reason or "")


def test_deleting_the_last_row_is_detected_by_a_length_check_not_the_chain():
    """An honest limit, stated as a test rather than left to be discovered.

    Truncating the TAIL of a hash chain leaves the remainder internally
    consistent — that is a property of chains, not a bug here. Detection needs an
    external witness: the row count (or the tip hash) recorded elsewhere. This
    test pins the behaviour so nobody mistakes ``verify_chain() == ok`` for "no
    rows were ever removed".
    """
    for index in range(3):
        _append(thread_id=f"t{index}")
    tip_before = reco_store.latest(limit=1)[0]["row_hash"]
    conn = _raw_connect()
    try:
        conn.execute(
            f"DELETE FROM {reco_store.TABLE} WHERE id = "
            f"(SELECT MAX(id) FROM {reco_store.TABLE})"
        )
        conn.commit()
    finally:
        conn.close()

    result = reco_store.verify_chain()
    assert result.ok is True, "a truncated tail still verifies — this is why..."
    assert result.rows == 2, "...the row count is the witness that must be kept"
    assert reco_store.latest(limit=1)[0]["row_hash"] != tip_before


def test_reordering_two_rows_is_detected():
    """Swapping two rows' contents leaves both hashes wrong."""
    ids = [_append(thread_id=f"t{i}", conviction=40 + i) for i in range(3)]
    conn = _raw_connect()
    try:
        first = conn.execute(
            f"SELECT conviction FROM {reco_store.TABLE} WHERE id=?", (ids[0],)
        ).fetchone()["conviction"]
        second = conn.execute(
            f"SELECT conviction FROM {reco_store.TABLE} WHERE id=?", (ids[1],)
        ).fetchone()["conviction"]
        conn.execute(
            f"UPDATE {reco_store.TABLE} SET conviction=? WHERE id=?", (second, ids[0])
        )
        conn.execute(
            f"UPDATE {reco_store.TABLE} SET conviction=? WHERE id=?", (first, ids[1])
        )
        conn.commit()
    finally:
        conn.close()
    assert reco_store.verify_chain().ok is False


def test_verification_of_a_missing_table_is_a_failure_not_an_exception(monkeypatch, tmp_path):
    """An audit tool needs an answer in every case, including "there is no chain"."""
    monkeypatch.setenv("COMPLIANCE_DB_PATH", str(tmp_path / "absent.db"))
    result = reco_store.verify_chain()
    assert result.ok is False
    assert result.rows == 0
    assert "recommendations" in (result.reason or "")


# ── Append-only is enforced by the database ───────────────────────────────────


def test_update_is_refused_by_the_trigger():
    """Not "we do not call UPDATE" — UPDATE is refused when it IS called."""
    _append(thread_id="t1")
    conn = hashchain.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError) as excinfo:
            conn.execute(f"UPDATE {reco_store.TABLE} SET action='SELL'")
        assert "append-only" in str(excinfo.value)
    finally:
        conn.close()
    # The refused statement changed nothing.
    assert reco_store.latest(limit=1)[0]["action"] == "BUY"
    assert reco_store.verify_chain().ok is True


def test_delete_is_refused_by_the_trigger():
    _append(thread_id="t1")
    conn = hashchain.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError) as excinfo:
            conn.execute(f"DELETE FROM {reco_store.TABLE}")
        assert "append-only" in str(excinfo.value)
    finally:
        conn.close()
    assert reco_store.count() == 1


def test_the_module_exposes_no_mutation_api():
    """No update/delete/purge entry point exists to be called by accident.

    ``journal.py`` deliberately has ``purge()``; this store must not grow one, and
    a test is the only thing that keeps that true as the file is edited.
    """
    for forbidden in ("update", "delete", "purge", "clear", "reset", "drop"):
        assert not hasattr(reco_store, forbidden), (
            f"reco_store must not expose a mutation API, found {forbidden!r}"
        )


# ── One recommendation per thread ─────────────────────────────────────────────


def test_repeated_record_for_one_thread_writes_exactly_one_row():
    """LangGraph checkpoint replay must not duplicate a recommendation.

    Two rows for one committed decision reads as two recommendations to anyone
    auditing the chain — a fabricated publication.
    """
    first = _append(thread_id="thread-A")
    second = _append(thread_id="thread-A", action="SELL", entry=1.0)
    assert second == first, "a repeated commit must return the FIRST row's id"
    assert reco_store.count() == 1
    # The first recommendation is what stands; the replay did not overwrite it.
    assert reco_store.latest(limit=1)[0]["action"] == "BUY"


def test_thread_less_rows_are_not_collapsed_together():
    """The uniqueness index is partial, so NULL thread ids stay distinct."""
    _append(thread_id=None)
    _append(thread_id=None)
    assert reco_store.count() == 2
    assert reco_store.verify_chain().ok is True


def test_blank_thread_id_is_stored_as_null():
    _append(thread_id="   ")
    assert reco_store.latest(limit=1)[0]["thread_id"] is None


# ── Non-finite and absent values are stored as NULL, never fabricated ─────────


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), None, "x", True])
def test_non_finite_levels_are_stored_as_null(bad):
    _append(thread_id="t1", entry=bad)
    row = reco_store.latest(limit=1)[0]
    assert row["entry"] is None, f"{bad!r} must not become a price in the record"
    assert reco_store.verify_chain().ok is True


def test_a_hold_with_no_levels_still_records():
    """A HOLD is a published output too, and must be recorded as one."""
    _append(
        thread_id="t1",
        action="HOLD",
        entry=None,
        stop_loss=None,
        take_profit=None,
        risk_reward=None,
        conviction=None,
    )
    row = reco_store.latest(limit=1)[0]
    assert row["action"] == "HOLD"
    assert row["entry"] is None and row["conviction"] is None
    assert reco_store.verify_chain().ok is True


def test_action_is_normalised_to_upper_case():
    _append(thread_id="t1", action="buy")
    assert reco_store.latest(limit=1)[0]["action"] == "BUY"


# ── The finalize chokepoint actually writes ───────────────────────────────────


def _finalize(state, decision, thread_id=None):
    """Run the real chokepoint with only the JOURNAL stubbed out.

    ``reco_store`` is NOT stubbed — the whole point is to prove the real store
    receives the row. The journal is stubbed because this test is about P2 and a
    journal write would need its own DB isolation.
    """
    original = graph.journal.record_decision
    graph.journal.record_decision = lambda *a, **k: None
    try:
        return graph._finalize_decision(state, decision, thread_id)
    finally:
        graph.journal.record_decision = original


def _state(**overrides):
    state = {
        "messages": [
            StubToolMessage("get_multi_tf_trend", {"1D": "Bullish"}),
            StubToolMessage("get_consensus_report", {"atr_14": 55.0}),
        ],
        "mode": "FIND",
        "symbol": "NIFTY",
        "timeframe": "15m",
        "profile": "INTRADAY",
    }
    state.update(overrides)
    return state


def test_finalize_decision_writes_one_recommendation_row():
    decision = {
        "action": "BUY",
        "conviction_score": 68,
        "entry": 24500.0,
        "stop_loss": 24400.0,
        "take_profit": 24700.0,
    }
    _finalize(_state(), decision, thread_id="thread-1")

    assert reco_store.count() == 1
    row = reco_store.latest(limit=1)[0]
    assert row["action"] == "BUY"
    assert row["symbol"] == "NIFTY"
    assert row["timeframe"] == "15m"
    assert row["mode"] == "FIND"
    assert row["profile"] == "INTRADAY"
    assert row["conviction"] == 68
    assert row["thread_id"] == "thread-1"
    # The composed prompt and the prompt library are both fingerprinted, and
    # neither is the "<unavailable>" sentinel on a real run.
    assert len(row["prompt_hash"]) == 64
    assert len(row["prompt_set_hash"]) == 64
    assert row["model_id"]
    # The rationale IS the defensibility record, and the tool results the
    # reasoning was drawn from are stored alongside it.
    rationale = json.loads(row["rationale_json"])
    assert rationale["action"] == "BUY"
    assert "get_consensus_report" in json.loads(row["tool_inputs_json"])
    assert reco_store.verify_chain().ok is True


def test_finalize_decision_is_idempotent_per_thread():
    """Mirrors the journal's Bug-5 idempotency guarantee for the P2 store."""
    decision = {"action": "BUY", "conviction_score": 60, "entry": 24500.0,
                "stop_loss": 24400.0, "take_profit": 24700.0}
    state = _state()
    _finalize(state, decision, thread_id="thread-X")
    _finalize(state, dict(decision), thread_id="thread-X")
    _finalize(state, dict(decision), thread_id="thread-X")
    assert reco_store.count() == 1


def test_finalize_records_the_user_the_recommendation_went_to():
    """"To whom was this published?" is a column, answered from the run context."""
    import run_context

    token_set = False
    try:
        run_context.set_run_user_id("user-42")
        token_set = True
        _finalize(_state(), {"action": "HOLD"}, thread_id="thread-U")
    finally:
        if token_set:
            run_context.set_run_user_id(None)
    assert reco_store.latest(limit=1)[0]["user_id"] == "user-42"


def test_finalize_records_a_hold_with_no_levels():
    """The data-gating and forced HOLD paths produce records too."""
    _finalize(_state(), {"action": "HOLD"}, thread_id="thread-H")
    row = reco_store.latest(limit=1)[0]
    assert row["action"] == "HOLD"
    assert row["entry"] is None
    assert reco_store.verify_chain().ok is True


def test_finalize_never_raises_when_the_store_is_unwritable(monkeypatch, capsys):
    """A compliance write must not be able to abort a run — but it must be loud.

    Deliberate posture: ``reco_store.record`` raises (a dropped regulatory row is
    the defect it exists to prevent) and the CALLER swallows it with a WARN, so an
    operator can see that a recommendation went out unrecorded.
    """
    def boom(**kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(reco_store, "record", boom)
    record = _finalize(_state(), {"action": "BUY", "entry": 1.0}, thread_id="thread-B")
    assert isinstance(record, dict), "the run must still produce its decision"
    assert "reco_store.record failed" in capsys.readouterr().out


def test_finalize_reads_levels_resolved_by_the_defensibility_record():
    """A level parsed out of the plan prose still reaches the record.

    ``journal.record_decision`` falls back to ``defensibility["levels"]`` when the
    structured ``declare_trade`` args were incomplete; the P2 store uses the same
    precedence so the two stores can never disagree about what was recommended.
    """
    decision = {"action": "BUY", "conviction_score": 55}
    record = _finalize(_state(), decision, thread_id="thread-L")
    levels = record.get("levels") or {}
    row = reco_store.latest(limit=1)[0]
    for column, key in (("entry", "entry"), ("stop_loss", "stop_loss"), ("take_profit", "take_profit")):
        expected = levels.get(key)
        if isinstance(expected, (int, float)):
            assert row[column] == pytest.approx(expected)
        else:
            assert row[column] is None


# ── Prompt / model fingerprints ───────────────────────────────────────────────


def test_prompt_hash_is_stable_across_line_endings():
    """The repo is CRLF on disk; a hash that disagreed per platform is useless."""
    assert prompt_version.prompt_hash("a\r\nb") == prompt_version.prompt_hash("a\nb")


def test_prompt_hash_changes_when_the_prompt_changes():
    assert prompt_version.prompt_hash("analyse X") != prompt_version.prompt_hash("analyse Y")


def test_missing_prompt_is_the_sentinel_not_the_empty_hash():
    """A record must not claim a prompt it never captured."""
    assert prompt_version.prompt_hash(None) == "<unavailable>"
    assert prompt_version.prompt_hash("") != prompt_version.prompt_hash(None)


def test_prompt_set_hash_covers_every_declared_constant():
    report = prompt_version.prompt_version_report()
    assert report["missing"] == [], f"prompt library incomplete: {report['missing']}"
    for name in prompt_version.PROMPT_CONSTANTS:
        assert report["prompts"][name] != "<unavailable>"
    # The personalisation rule is part of the published Q&A behaviour (P8a), so a
    # change to it is a change to the analyst.
    assert "personalisation.QA_PROMPT_RULE" in report["prompts"]


def test_prompt_set_hash_is_deterministic():
    assert prompt_version.prompt_set_hash() == prompt_version.prompt_set_hash()


def test_prompt_set_hash_moves_when_a_prompt_is_edited(monkeypatch):
    before = prompt_version.prompt_set_hash()
    monkeypatch.setattr(graph, "RISK_MANAGER_PROMPT", graph.RISK_MANAGER_PROMPT + "\nnew rule")
    assert prompt_version.prompt_set_hash() != before


def test_model_id_prefers_the_runs_override(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "deployment/default")
    assert prompt_version.model_id("  run/override  ") == "run/override"
    assert prompt_version.model_id(None) == "deployment/default"


def test_model_id_default_matches_the_graph_default(monkeypatch):
    """The duplicated default must not drift from ``graph``'s.

    ``prompt_version`` cannot import ``graph`` (that is the cycle it exists to
    avoid), so the constant is duplicated — and pinned here, which is the only
    thing that keeps the duplication honest.
    """
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert prompt_version.model_id(None) == graph.OPENROUTER_DEFAULT_MODEL


def test_model_id_is_never_none(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    for override in (None, "", "   "):
        assert isinstance(prompt_version.model_id(override), str)
        assert prompt_version.model_id(override).strip()


# ── Canonical serialisation ───────────────────────────────────────────────────


def test_canonical_json_is_key_order_independent():
    assert hashchain.canonical_json({"a": 1, "b": 2}) == hashchain.canonical_json({"b": 2, "a": 1})


def test_canonical_json_nulls_non_finite_floats():
    """NaN never equals itself, so a NaN in a payload could never re-verify."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        assert hashchain.canonical_json({"v": bad}) == '{"v":null}'


def test_canonical_json_keeps_booleans_distinct_from_ints():
    """``isinstance(True, int)`` is True in Python; the check order decides this."""
    assert hashchain.canonical_json({"v": True}) == '{"v":true}'
    assert hashchain.canonical_json({"v": 1}) == '{"v":1}'


def test_hash_payload_separates_prev_hash_from_the_payload():
    """Without the separator, characters could be moved between the two fields."""
    assert hashchain.hash_payload("ab", "c") != hashchain.hash_payload("a", "bc")


def test_hash_payload_treats_no_prev_hash_as_genesis():
    assert hashchain.hash_payload(None, {"a": 1}) == hashchain.hash_payload(
        hashchain.GENESIS_HASH, {"a": 1}
    )


def test_stored_rationale_text_and_hashed_text_cannot_disagree():
    """The row stores exactly the bytes that were hashed.

    If the column were serialised with plain ``json.dumps`` while the hash used
    the canonicaliser, every row with a NaN or a non-string key would verify as
    tampered on a different machine.
    """
    rationale = {"z": 1, "a": {"nested": float("nan")}}
    _append(thread_id="t1", rationale=rationale)
    row = reco_store.latest(limit=1)[0]
    assert row["rationale_json"] == hashchain.canonical_json(rationale)
    assert reco_store.verify_chain().ok is True
