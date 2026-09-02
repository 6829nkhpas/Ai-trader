"""Session_API tests — the authenticated surface, and the cross-user matrix.

The centrepiece is ``TestCrossUserAccess``: every route, attempted by a user who does not
own the session, must answer **404** — not 403, not an empty 200. A 403 confirms the id
exists and turns any of these endpoints into an enumeration oracle, and an empty 200 is
indistinguishable from a real empty result, so a client could not tell "not yours" from
"nothing here".

The flag is exercised too. With ``DEEP_QUANT_SESSIONS_ENABLED`` off the routes must be
genuinely ABSENT, so the surface cannot be probed before it is meant to exist.
"""

from __future__ import annotations

import importlib
import os

import pytest
from fastapi.testclient import TestClient

import internal_identity as ident

SECRET = "s" * 64


@pytest.fixture(autouse=True)
def _identity_env(monkeypatch):
    monkeypatch.setenv(ident.ENV_IDENTITY_SECRET, SECRET)
    monkeypatch.setenv(ident.ENV_SERVICE_SECRET, "v" * 64)
    monkeypatch.setenv("DEEP_QUANT_SESSIONS_ENABLED", "1")
    monkeypatch.delenv(ident.ENV_REQUIRE_IDENTITY, raising=False)
    monkeypatch.delenv("SKU_ENFORCE", raising=False)
    ident._warned_unenforced = False
    yield
    ident._warned_unenforced = False


@pytest.fixture
def client(monkeypatch):
    """A TestClient with the session router mounted.

    ``main`` is reloaded because the router is mounted at IMPORT — a router cannot be
    added per request, so the flag has to be set before the module body runs. The
    autouse fixture above sets it; this reload is what makes that take effect for a
    module another test may already have imported.
    """
    monkeypatch.setenv("DEEP_QUANT_SESSIONS_ENABLED", "1")
    import main

    importlib.reload(main)
    return TestClient(main.app)


def _auth(user: str) -> dict:
    return {ident.HEADER_IDENTITY: ident.sign_identity(user)}


def _create(client, user="alice", **over):
    body = {"symbol": "RELIANCE", "profile": "INTRADAY", "timeframe": "10m"}
    body.update(over)
    return client.post("/sessions", json=body, headers=_auth(user))


# ── Mounting ──────────────────────────────────────────────────────────────────


def test_routes_are_absent_when_the_flag_is_off(monkeypatch):
    """Not merely refusing — absent. The surface cannot be probed before it exists."""
    monkeypatch.setenv("DEEP_QUANT_SESSIONS_ENABLED", "0")
    import main

    importlib.reload(main)
    off = TestClient(main.app)
    assert off.post("/sessions", json={}, headers=_auth("alice")).status_code == 404
    assert off.get("/sessions", headers=_auth("alice")).status_code == 404
    # The analysis surface is untouched by the flag.
    assert off.post("/cancel", json={"thread_id": "t"}).status_code == 200


def test_the_flag_accepts_the_documented_spellings(monkeypatch):
    import session_api

    for value, expected in [
        ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
        ("0", False), ("false", False), ("", False), ("nonsense", False),
    ]:
        monkeypatch.setenv("DEEP_QUANT_SESSIONS_ENABLED", value)
        assert session_api.sessions_enabled() is expected, value


# ── Authentication ────────────────────────────────────────────────────────────


