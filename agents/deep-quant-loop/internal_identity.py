"""Internal_Identity — the verified-caller boundary for this service.

The problem
-----------
``user_id`` arrives in the REQUEST BODY (``RunRequest.user_id`` and friends) and
nothing verifies it. The browser puts it there. That is survivable while the service
only streams analysis, because a forged id buys you someone else's LLM key quota at
worst. It is NOT survivable once sessions, messages and transcripts are stored per
user: a self-asserted id would make ``GET /sessions/{id}`` readable by anyone willing
to change one JSON field.

The trust chain
---------------
::

    Browser
      │  httpOnly cookie pair on .stratai.live — JavaScript cannot read it
      ▼
    Next.js route handler        ← THE AUTHENTICATION BOUNDARY
      │  verifies the cookie against api-web /api/v1/users/me (it holds no signing
      │  key, so it asks the service that does), then MINTS a short-lived assertion
      │  X-StratAI-Identity: <payload>.<mac>
      ▼
    this module                  ← verifies the MAC, returns the subject
      │
      ▼
    session_store                 every read/write filters on that subject

Two secrets, deliberately separate:

* ``INTERNAL_IDENTITY_SECRET`` — the Next tier asserting *a user*.
* ``INTERNAL_SERVICE_SECRET``  — the headless Rust watcher asserting *itself* on
  ``/resume``. It has no user session and must never be able to mint a user
  identity; splitting the keys is what makes that structural rather than a
  convention. See ``require_service``.

Why a MAC and not a JWT library
-------------------------------
The payload is three fields travelling one hop across a private Docker network. A
JWT library brings algorithm negotiation with it, and ``alg: none`` / algorithm
confusion is the classic way that goes wrong. One hard-coded HMAC-SHA256 has no
negotiation to attack. stdlib only — no new dependency.

Why the MAC covers the received bytes
-------------------------------------
``verify`` HMACs the payload segment **exactly as it arrived** and never
re-serialises it. So the two implementations (Python here, TypeScript in
``frontend/src/app/api/_identity.ts``) do not have to agree on JSON key order,
whitespace or unicode escaping — a cross-language canonicalisation dependency that
would be a silent, intermittent 401 the first time one side reordered a key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional, Tuple

# ── Configuration ─────────────────────────────────────────────────────────────

ENV_IDENTITY_SECRET = "INTERNAL_IDENTITY_SECRET"
ENV_SERVICE_SECRET = "INTERNAL_SERVICE_SECRET"
ENV_REQUIRE_IDENTITY = "DEEP_QUANT_REQUIRE_IDENTITY"

HEADER_IDENTITY = "X-StratAI-Identity"
HEADER_SERVICE = "X-StratAI-Service"

# Assertions are minted per request and travel one hop, so the window only has to
# cover clock skew plus the request. 60s is generous for both.
DEFAULT_TTL_SECONDS = 60

# Tolerance for a clock ahead of ours. Without it, a peer a second fast fails every
# request; with too much, a captured assertion stays usable for that long.
CLOCK_SKEW_SECONDS = 30

# A well-formed assertion is ~150 bytes. The cap exists so a hostile header cannot
# make us base64-decode and JSON-parse a megabyte before rejecting it.
MAX_TOKEN_CHARS = 4096

# Minimum secret length, in characters. `openssl rand -hex 32` yields 64, which is
# the documented way to generate these. A short secret is a brute-forceable MAC, so
# this is enforced rather than advised.
MIN_SECRET_CHARS = 32


class IdentityError(Exception):
    """An assertion was absent, malformed, expired, or not authentic.

    Carries no detail about WHICH of those it was in its string form when surfaced
    to a caller — see ``require_user``. Distinguishing "bad signature" from
    "expired" for an unauthenticated caller is free information for someone probing
    the boundary.
    """


def _secret(env_name: str) -> str:
    value = (os.getenv(env_name) or "").strip()
    if not value:
        raise IdentityError(f"{env_name} is not configured")
    if len(value) < MIN_SECRET_CHARS:
        raise IdentityError(
            f"{env_name} is too short ({len(value)} chars, need >= {MIN_SECRET_CHARS}). "
            f"Generate one with: openssl rand -hex 32"
        )
    return value


def enforcement_enabled() -> bool:
    """Whether an unverified caller is refused.

    Read per call, not captured at import, so the flag can be flipped by a container
    restart rather than a rebuild — the same convention the feature switches use.

    Defaults to OFF so this module can ship and be exercised before the Next tier is
    minting assertions. ``assert_startup_config`` is what stops that default from
    quietly becoming production's posture.
    """
    return (os.getenv(ENV_REQUIRE_IDENTITY) or "0").strip().lower() in ("1", "true", "yes", "on")


def assert_startup_config() -> None:
    """Refuse to start with enforcement ON and no usable secret.

    Called from ``main`` at import. A session store guarded by an absent secret is
    worse than no session store: every request would fail closed at the boundary and
    the deployment would look like a total outage with an unrelated-looking cause.
    Failing at startup names the actual problem once, in the right place.

    Deliberately NOT lenient: there is no "warn and continue" branch, because that
    is how a deployment ends up serving with enforcement believed-on and actually-off.
    """
    if not enforcement_enabled():
        return
    for env_name in (ENV_IDENTITY_SECRET, ENV_SERVICE_SECRET):
        try:
            _secret(env_name)
        except IdentityError as exc:
            raise RuntimeError(
                f"{ENV_REQUIRE_IDENTITY} is on but {exc}. Set it on the deep-quant service "
                f"(and the matching value on the frontend / tool-server), or turn "
                f"{ENV_REQUIRE_IDENTITY} off. Refusing to start with an unguarded "
                f"session store."
            ) from exc


# ── Encoding ──────────────────────────────────────────────────────────────────


def _b64u_encode(raw: bytes) -> str:
    """Unpadded base64url. Padding is stripped so the token is URL/header-safe."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64u_decode(text: str) -> bytes:
    """Decode unpadded base64url, restoring the padding the encoder dropped.

    Strict on purpose. ``base64.urlsafe_b64decode`` accepts no ``validate`` flag and
    silently ignores characters outside its alphabet, so a payload carrying
    standard-base64 ``+``/``/`` (or stray whitespace) would decode to *something*
    rather than being refused. Two encodings that both "work" is how a MAC mismatch
    becomes an unreproducible bug, so this spells the decode out with
    ``altchars=b"-_"`` and ``validate=True``: the base64url alphabet, and nothing else.
    """
    pad = "=" * (-len(text) % 4)
    return base64.b64decode(text + pad, altchars=b"-_", validate=True)


