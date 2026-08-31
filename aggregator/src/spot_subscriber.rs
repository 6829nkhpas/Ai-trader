// spot_subscriber.rs — tells the ingestion service which EQUITY/INDEX instruments
// to stream ticks for.
//
// WHY THIS EXISTS
// ---------------
// The ingestion service starts with an EMPTY instrument map by design and waits for
// `subscribe:TOKEN:SYMBOL` lines on its TCP control port (:8085). Its own log says
// so on every boot:
//
//   Instrument map initialised EMPTY. Subscriptions arrive dynamically via TCP control port.
//   Direct-stream: No initial subscriptions. Sitting idle — awaiting dynamic subscribe commands
//
// The sender used to be the desktop app (`src-tauri/src/commands/ticker.rs`, via the
// `subscribe_ticker` Tauri command). When the desktop shell was deleted, nothing
// replaced it for CASH instruments — `option_chain_selector` covers F&O only, over
// a different command (`option_chain_set`). So the Kite WebSocket connected
// successfully (HTTP 101), subscribed to nothing, and `live_ticks` stopped growing.
//
// The failure is silent in the worst way: every health check passes, the WS
// handshake succeeds, the token is valid, and the logs contain no error at all —
// the service is idle, not broken. It was only visible as a stale `max(timestamp)`
// in QuestDB, hours old, with the market open.
//
// My own note on `subscribe_ticker` in `webAdapters.ts` claimed "the WS feeds are
// symbol-agnostic", which is what let this ship. They are not: ingestion streams
// exactly the tokens it has been told about.
//
// WHY THE AGGREGATOR
// ------------------
// Same reasoning as `option_chain_selector`, and the same two capabilities: this
// service already owns the instrument cache needed to turn a tradingsymbol into a
// Kite instrument token (`KiteApiState::resolve_token`), and it already pushes to
// the ingestion control port. `ingestion` cannot do it itself — it has no
// instrument master, only tokens handed to it.
//
// Config:
//   SPOT_SYMBOLS  — comma-separated NSE tradingsymbols to stream. Defaults below.
//   SPOT_SUBSCRIBE_CYCLE_SECS — re-assert cadence (default 300).
//   INGESTION_HOST / INGESTION_CONTROL_PORT — where to push (default ingestion:8085,
//                             the compose service name — see ingestion_control_addr).

use std::collections::HashSet;
use std::sync::Arc;
use std::time::Duration;

use log::{error, info, warn};
use tokio::io::AsyncWriteExt;

use crate::kite_api::KiteApiState;

/// Default watchlist when `SPOT_SYMBOLS` is unset.
///
/// The five index symbols the macro strip renders (`useMacroIndicators.ts`) plus
/// the large caps the sentiment agent and the default watchlist use. Indices are
/// included deliberately: `NIFTY 50` and `NIFTY BANK` are what the F&O workspace
/// reads as spot, so without them `option_chain_selector` cannot resolve ATM from
/// live prices and falls back to a REST quote per cycle.
const DEFAULT_SPOT_SYMBOLS: &[&str] = &[
    "NIFTY 50",
    "NIFTY BANK",
    // BSE index, so it resolves through `resolve_token`'s BSE leg. Needed for the
    // same reason as the two NSE indices above: `option_chain_selector` reads it as
    // spot to place ATM, and `options.py::read_spot` reads it from `live_ticks` to
    // decide whether the F&O analytics can be computed at all.
    "SENSEX",
    "INDIA VIX",
    "NIFTY IT",
    "NIFTY FIN SERVICE",
    "RELIANCE",
    "TCS",
    "HDFCBANK",
    "INFY",
    "ICICIBANK",
    "SBIN",
    "ITC",
];

/// Delay before the first push, so the instrument cache and the ingestion control
/// port are up. A failed cycle is retried, so this is politeness not correctness.
const STARTUP_GRACE: Duration = Duration::from_secs(10);

fn cycle_interval() -> Duration {
    let secs = std::env::var("SPOT_SUBSCRIBE_CYCLE_SECS")
        .ok()
        .and_then(|v| v.trim().parse::<u64>().ok())
        .filter(|v| *v > 0)
        .unwrap_or(300);
    Duration::from_secs(secs)
}

/// The configured symbol list, or the default.
pub fn configured_symbols() -> Vec<String> {
    let raw = std::env::var("SPOT_SYMBOLS").unwrap_or_default();
    let parsed: Vec<String> = raw
        .split(',')
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .map(|s| s.to_uppercase())
        .collect();
    if parsed.is_empty() {
        DEFAULT_SPOT_SYMBOLS.iter().map(|s| s.to_string()).collect()
    } else {
        parsed
    }
}

