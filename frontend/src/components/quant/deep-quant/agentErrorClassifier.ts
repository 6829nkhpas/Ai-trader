// agentErrorClassifier.ts — turn a raw agent failure into an honest explanation.
//
// WHY THIS EXISTS
// ---------------
// `AgentTerminal` used to render ONE hardcoded sentence for every failed run:
//
//   "The LangGraph agent loop returned a pipeline error. This usually occurs if
//    your LLM API key (e.g. Google Gemini or OpenAI) is expired, rate-limited, or
//    out of quota."
//
// That is a diagnosis, printed unconditionally, for a state the component has not
// examined. Four unrelated causes reach that branch — the RESEARCH SKU gate
// short-circuiting before any request is made, a transport failure to the proxy, an
// idle-stream timeout, and a genuine agent-reported ERROR frame — and all four told
// the user to go check an LLM key.
//
// It cost real debugging time: a run was reported as an expired key while the key
// was funded, the agent was healthy, and the true cause was the compliance gate. A
// wrong explanation is worse than none, because it sends you to the wrong system.
//
// So the message is derived from the error text the store already captured, and the
// LLM-quota wording appears ONLY when the evidence points there.
//
// DESIGN
// ------
// Pure, synchronous, and total: any input yields a renderable result, so the error
// panel can never itself throw. Classification is by substring on the upstream
// message, which is inherently heuristic — hence `kind: 'unknown'` as an honest
// default that shows the raw text and blames nothing.

/** What actually went wrong, as far as the evidence supports. */
export type AgentErrorKind =
  | 'research-locked'
  | 'llm-quota'
  | 'llm-auth'
  | 'upstream-unreachable'
  | 'feature-disabled'
  | 'timeout'
  | 'cancelled'
  | 'no-data'
  | 'unknown';

export interface ClassifiedAgentError {
  kind: AgentErrorKind;
  /** Short heading for the panel. */
  title: string;
  /** One or two sentences on what happened and what to do. No blame guessing. */
  explanation: string;
  /** The raw upstream text, always shown verbatim beneath the explanation. */
  detail: string;
  /**
   * False when the user cannot fix it by trying again — a locked plan or a
   * disabled feature. Lets the caller hide a Retry that would only fail again.
   */
  retryable: boolean;
}

/** Matched in order; the first hit wins, so most-specific patterns come first. */
const RULES: Array<{
  kind: AgentErrorKind;
  match: RegExp;
  title: string;
  explanation: string;
  retryable: boolean;
}> = [
  {
    // The store sets RESEARCH_LOCKED_MESSAGE and never issues a request. Nothing
    // is wrong with the agent or the key, so saying "check your LLM key" sent
    // people to inspect a perfectly healthy system.
    kind: 'research-locked',
    match: /RESEARCH plan|SEBI-registered research service/i,
    title: 'Requires the RESEARCH plan',
    explanation:
      'Trade analysis and recommendations are part of the RESEARCH plan. No analysis ' +
      'was run — this is a plan restriction, not a failure.',
    retryable: false,
  },
  {
    // assertFeatureEnabled in the proxy route. Also not a fault.
    kind: 'feature-disabled',
    match: /disabled in this deployment|operator controls this switch/i,
    title: 'Turned off for this deployment',
    explanation:
      'Deep Quant analysis is switched off server-side. Your plan is not the ' +
      'limitation — the operator controls this switch.',
    retryable: false,
  },
  {
    kind: 'llm-quota',
    match: /quota|rate limit|rate-limit|429|insufficient (credit|balance|funds)|billing/i,
    title: 'LLM provider refused the request',
    explanation:
      'The language-model provider rejected the call for quota, rate-limit or ' +
      'billing reasons. Check the balance and rate limits on the configured gateway.',
    retryable: true,
  },
  {
    kind: 'llm-auth',
    match: /invalid api key|incorrect api key|unauthori[sz]ed|401|403|authentication|expired key|TokenException/i,
    title: 'LLM provider rejected the credential',
    explanation:
      'The provider answered but refused the credential. The key is likely wrong, ' +
      'revoked, or lacks access to the requested model.',
    retryable: true,
  },
  {
    // ORDERED BEFORE 'upstream-unreachable' deliberately. The store's stall
    // message is "…stalled — no activity for 120s. The Python agent server may be
    // unreachable or the LLM request stalled", which contains "unreachable" and
    // would otherwise be classified as a transport failure. A stall and a refused
    // connection want different responses — one means wait/retry, the other means
    // the service is down — and the stall wording is the more specific evidence.
    kind: 'timeout',
    match: /stalled|timed? ?out|timeout|no activity for/i,
    title: 'Analysis stalled',
    explanation:
      'The run started but stopped producing output. This is usually a slow or ' +
      'wedged model call rather than a rejected one.',
    retryable: true,
  },
  {
    kind: 'upstream-unreachable',
    match: /unreachable|ECONNREFUSED|Connection refused|fetch failed|Failed to fetch|502|503|504|network error|socket hang up/i,
    title: 'Analysis service unreachable',
    explanation:
      'The request never reached the analysis service. It may be restarting or ' +
      'temporarily down; the LLM was not involved.',
    retryable: true,
  },
  {
    kind: 'cancelled',
    match: /cancel+ed|aborted|AbortError/i,
    title: 'Analysis cancelled',
    explanation: 'The run was stopped before it finished.',
    retryable: true,
  },
  {
    // Real and common: the agent cannot analyse a symbol with no candles. Telling
    // someone to check their LLM key here is actively misleading.
    kind: 'no-data',
    match: /no candles|insufficient (data|history|candles)|no historical data|empty dataset/i,
    title: 'Not enough market data',
    explanation:
      'The agent could not assemble enough price history for this symbol and ' +
      'timeframe. Try a longer timeframe or a more liquid instrument.',
    retryable: true,
  },
];

/**
 * Classify a raw agent error.
 *
 * @param raw - `analysisError` from the store. May be null/empty when a run failed
 *   without any message at all.
 * @returns Always a renderable result; `kind: 'unknown'` when nothing matches.
 */
export function classifyAgentError(raw: string | null | undefined): ClassifiedAgentError {
  const detail = String(raw ?? '').trim();

  if (!detail) {
    // A failure with no message. Previously this rendered the LLM-key diagnosis
    // plus an invented "Connection refused: port :8086 unreachable" as though it
    // had been observed. Say what is actually known instead.
    return {
      kind: 'unknown',
      title: 'Analysis failed',
      explanation:
        'The run ended without a reason. Retry; if it persists, the agent service ' +
        'logs will have the cause.',
      detail: 'No error detail was reported.',
      retryable: true,
    };
  }

  for (const rule of RULES) {
    if (rule.match.test(detail)) {
      return {
        kind: rule.kind,
        title: rule.title,
        explanation: rule.explanation,
        detail,
        retryable: rule.retryable,
      };
    }
  }

  // Unrecognised. Show the upstream text and blame nothing — guessing is what
  // this module exists to stop.
  return {
    kind: 'unknown',
    title: 'Analysis failed',
    explanation: 'The analysis service reported an error. The message it returned is below.',
    detail,
    retryable: true,
  };
}
