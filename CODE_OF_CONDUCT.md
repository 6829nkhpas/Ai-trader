# 📝 Code of Conduct: Quantitative Engineering & Trading Operations

## 1. Our Purpose
This Code of Conduct governs developer behavior, analytical review, and trading integrations within our private organization. The system operates on massive automated capital and high leverage on the NSE; therefore, our code of conduct prioritizes **operational safety, mathematical precision, and professional integrity**.

---

## 2. Core Standards of Professionalism

To maintain the safety and performance of our automated trading infrastructure, all contributors must:

*   **Rigorously Validate All Logic**: Never deploy untested technical formulas or experimental statistical projections into production streams. All quantitative indicator calculations must be mathematically validated.
*   **Practice Constructive Peer Review**: Treat code reviews as collaborative math validation sessions. Focus critiques entirely on logical clarity, performance optimizations, memory safety, and risk models.
*   **Adhere to Confined Operations**: Do not execute ad-hoc overrides or modify database entries on active live tick tables (`live_ticks`) without prior operational sync and executive sign-off.
*   **Strictly Protect Confidentiality**: Safeguard proprietary math formulas, dynamic weights, and transaction logs. Our code and operational models must never be discussed outside organizational boundaries.

---

## 3. Operational Integrity & Safety

We maintain a zero-tolerance policy for:
*   **Negligent Deployments**: Bypassing pre-merge compiler and unit testing suites to push raw hot-patches onto production brokers.
*   **Unauthorized Arbitrage/Trading Modifications**: Injecting unauthorized account integrations, altered order size allocations, or external routing logic that deviates from approved risk management boundaries.
*   **Harassment & Non-Collaborative Behavior**: Disrespectful treatment, personal attacks, or dismissive attitudes during algorithm evaluations or post-mortem incident analyses.

---

## 4. Enforcement & Reporting

Violations of this Code of Conduct compromise institutional capital and organization safety. 
*   Operational bypasses, negligent key leakage, or behavioral misconduct will be escalated to engineering directors and human resources.
*   Consequences for verified violations include revocation of repository write access, suspension of staging/live environment deployment rights, and termination of employment/consulting agreements under contract conditions.