/// Build one `subscribe:TOKEN:SYMBOL` control line.
///
/// Wire format consumed by `ingestion/src/main.rs` (`line.starts_with("subscribe:")`
/// then `splitn(3, ':')`), so the symbol must contain no `:` or newline. A symbol
/// with an interior newline would let a second command be injected on the control
/// port; tokens only resolve for real instruments, but the guard is cheap and this
/// is a socket that takes commands.
///
/// Pure, so the wire contract is testable without a socket.
pub fn build_subscribe_line(token: u64, symbol: &str) -> Option<String> {
    let sym = symbol.trim().to_uppercase();
    if sym.is_empty() || sym.contains(':') || sym.contains('\n') || sym.contains('\r') {
        return None;
    }
    // Ingestion parses the token as u32.
    let token32 = u32::try_from(token).ok()?;
    Some(format!("subscribe:{token32}:{sym}\n"))
}

/// The ingestion control-port address.
///
/// Default host is the compose SERVICE NAME, not `127.0.0.1`. Inside a container
/// `127.0.0.1` is that container itself, so a localhost default made both this and
/// `option_chain_selector` push to the aggregator and get
/// `Connection refused (os error 111)` on every cycle — the selector had been
/// failing that way silently since it was written. `INGESTION_HOST` still overrides
/// for a bare-metal / single-host run.
///
/// Shared by `option_chain_selector` so the two cannot drift apart again.
pub fn ingestion_control_addr() -> String {
    let host = std::env::var("INGESTION_HOST")
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| "ingestion".to_string());
    let port = std::env::var("INGESTION_CONTROL_PORT")
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| "8085".to_string());
    format!("{host}:{port}")
}

/// Push a batch of subscribe lines over one connection.
///
/// One connection for the whole batch rather than per symbol: ingestion reads
/// newline-delimited lines in a loop, so a dozen symbols cost one TCP setup.
async fn push_subscriptions(lines: &[String]) -> Result<(), String> {
    if lines.is_empty() {
        return Ok(());
    }
    let addr = ingestion_control_addr();

    let mut stream = tokio::net::TcpStream::connect(&addr).await.map_err(|e| {
        format!("cannot reach ingestion control port {addr} — is the ingestion service up? ({e})")
    })?;
    stream
        .write_all(lines.concat().as_bytes())
        .await
        .map_err(|e| format!("control write to {addr} failed: {e}"))?;
    Ok(())
}

