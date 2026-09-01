//! Internal service credential for the watcher's `POST /resume` handoff.
//!
//! # Why the watcher signs as a SERVICE, not as a user
//!
//! When a price watch fires, this server POSTs `/resume` to the deep-quant service.
//! It has no user session and never will: it is a headless background process woken
//! by a candle, not by a request. Once `/resume` is authenticated, the watcher
//! therefore needs a credential of its own — and it must be a *different* credential
//! from a user assertion, signed with a *different* secret, so that:
//!
//!   * a compromised watcher cannot mint a user identity and read someone's sessions,
//!     and
//!   * a leaked user-identity secret cannot drive the resume path.
//!
//! Giving the watcher a synthesised user identity would have been fewer moving parts
//! and would have been exactly the fake authentication this boundary exists to avoid.
//! The owning user is read from the run row on the deep-quant side instead.
//!
//! # Wire format
//!
//! `base64url(payload_json).base64url(hmac_sha256(payload_segment, secret))`, both
//! unpadded. The payload is `{"exp":<f>,"iat":<f>,"svc":"<name>"}` with keys in
//! sorted order. This is the third implementation of the same MAC — the others are
//! `agents/deep-quant-loop/internal_identity.py` (the verifier) and
//! `frontend/src/app/api/_identity.ts` (the user-identity minter) — so all three
//! assert the same fixed test vector. A divergence has to be a failing unit test
//! rather than a watcher that silently stops resuming runs in production.
//!
//! The MAC covers the *encoded payload segment*, never a re-serialisation, so the
//! three languages do not have to agree on JSON key order or escaping.

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine as _;
use hmac::{Hmac, Mac};
use sha2::Sha256;

/// Header the deep-quant service reads. Must match `internal_identity.HEADER_SERVICE`.
pub const SERVICE_HEADER: &str = "X-StratAI-Service";

/// Identifies this service in the assertion. Informational on the verifying side.
pub const SERVICE_NAME: &str = "tool-server";

/// Assertion lifetime. One hop on a private network; only has to cover clock skew.
const TTL_SECONDS: f64 = 60.0;

/// Minimum secret length. `openssl rand -hex 32` yields 64 chars, which is the
/// documented way to generate these. A short secret is a brute-forceable MAC.
const MIN_SECRET_CHARS: usize = 32;

/// Mint an assertion for `service`, valid from `iat` for `ttl` seconds.
///
/// Separated from [`service_header`] purely so the shared test vector can pin an
/// exact output at a fixed timestamp.
pub fn sign_service(secret: &str, service: &str, iat: f64, ttl: f64) -> String {
    // `serde_json` would reorder nothing here, but writing the object literally
    // keeps the byte-for-byte correspondence with the other two implementations
    // visible at the call site rather than depending on a serialiser's behaviour.
    let json = format!(
        r#"{{"exp":{},"iat":{},"svc":"{}"}}"#,
        format_ts(iat + ttl),
        format_ts(iat),
        service
    );
    let payload = URL_SAFE_NO_PAD.encode(json.as_bytes());

    let mut mac = <Hmac<Sha256>>::new_from_slice(secret.as_bytes())
        .expect("HMAC accepts a key of any length");
    mac.update(payload.as_bytes());
    let sig = URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes());

    format!("{payload}.{sig}")
}

/// Render a timestamp the way Python's `json.dumps` renders the same float.
///
/// This is not cosmetic. Python writes an integral float as `1700000000.0`, and Rust's
/// default `{}` for `f64` writes `1700000000` — so the JSON bytes would differ, the
/// base64 payload would differ, and the shared test vector would not match even though
/// both sides computed the MAC correctly. Since the verifier MACs the received bytes,
/// a mismatch here would NOT break verification in production; it would only make the
/// cross-language vector test fail and hide a real divergence in the noise. Keeping the
/// encodings identical is what keeps that test meaningful.
fn format_ts(v: f64) -> String {
    if v.fract() == 0.0 && v.is_finite() {
        format!("{v:.1}")
    } else {
        format!("{v}")
    }
}

/// The secret, if this deployment has a usable one.
///
/// `None` means "do not send the header". That is the correct behaviour for local
/// development and for every stage of the rollout before enforcement is switched on:
/// the deep-quant side treats a missing credential as acceptable while
/// `DEEP_QUANT_REQUIRE_IDENTITY` is off, and the watcher must keep resuming runs
/// throughout. A too-short secret is logged rather than silently ignored, because a
/// truncated value in a `.env` would otherwise present as "enforcement broke the
/// watcher" much later.
fn service_secret() -> Option<String> {
    let raw = std::env::var("INTERNAL_SERVICE_SECRET").ok()?;
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return None;
    }
    if trimmed.len() < MIN_SECRET_CHARS {
        log::warn!(
            "[watcher] INTERNAL_SERVICE_SECRET is {} chars, need >= {}. Not sending the \
             service credential — /resume will be refused once \
             DEEP_QUANT_REQUIRE_IDENTITY is on. Generate one with `openssl rand -hex 32`.",
            trimmed.len(),
            MIN_SECRET_CHARS
        );
        return None;
    }
    Some(trimmed.to_string())
}