class TestAuthentication:
    """These routes NEVER accept an unidentified caller, even unenforced.

    Deliberately unlike `/run`, which keeps its body-`user_id` fallback so existing
    clients survive the rollout. These routes are new and return stored per-user data,
    so there is no compatibility to preserve and no reason to ever serve them anonymously.
    """

    def test_no_assertion_is_401_on_every_route(self, client):
        routes = [
            ("post", "/sessions", {"json": {"symbol": "X", "profile": "INTRADAY", "timeframe": "10m"}}),
            ("get", "/sessions", {}),
            ("get", "/sessions/sess_x", {}),
            ("patch", "/sessions/sess_x", {"json": {"title": "t"}}),
            ("delete", "/sessions/sess_x", {}),
            ("get", "/sessions/sess_x/messages", {}),
            ("get", "/sessions/sess_x/runs", {}),
            ("get", "/runs/run_x/events", {}),
        ]
        for method, path, kwargs in routes:
            res = getattr(client, method)(path, **kwargs)
            assert res.status_code == 401, f"{method.upper()} {path} -> {res.status_code}"
            assert res.json()["detail"] == "authentication required"

    def test_a_forged_assertion_is_401(self, client):
        res = client.get("/sessions", headers={ident.HEADER_IDENTITY: "forged.mac"})
        assert res.status_code == 401

    def test_a_service_credential_cannot_read_sessions(self, client):
        """The watcher has no business reading a user's conversations."""
        res = client.get("/sessions", headers={ident.HEADER_IDENTITY: ident.sign_service("tool-server")})
        assert res.status_code == 401

    def test_the_body_cannot_choose_the_owner(self, client):
        """`user_id` is not in the request model at all.

        The previous design took it from the body and verified nothing; this is the fix.
        """
        res = client.post(
            "/sessions",
            json={"symbol": "X", "profile": "INTRADAY", "timeframe": "10m", "user_id": "victim"},
            headers=_auth("alice"),
        )
        assert res.status_code == 201
        # The extra field is ignored, and the session belongs to the asserted caller.
        listed = client.get("/sessions", headers=_auth("victim")).json()
        assert listed["items"] == []
        assert len(client.get("/sessions", headers=_auth("alice")).json()["items"]) == 1


# ── Create ────────────────────────────────────────────────────────────────────


class TestCreate:
    def test_happy_path(self, client):
        res = _create(client, symbol="reliance", profile="intraday", timeframe="10m")
        assert res.status_code == 201
        body = res.json()
        assert body["session_id"].startswith("sess_")
        assert body["symbol"] == "RELIANCE"
        assert body["profile"] == "INTRADAY"
        assert body["status"] == "active"
        assert body["title"] is None
        assert body["message_count"] == 0
        assert body["last_run"] is None

    def test_the_summary_carries_what_a_tab_needs(self, client):
        """`RELIANCE - 10m - 10:31` must render from the session row alone.

        Otherwise a reopened session cannot label its tab until its messages load.
        """
        body = _create(client).json()
        for field in ("symbol", "timeframe", "profile", "updated_at", "status", "title"):
            assert field in body, field

    @pytest.mark.parametrize("profile", ["INTRADAY", "SWING", "INVESTOR", "FNO", "fno"])
    def test_valid_profiles(self, client, profile):
        assert _create(client, profile=profile).status_code == 201

    @pytest.mark.parametrize("profile", ["SCALPING", "", "  ", "INTRADAY2", "x" * 40])
    def test_an_invalid_profile_is_422(self, client, profile):
        """Profile is immutable and changes what the agent does, so a typo is permanent."""
        assert _create(client, profile=profile).status_code == 422

    @pytest.mark.parametrize("field", ["symbol", "timeframe"])
    @pytest.mark.parametrize("bad", ["", "   ", "\x00", "a\x00b", "line\nbreak", "x" * 200])
    def test_malformed_symbol_or_timeframe_is_422_not_500(self, client, field, bad):
        """A NUL used to reach SQLite, trip a CHECK, and surface as a 500.

        `'\\x00'.strip()` is truthy in Python but SQLite's `length('\\x00')` is 0.
        """
        assert _create(client, **{field: bad}).status_code == 422

    def test_an_unusual_but_plausible_timeframe_is_accepted(self, client):
        """Shape validation only, on purpose.

        The authoritative timeframe vocabulary lives in the charting layer; a second copy
        here would drift, and a stale allowlist refusing a legitimate instrument is worse
        than accepting an odd string the agent degrades on honestly.
        """
        for tf in ("1m", "3m", "10m", "1h", "4h", "1d", "1w", "45m"):
            assert _create(client, timeframe=tf).status_code == 201

    def test_an_fno_tradingsymbol_is_accepted(self, client):
        assert _create(client, symbol="RELIANCE26AUG1290CE", profile="FNO").status_code == 201

    def test_a_title_may_be_supplied(self, client):
        assert _create(client, title="Breakout idea").json()["title"] == "Breakout idea"

    def test_the_same_symbol_can_have_many_sessions(self, client):
        """The headline product fix: the old `${SYMBOL}::${PROFILE}` key collided."""
        for tf in ("10m", "5m", "10m"):
            assert _create(client, timeframe=tf).status_code == 201
        assert len(client.get("/sessions", headers=_auth("alice")).json()["items"]) == 3