# The base64url alphabet, and nothing else. Padding is stripped by `_b64u_encode`,
# so `=` is not in the set either.
_B64URL_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


def _require_b64url(segment: str, what: str) -> None:
    """Refuse a segment containing anything outside the base64url alphabet.

    This runs BEFORE the MAC is computed, and it is not merely tidiness — a property
    test found two genuine faults without it:

      * ``payload_segment.encode("ascii")`` raised ``UnicodeEncodeError`` on a
        non-ASCII byte, and
      * ``hmac.compare_digest`` raised ``TypeError`` ("comparing strings with
        non-ASCII characters is not supported") on a non-ASCII MAC segment.

    Both are unhandled exceptions inside the authentication path, i.e. a 500 where a
    401 belongs. A caller controls this header completely, so every malformed input
    must land on the same clean refusal.
    """
    if not segment or not _B64URL_ALPHABET.issuperset(segment):
        raise IdentityError(f"malformed assertion ({what} is not base64url)")


def _mac(secret: str, payload_segment: str) -> str:
    return _b64u_encode(
        hmac.new(secret.encode("utf-8"), payload_segment.encode("ascii"), hashlib.sha256).digest()
    )


def _sign(secret: str, claims: dict) -> str:
    # separators without spaces keeps the token compact; the verifier never
    # re-serialises, so the exact form here is not a contract.
    payload = _b64u_encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{payload}.{_mac(secret, payload)}"


