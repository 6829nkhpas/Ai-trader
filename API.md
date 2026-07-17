# Strat AI — API Reference

Complete API documentation for the Strat AI backend. Use this to integrate the frontend.

> Base URL: `http://localhost:3000` (dev) / your production backend URL
> API prefix: `/api/v1`
> Swagger UI: `/api/v1/docs`

---

## Table of Contents

1. [Conventions](#conventions)
2. [Authentication](#authentication)
3. [Desktop App Auth Flow](#desktop-app-auth-flow)
4. [Auth Module](#auth-module)
5. [User Module](#user-module)
6. [Plans Module](#plans-module)
7. [Credit Module](#credit-module)
8. [Payments Module](#payments-module)
9. [Billing Module](#billing-module)
10. [Coupon Module](#coupon-module)
11. [System Module](#system-module)
12. [Support Module](#support-module)
13. [Wishlist Module](#wishlist-module)
14. [Logs Module (dev only)](#logs-module-dev-only)
15. [Error Reference](#error-reference)
16. [Enums & Types](#enums--types)

---

## Conventions

### Standard Response Envelope

All success responses use this shape:

```json
{
  "success": true,
  "message": "ok",
  "data": { ... }
}
```

| Class            | HTTP | `message`              | `data`        |
|------------------|------|------------------------|---------------|
| `OkResponse`     | 200  | `"ok"`                 | payload       |
| `CreatedResponse` | 201 | custom message         | payload       |
| `AcceptedResponse` | 202 | custom message         | `null`        |

### Error Envelope

```json
{
  "success": false,
  "message": "Invalid credentials"
}
```

No `data` field on errors. Validation errors return `400` with the first Zod issue message.

### Request Body

- `Content-Type: application/json` for all POST/PATCH bodies unless noted.
- Validation uses Zod; the **first** validation issue's message is returned on failure.

### Rate Limiting

Global IP-based limiter: **10,000 requests / 15 min** for anonymous, **100,000 / 15 min** for authenticated users. Returns `429` with `{ error: "Too Many Requests, please try again later!" }`.

### reCAPTCHA

Endpoints that accept a `recapchaToken` field verify it via Google reCAPTCHA **in production only**. In dev/non-production, reCAPTCHA is skipped (the token can be any string).

---

## Authentication

### Tokens

| Token          | Lifetime | Cookie name       | Purpose                              |
|----------------|----------|-------------------|--------------------------------------|
| Access token   | 15 min   | `access_token`    | Authorize API requests               |
| Refresh token  | 7 days   | `refresh_token`   | Obtain new access tokens             |

Both are JWTs signed with server secrets. The refresh token is stored as a row in `AuthSession` (one per device), enabling per-device logout.

### Cookie Attributes

- `httpOnly: true` (not readable by JS)
- `secure: true` in production (HTTPS only)
- `sameSite: 'none'` in production, `'lax'` in dev
- Production domain: `.stratai.live`
- Access token `maxAge`: 15 minutes; Refresh token `maxAge`: 8 days

### Sending Credentials

Authenticated requests can send the access token via either:

1. **Cookie** (browser flow): `Cookie: access_token=...` (automatic)
2. **Header** (mobile/desktop): `Authorization: Bearer <accessToken>`

The refresh token can be sent via:
1. **Cookie**: `refresh_token=...` (automatic)
2. **Body**: `{ "refresh": "<refreshToken>" }` to `/auth/refresh-token`

### Auth Middleware Behavior

| Middleware     | Behavior                                                            |
|----------------|---------------------------------------------------------------------|
| `verifyJWT`    | Requires valid access token (cookie or `Authorization` header). Throws `401` if missing/invalid. Sets `req.user = { id, role }`. |
| `AdminOnly`    | Requires `req.user.role === 'admin'`. Throws `403` otherwise.      |

### Access/Refresh Flow

1. Login/signup → server sets `access_token` + `refresh_token` cookies **and** returns `{ accessToken, refreshToken }` in the body.
2. Use access token for protected endpoints.
3. When access token expires (15 min), call `POST /auth/refresh-token`.
4. On logout, both cookies are cleared and the session row is deleted.

---

## Desktop App Auth Flow

The desktop app authenticates via a browser-based OAuth bridge. Flow:

```
Desktop App                  Backend (API)                 Browser
-----------                  ------------                  -------
1. POST /auth/desktop/session ──> creates session
   <── { sessionId, loginUrl }
2. opens loginUrl in browser ───────────────────────────> opens FRONTEND_URL/?session=<sessionId>
3. user logs in (login/signup/google)
   POST /auth/login?session=<sessionId> ──> authenticateSession()
   <── { loginToken, sessionId }  (no cookies set)
4. polls GET /auth/desktop/session/:sessionId
   <── { status: 'authenticated', token: loginToken }
5. POST /auth/desktop/exchange  { token: loginToken }
   <── { accessToken, refreshToken, user }
```

- Session expires in **10 minutes** if not authenticated.
- Login token is single-use, expires in **5 minutes**.
- The `/open` route serves an HTML landing page that opens the desktop app via a `strat://` deep link.

See [Desktop endpoints](#desktop-endpoints) below.

---

## Auth Module

Base: `/api/v1/auth`

### POST /auth/login

Login with email + password.

**Body:**
```json
{
  "email": "user@example.com",
  "password": "secret123",
  "recapchaToken": "string"
}
```
- `email`: valid email
- `password`: min 6 chars
- `recapchaToken`: reCAPTCHA token (skipped in dev)

**Query (optional):** `?session=<desktopSessionId>` — if provided, authenticates the desktop session instead of setting cookies.

**Response (200):**

Browser flow (sets `access_token` + `refresh_token` cookies):
```json
{
  "success": true,
  "message": "ok",
  "data": {
    "accessToken": "<jwt>",
    "refreshToken": "<jwt>"
  }
}
```

Desktop flow (`?session=` present):
```json
{
  "success": true,
  "message": "ok",
  "data": {
    "loginToken": "<uuid>",
    "sessionId": "<uuid>"
  }
}
```

**Errors:** `404` User not found, `400` invalid credentials.

---

### POST /auth/send-email-before-signup

Trigger OTP email before registration.

**Body:**
```json
{
  "email": "user@example.com",
  "recapchaToken": "string"
}
```

**Response (202):**
```json
{ "success": true, "message": "OTP sent to email.", "data": null }
```

OTP is valid for 5 minutes. Rate limits: 3 OTPs/hour, 60s cooldown between requests.

---

### POST /auth/signup

Register a new user. Requires the OTP sent to email.

**Body:**
```json
{
  "name": "Jane Doe",
  "email": "user@example.com",
  "token": "123456",
  "password": "secret123"
}
```
- `name`: min 3 chars
- `token`: OTP code (min 6 chars)
- `password`: min 6 chars

**Query (optional):** `?session=<desktopSessionId>` for desktop flow.

**Response (201):**

Browser flow:
```json
{
  "success": true,
  "message": "User registered successfully.",
  "data": { "accessToken": "<jwt>", "refreshToken": "<jwt>" }
}
```

Desktop flow:
```json
{
  "success": true,
  "message": "User registered successfully.",
  "data": { "loginToken": "<uuid>", "sessionId": "<uuid>" }
}
```

**Errors:** `400` Email already in use / Invalid or expired OTP.

---

### POST /auth/google

Google OAuth login. Exchange authorization `code` for tokens.

**Query:**
```
GET /auth/google?code=<googleAuthCode>&session=<optionalDesktopSessionId>
```
- `code`: Google OAuth authorization code

**Response (200):** Same shape as `/auth/login` (browser sets cookies; desktop returns `loginToken`/`sessionId`).

**Errors:** `400` Google access token not found.

---

### POST /auth/google/one-tap

Google One Tap login (credential from the Google Identity Services popup).

**Body:**
```json
{ "credential": "<googleIdToken>" }
```

**Query (optional):** `?session=<desktopSessionId>` or pass `session` in body.

**Response (200):** Same shape as `/auth/login`.

---

### POST /auth/refresh-token

Rotate the refresh token and issue a new access token.

**Body (optional):**
```json
{ "refresh": "<refreshToken>" }
```
If `refresh` is omitted, the server reads the `refresh_token` cookie.

**Response (200):** Sets new cookies + returns:
```json
{
  "success": true,
  "message": "ok",
  "data": { "accessToken": "<jwt>", "refreshToken": "<jwt>" }
}
```

**Errors:** `400` Refresh token not provided / Invalid or expired refresh token.

---

### POST /auth/logout

Logout from the current device. Clears cookies and deletes the session row. Also expires all desktop sessions for the user.

**Body (optional):**
```json
{ "refreshToken": "<refreshToken>" }
```
Or relies on the `refresh_token` cookie.

**Response (202):**
```json
{ "success": true, "message": "Logged out successfully.", "data": null }
```

---

### POST /auth/logout-all

Logout from all devices. **Requires auth.**

**Response (202):**
```json
{ "success": true, "message": "Logged out from all devices successfully.", "data": null }
```

---

### GET /auth/sessions

List all active sessions for the current user. **Requires auth.**

**Response (200):**
```json
{
  "success": true,
  "message": "ok",
  "data": [
    {
      "id": "<sessionId>",
      "agent": "Mozilla/5.0 ...",
      "ip": "1.2.3.4",
      "location": "City, Region, Country",
      "createdAt": "2026-07-17T...",
      "lastLoggedAt": "2026-07-17T...",
      "current": true
    }
  ]
}
```

---

### DELETE /auth/sessions/:id

Revoke a specific session. **Requires auth.**

**Response (200):**
```json
{
  "success": true,
  "message": "ok",
  "data": { "message": "Session revoked successfully." }
}
```

**Errors:** `404` Session not found.

---

### POST /auth/change-password

Change password while logged in. **Requires auth.**

**Body:**
```json
{
  "currentPassword": "oldpass",
  "newPassword": "newpass123"
}
```
- Both min 6 chars.

**Response (200):**
```json
{
  "success": true,
  "message": "ok",
  "data": { "message": "Password changed successfully." }
}
```

**Errors:** `400` Incorrect current password.

---

### POST /auth/forgot-password

Request a password reset OTP. **Public.**

**Body:**
```json
{ "email": "user@example.com", "recapchaToken": "string" }
```

**Response (202):**
```json
{ "success": true, "message": "Password reset OTP sent to email.", "data": null }
```

**Errors:** `404` User not found.

---

### POST /auth/verify-email

Verify an OTP without resetting (e.g., confirm ownership). **Public.**

**Body:**
```json
{ "email": "user@example.com", "token": "123456" }
```

**Response (202):**
```json
{ "success": true, "message": "Email verified successfully.", "data": null }
```

**Errors:** `404` User not found, `400` Invalid or expired OTP.

---

### POST /auth/reset-password

Reset password using OTP. **Public.**

**Body:**
```json
{
  "email": "user@example.com",
  "token": "123456",
  "newPassword": "newpass123"
}
```

**Response (202):**
```json
{ "success": true, "message": "Password reset successfully.", "data": null }
```

---

### Desktop Endpoints

Base: `/api/v1/auth/desktop`

#### POST /auth/desktop/session

Create a new desktop login session. **Public.**

**Body:** none (empty object, strict).

**Response (201):**
```json
{
  "success": true,
  "message": "Desktop session created",
  "data": {
    "sessionId": "<uuid>",
    "loginUrl": "http://localhost:3000/?session=<uuid>"
  }
}
```

#### POST /auth/desktop/exchange

Exchange a one-time login token for access + refresh tokens. **Public.**

**Body:**
```json
{ "token": "<loginTokenUuid>" }
```

**Response (200):**
```json
{
  "success": true,
  "message": "ok",
  "data": {
    "accessToken": "<jwt>",
    "refreshToken": "<jwt>",
    "user": {
      "id": "<uuid>",
      "email": "user@example.com",
      "name": "Jane",
      "username": "usr_abc123",
      "role": "user"
    }
  }
}
```

**Errors:** `400` Invalid login token / already used / expired / no user.

#### GET /auth/desktop/session/:sessionId

Poll session status. **Public.**

**Params:** `sessionId` (UUID)

**Response (200):**

Pending:
```json
{ "success": true, "message": "ok", "data": { "status": "pending" } }
```

Authenticated (token available for exchange):
```json
{
  "success": true,
  "message": "ok",
  "data": { "status": "authenticated", "token": "<loginToken>" }
}
```

Expired:
```json
{ "success": true, "message": "ok", "data": { "status": "expired" } }
```

**Errors:** `404` Desktop session not found.

---

## User Module

Base: `/api/v1/users` — **All endpoints require auth.**

### GET /users/me

Get the current user's profile.

**Response (200):**
```json
{
  "success": true,
  "message": "ok",
  "data": {
    "id": "<uuid>",
    "email": "user@example.com",
    "username": "usr_abc123",
    "name": "Jane",
    "role": "user",
    "status": "active",
    "createdAt": "2026-07-17T...",
    "updatedAt": "2026-07-17T..."
  }
}
```

`passwordHash` is omitted from all user responses.

---

### PATCH /users/me

Update the current user's profile.

**Body:**
```json
{ "name": "New Name" }
```
- `name`: optional, min 3 chars (currently only `name` is editable).

**Response (200):** Updated user profile (same shape as `/users/me`).

---

### GET /users/

List all users (paginated, searchable). **Admin only.**

**Query:**
| Param   | Type   | Default | Notes                              |
|---------|--------|---------|------------------------------------|
| `page`  | number | 1       | 1-based                            |
| `limit` | number | 10      |                                    |
| `search`| string | —       | Matches name/email/username        |

**Response (200):**
```json
{
  "success": true,
  "message": "ok",
  "data": {
    "users": [
      {
        "id": "<uuid>",
        "email": "user@example.com",
        "username": "usr_abc123",
        "name": "Jane",
        "role": "user",
        "status": "active",
        "createdAt": "...",
        "updatedAt": "...",
        "apiConnected": true,
        "subscription": {
          "planName": "Pro",
          "credits": 150.5,
          "expiresAt": "2026-08-17T...",
          "accessFlags": {
            "canAccessDeepseekGLM": true,
            "canAccessMultiModel": false,
            "canAccessGhostline": false,
            "canAccessFootprint": false,
            "canAccessTopup": true,
            "canSeeInstantNewsSantiments": false,
            "canGetAdvanceChartAccess": false
          },
          "creditMultiplier": 200
        }
      }
    ],
    "total": 42,
    "page": 1,
    "limit": 10
  }
}
```

`subscription` is `null` if the user has no subscription.

**Errors:** `401`, `403`.

---

### GET /users/:id

Get detailed user info (subscription, API keys, payments, credit logs, activities). **Admin only.**

**Response (200):**
```json
{
  "success": true,
  "message": "ok",
  "data": {
    "id": "<uuid>",
    "email": "...",
    "username": "...",
    "name": "...",
    "role": "user",
    "status": "active",
    "subscription": { "planName": "...", "credits": 0, "expiresAt": null, "accessFlags": { ... }, "creditMultiplier": null },
    "apiKeys": [
      { "id": "...", "userId": "...", "key": "sk_...", "keyHash": "...", "status": "active", "provider": "openrouter", "lastSyncedUsage": 0, "createdAt": "...", "updateAt": "..." }
    ],
    "payments": [ { ...Payment, "statusHistory": [ ... ] } ],
    "creditLogs": [ { "amount": 10, "previousBalance": 0, "newBalance": 10, "type": "subscription", "description": "...", "createdAt": "..." } ],
    "activities": [ { ...Activity } ]
  }
}
```

`creditLogs` amounts are divided by 1000 (returned in credit units, not internal milli-credits).

**Errors:** `401`, `403`, `404`.

---

### PATCH /users/:id/status

Change a user's status. **Admin only.**

**Body:**
```json
{ "status": "blocked" }
```
- `status`: `"active"` | `"blocked"` | `"deleted"`

**Response (200):** Updated user profile.

**Errors:** `401`, `403`, `404`.

---

### POST /users/:id/credits

Adjust a user's credit balance (admin manual adjustment). **Admin only.**

**Body:**
```json
{ "amount": 50 }
```
- `amount`: number (positive to add, negative to deduct)

If the user has no subscription, one is created on the `basic` plan with 0 credits before adjusting.

**Response (200):**
```json
{
  "success": true,
  "message": "ok",
  "data": {
    "credits": 50,
    "subscription": { ...UserSubscription }
  }
}
```

**Errors:** `401`, `403`, `404`.

---

## Plans Module

Base: `/api/v1/plans`

### GET /plans/

List all active (non-deleted) plans, ordered by `priceINR` ascending. **Public.**

**Response (200):**
```json
{
  "success": true,
  "message": "ok",
  "data": [
    {
      "id": "<uuid>",
      "name": "Basic",
      "priceINR": 499,
      "creditsGiven": 100,
      "description": "...",
      "canAccessDeepseekGLM": false,
      "canAccessMultiModel": false,
      "canAccessGhostline": false,
      "canAccessFootprint": false,
      "canAccessTopup": false,
      "canSeeInstantNewsSantiments": false,
      "canGetAdvanceChartAccess": false,
      "creditMultiplier": null,
      "deletedAt": null,
      "createdAt": "...",
      "updatedAt": "..."
    }
  ]
}
```

---

### GET /plans/:id

Get a single plan. **Public.**

**Response (200):** A single `Plan` object (see above).

**Errors:** `404` Plan not found.

---

### PATCH /plans/:id

Update a plan. **Admin only.**

**Body:** any subset of Plan fields:
```json
{
  "name": "Pro",
  "priceINR": 999,
  "creditsGiven": 500,
  "description": "Updated",
  "canAccessTopup": true,
  "creditMultiplier": 200
}
```

**Response (200):** Updated plan.

**Errors:** `400`, `401`, `403`, `404`.

---

### DELETE /plans/:id

Soft-delete a plan (sets `deletedAt`). **Admin only.**

**Response (200):**
```json
{ "success": true, "message": "Plan deleted successfully", "data": null }
```

**Errors:** `401`, `403`, `404`.

---

## Credit Module

Base: `/api/v1/credit` — **Requires auth.**

### GET /credit/

Get the current user's credit balance, subscription status, access flags, and recent transactions.

**Response (200):**
```json
{
  "success": true,
  "message": "ok",
  "data": {
    "hasActiveSubscription": true,
    "credits": 150.5,
    "planName": "Pro",
    "expiresAt": "2026-08-17T...",
    "accessFlags": {
      "canAccessDeepseekGLM": true,
      "canAccessMultiModel": false,
      "canAccessGhostline": false,
      "canAccessFootprint": false,
      "canAccessTopup": true,
      "canSeeInstantNewsSantiments": false,
      "canGetAdvanceChartAccess": false
    },
    "creditMultiplier": 200,
    "creditLogs": [
      {
        "id": "...",
        "userId": "...",
        "amount": 100,
        "previousBalance": 0,
        "newBalance": 100,
        "type": "subscription",
        "description": "Subscribed to plan: Pro",
        "createdAt": "..."
      }
    ]
  }
}
```

If no active subscription:
```json
{
  "success": true,
  "message": "ok",
  "data": {
    "hasActiveSubscription": false,
    "credits": 0,
    "planName": "none",
    "expiresAt": null,
    "accessFlags": { ...all false },
    "creditLogs": [ ... ]
  }
}
```

`creditLogs` returns the **last 50** transactions. Amounts are in credit units (divided by 1000 from internal storage).

---

## Payments Module

Base: `/api/v1/payments`

### POST /payments/subscribe

Create a subscription checkout session for a plan. **Requires auth.**

**Body:**
```json
{ "planId": "<uuid>" }
```

**Response (201):**
```json
{
  "success": true,
  "message": "Subscription checkout session created successfully",
  "data": {
    "paymentId": "<uuid>",
    "invoiceId": "INV-...",
    "gatewayOrderId": "<phonePeOrderId>",
    "gatewayPaymentId": "pay_<hex>",
    "checkoutUrl": "https://phonepe.com/..."
  }
}
```

Redirect the user to `checkoutUrl`. After payment, poll `/payments/verify-status`.

**Errors:** `401`, `404` Plan not found.

---

### POST /payments/topup

Create a top-up checkout session to buy additional credits. **Requires auth.**

The user must have an active subscription with `canAccessTopup: true`.

**Body:**
```json
{ "credits": 50 }
```
- `credits`: positive integer

The INR amount is computed as `credits * USD_to_INR_rate * creditMultiplier`. The multiplier is normalized: if `>= 100`, divided by 100 (so `200` = `2x`).

**Response (201):** Same shape as `/payments/subscribe`.

**Errors:** `400` Can't top up / subscription expired, `401`.

---

### GET /payments/verify-status

Verify the status of a payment by polling PhonePe. **Requires auth.**

**Query:**
```
?merchantOrderId=pay_<hex>
```

**Response (200):**
```json
{
  "success": true,
  "message": "ok",
  "data": {
    "status": "paid",
    "paymentId": "<uuid>"
  }
}
```
`status` is `"pending"` | `"paid"` | `"failed"`. If still pending, the frontend should retry. When status becomes `paid`, the backend automatically provisions credits + OpenRouter API key.

**Errors:** `400` merchantOrderId required, `404` Payment not found, `400` no access.

---

### POST /payments/webhook

Webhook receiver for PhonePe payment status callbacks. **Public** (verified via the `Authorization` header signed by PhonePe).

**Headers:** `Authorization: <phonePeSignature>`

**Body:** raw PhonePe webhook payload (server reads `req.body` and stringifies it).

**Response (200):**
```json
{
  "success": true,
  "message": "ok",
  "data": { ...updatedPayment }
}
```

The server validates the signature via the PhonePe SDK, maps the callback `type` to an internal status, writes a `PaymentStatusHistory` row, and—if `paid`—runs the billing logic (provision credits + OpenRouter key).

**Errors:** `400` Authorization header missing, `404` payment/order not found.

---

## Billing Module

Base: `/api/v1/billing` — **Requires auth.**

### GET /billing/history

Get the current user's payment history (with status history per payment).

**Response (200):**
```json
{
  "success": true,
  "message": "ok",
  "data": [
    {
      "id": "<uuid>",
      "userId": "<uuid>",
      "invoiceId": "INV-...",
      "gatewayPaymentId": "pay_...",
      "gatewayOrderId": "...",
      "webhookEventId": "...",
      "processing": false,
      "amount": 499,
      "type": "subscription",
      "planId": "<uuid>",
      "topupCredits": null,
      "createdAt": "...",
      "updatedAt": "...",
      "statusHistory": [
        { "id": "...", "paymentId": "...", "status": "pending", "createdAt": "..." },
        { "id": "...", "paymentId": "...", "status": "paid", "createdAt": "..." }
      ]
    }
  ]
}
```

---

### GET /billing/:id

Get a specific invoice/payment for the current user.

**Response (200):** A `Payment` object including `statusHistory` and a `user` brief:
```json
{
  "success": true,
  "message": "ok",
  "data": {
    ...Payment,
    "user": { "id": "...", "name": "...", "email": "..." },
    "statusHistory": [ ... ]
  }
}
```

**Errors:** `401`, `404` Invoice not found.

---

### GET /billing/all

Get all platform payments (all users). **Admin only.**

**Response (200):** Array of `Payment` objects, each with a `user` brief and `statusHistory`.

**Errors:** `401`, `403`.

---

### POST /billing/invoices/:id/refund

Mark an invoice as refunded. **Admin only.**

**Response (200):** The created `PaymentStatusHistory` row:
```json
{
  "success": true,
  "message": "ok",
  "data": { "id": "...", "paymentId": "...", "status": "refunded", "createdAt": "..." }
}
```

**Errors:** `404` Invoice not found, `400` already refunded.

---

## Coupon Module

Base: `/api/v1/coupons` — **Requires auth.** `/validate` is user-accessible; all others are **Admin only.**

### POST /coupons/validate

Validate a coupon code against a price. Checks: active, not expired, meets min price, not already used by this user.

**Body:**
```json
{
  "code": "SAVE10",
  "price": 999
}
```

**Response (200):** The coupon object:
```json
{
  "success": true,
  "message": "ok",
  "data": {
    "id": "<uuid>",
    "code": "SAVE10",
    "discount": 10,
    "minPrice": 500,
    "expiresOn": "2026-12-31T...",
    "status": "active",
    "isGlobal": true,
    "deletedAt": null,
    "createdAt": "...",
    "updatedAt": "..."
  }
}
```

**Errors:** `400` Invalid coupon / expired / below min price / already used.

---

### POST /coupons/

Create a coupon. **Admin only.**

**Body:**
```json
{
  "code": "SAVE10",
  "discount": 10,
  "minPrice": 500,
  "expiresOn": "2026-12-31T23:59:59Z",
  "isGlobal": true,
  "status": "active"
}
```
- `discount`: positive number
- `minPrice`: non-negative
- `expiresOn`: ISO datetime string
- `status`: `"active"` (default) | `"inactive"`

**Response (201):** The created coupon.

**Errors:** `400` Code already exists, `401`, `403`.

---

### GET /coupons/

List all active (non-deleted) coupons. **Admin only.**

**Response (200):** Array of coupon objects.

---

### PATCH /coupons/:id

Update a coupon. **Admin only.**

**Body:** any subset of: `code`, `discount`, `minPrice`, `expiresOn`, `isGlobal`, `status`.

**Response (200):** Updated coupon.

**Errors:** `400`, `401`, `403`, `404` Coupon not found.

---

### GET /coupons/:id/usages

Get usage logs for a coupon. **Admin only.**

**Response (200):**
```json
{
  "success": true,
  "message": "ok",
  "data": [
    {
      "id": "<uuid>",
      "userId": "<uuid>",
      "couponId": "<uuid>",
      "usedAt": "...",
      "paymentId": null,
      "expiresAt": null,
      "user": { "id": "...", "name": "...", "email": "..." }
    }
  ]
}
```

**Errors:** `401`, `403`, `404` Coupon not found.

---

## System Module

Base: `/api/v1/system`

### GET /system/config

Get public system configurations. **Public.**

**Query (optional):** `?key=<configKey>` — returns a single config object. Without `key`, returns all configs.

**Response (200, single):**
```json
{
  "success": true,
  "message": "ok",
  "data": { "id": "...", "key": "maintenance_mode", "value": "false", "updatedAt": "..." }
}
```

**Response (200, all):** Array of config objects.

**Errors:** `404` (only when `key` is specified and not found).

---

### PATCH /system/config

Upsert a system config. **Admin only.**

**Body:**
```json
{ "key": "maintenance_mode", "value": "true" }
```

**Response (200):** The upserted config object.

**Errors:** `400`, `401`, `403`.

---

### GET /system/notifications

Get the current user's notification feed. **Requires auth.**

**Response (200):**
```json
{
  "success": true,
  "message": "ok",
  "data": [
    {
      "id": "<uuid>",
      "userId": "<uuid>",
      "title": "Welcome",
      "description": "...",
      "isRead": false,
      "priority": "high",
      "createdAt": "...",
      "time": "..."
    }
  ]
}
```

---

### PATCH /system/notifications/:id/read

Mark a notification as read. **Requires auth.**

**Response (200):** The updated notification.

**Errors:** `404` Notification not found.

---

### GET /system/activities

Get the current user's activity logs. **Requires auth.**

**Response (200):**
```json
{
  "success": true,
  "message": "ok",
  "data": [
    {
      "id": "<uuid>",
      "userId": "<uuid>",
      "title": "USER_LOGIN",
      "data": "{...json string...}",
      "before": null,
      "after": null,
      "ip": "1.2.3.4",
      "type": "frequent",
      "createdAt": "..."
    }
  ]
}
```

---

### GET /system/activities/all

Get all activities across all users. **Admin only.**

**Response (200):** Array of activities, each including a `user` brief:
```json
{
  "...Activity fields...",
  "user": { "id": "...", "name": "...", "email": "..." }
}
```

---

### GET /system/telemetry

Get live system health telemetry. **Admin only.**

Returns health status for monitored services (DB, Redis, API gateway, etc.) with CPU/memory/latency/uptime.

**Response (200):**
```json
{
  "success": true,
  "message": "ok",
  "data": [
    {
      "id": "API-GATEWAY",
      "name": "API Gateway Node",
      "status": "healthy",
      "uptime": "2d 5h 30m",
      "latency": "12ms",
      "cpu": 24,
      "memory": 45
    }
  ]
}
```
- `status`: `"healthy"` | `"degraded"` | `"failing"`

---

### POST /system/services

Add a monitored service. **Admin only.**

**Body:**
```json
{
  "id": "NEW-SERVICE",
  "name": "My Service",
  "type": "ping",
  "url": "http://example.com/health"
}
```
- `id`: uppercased automatically
- `type`: `"db"` | `"redis"` | `"ping"` | `"simulated"`
- `url`: required for `ping` type

**Response (200):** Updated array of all services.

---

### DELETE /system/services/:id

Remove a monitored service. **Admin only.**

**Response (200):** Updated array of remaining services.

---

## Support Module

Base: `/api/v1/support`

### POST /support/contact

Submit a pre-sales contact form inquiry. **Public.**

**Body:**
```json
{
  "email": "user@example.com",
  "mobile": "+1234567890",
  "type": "sales"
}
```
- `mobile`: min 5 chars
- `type`: free-text category

**Response (201):**
```json
{
  "success": true,
  "message": "Contact form submitted successfully",
  "data": {
    "id": "<uuid>",
    "email": "...",
    "mobile": "...",
    "type": "...",
    "status": "pending",
    "reply": null,
    "createdAt": "...",
    "updatedAt": "..."
  }
}
```

---

### POST /support/complaints

Create a new complaint ticket. **Requires auth.** One active complaint per user.

**Body:**
```json
{
  "type": "billing",
  "description": "I was charged twice for my subscription"
}
```
- `description`: min 5 chars

**Response (201):**
```json
{
  "success": true,
  "message": "Complaint created successfully",
  "data": {
    "id": "<uuid>",
    "userId": "<uuid>",
    "type": "billing",
    "description": "...",
    "status": "pending",
    "reply": []
  }
}
```

**Errors:** `400` Already have a registered complaint.

---

### GET /support/complaints

List complaints. **Requires auth.** Returns the user's complaints, or all complaints if admin.

**Response (200):** Array of complaints with nested `reply` array. For admins, each includes a `user` brief:
```json
{
  "id": "<uuid>",
  "userId": "<uuid>",
  "type": "billing",
  "description": "...",
  "status": "pending",
  "reply": [
    { "id": "...", "role": "user", "message": "...", "complaintId": "..." }
  ],
  "user": { "id": "...", "name": "...", "email": "..." }
}
```

---

### POST /support/complaints/:id/reply

Add a reply to a complaint thread. **Requires auth.** Users can only reply to their own; admins can reply to any.

**Body:**
```json
{ "message": "Thanks, looking into it" }
```

**Response (201):**
```json
{
  "success": true,
  "message": "Reply added successfully",
  "data": { "id": "...", "role": "support", "message": "...", "complaintId": "..." }
}
```
- `role`: `"user"` (for regular users) or `"support"` (for admins)

**Errors:** `403` Not authorized, `404` Complaint not found.

---

### PATCH /support/complaints/:id/status

Update a complaint's status. **Admin only.**

**Body:**
```json
{ "status": "processing" }
```
- `status`: `"pending"` | `"processing"` | `"completed"` | `"rejected"`

**Response (200):** The updated complaint.

---

## Wishlist Module

Base: `/api/v1/wishlist` — **Public.**

### POST /wishlist/email

Add an email to the waitlist. Sends a confirmation email.

**Body:**
```json
{
  "name": "Jane",
  "email": "jane@example.com",
  "recapchaToken": "string"
}
```
- `name`: min 3 chars
- `email`: valid email

**Response (201):**
```json
{
  "success": true,
  "message": "Wishlist created successfully",
  "data": { "count": "42" }
}
```
`count` is the total waitlist count (as a string).

**Errors:** `400` Wishlist already exists.

---

### POST /wishlist/google

Add to waitlist via Google OAuth (fetches name/email from Google).

**Query:**
```
GET /wishlist/google?code=<googleAuthCode>
```

**Response (201):** Same shape as `/wishlist/email`.

---

## Logs Module (dev only)

Base: `/api/v1/logs`

### GET /logs/

Returns an HTML page of server logs (paginated, searchable). **Only available in development** (`NODE_ENV=development`); returns `403 Forbidden` otherwise.

**Query:**
| Param   | Default | Notes                  |
|---------|---------|------------------------|
| `page`  | 1       |                        |
| `limit` | 50      |                        |
| `search`| ""      | Case-insensitive filter|

### WebSocket: /api/v1/logs/live

Live log streaming via WebSocket at `ws://localhost:<PORT>/api/v1/logs/live`.

---

## Error Reference

All errors return JSON with `{ success: false, message }`.

| HTTP | Error class            | When                                                  |
|------|------------------------|-------------------------------------------------------|
| 400  | `ValidationError`      | Zod validation failure, business rule violation       |
| 401  | `UnauthorizedError`    | Missing/invalid access token                          |
| 403  | `ForbiddenError`       | Authenticated but not admin (or not owner)            |
| 404  | `NotFoundError`        | Resource not found                                     |
| 429  | `TooManyRequestError`  | Rate limit exceeded (global limiter)                  |
| 500  | (generic)              | Unhandled server error; message is generic           |
| 502  | `DatabaseError`        | Database operation failed                             |

Unknown errors return `{ success: false, message: "Internal Server Error" }` with status `500`.

---

## Enums & Types

### UserRole
`user` | `admin`

### UserStatus
`active` | `blocked` | `deleted`

### ApiKeyStatus
`active` | `inactive`

### NotificationPriority
`low` | `medium` | `high`

### ActivityType
`frequent` | `likely` | `possible` | `rare` | `unlikely`

### CouponStatus
`active` | `inactive`

### PaymentStatus
`pending` | `paid` | `failed` | `refunded`

### PaymentType
`subscription` | `topup`

### DesktopSessionStatus
`pending` | `authenticated` | `expired`

### ContactStatus
`pending` | `processing` | `completed` | `rejected`

### ReplyRole
`user` | `support`

### MonitoredServiceType
`db` | `redis` | `ping` | `simulated`

### HealthStatus
`healthy` | `degraded` | `failing`

### Access Flags (Plan / Subscription)
- `canAccessDeepseekGLM`
- `canAccessMultiModel`
- `canAccessGhostline`
- `canAccessFootprint`
- `canAccessTopup`
- `canSeeInstantNewsSantiments`
- `canGetAdvanceChartAccess`
- `creditMultiplier` (nullable; `200` = 2x, normalized by `/100` when `>= 100`)

### Credits

Credits are stored internally as integers × 1000 (milli-credits). All API responses divide by 1000, so `credits: 150.5` means 150.5 credits. Credit logs also return amounts divided by 1000.

---

## Integration Notes

1. **Browser (web) flow:** Use `credentials: 'include'` on all fetch/axios calls so cookies are sent. The server sets `access_token` + `refresh_token` as httpOnly cookies on login/signup.

2. **Mobile/desktop flow:** Don't rely on cookies. Capture `accessToken`/`refreshToken` from the login response body and send them as `Authorization: Bearer <accessToken>`. For refresh, send `{ refresh: <refreshToken> }` in the body.

3. **Token refresh:** Access tokens expire in 15 minutes. On `401`, call `POST /auth/refresh-token` and retry the original request. The refresh endpoint rotates the refresh token (old one becomes invalid).

4. **403 vs 401:** `401` = not logged in. `403` = logged in but lacks admin role (or doesn't own the resource).

5. **reCAPTCHA:** Required in production for `/auth/login`, `/auth/send-email-before-signup`, `/auth/forgot-password`, and `/wishlist/email`. Pass the token from the Google reCAPTCHA widget.

6. **OTP rate limits:** Max 3 OTPs per email per hour, 60s cooldown between requests. OTPs expire in 5 minutes.

7. **Payment flow:** Create checkout → redirect user to `checkoutUrl` → after redirect, poll `GET /payments/verify-status?merchantOrderId=<gatewayPaymentId>` until `status` is `paid` or `failed`. The webhook endpoint is a backup; rely on polling for UX.

8. **CORS:** Allowed origins are configured server-side via `allowedDomains`. Credentials are enabled (`credentials: true`), so the frontend origin must be in the allowlist.

9. **Production cookies:** In production, cookies are `secure` + `sameSite=none` with domain `.stratai.live`. The frontend and backend must be served over HTTPS and share the `.stratai.live` parent domain for cookies to flow.

10. **Swagger UI:** Interactive docs available at `/api/v1/docs` when the server is running.