/// The `X-StratAI-Service` header value for a resume handoff, or `None` when this
/// deployment has no secret configured.
pub fn service_header() -> Option<String> {
    let secret = service_secret()?;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    Some(sign_service(&secret, SERVICE_NAME, now, TTL_SECONDS))
}

#[cfg(test)]
mod tests {
    use super::*;

    // Mirrors `internal_identity.VECTOR_*` on the Python side.
    const VECTOR_SECRET: &str =
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    const VECTOR_IAT: f64 = 1_700_000_000.0;
    const VECTOR_TTL: f64 = 60.0;

    /// Captured from the Python reference implementation:
    ///
    /// ```text
    /// python -c "import internal_identity as i, json; \
    ///   c={'svc':'tool-server','iat':1700000000.0,'exp':1700000060.0}; \
    ///   p=i._b64u_encode(json.dumps(c,separators=(',',':'),sort_keys=True).encode()); \
    ///   print(p, i._mac(i.VECTOR_SECRET, p))"
    /// ```
    ///
    /// If this changes, the Python verifier changed and BOTH must move in one commit.
    const EXPECTED: &str = concat!(
        "eyJleHAiOjE3MDAwMDAwNjAuMCwiaWF0IjoxNzAwMDAwMDAwLjAsInN2YyI6InRvb2wtc2VydmVyIn0",
        ".",
        "AZDAbxfhnkl9MiOPy9HXRcFHJ_2BJeuvaw620bPyR1w"
    );

    #[test]
    fn reproduces_the_python_reference_vector_exactly() {
        // The assertion that keeps three implementations of one MAC honest.
        let token = sign_service(VECTOR_SECRET, SERVICE_NAME, VECTOR_IAT, VECTOR_TTL);
        assert_eq!(
            token, EXPECTED,
            "diverged from the Python verifier — /resume would be refused in production \
             once DEEP_QUANT_REQUIRE_IDENTITY is on"
        );
    }

    #[test]
    fn payload_decodes_to_the_expected_claims() {
        let token = sign_service(VECTOR_SECRET, SERVICE_NAME, VECTOR_IAT, VECTOR_TTL);
        let payload = token.split('.').next().unwrap();
        let decoded = URL_SAFE_NO_PAD.decode(payload).expect("valid base64url");
        let json = String::from_utf8(decoded).expect("valid utf8");
        assert_eq!(
            json,
            r#"{"exp":1700000060.0,"iat":1700000000.0,"svc":"tool-server"}"#
        );
    }

    #[test]
    fn token_is_unpadded_base64url() {
        let token = sign_service(VECTOR_SECRET, SERVICE_NAME, VECTOR_IAT, VECTOR_TTL);
        assert!(!token.contains('='), "padding must be stripped: {token}");
        assert!(!token.contains('+'), "must be the url-safe alphabet: {token}");
        assert!(!token.contains('/'), "must be the url-safe alphabet: {token}");
        assert_eq!(token.split('.').count(), 2);
    }

    #[test]
    fn mac_is_43_chars_unpadded_sha256() {
        let token = sign_service(VECTOR_SECRET, SERVICE_NAME, VECTOR_IAT, VECTOR_TTL);
        let mac = token.split('.').nth(1).unwrap();
        assert_eq!(mac.len(), 43);
    }

    #[test]
    fn mac_changes_with_the_secret() {
        let a = sign_service(VECTOR_SECRET, SERVICE_NAME, VECTOR_IAT, VECTOR_TTL);
        let b = sign_service("f".repeat(64).as_str(), SERVICE_NAME, VECTOR_IAT, VECTOR_TTL);
        assert_ne!(a, b);
    }

    #[test]
    fn mac_changes_with_the_timestamp() {
        let a = sign_service(VECTOR_SECRET, SERVICE_NAME, VECTOR_IAT, VECTOR_TTL);
        let b = sign_service(VECTOR_SECRET, SERVICE_NAME, VECTOR_IAT + 1.0, VECTOR_TTL);
        assert_ne!(a, b);
    }

    #[test]
    fn integral_timestamps_render_like_python() {
        // The one detail that would silently break the shared vector.
        assert_eq!(format_ts(1_700_000_000.0), "1700000000.0");
        assert_eq!(format_ts(1_700_000_000.5), "1700000000.5");
    }
}