# ── Read + pagination ─────────────────────────────────────────────────────────


class TestRead:
    def test_get_round_trips(self, client):
        created = _create(client).json()
        got = client.get(f"/sessions/{created['session_id']}", headers=_auth("alice")).json()
        assert got["session_id"] == created["session_id"]

    def test_an_unknown_session_is_404(self, client):
        assert client.get("/sessions/sess_NOPE", headers=_auth("alice")).status_code == 404

    def test_list_is_newest_activity_first(self, client):
        ids = [_create(client, timeframe=f"{i}m").json()["session_id"] for i in (1, 2, 3)]
        listed = client.get("/sessions", headers=_auth("alice")).json()["items"]
        assert [i["session_id"] for i in listed] == list(reversed(ids))

    def test_pagination_covers_every_session_exactly_once(self, client):
        made = {_create(client, timeframe=f"{i}m").json()["session_id"] for i in range(1, 13)}
        seen, cursor = [], None
        for _ in range(10):
            params = {"limit": 4}
            if cursor:
                params["cursor"] = cursor
            page = client.get("/sessions", params=params, headers=_auth("alice")).json()
            seen.extend(i["session_id"] for i in page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert cursor is None
        assert len(seen) == len(set(seen)) == len(made)
        assert set(seen) == made

    def test_search_filters_by_symbol_and_title(self, client):
        a = _create(client, symbol="RELIANCE").json()
        b = _create(client, symbol="TCS").json()
        client.patch(f"/sessions/{b['session_id']}", json={"title": "Momentum"}, headers=_auth("alice"))
        by_symbol = client.get("/sessions", params={"q": "relian"}, headers=_auth("alice")).json()
        assert [i["session_id"] for i in by_symbol["items"]] == [a["session_id"]]
        by_title = client.get("/sessions", params={"q": "momentum"}, headers=_auth("alice")).json()
        assert [i["session_id"] for i in by_title["items"]] == [b["session_id"]]

    @pytest.mark.parametrize("status", ["deleted", "bogus", ""])
    def test_an_unlistable_status_is_422(self, client, status):
        """`deleted` is not listable and the store's "every status" mode is not exposed."""
        res = client.get("/sessions", params={"status": status}, headers=_auth("alice"))
        assert res.status_code == 422

    @pytest.mark.parametrize("limit", [0, -1, 101, 10_000])
    def test_an_out_of_range_limit_is_refused_by_the_query_validator(self, client, limit):
        res = client.get("/sessions", params={"limit": limit}, headers=_auth("alice"))
        assert res.status_code == 422


# ── Patch ─────────────────────────────────────────────────────────────────────


class TestPatch:
    def test_rename(self, client):
        s = _create(client).json()
        res = client.patch(f"/sessions/{s['session_id']}", json={"title": "My idea"}, headers=_auth("alice"))
        assert res.status_code == 200
        assert res.json()["title"] == "My idea"

    def test_a_null_title_clears_the_rename(self, client):
        """`null` must clear, not read as "unchanged".

        This is why the handler inspects `model_fields_set` rather than truthiness.
        """
        s = _create(client, title="Named").json()
        res = client.patch(f"/sessions/{s['session_id']}", json={"title": None}, headers=_auth("alice"))
        assert res.json()["title"] is None

    def test_timeframe_is_mutable(self, client):
        s = _create(client, timeframe="10m").json()
        res = client.patch(f"/sessions/{s['session_id']}", json={"timeframe": "5m"}, headers=_auth("alice"))
        assert res.json()["timeframe"] == "5m"

    @pytest.mark.parametrize("field,value", [("symbol", "TCS"), ("profile", "SWING")])
    def test_an_immutable_field_is_409(self, client, field, value):
        """409, not 422: the request is well-formed, the operation does not exist.

        Answered explicitly so a client is never left believing it succeeded.
        """
        s = _create(client).json()
        res = client.patch(f"/sessions/{s['session_id']}", json={field: value}, headers=_auth("alice"))
        assert res.status_code == 409
        assert field in res.json()["detail"]
        # And nothing changed.
        assert client.get(f"/sessions/{s['session_id']}", headers=_auth("alice")).json()[field] == s[field]

    def test_an_empty_patch_is_a_no_op(self, client):
        s = _create(client).json()
        res = client.patch(f"/sessions/{s['session_id']}", json={}, headers=_auth("alice"))
        assert res.status_code == 200
        assert res.json()["session_id"] == s["session_id"]

    def test_status_can_archive_and_reopen(self, client):
        s = _create(client).json()
        archived = client.patch(
            f"/sessions/{s['session_id']}", json={"status": "archived"}, headers=_auth("alice")
        ).json()
        assert archived["status"] == "archived"
        assert archived["archived_at"] is not None
        assert client.get("/sessions", headers=_auth("alice")).json()["items"] == []
        assert len(
            client.get("/sessions", params={"status": "archived"}, headers=_auth("alice")).json()["items"]
        ) == 1
        reopened = client.patch(
            f"/sessions/{s['session_id']}", json={"status": "active"}, headers=_auth("alice")
        ).json()
        assert reopened["status"] == "active"
        assert reopened["archived_at"] is None

    def test_status_cannot_be_used_to_delete(self, client):
        s = _create(client).json()
        res = client.patch(
            f"/sessions/{s['session_id']}", json={"status": "deleted"}, headers=_auth("alice")
        )
        assert res.status_code == 422
        assert "DELETE" in res.json()["detail"]

    def test_patching_an_unknown_session_is_404(self, client):
        res = client.patch("/sessions/sess_NOPE", json={"title": "x"}, headers=_auth("alice"))
        assert res.status_code == 404

    def test_active_run_id_must_belong_to_this_session(self, client):
        """A cross-session grounding target would make every later Q&A answer about
        the wrong analysis. Correctness, not security — cross-USER is already impossible.
        """
        s = _create(client).json()
        res = client.patch(
            f"/sessions/{s['session_id']}", json={"active_run_id": "run_NOPE"}, headers=_auth("alice")
        )
        assert res.status_code == 422
        assert "not a run of this session" in res.json()["detail"]


# ── Delete ────────────────────────────────────────────────────────────────────


class TestDelete:
    def test_soft_delete_by_default(self, client):
        s = _create(client).json()
        res = client.delete(f"/sessions/{s['session_id']}", headers=_auth("alice"))
        assert res.status_code == 200
        assert res.json()["status"] == "deleted"
        assert res.json()["hard"] is False
        # A deleted session is gone from every listable view and reads as 404.
        assert client.get(f"/sessions/{s['session_id']}", headers=_auth("alice")).status_code == 404
        assert client.get("/sessions", headers=_auth("alice")).json()["items"] == []

    def test_hard_delete(self, client):
        s = _create(client).json()
        res = client.delete(
            f"/sessions/{s['session_id']}", params={"hard": True}, headers=_auth("alice")
        )
        assert res.status_code == 200
        assert res.json()["hard"] is True

    def test_deleting_an_unknown_session_is_404(self, client):
        assert client.delete("/sessions/sess_NOPE", headers=_auth("alice")).status_code == 404

    def test_a_deleted_session_cannot_be_reopened_by_patch(self, client):
        """A conversation the user deleted must stay deleted.

        This failed when written: the handler inspected the POST-update row, and
        `status: active` had already been written by then, so the check passed and the
        session came back. An automatic client retry could have done it with nobody
        asking. Fixed by reading before patching.
        """
        s = _create(client).json()
        client.delete(f"/sessions/{s['session_id']}", headers=_auth("alice"))
        res = client.patch(
            f"/sessions/{s['session_id']}", json={"status": "active"}, headers=_auth("alice")
        )
        assert res.status_code == 404
        assert client.get("/sessions", headers=_auth("alice")).json()["items"] == []

    def test_a_deleted_session_cannot_be_renamed_either(self, client):
        """Same pre-read; any patch on a deleted session is 404."""
        s = _create(client).json()
        client.delete(f"/sessions/{s['session_id']}", headers=_auth("alice"))
        assert client.patch(
            f"/sessions/{s['session_id']}", json={"title": "back?"}, headers=_auth("alice")
        ).status_code == 404

    def test_deleting_twice_is_idempotent(self, client):
        """A retried delete is not an error — the outcome the caller wanted holds."""
        s = _create(client).json()
        assert client.delete(f"/sessions/{s['session_id']}", headers=_auth("alice")).status_code == 200
        assert client.delete(f"/sessions/{s['session_id']}", headers=_auth("alice")).status_code == 200


# ── Messages / runs / events ──────────────────────────────────────────────────


class TestChildCollections:
    def test_messages_start_empty_and_paginate_by_seq(self, client):
        import session_store as store

        s = _create(client).json()
        for i in range(5):
            store.create_message(
                session_id=s["session_id"], role="user", kind="qa_question",
                status="complete", content=f"q{i}",
            )
        body = client.get(f"/sessions/{s['session_id']}/messages", headers=_auth("alice")).json()
        assert [m["seq"] for m in body["items"]] == [1, 2, 3, 4, 5]
        assert body["last_seq"] == 5

        page = client.get(
            f"/sessions/{s['session_id']}/messages",
            params={"after_seq": 3, "limit": 1},
            headers=_auth("alice"),
        ).json()
        assert [m["seq"] for m in page["items"]] == [4]
        assert page["last_seq"] == 5, "last_seq is the session's max, not the page's"

    def test_runs_and_events_round_trip(self, client):
        import session_store as store

        s = _create(client).json()
        run = store.create_run(
            session_id=s["session_id"], user_id="alice", kind="find",
            symbol="RELIANCE", timeframe="10m", profile="INTRADAY",
        )
        levels = {"entry": 2470.0, "stop_loss": 2435.0, "take_profit": 2550.0}
        store.append_run_events(
            run["run_id"],
            [("RUN_STARTED", {"thread_id": run["thread_id"]}),
             ("DECISION", {"action": "BUY", "execution_levels": levels})],
        )

        runs = client.get(f"/sessions/{s['session_id']}/runs", headers=_auth("alice")).json()
        assert [r["run_id"] for r in runs["items"]] == [run["run_id"]]

        events = client.get(f"/runs/{run['run_id']}/events", headers=_auth("alice")).json()
        assert [e["event"] for e in events["items"]] == ["RUN_STARTED", "DECISION"]
        assert events["last_seq"] == 2
        # Structure must survive: DECISION.execution_levels is not prose.
        assert events["items"][1]["data"]["execution_levels"] == levels

    def test_after_seq_replays_only_the_gap(self, client):
        """The read side of gap-free reattach."""
        import session_store as store

        s = _create(client).json()
        run = store.create_run(
            session_id=s["session_id"], user_id="alice", kind="find",
            symbol="RELIANCE", timeframe="10m", profile="INTRADAY",
        )
        store.append_run_events(run["run_id"], [(f"E{i}", {"i": i}) for i in range(1, 6)])
        body = client.get(
            f"/runs/{run['run_id']}/events", params={"after_seq": 3}, headers=_auth("alice")
        ).json()
        assert [e["event"] for e in body["items"]] == ["E4", "E5"]
        assert body["last_seq"] == 5

    def test_a_run_summary_appears_on_the_session(self, client):
        """The history row shows what the last run concluded."""
        import session_store as store

        s = _create(client).json()
        run = store.create_run(
            session_id=s["session_id"], user_id="alice", kind="find",
            symbol="RELIANCE", timeframe="10m", profile="INTRADAY",
        )
        store.finalize_run(run["run_id"], store.RUN_COMPLETE)
        body = client.get(f"/sessions/{s['session_id']}", headers=_auth("alice")).json()
        assert body["active_run_id"] == run["run_id"]
        assert body["last_run"]["status"] == "complete"
        assert body["last_run"]["kind"] == "find"

    def test_an_unknown_run_is_404(self, client):
        assert client.get("/runs/run_NOPE/events", headers=_auth("alice")).status_code == 404

    def test_messages_and_runs_of_an_unknown_session_are_404(self, client):
        assert client.get("/sessions/sess_NOPE/messages", headers=_auth("alice")).status_code == 404
        assert client.get("/sessions/sess_NOPE/runs", headers=_auth("alice")).status_code == 404


# ── The cross-user matrix ─────────────────────────────────────────────────────


class TestCrossUserAccess:
    """Every route, attempted by a non-owner, must be 404.

    Not 403 — that confirms the id exists and turns each endpoint into an enumeration
    oracle. Not an empty 200 — that is indistinguishable from a genuine empty result, so
    a caller could not tell "not yours" from "nothing here", and a client bug would look
    like a permissions bug.
    """

    @pytest.fixture
    def alice_session(self, client):
        import session_store as store

        s = _create(client, user="alice", title="Alice's private idea").json()
        run = store.create_run(
            session_id=s["session_id"], user_id="alice", kind="find",
            symbol="RELIANCE", timeframe="10m", profile="INTRADAY",
        )
        store.create_message(
            session_id=s["session_id"], role="user", kind="qa_question",
            status="complete", content="alice's secret question",
        )
        store.append_run_events(run["run_id"], [("REASONING", {"content": "alice's reasoning"})])
        return s, run

    def test_read_is_404(self, client, alice_session):
        s, _ = alice_session
        res = client.get(f"/sessions/{s['session_id']}", headers=_auth("bob"))
        assert res.status_code == 404

    def test_the_404_body_leaks_nothing(self, client, alice_session):
        s, _ = alice_session
        res = client.get(f"/sessions/{s['session_id']}", headers=_auth("bob"))
        assert res.json() == {"detail": "session not found"}
        assert "Alice" not in res.text and "RELIANCE" not in res.text

    def test_a_non_owned_session_is_indistinguishable_from_a_missing_one(self, client, alice_session):
        s, _ = alice_session
        real = client.get(f"/sessions/{s['session_id']}", headers=_auth("bob"))
        fake = client.get("/sessions/sess_TOTALLYMADEUP000000000000", headers=_auth("bob"))
        assert real.status_code == fake.status_code == 404
        assert real.json() == fake.json(), "the two must not be distinguishable"

    def test_list_excludes_it(self, client, alice_session):
        assert client.get("/sessions", headers=_auth("bob")).json()["items"] == []

    def test_rename_is_404_and_changes_nothing(self, client, alice_session):
        s, _ = alice_session
        assert client.patch(
            f"/sessions/{s['session_id']}", json={"title": "pwned"}, headers=_auth("bob")
        ).status_code == 404
        assert client.get(
            f"/sessions/{s['session_id']}", headers=_auth("alice")
        ).json()["title"] == "Alice's private idea"

    def test_archive_is_404_and_changes_nothing(self, client, alice_session):
        s, _ = alice_session
        assert client.patch(
            f"/sessions/{s['session_id']}", json={"status": "archived"}, headers=_auth("bob")
        ).status_code == 404
        assert client.get(
            f"/sessions/{s['session_id']}", headers=_auth("alice")
        ).json()["status"] == "active"

    def test_delete_is_404_and_deletes_nothing(self, client, alice_session):
        s, _ = alice_session
        assert client.delete(
            f"/sessions/{s['session_id']}", params={"hard": True}, headers=_auth("bob")
        ).status_code == 404
        alive = client.get(f"/sessions/{s['session_id']}", headers=_auth("alice"))
        assert alive.status_code == 200
        assert alive.json()["message_count"] == 1

    def test_messages_are_404_not_an_empty_page(self, client, alice_session):
        s, _ = alice_session
        res = client.get(f"/sessions/{s['session_id']}/messages", headers=_auth("bob"))
        assert res.status_code == 404
        assert "secret" not in res.text

    def test_runs_are_404_not_an_empty_list(self, client, alice_session):
        s, _ = alice_session
        assert client.get(f"/sessions/{s['session_id']}/runs", headers=_auth("bob")).status_code == 404

    def test_run_events_are_404(self, client, alice_session):
        _, run = alice_session
        res = client.get(f"/runs/{run['run_id']}/events", headers=_auth("bob"))
        assert res.status_code == 404
        assert "reasoning" not in res.text.lower()

    def test_bob_cannot_retarget_alices_run_as_his_grounding(self, client, alice_session):
        """Cross-user grounding would answer Bob's questions from Alice's analysis."""
        _, alice_run = alice_session
        bob = _create(client, user="bob").json()
        res = client.patch(
            f"/sessions/{bob['session_id']}",
            json={"active_run_id": alice_run["run_id"]},
            headers=_auth("bob"),
        )
        assert res.status_code == 422
        assert client.get(
            f"/sessions/{bob['session_id']}", headers=_auth("bob")
        ).json()["active_run_id"] is None

    def test_two_users_sessions_are_fully_independent(self, client):
        a = _create(client, user="alice", symbol="RELIANCE").json()
        b = _create(client, user="bob", symbol="RELIANCE").json()
        assert a["session_id"] != b["session_id"]
        assert [i["session_id"] for i in client.get("/sessions", headers=_auth("alice")).json()["items"]] == [a["session_id"]]
        assert [i["session_id"] for i in client.get("/sessions", headers=_auth("bob")).json()["items"]] == [b["session_id"]]
