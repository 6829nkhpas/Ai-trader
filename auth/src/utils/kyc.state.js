// ──────────────────────────────────────────────────────────────
// utils/kyc.state.js — KYC State Machine Logic
// ──────────────────────────────────────────────────────────────

export const KYC_STATES = {
  PENDING: 'PENDING',
  BASIC_INFO_DONE: 'BASIC_INFO_DONE',
  KYC_SUBMITTED: 'KYC_SUBMITTED',
  VERIFIED: 'VERIFIED',
  REJECTED: 'REJECTED'
};

const VALID_TRANSITIONS = {
  [KYC_STATES.PENDING]: [KYC_STATES.BASIC_INFO_DONE],
  [KYC_STATES.BASIC_INFO_DONE]: [KYC_STATES.KYC_SUBMITTED],
  [KYC_STATES.KYC_SUBMITTED]: [KYC_STATES.VERIFIED, KYC_STATES.REJECTED],
  [KYC_STATES.VERIFIED]: [], // Terminal state, or could allow re-verify later
  [KYC_STATES.REJECTED]: [KYC_STATES.KYC_SUBMITTED]
};

export function transitionState(currentState, newState) {
  const allowed = VALID_TRANSITIONS[currentState] || [];
  if (!allowed.includes(newState)) {
    throw new Error(`Invalid state transition: Cannot move from ${currentState} to ${newState}`);
  }
  return newState;
}
