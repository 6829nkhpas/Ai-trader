"""Internal_Identity tests — the boundary that replaces a self-asserted user_id.

The property that matters: **no input a caller controls can produce a verified
subject unless it was minted with the secret.** Everything else (expiry, skew,
size caps, the service/user key split) is a supporting refusal.

Every test sets its own secrets via monkeypatch, so none of them depend on the
ambient environment — a suite that passes only because a developer exported a secret
is not a test of anything.
"""

from __future__ import annotations

import base64
import json
import os
import time
from unittest import mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import internal_identity as ident

SECRET = "a" * 64
OTHER_SECRET = "b" * 64
SERVICE_SECRET = "c" * 64


@pytest.fixture(autouse=True)
def _reset_warn_state():
    """The unenforced warning is once-per-process; reset it so tests are order-free."""
    ident._warned_unenforced = False
    yield
    ident._warned_unenforced = False


@pytest.fixture
def secrets(monkeypatch):
    monkeypatch.setenv(ident.ENV_IDENTITY_SECRET, SECRET)
    monkeypatch.setenv(ident.ENV_SERVICE_SECRET, SERVICE_SECRET)
    monkeypatch.delenv(ident.ENV_REQUIRE_IDENTITY, raising=False)


# ── Round trip ────────────────────────────────────────────────────────────────


def test_round_trip(secrets):
    token = ident.sign_identity("user_42")
    assert ident.verify_identity_token(token) == "user_42"


def test_round_trip_trims(secrets):
    assert ident.verify_identity_token(ident.sign_identity("  user_42  ")) == "user_42"


def test_signing_an_empty_user_is_refused(secrets):
    for value in ("", "   ", None):
        with pytest.raises(ident.IdentityError):
            ident.sign_identity(value)  # type: ignore[arg-type]


def test_token_shape(secrets):
    token = ident.sign_identity("u")
    payload, mac = token.split(".")
    assert "=" not in token, "padding must be stripped so the token is header-safe"
    assert json.loads(ident._b64u_decode(payload))["sub"] == "u"
    assert mac


# ── Forgery ───────────────────────────────────────────────────────────────────


def test_tampered_payload_is_refused(secrets):
    """The whole point: editing `sub` must not change who you are."""
    token = ident.sign_identity("user_a")
    _, mac = token.split(".")
    forged_payload = ident._b64u_encode(
        json.dumps({"sub": "user_b", "iat": time.time(), "exp": time.time() + 60}).encode()
    )
    with pytest.raises(ident.IdentityError):
        ident.verify_identity_token(f"{forged_payload}.{mac}")


def test_tampered_mac_is_refused(secrets):
    payload, mac = ident.sign_identity("user_a").split(".")
    flipped = ("A" if mac[0] != "A" else "B") + mac[1:]
    with pytest.raises(ident.IdentityError):
        ident.verify_identity_token(f"{payload}.{flipped}")


def test_wrong_secret_is_refused(secrets, monkeypatch):
    token = ident.sign_identity("user_a")
    monkeypatch.setenv(ident.ENV_IDENTITY_SECRET, OTHER_SECRET)
    with pytest.raises(ident.IdentityError):
        ident.verify_identity_token(token)


def test_unsigned_payload_is_refused(secrets):
    """A payload with no MAC, and one with an empty MAC, are both refusals."""
    payload = ident._b64u_encode(json.dumps({"sub": "x", "iat": 0, "exp": 9e9}).encode())
    for candidate in (payload, f"{payload}.", f".{payload}"):
        with pytest.raises(ident.IdentityError):
            ident.verify_identity_token(candidate)


def test_service_token_cannot_authenticate_a_user(secrets):
    """The key split is structural, not conventional.

    A compromised watcher must not be able to mint a user identity.
    """
    svc = ident.sign_service("tool-server")
    with pytest.raises(ident.IdentityError):
        ident.verify_identity_token(svc)


def test_user_token_cannot_authenticate_a_service(secrets):
    user = ident.sign_identity("user_a")
    with pytest.raises(ident.IdentityError):
        ident.verify_service_token(user)


# `monkeypatch` is function-scoped and Hypothesis (correctly) refuses to let a
# @given test rely on it, since it is not reset between generated inputs. A context
# manager inside the test is the fix the health check asks for — suppressing the
# check would be papering over a real footgun.
def _with_secret():
    return mock.patch.dict(os.environ, {ident.ENV_IDENTITY_SECRET: SECRET}, clear=False)


