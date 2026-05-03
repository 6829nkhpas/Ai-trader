// ──────────────────────────────────────────────────────────────
// utils/circuit-breaker.js — Custom Circuit Breaker for Vendor Resilience
// States: CLOSED (normal), OPEN (failing fast), HALF_OPEN (probing recovery)
// ──────────────────────────────────────────────────────────────

export const CB_STATES = {
  CLOSED: 'CLOSED',
  OPEN: 'OPEN',
  HALF_OPEN: 'HALF_OPEN'
};

export class CircuitBreaker {
  constructor(name, failureThreshold = 3, resetTimeout = 30000) {
    this.name = name;
    this.failureThreshold = failureThreshold;
    this.resetTimeout = resetTimeout;
    
    this.state = CB_STATES.CLOSED;
    this.failureCount = 0;
    this.nextAttemptTime = null;
  }

  /**
   * Executes the given async action using the circuit breaker.
   * @param {() => Promise<any>} action
   */
  async fire(action) {
    if (this.state === CB_STATES.OPEN) {
      if (Date.now() >= this.nextAttemptTime) {
        this.state = CB_STATES.HALF_OPEN;
        console.warn(`[CIRCUIT-BREAKER] [${this.name}] State changed to HALF_OPEN. Probing...`);
      } else {
        const err = new Error(`Circuit Breaker OPEN for [${this.name}]. Fast failing.`);
        err.isCircuitOpen = true;
        throw err;
      }
    }

    try {
      const result = await action();
      this.onSuccess();
      return result;
    } catch (err) {
      // Typically, client errors (4xx) shouldn't trip the breaker, but 5xx should.
      // For this simple mock, we treat specific internal mock errors or network errors as failures.
      if (err.isVendorOutage) {
        this.onFailure();
      }
      throw err;
    }
  }

  onSuccess() {
    if (this.state !== CB_STATES.CLOSED) {
      console.log(`[CIRCUIT-BREAKER] [${this.name}] State changed to CLOSED. Recovery successful.`);
    }
    this.failureCount = 0;
    this.state = CB_STATES.CLOSED;
    this.nextAttemptTime = null;
  }

  onFailure() {
    this.failureCount += 1;
    if (this.state === CB_STATES.HALF_OPEN || this.failureCount >= this.failureThreshold) {
      this.state = CB_STATES.OPEN;
      this.nextAttemptTime = Date.now() + this.resetTimeout;
      console.error(`[CIRCUIT-BREAKER] [${this.name}] State changed to OPEN. Tripped due to failures.`);
    }
  }
}
