# 🛡️ Internal Security Policy

This is a private, proprietary quantitative trading codebase. All potential vulnerabilities, logic errors, trading glitches, or credential exposures must be reported **internally** and processed with the highest level of confidentiality.

## 🚫 No Public Disclosure
**DO NOT** open public Git issues, public pull requests, or discuss code anomalies on public forums (e.g., StackOverflow, Reddit, public Discord servers). Publicly disclosing any aspect of this trading system is a severe security violation.

---

## 🔒 Reporting Vulnerabilities

If you identify a security issue, key leak, or critical quantitative exploitation path (arbitrage anomalies, trading loop bypasses), report it immediately using one of the following channels:

1.  **Direct Communication**: Contact the Chief Technology Officer (CTO) or Head of Quantitative Strategies directly via secure organization communication channels (e.g., Slack, encrypted email).
2.  **Internal Audits**: Log detailed replication steps inside a password-encrypted document and share it exclusively with the system administrators.

---

## 🔑 Credential Leaks & Emergency Rotation

If you accidentally commit a production key (Kite API key, model endpoint key, database credentials) to a local git commit:

1.  **Do Not Push**: Stop and do not push the commits to the remote origin.
2.  **Purge History**: Use git history purging utilities (e.g., `git-filter-repo` or `bfg`) to completely scrub the credential from the local repository timeline.
3.  **Emergency Rotation**: If the commit was pushed to the remote server, immediately trigger the rotation protocol:
    *   Revoke the Kite Connect API credentials on the Zerodha developer portal.
    *   Rotate the LLM access keys on the provider dashboard (Hugging Face / DeepSeek).
    *   Deploy updated configurations to production vault variables.
