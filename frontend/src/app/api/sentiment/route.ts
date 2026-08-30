// app/api/sentiment/route.ts — same-origin news-sentiment endpoint for the web.
//
// This is the fix for the reported "AI NEWS SENTIMENT — Cannot read properties of
// undefined (reading 'invoke')": `useQuantStore` invoked the Tauri command
// `fetch_symbol_sentiment` with no browser fallback, so `invoke` dereferenced an
// absent `window.__TAURI_INTERNALS__` and the raw TypeError was written into
// `sentimentError`, which the panel renders verbatim.
//
// Unlike the other four routes this is NOT a pass-through, because the two
// producers disagree on shape. `commands/sentiment.rs` scores Google News RSS
// headlines with an LLM and returns `SentimentPayload`; the deployed Node service
// (`agents/sentiment`, :8090) returns a richer strategic verdict on a different
// scale (`conviction_score` is 1–100 with 50 = neutral, per
// `agents/sentiment/src/analyzer.js:84`). This handler translates the second into
// the first so the frontend contract is byte-identical on both transports.
//
// A cache miss is reported HONESTLY. The Node service answers 404 while an
// on-demand classification is still running; rather than fabricate a Neutral 0 —
// which would be indistinguishable from a real neutral verdict — we return a
// non-2xx with an explanatory message and let the panel show it. Desktop's
// neutral fallback is a different situation: there the headlines exist and only
// the scoring failed.

import {
  describeUpstreamFailure,
  forwardHeaders,
  isCredentialFault,
  credentialFaultMessage,
  proxyError,
  upstreamBase,
  PROXY_TIMEOUT_MS,
} from '../_gateway';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/** The frontend contract — mirrors `commands/sentiment.rs::SentimentPayload`. */
interface SentimentPayload {
  symbol: string;
  score: number;
  label: string;
  top_headline: string;
  impact: string;
  headlines: string[];
}

/** The upstream verdict — mirrors `agents/sentiment/src/index.js`'s cache entry. */
interface StrategicVerdict {
  symbol?: string;
  conviction_score?: number;
  label?: string;
  thesis?: string;
  drivers?: Array<{ headline?: string; impact?: string }>;
  headlines?: string[];
}

/**
 * Map the upstream 1–100 conviction scale onto the frontend's −100…+100 score.
 * 50 (neutral) maps to 0; 1 → −98 and 100 → +100.
 */
export function convictionToScore(conviction: unknown): number {
  const n = typeof conviction === 'number' && Number.isFinite(conviction) ? conviction : 50;
  return Math.max(-100, Math.min(100, Math.round((n - 50) * 2)));
}

/**
 * Derive `impact` from the score using the same ±20 thresholds as
 * `commands/sentiment.rs`, so the two transports colour the panel identically.
 */
export function scoreToImpact(score: number): string {
  if (score > 20) return 'positive';
  if (score < -20) return 'negative';
  return 'neutral';
}

/** Derive `label` the same way `commands/sentiment.rs` does when the LLM omits it. */
export function scoreToLabel(score: number): string {
  if (score > 20) return 'Bullish';
  if (score < -20) return 'Bearish';
  return 'Neutral';
}

/** Translate an upstream strategic verdict into the frontend's payload shape. */
export function toSentimentPayload(symbol: string, verdict: StrategicVerdict): SentimentPayload {
  const score = convictionToScore(verdict.conviction_score);
  const headlines = Array.isArray(verdict.headlines)
    ? verdict.headlines.filter((h): h is string => typeof h === 'string' && h.trim().length > 0)
    : [];
  const driverHeadline = verdict.drivers?.find((d) => typeof d?.headline === 'string' && d.headline.trim())?.headline;
  const topHeadline =
    driverHeadline?.trim() || headlines[0] || `No notable headline for ${symbol}.`;

  return {
    symbol: typeof verdict.symbol === 'string' && verdict.symbol.trim() ? verdict.symbol : symbol,
    score,
    label: typeof verdict.label === 'string' && verdict.label.trim() ? verdict.label : scoreToLabel(score),
    top_headline: topHeadline,
    impact: scoreToImpact(score),
    headlines,
  };
}

export async function GET(req: Request): Promise<Response> {
  const symbol = (new URL(req.url).searchParams.get('symbol') ?? '').trim().toUpperCase();
  if (!symbol) return proxyError(400, 'sentiment: a symbol query parameter is required');

  const url = `${upstreamBase('sentiment')}/sentiment?symbol=${encodeURIComponent(symbol)}`;

  // The upstream classifies on demand on a cache miss and waits up to
  // SENTIMENT_ON_DEMAND_TIMEOUT_MS (default 25s) before degrading to 404, so
  // allow more headroom here than the shared proxy timeout.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.max(PROXY_TIMEOUT_MS, 35_000));

  let upstream: Response;
  try {
    upstream = await fetch(url, {
      headers: forwardHeaders(req),
      signal: controller.signal,
      cache: 'no-store',
    });
  } catch (err) {
    return proxyError(502, describeUpstreamFailure(err, 'sentiment'));
  } finally {
    clearTimeout(timer);
  }

  if (isCredentialFault(upstream.status)) {
    return proxyError(upstream.status, credentialFaultMessage('sentiment'));
  }

  if (upstream.status === 404) {
    // The upstream distinguishes "still classifying" from "tried and failed", and
    // only the first justifies telling the user to wait. Reporting a 429'd LLM as
    // work-in-progress kept the panel promising a verdict that was never going to
    // arrive, so pass the real cause through when there is one.
    const detail = (await upstream
      .json()
      .catch(() => null)) as { still_running?: boolean; reason?: string } | null;
    const reason = typeof detail?.reason === 'string' ? detail.reason.trim() : '';
    if (reason && detail?.still_running !== true) {
      return proxyError(503, `Sentiment unavailable for ${symbol}: ${reason}`);
    }
    return proxyError(
      503,
      `No sentiment computed yet for ${symbol}. Classification is running in the background — try again shortly.`,
    );
  }

  if (!upstream.ok) {
    return proxyError(502, `sentiment upstream returned HTTP ${upstream.status}`);
  }

  let verdict: StrategicVerdict;
  try {
    verdict = (await upstream.json()) as StrategicVerdict;
  } catch (err) {
    return proxyError(
      502,
      `sentiment upstream returned a non-JSON body: ${err instanceof Error ? err.message : String(err)}`,
    );
  }

  return Response.json(toSentimentPayload(symbol, verdict), {
    headers: { 'Cache-Control': 'no-store' },
  });
}