@given(st.text(max_size=200))
@settings(max_examples=300, deadline=None)
def test_no_arbitrary_string_verifies(candidate):
    """Property: nothing a caller can type verifies.

    The single most important assertion in this module.
    """
    with _with_secret():
        with pytest.raises(ident.IdentityError):
            ident.verify_identity_token(candidate)


@given(st.binary(max_size=120))
@settings(max_examples=200, deadline=None)
def test_no_arbitrary_payload_with_a_random_mac_verifies(blob):
    with _with_secret():
        payload = ident._b64u_encode(blob)
        mac = ident._b64u_encode(blob[::-1] or b"x")
        with pytest.raises(ident.IdentityError):
            ident.verify_identity_token(f"{payload}.{mac}")


# ── Time ──────────────────────────────────────────────────────────────────────


def test_expired_is_refused(secrets):
    token = ident.sign_identity("u", ttl=10, now=1_000_000)
    # Beyond exp AND beyond the skew allowance.
    with pytest.raises(ident.IdentityError):
        ident.verify_identity_token(token, now=1_000_000 + 10 + ident.CLOCK_SKEW_SECONDS + 1)


def test_within_skew_of_expiry_is_accepted(secrets):
    """A peer a few seconds behind must not fail every request."""
    token = ident.sign_identity("u", ttl=10, now=1_000_000)
    assert ident.verify_identity_token(token, now=1_000_000 + 10 + 1) == "u"


def test_future_issue_time_is_refused(secrets):
    token = ident.sign_identity("u", ttl=3600, now=2_000_000)
    with pytest.raises(ident.IdentityError):
        ident.verify_identity_token(token, now=2_000_000 - ident.CLOCK_SKEW_SECONDS - 5)


@pytest.mark.parametrize("claims", [
    {"sub": "u", "iat": 0},                       # no exp
    {"sub": "u", "exp": 9e9},                     # no iat
    {"sub": "u", "iat": 0, "exp": "soon"},        # non-numeric exp
    {"sub": "u", "iat": "now", "exp": 9e9},       # non-numeric iat
    {"sub": "u", "iat": True, "exp": 9e9},        # bool is not a timestamp
    {"iat": 0, "exp": 9e9},                       # no sub
    {"sub": "", "iat": 0, "exp": 9e9},            # empty sub
    {"sub": "   ", "iat": 0, "exp": 9e9},         # blank sub
    {"sub": 42, "iat": 0, "exp": 9e9},            # non-string sub
])
def test_malformed_claims_are_refused(secrets, claims):
    """A correctly-MAC'd token with bad claims is still a refusal.

    These are signed with the real secret, so only the claim validation can catch
    them — which is exactly why the checks are not "belt and braces".
    """
    token = ident._sign(SECRET, claims)
    with pytest.raises(ident.IdentityError):
        ident.verify_identity_token(token)


def test_non_object_payload_is_refused(secrets):
    token = ident._sign(SECRET, ["not", "an", "object"])  # type: ignore[arg-type]
    with pytest.raises(ident.IdentityError):
        ident.verify_identity_token(token)


# ── Input hardening ───────────────────────────────────────────────────────────


def test_oversized_token_is_refused_before_parsing(secrets):
    huge = "a" * (ident.MAX_TOKEN_CHARS + 1) + ".b"
    with pytest.raises(ident.IdentityError, match="too large"):
        ident.verify_identity_token(huge)


@pytest.mark.parametrize("token", [None, "", "   ", "onlyonepart", "a.b.c"])
def test_structurally_invalid_tokens(secrets, token):
    with pytest.raises(ident.IdentityError):
        ident.verify_identity_token(token)


@pytest.mark.parametrize("token", [
    "\x80.0",        # non-ASCII payload  -> was UnicodeEncodeError (a 500, not a 401)
    "0.\x80",        # non-ASCII MAC      -> was TypeError in compare_digest
    "0.\u00ff",
    "café.abc",
    "abc.café",
    "a b.cd",        # embedded space
    "ab\n.cd",       # embedded newline
    "ab=.cd",        # padding is stripped by the minter, so it is not in the alphabet
])
def test_non_ascii_and_out_of_alphabet_tokens_refuse_cleanly(secrets, token):
    """Every malformed header must land on IdentityError, never an unhandled exception.

    Found by `test_no_arbitrary_string_verifies`: without the alphabet guard the first
    two of these raised out of the auth path, which FastAPI turns into a 500 where a
    401 belongs. Pinned as explicit cases so a future refactor cannot lose it and
    still pass the property test by luck of the draw.
    """
    with pytest.raises(ident.IdentityError):
        ident.verify_identity_token(token)