/// Resolve every configured symbol to a token and subscribe it, then re-assert on a
/// timer.
///
/// Never returns, never panics. Re-asserting matters: ingestion's map is in-memory,
/// so a restart of THAT service silently empties it, and without a periodic re-push
/// the tick feed would stop until the aggregator happened to restart too.
/// Re-subscribing an existing token is a no-op on the ingestion side ("already
/// subscribed"), so the repeat is free.
pub async fn run(state: Arc<KiteApiState>) {
    let symbols = configured_symbols();
    info!(
        "[spot-subscriber] starting: {} symbols, re-assert every {}s",
        symbols.len(),
        cycle_interval().as_secs()
    );

    tokio::time::sleep(STARTUP_GRACE).await;

    // Tokens successfully pushed at least once. Only used to log the delta, so a
    // steady state is quiet instead of printing the same list every cycle.
    let mut announced: HashSet<String> = HashSet::new();
    let mut ticker = tokio::time::interval(cycle_interval());

    loop {
        ticker.tick().await;

        let mut lines: Vec<String> = Vec::with_capacity(symbols.len());
        let mut fresh: Vec<String> = Vec::new();
        let mut unresolved: Vec<String> = Vec::new();

        for symbol in &symbols {
            match state.resolve_token(symbol).await {
                Some(token) => match build_subscribe_line(token, symbol) {
                    Some(line) => {
                        if announced.insert(symbol.clone()) {
                            fresh.push(format!("{symbol}({token})"));
                        }
                        lines.push(line);
                    }
                    // A symbol the wire format cannot carry. Dropped rather than
                    // sent malformed, which ingestion would silently ignore.
                    None => unresolved.push(format!("{symbol}(unencodable)")),
                },
                None => unresolved.push(symbol.clone()),
            }
        }

        if !unresolved.is_empty() {
            warn!(
                "[spot-subscriber] {} symbol(s) did not resolve to a token: {}",
                unresolved.len(),
                unresolved.join(", ")
            );
        }

        if lines.is_empty() {
            warn!("[spot-subscriber] nothing to subscribe this cycle — no tokens resolved");
            continue;
        }

        match push_subscriptions(&lines).await {
            Ok(()) => {
                if fresh.is_empty() {
                    // Steady state: re-asserted, nothing new.
                    log::debug!("[spot-subscriber] re-asserted {} subscription(s)", lines.len());
                } else {
                    info!(
                        "[spot-subscriber] subscribed {} new: {}",
                        fresh.len(),
                        fresh.join(", ")
                    );
                }
            }
            Err(e) => {
                // Drop the announced set so the next cycle logs the full list again
                // — otherwise a failed push would look like a successful one.
                announced.clear();
                error!("[spot-subscriber] push failed: {e}");
            }
        }
    }
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_the_wire_format_ingestion_parses() {
        // `ingestion/src/main.rs` does `splitn(3, ':')` after the prefix and parses
        // field 1 as u32, so this shape is a contract, not a preference.
        assert_eq!(
            build_subscribe_line(738561, "RELIANCE").unwrap(),
            "subscribe:738561:RELIANCE\n"
        );
    }

    #[test]
    fn uppercases_and_trims_the_symbol() {
        // Ingestion uppercases on its side too; matching here keeps the logged
        // symbol identical to the stored one.
        assert_eq!(
            build_subscribe_line(1, "  reliance  ").unwrap(),
            "subscribe:1:RELIANCE\n"
        );
    }

    #[test]
    fn preserves_spaces_in_index_symbols() {
        // "NIFTY 50" is a real tradingsymbol. Splitting or stripping it would make
        // the index feeds — which the F&O workspace reads as spot — unsubscribable.
        assert_eq!(
            build_subscribe_line(256265, "NIFTY 50").unwrap(),
            "subscribe:256265:NIFTY 50\n"
        );
    }

    #[test]
    fn refuses_a_symbol_that_could_forge_a_second_command() {
        // The control port takes commands, so a newline in a symbol is command
        // injection. Also rejects ':' since it would corrupt the splitn fields.
        assert!(build_subscribe_line(1, "RELIANCE\nsubscribe:2:EVIL").is_none());
        assert!(build_subscribe_line(1, "RELIANCE\r\nx").is_none());
        assert!(build_subscribe_line(1, "NSE:RELIANCE").is_none());
        assert!(build_subscribe_line(1, "").is_none());
        assert!(build_subscribe_line(1, "   ").is_none());
    }

    #[test]
    fn rejects_a_token_that_does_not_fit_u32() {
        // Truncating would subscribe to a DIFFERENT instrument and mislabel every
        // tick it produced.
        assert!(build_subscribe_line(u64::from(u32::MAX) + 1, "RELIANCE").is_none());
        assert!(build_subscribe_line(u64::from(u32::MAX), "RELIANCE").is_some());
    }

    #[test]
    fn default_symbols_cover_the_macro_strip_and_fno_spot() {
        let syms = configured_symbols();
        // The F&O workspace resolves ATM from these two spot feeds.
        assert!(syms.iter().any(|s| s == "NIFTY 50"));
        assert!(syms.iter().any(|s| s == "NIFTY BANK"));
        assert!(!syms.is_empty());
    }

    #[test]
    fn control_addr_defaults_to_the_service_name_not_localhost() {
        // The regression this pins. `127.0.0.1` inside the aggregator container is
        // the aggregator, so a localhost default made every push fail with
        // `Connection refused (os error 111)` — silently, on a timer, for both this
        // module and option_chain_selector.
        //
        // Env is process-global and would race other tests, so only the no-override
        // path is asserted; that is the one that was wrong.
        if std::env::var("INGESTION_HOST").is_err() && std::env::var("INGESTION_CONTROL_PORT").is_err() {
            assert_eq!(ingestion_control_addr(), "ingestion:8085");
        }
        // Whatever the env says, the shape stays host:port with a non-empty host —
        // an empty INGESTION_HOST must not produce ":8085".
        let addr = ingestion_control_addr();
        let (host, port) = addr.rsplit_once(':').expect("addr must be host:port");
        assert!(!host.is_empty(), "host must never be empty: {addr}");
        assert!(!port.is_empty(), "port must never be empty: {addr}");
    }
}