def _verify(secret: str, token: Optional[str], *, now: Optional[float] = None) -> dict:
    """Verify ``token`` and return its claims. Raises ``IdentityError`` otherwise."""
    if not token or not token.strip():
        raise IdentityError("no assertion supplied")
    token = token.strip()
    if len(token) > MAX_TOKEN_CHARS:
        raise IdentityError("assertion too large")

    parts = token.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise IdentityError("malformed assertion")
    payload_segment, provided_mac = parts

    # Structural validation first: everything below assumes ASCII base64url, and a
    # caller controls both segments entirely.
    _require_b64url(payload_segment, "payload")
    _require_b64url(provided_mac, "signature")

    expected = _mac(secret, payload_segment)
    # Constant-time: a byte-by-byte comparison leaks the MAC one byte at a time.
    if not hmac.compare_digest(expected, provided_mac):
        raise IdentityError("assertion signature is not valid")

    try:
        claims = json.loads(_b64u_decode(payload_segment))
    except Exception as exc:  # noqa: BLE001
        raise IdentityError("assertion payload is not readable") from exc
    if not isinstance(claims, dict):
        raise IdentityError("assertion payload is not an object")

    current = time.time() if now is None else now

    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool):
        raise IdentityError("assertion has no expiry")
    if current > float(exp) + CLOCK_SKEW_SECONDS:
        raise IdentityError("assertion has expired")

    iat = claims.get("iat")
    if not isinstance(iat, (int, float)) or isinstance(iat, bool):
        raise IdentityError("assertion has no issue time")
    # A far-future iat is either a badly skewed peer or an attempt to mint something
    # that stays valid for a long time. Both are refusals.
    if float(iat) > current + CLOCK_SKEW_SECONDS:
        raise IdentityError("assertion is issued in the future")

    return claims


# ── User identity ─────────────────────────────────────────────────────────────


def sign_identity(user_id: str, *, ttl: int = DEFAULT_TTL_SECONDS, now: Optional[float] = None) -> str:
    """Mint a user assertion. Used by tests and by any Python-side internal caller.

    The production minter is the Next tier (``frontend/src/app/api/_identity.ts``);
    this is the reference implementation both sides are tested against.
    """
    uid = (user_id or "").strip()
    if not uid:
        raise IdentityError("cannot sign an empty user id")
    issued = time.time() if now is None else now
    return _sign(_secret(ENV_IDENTITY_SECRET), {"sub": uid, "iat": issued, "exp": issued + ttl})


def verify_identity_token(token: Optional[str], *, now: Optional[float] = None) -> str:
    """Return the verified user id carried by ``token``."""
    claims = _verify(_secret(ENV_IDENTITY_SECRET), token, now=now)
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub.strip():
        raise IdentityError("assertion carries no subject")
    return sub.strip()


# ── Service identity ──────────────────────────────────────────────────────────


def sign_service(service: str, *, ttl: int = DEFAULT_TTL_SECONDS, now: Optional[float] = None) -> str:
    """Mint a service assertion (the tool-server watcher's ``/resume`` credential)."""
    name = (service or "").strip()
    if not name:
        raise IdentityError("cannot sign an empty service name")
    issued = time.time() if now is None else now
    return _sign(_secret(ENV_SERVICE_SECRET), {"svc": name, "iat": issued, "exp": issued + ttl})


def verify_service_token(token: Optional[str], *, now: Optional[float] = None) -> str:
    """Return the verified service name carried by ``token``.

    Signed with a DIFFERENT secret from user assertions, so a compromised watcher
    cannot mint a user identity and a leaked user secret cannot mint a watcher.
    """
    claims = _verify(_secret(ENV_SERVICE_SECRET), token, now=now)
    svc = claims.get("svc")
    if not isinstance(svc, str) or not svc.strip():
        raise IdentityError("assertion carries no service name")
    return svc.strip()


# ── FastAPI dependencies ──────────────────────────────────────────────────────
#
# Imported lazily inside the functions so this module stays importable (and
# testable) without FastAPI — the pure signing/verification half has no web
# dependency and the tests exercise it directly.

_warned_unenforced = False


