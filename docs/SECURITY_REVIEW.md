# Security Review — Ai-trader / Strat AI Platform

**Date:** 2026-07-31
**Branch:** `main` (working tree, uncommitted change to `.github/workflows/desktop-release.yml`)
**Scope:** Whole-monorepo audit — every service, not only the branch diff
**Method:** Static source review with adversarial verification (each candidate finding was independently re-examined by a second reviewer instructed to refute it)

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [Scope and methodology](#2-scope-and-methodology)
3. [Findings at a glance](#3-findings-at-a-glance)
4. [Attack chains](#4-attack-chains)
5. [Detailed findings](#5-detailed-findings)
   - [SR-01 — OAuth `state` forgery → brokerage account takeover (HIGH)](#sr-01--oauth-state-forgery--brokerage-account-takeover)
   - [SR-02 — Hardcoded JWT signing secret → authentication bypass (HIGH)](#sr-02--hardcoded-jwt-signing-secret--authentication-bypass)
   - [SR-03 — Unsigned payment webhook → arbitrary entitlement grant (HIGH)](#sr-03--unsigned-payment-webhook--arbitrary-entitlement-grant)
   - [SR-04 — Committed internal API key on a public privileged route (HIGH)](#sr-04--committed-internal-api-key-on-a-public-privileged-route)
   - [SR-05 — Plaintext password storage and comparison (HIGH)](#sr-05--plaintext-password-storage-and-comparison)
   - [SR-06 — Self-service paid tier upgrade (MEDIUM)](#sr-06--self-service-paid-tier-upgrade)
   - [SR-07 — Live broker access token written to plaintext logs (MEDIUM)](#sr-07--live-broker-access-token-written-to-plaintext-logs)
6. [Sub-threshold issues worth fixing](#6-sub-threshold-issues-worth-fixing)
7. [Verified and rejected candidates](#7-verified-and-rejected-candidates)
8. [Areas audited and found clean](#8-areas-audited-and-found-clean)
9. [Remediation plan](#9-remediation-plan)
10. [Appendix A — the branch diff](#appendix-a--the-branch-diff)
11. [Appendix B — coverage matrix](#appendix-b--coverage-matrix)

---

## 1. Executive summary

Seven findings met the reporting bar (high-confidence, concretely exploitable): **five HIGH** and **two MEDIUM**. All seven are in the Node.js `alpha-backend/` services — the auth service and the payment service. The Rust services, the Python deep-quant agent, the Tauri desktop shell, and the Next.js frontend produced no findings that survived adversarial verification, though several sub-threshold defects there are worth fixing and are documented in §6.

The single most serious issue is **SR-01**: the Zerodha OAuth callback binds a user's real brokerage credentials to an entirely attacker-supplied `state` value with no session correlation. This lets an attacker capture a victim's live Kite `access_token` — a credential that authorizes reading holdings *and placing live orders* on a real, funded trading account — by getting the victim to click one link and complete a legitimate broker login.

The other four HIGH findings compound it. **SR-02** (a JWT signing secret committed to source *and* to `docker-compose.yml`) lets anyone with repository access mint a token for any account. **SR-03** and **SR-04** are unauthenticated entitlement-granting endpoints. **SR-05** stores every user's password in cleartext, which matters disproportionately here because the user base is retail traders whose passwords are likely reused at the broker.

Taken together, the auth service currently has no reliable authentication boundary: at least three independent paths reach privileged state without a valid credential.

**Overall posture by service:**

| Service | Language | Findings | Assessment |
|---|---|---|---|
| `alpha-backend/` (auth + payment) | Node/Express/Prisma | 5 HIGH, 2 MEDIUM | **Critical** — needs remediation before any production exposure |
| `aggregator/`, `alpha-terminal/` | Rust | 0 (2 hardening items) | Acceptable; unauthenticated `0.0.0.0` binds should be tightened |
| `agents/deep-quant-loop/` | Python/FastAPI | 0 (1 low IDOR) | Good — SQL construction is correctly escaped throughout |
| `frontend/src-tauri/` | Rust/Tauri 2 | 0 (2 sub-threshold) | Good; one real SQL-construction bug worth fixing |
| `frontend/src/` | Next.js/React | 0 | Clean — no unsafe HTML sinks reachable from remote data |
| `.github/workflows/` | YAML | 0 | Clean — no `pull_request_target`, no untrusted interpolation |

---

## 2. Scope and methodology

### 2.1 What was reviewed

The review was requested as a full-application audit covering each service. Every directory below was read directly, not merely sampled:

| Path | Contents reviewed |
|---|---|
| `alpha-backend/services/auth-service/` | All routes, controllers, middleware, services, repositories, Prisma schema, `package.json`, `docker-compose.yml` |
| `alpha-backend/services/payment-service/` | All routes, controllers, middleware, services, repositories, webhook verification |
| `aggregator/src/` | 13 Rust source files including `kite_api.rs`, `ws_server.rs`, `ohlc_server.rs`, `consumer.rs`, `main.rs` |
| `alpha-terminal/src/` | 6 Rust source files including `ws_server.rs` |
| `agents/deep-quant-loop/` | `main.py`, `options.py`, `options_bias.py`, `tools.py`, `graph.py`, `backtest.py`, `journal.py`, `telemetry.py`, `api_key_resolver.py`, `requirements.txt`, `Dockerfile` |
| `frontend/src-tauri/src/` | `lib.rs`, all `commands/`, all `services/`, `db/`, `quant/`, `execution/`, `tauri.conf.json`, `capabilities/` |
| `frontend/src/` | Full tree, with targeted sweeps for unsafe DOM sinks, `postMessage`, IPC listeners, auth state |
| `infra/`, `docker-compose.prod.yml` | Caddy gateway config, production topology, port publication |
| `.github/workflows/` | `desktop-release.yml` and all sibling workflows |
| `.env.example`, `.gitignore` | Committed credential material and exclusion rules |

### 2.2 Threat model

Four attacker positions were considered:

- **Remote unauthenticated** — can reach published HTTP endpoints (`alpha-backend`, the Caddy gateway) but holds no credential.
- **Remote authenticated (low privilege)** — holds a valid FREE-tier account and its JWT.
- **Local network** — shares a LAN segment with a running desktop installation or an operator's machine.
- **Local co-resident process** — arbitrary code running as the desktop user, but outside the app (e.g. a malicious npm postinstall, a browser extension native host).

Per the review rules, environment variables and CLI flags are treated as **trusted** — attacks requiring an attacker to *control* an env var are invalid. A **committed fallback default** for a secret is a distinct case and *is* treated as a valid finding, because it requires no attacker control of the environment.

### 2.3 Exclusions applied

The following classes were excluded by policy and are not reported, even where instances exist: denial of service and resource exhaustion; rate limiting; memory/CPU exhaustion; memory-safety issues in memory-safe languages; secrets at rest on disk that are otherwise access-controlled; outdated third-party dependencies; log spoofing; path-only SSRF; regex injection and ReDoS; lack of audit logging; findings in documentation; general lack of hardening absent a concrete exploit; theoretical race conditions; test-only files.

### 2.4 Confidence discipline

Every candidate finding was passed to an independent reviewer instructed to **refute** it, defaulting to "false positive" under uncertainty, and to assign a confidence score of 1–10. Only findings scoring **≥ 8** appear in §5. Of 19 initial candidates, 7 survived. The 12 that did not are documented in §6 and §7 with the reason each was dropped — several are still worth fixing, and are marked as such.

---

## 3. Findings at a glance

| ID | Title | Severity | Confidence | Service | Location |
|---|---|---|---|---|---|
| SR-01 | OAuth `state` forgery → brokerage account takeover | HIGH | 9/10 | auth-service | `controllers/auth.controller.ts:74` |
| SR-02 | Hardcoded JWT signing secret → auth bypass | HIGH | 9/10 | auth + payment | `middlewares/auth.middleware.ts:4` |
| SR-03 | Unsigned payment webhook → arbitrary entitlement grant | HIGH | 9/10 | payment-service | `controllers/payment.controller.ts:38` |
| SR-04 | Committed internal API key on public privileged route | HIGH | 9/10 | auth-service | `controllers/auth.controller.ts:262` |
| SR-05 | Plaintext password storage and comparison | HIGH | 9/10 | auth-service | `services/auth.service.ts:44` |
| SR-06 | Self-service paid tier upgrade | MEDIUM | 9/10 | auth-service | `controllers/auth.controller.ts:319` |
| SR-07 | Live broker access token in plaintext logs | MEDIUM | 8/10 | auth-service | `services/auth.service.ts:189` |

**Category distribution:** broken access control (3), authentication bypass (1), credential storage (1), signature bypass (1), sensitive data logging (1).

---

## 4. Attack chains

The findings are not independent. Three chains materially amplify impact:

### Chain A — Full brokerage compromise (SR-01 → SR-07/`/auth/me`)

1. Attacker registers a free account, obtaining their own `userId`.
2. Attacker sends the victim `GET /api/broker/zerodha/connect?userId=<ATTACKER_UUID>` — a link to the application's own legitimate domain.
3. Victim authenticates at the genuine `kite.trade` with their own credentials.
4. Callback carries `state=<ATTACKER_UUID>`; the victim's live Kite `access_token` is written to the attacker's row (**SR-01**).
5. Attacker calls `GET /api/auth/me` under their own valid JWT and receives the raw `accessToken` and `apiKey`.
6. Attacker now holds a credential that authorizes **order placement** against the victim's funded account, used directly against `api.kite.trade` with no further application access needed.

Step 5 is what upgrades this from read-only portfolio disclosure to full trading control.

### Chain B — Mass account impersonation (SR-02 → SR-01/SR-07)

The JWT secret is public. Any `userId` observed in a broker-connect URL, a `strat://broker-callback` deep link, or a support ticket becomes a forgeable identity. `/api/auth/me` then discloses that account's stored broker credentials. No user interaction is required at all.

### Chain C — Entitlement bypass, three independent routes

**SR-03** (unsigned webhook, PREMIUM, any user), **SR-04** (committed internal key, PREMIUM, any user), and **SR-06** (self-service, PRO, own account) each independently defeat billing. Fixing any one of them does not fix the others, and the payment-service salt fallback (§6) becomes live as soon as SR-03 is closed.

---

## 5. Detailed findings

---

### SR-01 — OAuth `state` forgery → brokerage account takeover

| | |
|---|---|
| **Severity** | HIGH |
| **Confidence** | 9/10 |
| **Category** | `oauth-state-forgery` / `broken-access-control` |
| **CWE** | CWE-352 (CSRF), CWE-639 (Authorization bypass through user-controlled key) |
| **Location** | `alpha-backend/services/auth-service/src/controllers/auth.controller.ts:74-80` |
| **Sinks** | `src/services/auth.service.ts:114-126`, `:141-148`, `:215` |
| **Routes** | `src/routes/auth.routes.ts:11-12` |

#### Description

Both broker OAuth routes are registered without authentication middleware:

```ts
// auth.routes.ts:11-12  — note: no authenticateJWT on either
router.get('/broker/zerodha/connect',  (req, res) => controller.connectBroker(req, res));
router.get('/broker/zerodha/callback', (req, res) => controller.callbackBroker(req, res));
```

The callback handler reads `state` straight from the query string and forwards it as the identity to bind credentials to:

```ts
// auth.controller.ts:74-80
const { request_token, state } = req.query;
...
const result = await authService.saveBrokerAccessToken(
  state as string || undefined,
  request_token as string
);
```

In the service layer, `state` is accepted verbatim as the target user with no nonce store, no session lookup, and no comparison against any authenticated principal:

```ts
// auth.service.ts:141-148
let targetUserId: string;
if (userId) {
  targetUserId = userId;          // ← attacker-supplied, unvalidated
} else {
  const first = await this.findFirstUser();   // ← binds to an arbitrary row
  targetUserId = first.id;
}
```

The genuine token exchange against `https://api.kite.trade/session/token` then runs (`:169`), and the resulting live credential is written to the attacker-chosen row:

```ts
// auth.service.ts:215
await brokerRepository.upsertBrokerConnection(targetUserId, brokerData);
```

The initiating route has the mirror-image defect — it takes `userId` from an unauthenticated query string and embeds it as the OAuth `state`:

```ts
// auth.service.ts:126
const url = `${KITE_LOGIN_URL}?api_key=${apiKey}&v=3&redirect_params=state%3D${targetUserId}`;
```

The stored credential is consumed by the portfolio controller using the caller's *own* JWT identity, which is what closes the loop:

```ts
// portfolioController.ts:21-32
const conn = await prisma.brokerConnection.findUnique({ where: { userId } });
// → getHoldings / getPositions / getOrders / getTrades / getMargins
```

#### Exploit scenario

1. Attacker signs up via the public `POST /api/auth/signup` and notes their own `userId`.
2. Attacker sends the victim a link on the application's own domain — this is important, because the domain looks correct and the destination *is* the real service:
   `https://<app-host>/api/broker/zerodha/connect?userId=<ATTACKER_UUID>`
3. Victim is redirected to the genuine Zerodha Kite login and enters their own credentials. Nothing about the login page is spoofed.
4. Zerodha redirects to `/api/broker/zerodha/callback?request_token=...&state=<ATTACKER_UUID>`.
5. The service exchanges the token legitimately and upserts the **victim's** `access_token`, `public_token`, `refresh_token`, `api_key` and profile name onto the **attacker's** account row.
6. Attacker calls `GET /api/portfolio/holdings` (and `positions`, `orders`, `trades`, `margins`) under their own legitimate JWT and reads the victim's live brokerage account.
7. Via `GET /api/auth/me` (`auth.service.ts:474` returns `brokerConnection` whole) the attacker also extracts the raw `access_token` and can then call `api.kite.trade` directly — including order-placement endpoints.

**Reverse direction:** an attacker replaying their own `request_token` with `state=<VICTIM_UUID>` overwrites the victim's broker link with attacker-controlled credentials, and also overwrites the victim's profile `name` (`auth.service.ts:219-222`).

**Third variant:** omitting `state` entirely triggers `findFirstUser()` at `:118-122` and `:141-145`, silently binding a stranger's brokerage credentials to whichever user happens to be first in the table.

#### Why this is not contrived

"Victim clicks a link and completes a legitimate login at the honest identity provider" is the *defining* precondition of OAuth CSRF / account mix-up. It is precisely the attack RFC 6749 §10.12 mandates a bound `state` parameter to prevent. The unguessability of UUIDs offers no protection here, because the attacker supplies their **own** UUID.

#### Recommendation

1. Require an authenticated session on `/broker/zerodha/connect`; derive `userId` from the verified JWT, never from the query string.
2. Generate a cryptographically random, single-use `state` value server-side, store it (Redis, ≤10 min TTL) bound to that session's `userId`, and include it in the Kite redirect.
3. On callback, look the `state` up, reject unknown or already-consumed values, and take the target `userId` from the stored record — never from the request.
4. Delete both `findFirstUser()` fallbacks; a callback with no recognized `state` must fail closed.
5. Treat all currently stored `brokerConnection` rows as potentially mis-bound: invalidate them and require re-linking.

---

### SR-02 — Hardcoded JWT signing secret → authentication bypass

| | |
|---|---|
| **Severity** | HIGH |
| **Confidence** | 9/10 |
| **Category** | `authentication-bypass` / `hardcoded-credential` |
| **CWE** | CWE-798 (Use of hard-coded credentials), CWE-321 (Hard-coded cryptographic key) |
| **Locations** | `auth-service/src/middlewares/auth.middleware.ts:4`, `auth-service/src/services/auth.service.ts:12`, `payment-service/src/middlewares/auth.middleware.ts:4` |
| **Also** | `alpha-backend/docker-compose.yml:46`, `:69` |

#### Description

The same literal appears in three files and is used on both sides of the trust boundary:

```ts
const JWT_SECRET = process.env.JWT_SECRET || 'alpha-jwt-secret-key-39281!@';
```

Signing (`auth.service.ts:18`):
```ts
jwt.sign({ userId, tier }, JWT_SECRET, { expiresIn: '24h' })
```

Verification (`auth.middleware.ts:32`):
```ts
const decoded = jwt.verify(token, JWT_SECRET);
```

`jwt.verify` is otherwise used correctly — default HS256, no `alg: none` confusion, no `jwt.decode` misuse. That is irrelevant once the key is public.

**This is not merely a dormant fallback.** The checked-in deployment configuration sets the identical value explicitly:

```yaml
# docker-compose.yml:46 and :69
JWT_SECRET=alpha-jwt-secret-key-39281!@
```

So the signing key is public whether or not the `||` branch ever executes. Neither `auth-service/src/index.ts` nor `payment-service/src/index.ts` contains a startup assertion on `JWT_SECRET`, so nothing fails closed.

#### Exploit scenario

Anyone with repository access — current collaborators, former collaborators, anyone holding a historical clone, anyone who has seen a CI log or a fork — mints a valid token:

```js
jwt.sign({ userId: '<target-uuid>', tier: 'PREMIUM' },
         'alpha-jwt-secret-key-39281!@',
         { expiresIn: '24h' })
```

```
GET /api/auth/me                Authorization: Bearer <forged>
GET /api/portfolio/holdings     Authorization: Bearer <forged>
```

`getUserProfile` (`auth.service.ts:474`) returns the target's `brokerConnection` verbatim — including the live Kite `accessToken` and `apiKey` per `user.repository.ts:11-16` — and `/api/portfolio/*` proxies to `api.kite.trade` using them.

Impersonating a *specific* victim requires that victim's `userId` UUID, which is unguessable in isolation. It is not, however, secret: it appears in `/api/broker/zerodha/connect?userId=...` URLs, in the `strat://broker-callback?...&userId=...` deep link (`auth.controller.ts:242`), and in `/api/auth/me` responses. A self-issued token with `tier: 'PREMIUM'` also grants premium entitlements outright, with no UUID needed.

#### Recommendation

1. Remove the `||` fallback from all three files.
2. Fail closed at boot: `if (!process.env.JWT_SECRET) throw new Error('JWT_SECRET is required');`
3. Remove the literal from `docker-compose.yml`; inject it from a secret store.
4. **Rotate the secret** — this invalidates all outstanding tokens, which is the intended effect.
5. Pass `{ algorithms: ['HS256'] }` explicitly to every `jwt.verify` call.

#### Related, same class

`auth.service.ts:156` contains `process.env.KITE_API_SECRET || 'lic12bvwjz1d89tkepbk2cbsxfwfbofn'` — a plausibly real Zerodha application secret, overridden only by a mock value in `docker-compose.yml`. Rotate it at Zerodha and remove the default.

---

### SR-03 — Unsigned payment webhook → arbitrary entitlement grant

| | |
|---|---|
| **Severity** | HIGH |
| **Confidence** | 9/10 |
| **Category** | `webhook-signature-bypass` / `broken-access-control` |
| **CWE** | CWE-345 (Insufficient verification of data authenticity), CWE-306 (Missing authentication) |
| **Location** | `alpha-backend/services/payment-service/src/controllers/payment.controller.ts:36-47` |
| **Route** | `src/routes/payment.routes.ts:12` |
| **Sinks** | `src/services/payment.service.ts:76`, `:85`; `repositories/subscription.repository.ts:5` |

#### Description

The webhook route carries no middleware — note the deliberate contrast with the line directly above it:

```ts
// payment.routes.ts:11-12
router.post('/initiate', authenticateJWT, (req, res) => controller.initiate(req, res));
router.post('/phonepe/webhook',           (req, res) => controller.phonepeWebhook(req, res));
```

It is mounted publicly (`payment-service/src/index.ts:21`: `app.use('/api/payments', paymentRoutes)`).

The handler's signature check is only reached when *both* the body field and the header are present. Omitting them takes an unauthenticated branch whose only test is attacker-supplied body content:

```ts
// payment.controller.ts:36-47
const { response } = req.body;
const xVerifyHeader = req.headers['x-verify'];

if (!response || !xVerifyHeader) {
  // Fallback for internal test driver execution
  if (req.body.event === 'payment.success' && req.body.userId && req.body.tier) {
    console.log('[PaymentController] Webhook fallback detected.');
    const result = await paymentService.forceUpgradeSync(req.body.userId, req.body.tier);
    ...
```

`forceUpgradeSync` (`payment.service.ts:76`) calls `executeTierUpgradeSync`, which writes an `ACTIVE` subscription (`:85` → `subscription.repository.ts:5`) and then POSTs to the auth service's `/api/internal/upgrade-tier` with the internal key, flipping `User.tier`.

**There is no `NODE_ENV` or test-mode guard anywhere on this path** — only the comment claiming it serves an "internal test driver." That claim is false: the actual test driver at `alpha-backend/verify-flow.js:139-145` uses the properly *signed* path and does not need this branch. The branch is unconditional production code in the shipped controller.

#### Exploit scenario

Unauthenticated, no signature, no secret knowledge, no UUID guessing (an attacker can use their own id from signup):

```http
POST /api/payments/phonepe/webhook HTTP/1.1
Content-Type: application/json

{"event":"payment.success","userId":"<any-uuid>","tier":"PREMIUM"}
```

Result: PREMIUM granted, `ACTIVE` subscription row written, no payment taken. Supplying another user's UUID tampers with that account's entitlements.

This matters specifically in production, because `getEffectiveTier` (`auth.service.ts:26-34`) returns the stored `user.tier` only when `NODE_ENV === 'production'` — outside production everyone is premium anyway, so the fallback's impact is *exclusively* a production impact.

#### Recommendation

1. **Delete the fallback branch entirely.** Return `400`/`401` when `response` or `x-verify` is absent.
2. Make signature verification the only path to `forceUpgradeSync`, and compare digests with `crypto.timingSafeEqual`.
3. Do not trust `userId`/`tier` from the webhook body — resolve the order server-side by merchant transaction id and verify status against PhonePe's status API before granting anything.
4. Audit existing `Subscription` rows for grants with no corresponding payment record.

#### Secondary (currently masked by the above)

`payment.service.ts:8` contains `const SALT_KEY = process.env.PHONEPE_SALT_KEY || 'mock-salt-key-9283-1029';`, and verification at `:53-58` computes `SHA256(response + SALT_KEY)`. If `PHONEPE_SALT_KEY` is unset, an attacker forges a valid `x-verify` for any payload; `userId` is then taken from the attacker's own `data.merchantUserId` (`:65`) with `tier` hardcoded to `PREMIUM` (`:66`). This is strictly dominated by the unsigned fallback today, but **becomes the live bug the moment SR-03 is fixed**. Remove the default and fail boot when the variable is absent.

---

### SR-04 — Committed internal API key on a public privileged route

| | |
|---|---|
| **Severity** | HIGH |
| **Confidence** | 9/10 |
| **Category** | `broken-access-control` / `hardcoded-credential` |
| **CWE** | CWE-798, CWE-306 |
| **Locations** | `auth-service/src/controllers/auth.controller.ts:262`; matching fallback at `payment-service/src/services/payment.service.ts:11` |
| **Route** | `auth-service/src/routes/auth.routes.ts:13` |

#### Description

```ts
// auth.controller.ts:262
const expectedKey = process.env.INTERNAL_API_KEY || 'alpha-internal-super-secret-key-29831!';
```

`POST /api/internal/upgrade-tier` is registered with **no JWT middleware** and is mounted on the same public `/api` router as `/auth/login` and `/auth/signup` — it is not bound to a separate internal listener, port, or network interface. Its only gate is a header compared against the value above, which is committed to the repository.

Unlike the JWT-protected sibling route (SR-06), `tier` is **not validated** on this path, so `PREMIUM` — the highest tier — is directly settable.

#### Exploit scenario

```http
POST /api/internal/upgrade-tier HTTP/1.1
x-internal-key: alpha-internal-super-secret-key-29831!
Content-Type: application/json

{"userId":"<any-uuid>","tier":"PREMIUM"}
```

No authentication, no payment, arbitrary target account, highest tier. This also bypasses SR-06's self-scoping limitation — it works against *other* users' accounts.

#### Recommendation

1. Require `INTERNAL_API_KEY` from the environment with no default; fail boot if absent. Rotate the current value.
2. Compare with `crypto.timingSafeEqual`, not `===`.
3. Move internal routes off the public router — bind them to a separate listener on a private interface, or enforce a network-level policy so they are unreachable from outside the service mesh.
4. Validate `tier` against an explicit allowlist on this path as well.

---

### SR-05 — Plaintext password storage and comparison

| | |
|---|---|
| **Severity** | HIGH |
| **Confidence** | 9/10 |
| **Category** | `plaintext-credentials` |
| **CWE** | CWE-256 (Plaintext storage of password), CWE-257 (Storing passwords in a recoverable format) |
| **Locations** | `auth-service/src/services/auth.service.ts:44`; `src/repositories/user.repository.ts:27`; `prisma/schema.prisma:14` |

#### Description

Authentication is a raw string comparison:

```ts
// auth.service.ts:44
if (user.password !== password) {
```

Storage writes the value verbatim:

```ts
// user.repository.ts:27
password: data.password,
```

into a plain column:

```prisma
// schema.prisma:14
password String
```

A tree-wide search for `bcrypt|argon2|scrypt|pbkdf2` across `alpha-backend` returns **zero** hits in the authentication flow — the only SHA-256 uses are the PhonePe and Kite checksum computations. `services/auth-service/package.json:11-18` lists no hashing library as a dependency at all.

This does **not** fall under the "secrets at rest are handled separately" exclusion. That exclusion concerns the application's own secrets in configuration artifacts. These are *third-party principals' reusable credentials* held in a relational database, and they are the opposite of "otherwise secured."

#### Exploit scenario

Any read primitive against the `User` table yields every user's cleartext password: a database backup, a read replica, a compromised Postgres container, an over-broad support query, or an injection flaw introduced anywhere in future. Because the user base is retail traders, those passwords are high-value for credential stuffing against the brokerage itself and against the email accounts used for broker 2FA recovery — the blast radius extends well beyond this application.

#### Recommendation

1. Hash with argon2id (preferred) or bcrypt at cost ≥ 12 on write.
2. Verify with the library's constant-time comparison, never `!==`.
3. Migrate: add a `passwordHash` column, hash-on-next-successful-login, then drop the plaintext column.
4. Treat the current column contents as compromised — force a password reset for all users and notify them that credential reuse elsewhere should be changed.

#### Note on the adjacent auto-registration behavior

`auth.service.ts:40-42` auto-creates an account when `POST /api/auth/login` receives an unknown email. This was assessed and is **not** a separate vulnerability: `auth.routes.ts:10` already exposes unauthenticated `POST /auth/signup`, so email squatting is achievable without it, and when the account does exist the password check at `:44` still runs — no auth bypass, no privilege gained. It is a correctness wart worth cleaning up (unknown email should return 401), not an attack.

---

### SR-06 — Self-service paid tier upgrade

| | |
|---|---|
| **Severity** | MEDIUM |
| **Confidence** | 9/10 |
| **Category** | `broken-access-control` / `billing-bypass` |
| **CWE** | CWE-285 (Improper authorization) |
| **Location** | `auth-service/src/controllers/auth.controller.ts:319-324` |
| **Route** | `src/routes/auth.routes.ts:15` |
| **Sink** | `src/services/auth.service.ts:279`, `:286-293` |

#### Description

The route is authenticated but not authorized — it validates the tier *string* and nothing else:

```ts
// auth.controller.ts:319-324
const { tier } = req.body;
if (!tier || (tier !== 'FREE' && tier !== 'PRO')) {
  return res.status(400).json({ error: 'Invalid tier specified. Supported: FREE, PRO' });
}
const updatedUser = await authService.upgradeUserTier(userId, tier);
```

`upgradeUserTier` calls `userRepository.updateTier` (`auth.service.ts:279`) and then writes a 30-day active subscription:

```ts
// auth.service.ts:286-293
await prisma.$executeRaw`
  INSERT INTO "Subscription" (...) VALUES (..., 'ACTIVE', ${currentPeriodEnd})
  ...`;
```

There is no payment check, receipt verification, or entitlement lookup anywhere on this path. The *legitimate* upgrade path is `POST /api/internal/upgrade-tier`, reached only after the payment service verifies a webhook — this JWT-gated endpoint bypasses that trust boundary entirely.

#### Exploit scenario

```http
POST /api/auth/subscription/tier HTTP/1.1
Authorization: Bearer <own valid FREE-tier token>
Content-Type: application/json

{"tier":"PRO"}
```

Any FREE user mints a 30-day `ACTIVE` PRO subscription for free. The tier string check does reject `PREMIUM`, so this path caps at PRO — SR-03 and SR-04 reach PREMIUM.

Rated MEDIUM rather than HIGH because the impact is self-scoped: no other user's data or account is affected, and the loss is revenue rather than confidentiality or integrity of user assets.

#### Recommendation

Remove client-driven tier setting entirely. Tier changes should originate only from (a) a signature-verified payment webhook, or (b) an admin route with an explicit role claim check on the JWT. If a downgrade-to-FREE self-service action is genuinely wanted, keep that direction only and hardcode the target tier.

---

### SR-07 — Live broker access token written to plaintext logs

| | |
|---|---|
| **Severity** | MEDIUM |
| **Confidence** | 8/10 |
| **Category** | `sensitive-data-logging` |
| **CWE** | CWE-532 (Insertion of sensitive information into log file) |
| **Location** | `auth-service/src/services/auth.service.ts:189` |

#### Description

```ts
// auth.service.ts:189
console.log('[Auth Service] Zerodha OAuth raw response payload:',
            JSON.stringify(result, null, 2));
```

`result.data` is destructured immediately afterwards at `:194-201` into `accessToken: kiteData.access_token`, `publicToken`, and `refreshToken` — so the logged object provably contains the plaintext broker credential, not merely a URL or a non-sensitive identifier.

A Kite `access_token` authorizes reading holdings **and placing live orders** on a real, funded brokerage account. It is a high-value secret, so the "logging non-PII data is not a vulnerability" carve-out does not apply.

#### Exploit scenario

Every successful broker connection writes a usable live trading credential to stdout. In the containerized deployment this flows into Docker logs and onward to any aggregator (CloudWatch, Loki, Datadog, Splunk). Anyone with log-read access — an SRE, a support engineer, a third-party observability vendor, a compromised log shipper, or an attacker who reaches the logging backend — extracts tokens and trades directly against `api.kite.trade`. No access to the application itself is required, and no application-side audit trail records the use.

#### Recommendation

1. Redact `access_token`, `public_token`, and `refresh_token` before logging; log only `user_id`, `user_name`, and `broker`.
2. Introduce a shared redaction helper and apply it to every `JSON.stringify` of a third-party response.
3. Purge or rotate log retention covering the period this line has been live, and invalidate broker tokens issued during it.

#### Explicitly assessed and *not* reported

Two related locations were examined and rejected as findings:

- `auth.controller.ts:242` / `:246` render `access_token` into a `strat://broker-callback?...` deep link and a `window.location.href`. This delivers the token to the browser of the principal who just authorized it — their own credential over the channel the desktop app is designed around. No cross-principal disclosure occurs. (It does amplify SR-01 once the row is mis-bound; that impact is recorded under SR-01, not here.)
- `auth.service.ts:474` returns the whole `brokerConnection` from `/api/auth/me`, where `userId` derives from the verified JWT (`auth.controller.ts:293`). A user retrieving their own broker token is expected behavior for this architecture. It is, again, an amplifier of SR-01 and SR-02 rather than a standalone defect — though narrowing the response to `{broker, brokerUserId, userName, connected}` would meaningfully reduce the blast radius of both.

---

## 6. Sub-threshold issues worth fixing

These did not meet the ≥8 confidence bar for the formal findings list, but each is a genuine defect and each is cheap to fix. They are ordered by how much they reduce future risk.

### 6.1 Unvalidated `timeframe` interpolated into a QuestDB query

**`frontend/src-tauri/src/commands/deep_quant.rs:840`** — verifier confidence 7/10, severity MEDIUM.

`load_candles_with_ts` computes `base_tf` from a match with a safe `_ => "10m"` default (`:819-828`), then sets `is_derived = timeframe.to_lowercase() != base_tf` (`:829`). Any unrecognized string therefore makes `is_derived` **true**, entering a branch whose inner match ends with `_ => timeframe` — the caller's raw string — which is interpolated at `:842-855`:

```rust
SAMPLE BY {} ALIGN TO CALENDAR
```

and executed via `sqlx::query(&derived_query)` at `:856`.

Reachability is real: `frontend/src-tauri/src/quant/tool_server.rs:183-208` (`get_candles`) passes `payload.timeframe` through with **no validation**, while the sibling handler `get_support_resistance` at `:325-339` explicitly calls `crate::quant::validate_timeframe(&tf)` — the validator exists and is simply unwired on this route. The router at `:1952-1979` binds `127.0.0.1:8084` with no auth. The LLM tool path is likewise unguarded (`agents/deep-quant-loop/tools.py:855-876` forwards the model-chosen timeframe verbatim, whereas `get_market_regime` and `get_session_context` do check `SUPPORTED_TIMEFRAMES`). `commands/radar.rs:60-99` is a third unvalidated entry point.

It was held below the bar because sqlx's extended query protocol blocks stacked statements (no `DROP`/`INSERT`), the pool reaches only market-data tables, and a browser cannot forge the `application/json` POST without CORS — leaving co-resident local processes and prompt-injected LLM tool calls as the realistic vectors. Note that `deep_quant.rs:287-298` echoes database errors back as `format!("candle store fault: {}: {}", source, detail)`, which is a working oracle for blind exploitation.

**Fix (small):** change `_ => timeframe` to `_ => "10m"`, and call `validate_timeframe` in `tool_server::get_candles` and `commands::radar::scan_radar_symbol`.

### 6.2 `strat://login?t=` deep link accepts an uncorrelated auth token

**`frontend/src-tauri/src/lib.rs:56-67`** and **`frontend/src/store/useAuthStore.ts:128`** — verifier confidence 7/10, severity MEDIUM. Confirmed true positive, held below the bar only because exploitation needs a login in flight plus victim click-through on an OS scheme prompt.

The Rust handler matches any deep link whose path merely `contains("login")` (`:25`), extracts the `t` query parameter with no validation, and emits `app.emit("desktop-login-success", json!({ "token": token }))`. The payload carries only the token — no session identifier is even available downstream. The frontend resolves on it unconditionally, and the winner of `Promise.race` (`useAuthStore.ts:175`) goes straight to `exchangeToken(finalToken)`.

The defect is one of omission: `login()` creates a `sessionId` at `:80` and the *polling* branch correctly scopes to `/auth/desktop/session/${sessionId}` at `:146`, but the deep-link branch never compares against it. `POST /auth/desktop/exchange` is a public endpoint taking only `{token}`, so the server is structurally unable to detect the substitution — the attacker's login token is genuinely valid and unexpired, and the server correctly mints tokens for its rightful owner.

**Impact if exploited:** the victim's terminal is silently authenticated *as the attacker*. `BrokerConnectCard.tsx:21-23` then builds `connect?userId=${userId}` from the now-attacker-controlled `user.id`, so the victim authorizes their own Zerodha account into the attacker's Strat AI account — the same end state as SR-01 by a different route.

**Fix:** include `sessionId` in the deep link and reject any `desktop-login-success` whose session does not match the in-flight one; require `sessionId` alongside `token` at `/auth/desktop/exchange`. Also replace `path().contains("login")` with an exact host/path match, and require `scheme() == "strat"` in the single-instance argv branch at `lib.rs:189`.

### 6.3 Unauthenticated services bound to `0.0.0.0`

- `aggregator/src/kite_api.rs:817` — `format!("0.0.0.0:{}", port)` with `CorsLayer::new().allow_origin(Any).allow_methods(Any).allow_headers(Any)` at `:805-808` and no auth layer on the router at `:810-815`. Every handler attaches the operator's live Kite credential via `state.auth_header()` (`:189-194`).
- `aggregator/src/ws_server.rs:34`, `aggregator/src/ohlc_server.rs:127`, `alpha-terminal/src/ws_server.rs:7` — bare `accept_async`, no token, no origin check.

These were rejected as findings because the registered Kite routes expose only instrument, quote, and candle data (no holdings, positions, orders, profile, or margins route exists, and every upstream call is a `.get()`), and the WebSocket streams are write-only fan-outs of model output derived from public NSE market data.

**They are one route away from being HIGH.** The day a `/portfolio/*` passthrough or a `.post()` order route is added to `kite_api.rs:810-815`, the missing auth becomes a live brokerage-compromise path. Bind to `127.0.0.1` and add a shared-secret check now, while it is cheap.

### 6.4 Unvalidated `interval` reaches Kite path construction

**`aggregator/src/kite_api.rs:672`, `:714-717`.** `interval` is taken from the query string with no allowlist despite the doc comment at `:81` enumerating the eight legal values, and is interpolated into:

```rust
let url = format!("https://api.kite.trade/instruments/historical/{}/{}", token, interval);
```

axum's `Query` percent-decodes once and reqwest's URL parser applies RFC 3986 dot-segment removal, so `interval=..%2F..%2F..%2Fportfolio%2Fholdings` really does produce an authenticated `GET https://api.kite.trade/portfolio/holdings` bearing the operator's credential. It was rejected because the host is a hardcoded literal (path-only SSRF, excluded by policy) and the 2xx branch at `:754-759` extracts only `data.candles`, so account responses are filtered to `{"candles":[]}` — leaving a status-code oracle over the operator's own account and nothing readable.

**Fix anyway:** match `interval` against the eight known values and 400 on anything else. Same for `exchange` at `:514`/`:309`.

### 6.5 Thread-scoped IDOR in the deep-quant agent

**`agents/deep-quant-loop/main.py:389`, `:427`, `:353`, `:459`; `graph.py:5922-5925`.** `MemorySaver()` is a single process-wide namespace shared by all users, and no endpoint checks thread ownership — `/resume` tests only whether the graph is paused, `/cancel` does a bare `_CANCELLED.add(...)`, and `/stream/{thread_id}` succeeds even for a not-yet-existing thread (allowing pre-registration of an ambush queue). The identifier is not a UUID: `frontend/src-tauri/src/commands/deep_quant.rs:2344` builds it as `format!("thread_{}_{}", symbol, timestamp_millis())`.

Rated LOW (confidence 7) because the millisecond field yields ~2.2×10⁷ candidates per symbol per session with no confirmation oracle, and blanket pre-subscription across a millisecond window is excluded mass-connection behavior. Still: namespace checkpointer threads by authenticated user and generate `thread_id` server-side from a CSPRNG.

### 6.6 Live-looking LLM gateway key committed to `.env.example`

**`.env.example:54`** — `LLM_API_KEY=sk-ace5fc…-8f93e2-…`. Excluded from the findings list as a secret-at-rest in a private repository, which policy assigns to secret-scanning and rotation processes rather than code review.

Worth acting on regardless: the value's structure (`sk-` + 16 hex + 6 hex + 8 hex) matches the real key in the untracked `.env:28` exactly, and does *not* match the project's own dummy convention (`sk-xxxxx` in `frontend/src-tauri/src/services/llm.rs:17`, `change-me-strong-password` at `.env.example:91`). Rotate the key at the omniroute gateway and blank line 54.

Also noted: `bedrock-api-key.txt` at the repository root is git-tracked and decodes to an AWS STS presigned Bedrock URL. It expired 2026-06-12, so it is not exploitable, but it should be purged from history and the `ASIA6HLWB63IOSAZ24CG` role reviewed.

### 6.7 Over-broad Tauri IPC surface

`fetch_questdb` (`frontend/src-tauri/src/commands/charts.rs:520-549`) accepts a complete SQL string from the webview and executes it against QuestDB `/exec` with compiled-in credentials. `open_browser` (`commands/security.rs:32-40`) launches arbitrary URLs through `rundll32.exe url.dll,FileProtocolHandler` with no scheme allowlist.

Both were rejected: no reachable injection sink exists today (every `dangerouslySetInnerHTML` and `innerHTML` use in the frontend is fed by the static `SVGS` map or string literals; `frontendDist` is a local static bundle), so exploitation presupposes webview script execution. They are privilege amplifiers that would turn a future XSS into database access and process launch. Consider replacing `fetch_questdb` with parameterized commands following the `get_historical_view` pattern (`charts.rs:139-174`), and adding a scheme allowlist to `open_browser`.

The Tauri CSP is also permissive — `script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:` with `dangerousDisableAssetCspModification: ["script-src","style-src"]`. Weak for a webview with IPC reach, but no live sink pairs with it, so it is recorded as hardening.

---

## 7. Verified and rejected candidates

Nineteen candidates were generated; twelve were rejected by adversarial verification. Recording them prevents re-litigation in future reviews.

| Candidate | Location | Verdict | Reason |
|---|---|---|---|
| `timeframe` SQL injection | `deep_quant.rs:840` | True positive, conf 7 | Below bar — no stacked statements, market-data tables only. See §6.1 |
| Deep-link session fixation | `lib.rs:56-67` | True positive, conf 7 | Below bar — needs login in flight + click-through. See §6.2 |
| `open_browser` scheme injection | `security.rs:32` | **False positive**, conf 2 | Args passed as a vector (no shell); `cmd` fallback only on `rundll32` spawn failure; all callers pass first-party data |
| `fetch_questdb` arbitrary SQL | `charts.rs:520` | **False positive**, conf 2 | No injection sink exists; requires prior webview compromise |
| Kite proxy unauth + wildcard CORS | `kite_api.rs:817` | **False positive**, conf 7 | Only market-data routes registered; credential never crosses the response boundary |
| `interval` path traversal | `kite_api.rs:714` | **False positive**, conf 8 | Path-only SSRF (excluded); 2xx response filtered to `candles`, nothing readable returns |
| Unauth WebSocket streams | `ws_server.rs`, `ohlc_server.rs` | **False positive**, conf 8 | Write-only fan-outs of public-market-derived model output; no orders/credentials/PII |
| Deep-quant `user_id` spoofing | `main.py:98` | **False positive**, conf 9 | `userId` is a v4 UUID (`schema.prisma:12`), unguessable; supplying one's own id resolves one's own key — the documented design |
| Deep-quant `thread_id` IDOR | `main.py:389` | True positive, LOW, conf 7 | Below bar — ~2.2×10⁷ search space, no confirmation oracle. See §6.5 |
| Deep-quant `0.0.0.0` bind | `main.py:706` | **False positive**, conf 9 | Shipped topology is Docker + Caddy basic auth with port 8086 unpublished (`docker-compose.prod.yml:358-380`); the unproxied path is the dev launcher only |
| `LLM_API_KEY` in `.env.example` | `.env.example:54` | **False positive**, conf 8 | Secret-at-rest in a private repo — excluded by policy. See §6.6 |
| PhonePe salt fallback | `payment.service.ts:8` | True positive, LOW, conf 8 | Strictly dominated by SR-03 today; folded in as SR-03's secondary |
| Login auto-registration | `auth.service.ts:40` | **False positive**, conf 9 | Public `/auth/signup` already permits squatting; password check still runs. Folded into SR-05 as a note |
| `access_token` in deep link / `/auth/me` | `auth.controller.ts:242`, `auth.service.ts:474` | **False positive**, conf 8 | Delivers the principal's own credential to the principal; amplifier of SR-01/SR-02, not standalone. Folded into SR-07 |

---

## 8. Areas audited and found clean

Recorded so future reviews can skip re-deriving these conclusions.

### 8.1 SQL injection

- **Python deep-quant service:** all eight `_questdb_select` call sites (`options.py:1278,1320,1399,1448,1514,1890`; `main.py:521,551`) route request- and LLM-controlled strings through `_escape_sql_literal` (`options.py:1207`), the correct SQL-standard doubling escape for QuestDB, which has no backslash alternative to break out with. Numeric interpolations are `int()`-coerced. `journal.py:1260,1397` build only the WHERE *shape* dynamically with `?` placeholders. `telemetry.py` interpolates module-level constant table names only.
- **Node services:** `prisma.$executeRaw`/`$queryRaw` at `auth.service.ts:286`, `:300`, `:442` are tagged templates, so interpolations are parameterized. The fluent Prisma API is used everywhere else. No string concatenation found.
- **Tauri backend:** all `rusqlite`/`sqlx` queries in `services/` are parameterized. `instrument_master.rs:242-245`, `:626-641` insert network-fetched CSV via `params!`. `instruments.rs:294-298` interpolates only a table name chosen between two static literals, with the symbol bound.
- **Aggregator / alpha-terminal:** contain no SQL, no database driver, and no QuestDB access at all.

### 8.2 Remote code execution

No `eval`, `exec`, `compile`, `pickle.load(s)`, `yaml.load` without `SafeLoader`, `os.system`, `subprocess`, `pandas.read_pickle`, `joblib.load`, or `jinja2.Template(...).render()` on attacker-controlled input in any non-test Python module. The only near-hit is `ast.literal_eval` (`graph.py:2159`), safe by construction. No `eval`/`vm`/`child_process` in the Node services. No `Command::new` in the aggregator or alpha-terminal.

### 8.3 LLM agent tool surface

All 19 `@tool` functions in `agents/deep-quant-loop/tools.py` are fixed-shape read-only market-data fetchers. There is no shell tool, no arbitrary-Python tool, and no arbitrary-SQL tool the model can drive. (The `timeframe` pass-through in §6.1 is the one place model output reaches query construction.)

### 8.4 Frontend XSS

`frontend/src/app/api/**` does not exist — the app is a static export (`output: 'export'`) wrapped by Tauri, so the entire server-side-route attack class is absent. Every `dangerouslySetInnerHTML` (`layout.tsx:37`, `page.tsx:166`, `TerminalLayout.tsx:309,349`, `RightSidebar.tsx:89`) and every `innerHTML` (`TradingViewWidget.tsx:32-52`) is fed by the static `SVGS` constant or a string literal. `utils/iframeDropdown.ts:129` interpolates `item.label`/`item.description`, but all three call sites pass hardcoded literals. No `postMessage` handlers exist anywhere in `frontend/src`.

### 8.5 Deep-link handlers other than login

`payment-success` and `broker-connection-success` emit an empty `json!({})` payload (`lib.rs:41`, `:51`), and an enumeration of all 15 frontend `listen()` call sites shows **no listener registered for either event**. Forged links of these types are inert; premium access derives from `accessFlags` fetched from the `/credit` API.

### 8.6 TLS, certificates, and outbound requests

No `danger_accept_invalid_certs` and no custom `ServerCertVerifier` in any Rust crate. All outbound hosts are hardcoded literals or env-derived (`api.kite.trade`, `AUTH_SERVICE_URL`, `QUESTDB_HTTP_URL`, `OPENROUTER_BASE_URL`); no host or protocol is request-controlled anywhere. `api_fetch` (`commands/security.rs:86-92`) enforces a correct dot-prefixed suffix allowlist (`localhost`, `127.0.0.1`, `*.stratai.live`) using `url::Url` parsing — no bypass found.

### 8.7 Kite integration correctness

The Kite Connect v3 checksum at `auth.service.ts:161-164` computes `SHA256(api_key + request_token + api_secret)` correctly. `quote_handler` re-encodes each `i=` parameter through `urlencoding::encode` (`kite_api.rs:585`), blocking query injection.

### 8.8 Control-protocol injection

`ticker.rs:123`/`:219` build `format!("subscribe:{}:{}\n", token, symbol)` for the :8085 control port, but a symbol containing an interior newline can never resolve an instrument token via either the SQLite or HTTP path, so the injection is unreachable. `option_chain_subscriber.rs:214` uses `serde_json` output, in which newlines are escaped.

### 8.9 CI/CD

Both GitHub workflows are clean. Triggers are `push` (tags / `main`) and `workflow_dispatch` — no `pull_request_target`, no checkout-and-execute of untrusted PR code. The only `${{ }}` values reaching a `run:` block are `vars.DEPLOY_PATH` and `secrets.*`, all repository-admin controlled. No untrusted PR-derived text (title, body, branch name, comment) is interpolated anywhere.

### 8.10 Tauri capabilities and browser-driven attacks

`capabilities/default.json` grants only `core:default` to the `main` window — no filesystem or shell plugin scopes. No `0.0.0.0` literal exists anywhere in `frontend/src-tauri/src`; the quant tool server defaults to `127.0.0.1` (`tool_server.rs:1964-1970`). Browser-driven CSRF and DNS rebinding against :8084 are blocked: axum's `Json<T>` extractor requires `Content-Type: application/json`, which forces a CORS preflight, and the router mounts no CORS layer or `OPTIONS` handler.

### 8.11 Secret logging in the desktop app

`llm.rs:496-499` masks the API key. `server.rs:197-215` `config_summary()` reports password *presence* only, with a regression test at `:302-317` asserting the value is never emitted.

---

## 9. Remediation plan

### Immediate (before any further production exposure)

| # | Action | Finding |
|---|---|---|
| 1 | Bind OAuth `state` to a server-side single-use nonce tied to an authenticated session; delete both `findFirstUser()` fallbacks; require auth on `/broker/zerodha/connect` | SR-01 |
| 2 | Invalidate all stored `brokerConnection` rows and require re-linking (they may be mis-bound) | SR-01 |
| 3 | Remove the `JWT_SECRET` fallback and the `docker-compose.yml` literal; fail boot when unset; **rotate the secret** | SR-02 |
| 4 | Delete the unsigned webhook fallback branch at `payment.controller.ts:38-47` | SR-03 |
| 5 | Remove the `INTERNAL_API_KEY` default; rotate it; move `/api/internal/*` off the public router | SR-04 |
| 6 | Rotate `KITE_API_SECRET` at Zerodha (currently defaulted in source) | SR-02 (related) |

### Short term (this sprint)

| # | Action | Finding |
|---|---|---|
| 7 | Migrate passwords to argon2id; force a global password reset; notify users about credential reuse | SR-05 |
| 8 | Remove client-driven tier setting; gate all tier changes behind verified payment or an admin role claim | SR-06 |
| 9 | Redact broker tokens before logging; purge affected log retention | SR-07 |
| 10 | Remove the PhonePe salt default; fail boot when absent; verify transactions via the status API | SR-03 (secondary) |
| 11 | Narrow `/auth/me` to `{broker, brokerUserId, userName, connected}` — removes the amplifier from Chains A and B | SR-01/SR-02 |
| 12 | Fix `_ => timeframe` → `_ => "10m"`; wire `validate_timeframe` into `get_candles` and `scan_radar_symbol` | §6.1 |
| 13 | Bind the deep-link login token to `sessionId` on both the Rust and server sides | §6.2 |

### Medium term (hardening backlog)

| # | Action | Ref |
|---|---|---|
| 14 | Bind `kite_api.rs` and all WebSocket servers to `127.0.0.1`; add a shared-secret check; replace `allow_origin(Any)` with an explicit origin | §6.3 |
| 15 | Allowlist `interval` and `exchange` in the Kite proxy | §6.4 |
| 16 | Namespace deep-quant checkpointer threads by authenticated user; generate `thread_id` server-side from a CSPRNG | §6.5 |
| 17 | Rotate the omniroute gateway key; blank `.env.example:54`; purge `bedrock-api-key.txt` from history | §6.6 |
| 18 | Replace `fetch_questdb` with parameterized IPC commands; add a scheme allowlist to `open_browser`; tighten the Tauri CSP | §6.7 |
| 19 | Add a startup configuration validator asserting every required secret is present and is not a known default value | Cross-cutting |
| 20 | Add secret scanning (gitleaks/trufflehog) to CI to prevent recurrence | Cross-cutting |

---

## Appendix A — the branch diff

The uncommitted change on `main` at the time of review:

```diff
--- a/.github/workflows/desktop-release.yml
+++ b/.github/workflows/desktop-release.yml
@@ -104,10 +104,10 @@ jobs:
         shell: bash
         run: |
           if [ -z "$QUESTDB_PASSWORD" ]; then
-            echo "::error::… Set it under Settings → Secrets and variables → Actions, …"
+            echo "::error::… Set it under Settings > Secrets and variables > Actions, …"
             exit 1
           fi
-          echo "QUESTDB_PASSWORD present (${#QUESTDB_PASSWORD} chars) — gateway auth will be baked in."
+          echo "QUESTDB_PASSWORD present (${#QUESTDB_PASSWORD} chars) - gateway auth will be baked in."
```

Two Unicode characters (`→`, `—`) replaced with ASCII equivalents inside literal message strings. **No security impact:**

- The secret presence check and its `exit 1` are unchanged.
- `${#QUESTDB_PASSWORD}` emits only the length, not the value.
- Both strings are static literals with no interpolated data, so no command-injection or annotation-injection path is introduced.
- No change to permissions, triggers, checkout refs, or action versions.

---

## Appendix B — coverage matrix

| Category | Reviewed | Findings |
|---|---|---|
| SQL injection | ✔ All 5 services | 0 (1 sub-threshold, §6.1) |
| Command injection | ✔ | 0 |
| Path traversal | ✔ | 0 |
| XXE / template injection | ✔ | 0 — no XML parsing; no dynamic templates |
| Deserialization RCE | ✔ | 0 — no pickle/unsafe YAML |
| Authentication bypass | ✔ | 1 (SR-02) |
| Authorization / IDOR | ✔ | 3 (SR-01, SR-04, SR-06) + 1 sub-threshold (§6.5) |
| Session management | ✔ | 1 sub-threshold (§6.2) |
| JWT handling | ✔ | 1 (SR-02) |
| Webhook / signature verification | ✔ | 1 (SR-03) |
| Hardcoded secrets | ✔ | 2 (SR-02, SR-04) + 2 sub-threshold |
| Password / credential storage | ✔ | 1 (SR-05) |
| Weak cryptography | ✔ | 0 — SHA-256 checksums are per-vendor spec |
| Certificate validation | ✔ | 0 |
| XSS (reflected/stored/DOM) | ✔ | 0 |
| CORS / CSP | ✔ | 0 (2 hardening, §6.3, §6.7) |
| SSRF | ✔ | 0 (path-only, §6.4) |
| Sensitive data exposure | ✔ | 1 (SR-07) |
| Debug information exposure | ✔ | 0 |
| CI/CD supply chain | ✔ | 0 |

---

*Findings SR-01 through SR-07 each passed an independent adversarial verification pass at confidence ≥ 8/10. Excluded categories are listed in §2.3. Line numbers reflect the working tree as of 2026-07-31.*