@pytest.mark.parametrize("raw", [b"\xfb\xff\xbf", b"\xff\xef\xfe", b"\x3e\x3f\xff"])
def test_standard_base64_alphabet_is_refused(secrets, raw):
    """`+` and `/` must not be accepted anywhere in the token.

    These byte triples encode to standard base64 containing `+` and/or `/`. Two
    independent guards refuse them — the alphabet check before the MAC, and
    `b64decode(..., validate=True)` at the decode. The alphabet check is what fires
    here; the strict decode stays as defence in depth, because `urlsafe_b64decode`
    silently *ignores* out-of-alphabet characters and would decode to something.
    """
    payload = base64.b64encode(raw).decode().rstrip("=")
    assert "+" in payload or "/" in payload, "fixture must exercise the standard alphabet"
    with pytest.raises(ident.IdentityError):
        ident.verify_identity_token(f"{payload}.{ident._mac(SECRET, payload)}")


def test_whitespace_inside_the_payload_is_refused(secrets):
    """A lenient decoder ignores embedded whitespace; this one must not."""
    payload = ident._b64u_encode(json.dumps({"sub": "u", "iat": 0, "exp": 9e9}).encode())
    mangled = payload[:4] + " " + payload[4:]
    with pytest.raises(ident.IdentityError):
        ident.verify_identity_token(f"{mangled}.{ident._mac(SECRET, mangled)}")


# ── Secret configuration ──────────────────────────────────────────────────────


def test_missing_secret_is_an_error_not_a_bypass(monkeypatch):
    monkeypatch.delenv(ident.ENV_IDENTITY_SECRET, raising=False)
    with pytest.raises(ident.IdentityError, match="not configured"):
        ident.verify_identity_token("anything.atall")


def test_short_secret_is_refused(monkeypatch):
    monkeypatch.setenv(ident.ENV_IDENTITY_SECRET, "tooshort")
    with pytest.raises(ident.IdentityError, match="too short"):
        ident.sign_identity("u")


def test_startup_config_passes_when_unenforced(monkeypatch):
    monkeypatch.delenv(ident.ENV_IDENTITY_SECRET, raising=False)
    monkeypatch.delenv(ident.ENV_REQUIRE_IDENTITY, raising=False)
    ident.assert_startup_config()  # must not raise: local dev


def test_startup_config_refuses_enforcement_without_a_secret(monkeypatch):
    """Enforcement on + no secret must fail LOUDLY at startup.

    Otherwise every request fails closed at the boundary and the deployment looks
    like an unrelated total outage.
    """
    monkeypatch.setenv(ident.ENV_REQUIRE_IDENTITY, "1")
    monkeypatch.delenv(ident.ENV_IDENTITY_SECRET, raising=False)
    monkeypatch.setenv(ident.ENV_SERVICE_SECRET, SERVICE_SECRET)
    with pytest.raises(RuntimeError, match="Refusing to start"):
        ident.assert_startup_config()


def test_startup_config_requires_the_service_secret_too(monkeypatch):
    monkeypatch.setenv(ident.ENV_REQUIRE_IDENTITY, "1")
    monkeypatch.setenv(ident.ENV_IDENTITY_SECRET, SECRET)
    monkeypatch.delenv(ident.ENV_SERVICE_SECRET, raising=False)
    with pytest.raises(RuntimeError):
        ident.assert_startup_config()


def test_startup_config_passes_when_fully_configured(monkeypatch):
    monkeypatch.setenv(ident.ENV_REQUIRE_IDENTITY, "1")
    monkeypatch.setenv(ident.ENV_IDENTITY_SECRET, SECRET)
    monkeypatch.setenv(ident.ENV_SERVICE_SECRET, SERVICE_SECRET)
    ident.assert_startup_config()


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("nonsense", False),
])
def test_enforcement_switch_parsing(monkeypatch, value, expected):
    monkeypatch.setenv(ident.ENV_REQUIRE_IDENTITY, value)
    assert ident.enforcement_enabled() is expected


# ── FastAPI dependencies ──────────────────────────────────────────────────────


class FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


def test_resolve_user_falls_back_to_body_when_unenforced(secrets):
    """Phase 1 must change nothing observable."""
    assert ident.resolve_user(FakeRequest(), "user_body") == "user_body"


def test_resolve_user_prefers_a_valid_assertion_even_when_unenforced(secrets):
    """Exercise the real path before enforcing it, so the flip is not the first test."""
    token = ident.sign_identity("user_header")
    req = FakeRequest({ident.HEADER_IDENTITY: token})
    assert ident.resolve_user(req, "user_body") == "user_header"