def _warn_unenforced_once(surface: str) -> None:
    """Warn that a caller-supplied ``user_id`` is being trusted.

    Emitted ONLY when a body id is actually used, not merely when enforcement is off.
    That distinction matters: ``/sessions`` calls ``resolve_user`` with no body fallback
    and 401s without a verified assertion, so warning there said the exact opposite of
    the truth — it would tell an operator the session surface was unguarded when it is
    the one surface that always requires an assertion. A warning that is wrong is worse
    than no warning, because it teaches people to ignore the channel.

    Plain ASCII: these are read through ``docker compose logs``, where an em-dash on a
    non-UTF-8 console arrives as mojibake (measured).
    """
    global _warned_unenforced
    if _warned_unenforced:
        return
    _warned_unenforced = True
    print(
        f"[identity] WARN: {ENV_REQUIRE_IDENTITY} is off, so {surface} is trusting the "
        f"caller-supplied user_id. Ownership is NOT enforced on that path. Set "
        f"{ENV_REQUIRE_IDENTITY}=1 with {ENV_IDENTITY_SECRET} configured before exposing "
        f"per-user session data."
    )


def resolve_user(request, body_user_id: Optional[str] = None, *, surface: str = "request") -> Optional[str]:
    """The caller's user id, verified when enforcement is on.

    Returns the verified subject with enforcement ON, raising ``HTTPException(401)``
    when the assertion is absent or invalid. With enforcement OFF it prefers a valid
    assertion when one is present (so the path is exercised before it is enforced)
    and otherwise falls back to ``body_user_id`` — today's behaviour, kept so Phase 1
    changes nothing observable.

    The 401 body says only "authentication required": telling an unauthenticated
    caller whether their assertion was expired, forged or malformed helps only them.
    """
    from fastapi import HTTPException

    header = request.headers.get(HEADER_IDENTITY) if request is not None else None

    if enforcement_enabled():
        try:
            return verify_identity_token(header)
        except IdentityError as exc:
            print(f"[identity] REFUSED {surface}: {exc}")
            raise HTTPException(status_code=401, detail="authentication required") from exc

    if header:
        try:
            return verify_identity_token(header)
        except IdentityError as exc:
            # Enforcement is off, so this is not fatal — but a header that fails to
            # verify while the secrets are supposed to match is a configuration fault
            # that must be visible BEFORE the flag is flipped in production.
            print(f"[identity] WARN: ignoring an unverifiable assertion on {surface}: {exc}")

    uid = (body_user_id or "").strip()
    if uid:
        # Warned here, and only here, so the message is true when it appears: a body id
        # is genuinely being trusted on this call. A caller that passes no fallback (the
        # /sessions surface) never triggers it, because nothing is being trusted.
        _warn_unenforced_once(surface)
        return uid
    return None


def require_service(request, *, surface: str = "resume") -> Optional[str]:
    """Verify the internal SERVICE credential (the watcher's ``/resume``).

    The watcher is a headless Rust service with no user session: it cannot present a
    user identity, and pretending it has one would be the fake authentication this
    boundary exists to avoid. It presents a service credential; the owning user is
    read from the run row instead.
    """
    from fastapi import HTTPException

    header = request.headers.get(HEADER_SERVICE) if request is not None else None

    if not enforcement_enabled():
        if header:
            try:
                return verify_service_token(header)
            except IdentityError as exc:
                print(f"[identity] WARN: ignoring an unverifiable service assertion on {surface}: {exc}")
        return None

    try:
        return verify_service_token(header)
    except IdentityError as exc:
        print(f"[identity] REFUSED {surface}: {exc}")
        raise HTTPException(status_code=401, detail="authentication required") from exc


# ── Cross-language test vector ────────────────────────────────────────────────
# The TypeScript minter in `frontend/src/app/api/_identity.ts` and the Rust signer in
# `tool-server/src/main.rs` are separate implementations of the same MAC. A shared
# fixture is what keeps them honest: each side asserts it reproduces this exact
# output, so a divergence is a failing unit test rather than a 401 in production.

VECTOR_SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
VECTOR_SUB = "user_abc123"
VECTOR_IAT = 1_700_000_000
VECTOR_TTL = 60


def reference_vector() -> Tuple[str, str]:
    """Return ``(payload_segment, mac)`` for the fixed vector above.

    Computed rather than hard-coded so the two halves cannot disagree with each
    other here; the OTHER languages hard-code the expected result and compare.
    """
    claims = {"sub": VECTOR_SUB, "iat": VECTOR_IAT, "exp": VECTOR_IAT + VECTOR_TTL}
    payload = _b64u_encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return payload, _mac(VECTOR_SECRET, payload)