def test_resolve_user_ignores_a_bad_assertion_when_unenforced(secrets, capsys):
    """A mismatched secret must be visible BEFORE the flag is flipped."""
    req = FakeRequest({ident.HEADER_IDENTITY: "garbage.mac"})
    assert ident.resolve_user(req, "user_body") == "user_body"
    assert "unverifiable assertion" in capsys.readouterr().out


def test_resolve_user_returns_none_for_no_identity_at_all(secrets):
    assert ident.resolve_user(FakeRequest(), None) is None
    assert ident.resolve_user(FakeRequest(), "  ") is None


def test_the_unenforced_warning_only_fires_when_a_body_id_is_actually_trusted(secrets, capsys):
    """A warning that is wrong teaches people to ignore the channel.

    `/sessions` calls resolve_user with no body fallback and 401s without an assertion, so
    warning there said the opposite of the truth — that the one surface which always
    requires an assertion was unguarded. Observed in a real startup log.
    """
    assert ident.resolve_user(FakeRequest(), None, surface="/sessions") is None
    assert "is trusting the caller-supplied user_id" not in capsys.readouterr().out

    ident._warned_unenforced = False
    assert ident.resolve_user(FakeRequest(), "u", surface="/run") == "u"
    assert "is trusting the caller-supplied user_id" in capsys.readouterr().out


def test_the_unenforced_warning_is_ascii(secrets, capsys):
    """Read through `docker compose logs`; an em-dash arrives as mojibake."""
    ident.resolve_user(FakeRequest(), "u", surface="/run")
    out = capsys.readouterr().out
    assert out.isascii(), repr([c for c in out if not c.isascii()])


def test_resolve_user_enforced_requires_the_header(secrets, monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setenv(ident.ENV_REQUIRE_IDENTITY, "1")
    with pytest.raises(HTTPException) as exc:
        ident.resolve_user(FakeRequest(), "user_body")
    assert exc.value.status_code == 401


def test_resolve_user_enforced_ignores_the_body(secrets, monkeypatch):
    """The whole point of the migration: a body user_id buys nothing."""
    from fastapi import HTTPException

    monkeypatch.setenv(ident.ENV_REQUIRE_IDENTITY, "1")
    with pytest.raises(HTTPException):
        ident.resolve_user(FakeRequest(), "administrator")


def test_resolve_user_enforced_accepts_a_valid_assertion(secrets, monkeypatch):
    monkeypatch.setenv(ident.ENV_REQUIRE_IDENTITY, "1")
    req = FakeRequest({ident.HEADER_IDENTITY: ident.sign_identity("user_x")})
    assert ident.resolve_user(req, "user_body") == "user_x"


def test_401_detail_leaks_nothing(secrets, monkeypatch):
    """Do not tell an unauthenticated caller which check failed."""
    from fastapi import HTTPException

    monkeypatch.setenv(ident.ENV_REQUIRE_IDENTITY, "1")
    for header in ({}, {ident.HEADER_IDENTITY: "junk"}, {ident.HEADER_IDENTITY: ident._sign(SECRET, {})}):
        with pytest.raises(HTTPException) as exc:
            ident.resolve_user(FakeRequest(header), None)
        assert exc.value.detail == "authentication required"


def test_require_service_enforced(secrets, monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setenv(ident.ENV_REQUIRE_IDENTITY, "1")
    with pytest.raises(HTTPException):
        ident.require_service(FakeRequest())
    req = FakeRequest({ident.HEADER_SERVICE: ident.sign_service("tool-server")})
    assert ident.require_service(req) == "tool-server"


def test_require_service_unenforced_allows_the_watcher_through(secrets):
    """The watcher must keep working while the flag is off — it is a hard requirement."""
    assert ident.require_service(FakeRequest()) is None


# ── Cross-language vector ─────────────────────────────────────────────────────


def test_reference_vector_is_stable():
    """Pins the wire format the TypeScript and Rust signers reproduce.

    If this changes, the other two implementations are now wrong and their own tests
    must be updated in the same commit — which is the point of pinning it.
    """
    payload, mac = ident.reference_vector()
    assert payload == "eyJleHAiOjE3MDAwMDAwNjAsImlhdCI6MTcwMDAwMDAwMCwic3ViIjoidXNlcl9hYmMxMjMifQ"
    claims = json.loads(ident._b64u_decode(payload))
    assert claims == {"sub": "user_abc123", "iat": 1_700_000_000, "exp": 1_700_000_060}
    assert mac == ident._mac(ident.VECTOR_SECRET, payload)
    assert len(mac) == 43, "unpadded base64url of a 32-byte SHA-256 digest"
