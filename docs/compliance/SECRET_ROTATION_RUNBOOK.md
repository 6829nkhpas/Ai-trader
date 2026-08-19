# Secret Rotation Runbook

**Owner:** [COMPLIANCE OFFICER — unassigned]
**Version 1.0 · August 2026**

> Operational runbook for credential rotation, written to close blocker **P7** in
> `docs/business/PLAN_OF_ACTION.md` §4.2 and `docs/business/SEBI_COMPLIANCE_BLUEPRINT.md` §5.1.
>
> **This is not legal advice.** The CSCRF incident-reporting obligations referenced here must be
> confirmed with securities counsel before being relied on. **[COUNSEL — review §5.]**

---

## 0. Why this document exists

A leaked broker credential inside a SEBI-regulated entity is a reportable cyber incident, not an
engineering embarrassment. Under CSCRF, a regulated entity is expected to know which credentials
exist, where they live, and how fast they can be replaced. This runbook is the answer to that
question, and it must stay current as credentials are added.

**The load-bearing principle:** untracking a secret from git does not un-leak it. Rotation is the
only action that actually revokes access. Everything in §1–§3 is ordered on that basis.

---

## 1. Status of the two known repository leaks

Both files were removed from git tracking on the `develop` branch (`git rm --cached`, working-tree
copies retained). **Both remain present in git history** — see §4 for why that is accepted for now.

### 1.1 `bedrock-api-key.txt` (repository root)

| | |
| --- | --- |
| **Introduced** | commit `4aeceb2`, 2026-06-12 |
| **Contents** | AWS STS presigned Bedrock invocation URL carrying a bearer token |
| **IAM principal** | `ASIA6HLWB63IOSAZ24CG` (temporary STS access key) |
| **Referenced by code** | **No.** Nothing in the tree reads this file. The LLM path is provider-agnostic and configured via `LLM_API_KEY` / `LLM_API_URL` |
| **Exploitability** | **Expired 2026-06-12** per `docs/SECURITY_REVIEW.md:644`. STS presigned URLs are time-bounded, so the token is not currently usable |
| **Tracked now** | No — untracked; `.gitignore:109` covers it |

**Required actions**

1. **Review, do not rotate.** There is no long-lived key to rotate — the credential was already
   ephemeral and has lapsed. Instead, in AWS IAM, review the role that issued
   `ASIA6HLWB63IOSAZ24CG`: confirm its trust policy, confirm it is not still assumable by a
   long-lived key that *was* committed elsewhere, and confirm no unexpected `bedrock:InvokeModel`
   calls occurred between 2026-06-12 and the expiry.
2. Check CloudTrail for `AssumeRole` and `bedrock:InvokeModel` events attributable to that principal.
3. If the underlying long-lived IAM user key that minted the STS token is still active, **rotate that
   key** and record it in the log at §6.
4. Delete the working-tree file once the AWS review is closed. It has no functional purpose.

> **Do not downgrade this to "no action".** The token being expired removes the urgency, not the
> obligation. A regulated entity is expected to be able to show it checked.

### 1.2 `scripts/powershell/auth/keys/private.pem` and `public.pem`

| | |
| --- | --- |
| **Introduced** | commit `a37ce32`, 2026-05-19; present in every commit since |
| **Contents** | A PEM-encoded private key (`-----BEGIN PRIVATE KEY-----`) and its public counterpart |
| **Referenced by code** | **No.** `scripts/powershell/auth/` contains *only* the `keys/` directory — the scripts that would have used this keypair do not exist in the tree |
| **Purpose** | Unknown / abandoned. Treat as unattributed key material |
| **Tracked now** | No — untracked; `.gitignore:118` (`keys`) covers it |

**Required actions**

1. **Establish what this key authenticates before destroying it.** Compute its fingerprint and
   compare against any deployed `authorized_keys`, JWT verification config, or certificate:

   ```bash
   openssl pkey -in scripts/powershell/auth/keys/private.pem -pubout -outform DER \
     | openssl sha256
   ```

2. If it matches nothing in production: **delete both files.** No rotation is needed for a key
   nothing trusts.
3. If it matches anything: treat as **fully compromised**, generate a replacement, deploy the new
   public key, remove the old one from every trust store, then delete the files.
4. Record the outcome — including "matched nothing, deleted" — in §6.

---

## 2. Untracked but live: `keys/`

This directory was **never committed** (verified: absent from `git log --all`). The
`SEBI_COMPLIANCE_BLUEPRINT.md` §5.1 claim that it needs purging from history is **incorrect**. It is
nonetheless the most sensitive material in the working tree, so it belongs in this inventory.

| File | What it is | Rotation procedure |
| --- | --- | --- |
| `keys/stratai_deploy` | Private SSH key for droplet deploys (used by `deploy-server.yml` → `redeploy.sh`) | `ssh-keygen -t ed25519 -f keys/stratai_deploy_new`, add the new public key to the droplet's `~/.ssh/authorized_keys`, update the GitHub Actions secret, verify a deploy succeeds, **then** remove the old key from `authorized_keys` |
| `keys/stratai_deploy.pub` | Public half of the above | Replaced alongside |
| `keys/tauri-updater.key` | **Signing key for desktop auto-updates** | See the warning below |
| `keys/tauri-updater.key.pub` | Public half, embedded in shipped clients | Replaced alongside |

> ⚠️ **`tauri-updater.key` is the highest-severity credential in this repository.** It signs the
> auto-update feed added in commit `a5bd197`. Anyone holding it can push a signed malicious update to
> every installed desktop client. It is also the **hardest to rotate**: the public key is compiled
> into already-shipped binaries, so rotating it orphans existing installs from the update channel
> unless a migration release is staged first.
>
> **Rotation requires a plan, not a command.** Do not rotate it reactively. If it is ever exposed,
> the correct response is a coordinated release plus direct user notification, and it is very likely
> a reportable incident. **[COUNSEL]**

**Action now:** confirm `keys/` is excluded from all build contexts and backups, and move both
keypairs into a managed secret store (§3). Verify exclusion:

```bash
git check-ignore -v keys/stratai_deploy keys/tauri-updater.key
```

---

## 3. Credential inventory — `.env` (38 variables)

`.env` was **never committed** (verified). It is covered by `.gitignore:59` and `:106`. Values are
deliberately not reproduced anywhere in this document.

### 3.1 Secrets — rotate these

| Variable | Service | Where to rotate | Notes |
| --- | --- | --- | --- |
| `KITE_API_KEY` | Zerodha Kite Connect | [Kite developer console](https://developers.kite.trade/apps) | App-level identifier; rotating it requires updating every service |
| `KITE_API_SECRET` | Zerodha Kite Connect | Kite developer console | **Highest business impact.** Broker credential — the exact class of leak that makes an incident reportable |
| `KITE_ACCESS_TOKEN` | Zerodha Kite Connect | Regenerated by the login flow | **Expires daily.** Short-lived by design; no manual rotation needed |
| `KITE_REQUEST_TOKEN` | Zerodha Kite Connect | Single-use, from the login redirect | Ephemeral; should not be persisted in `.env` at all — see §3.3 |
| `LLM_API_KEY` | LLM provider (OpenRouter-compatible) | Provider dashboard | Revoke old key only after the new one is confirmed live |
| `FINNHUB_API_KEY` | Finnhub | [finnhub.io dashboard](https://finnhub.io/dashboard) | Market data enrichment |
| `NEWSDATA_API_KEY` | NewsData.io | NewsData dashboard | Sentiment pipeline input |
| `QUESTDB_PASSWORD` | QuestDB | QuestDB user config, then restart | Also embedded in `QUESTDB_POSTGRES_URL` — rotate both together |
| `QUESTDB_USER` | QuestDB | QuestDB user config | Default `admin`; rename it |
| `QUESTDB_POSTGRES_URL` | QuestDB PG wire | Derived | **Contains the password inline.** Must be updated whenever `QUESTDB_PASSWORD` changes |

### 3.2 Not secrets — no rotation, but do not expose publicly

Endpoints, ports, topics and tuning flags: `DEEP_QUANT_HOST`, `DEEP_QUANT_PORT`, `DEEP_QUANT_URL`,
`INGESTION_CONTROL_PORT`, `KAFKA_BROKERS`, `KAFKA_BROKERS_INTERNAL`, `KAFKA_BROKER_URL`,
`KAFKA_TOPIC_SENTIMENT`, `KAFKA_TOPIC_SIGNALS`, `KITE_API_PORT`, `LLM_API_URL`, `LLM_EFFORT`,
`LLM_EFFORT_FIELD`, `LLM_MODEL`, `MOCK_BROKER`, `OPPORTUNITY_HEARTBEAT_CADENCE_SECS`,
`OPPORTUNITY_HEARTBEAT_ENABLED`, `OPPORTUNITY_HEARTBEAT_MAX`, `QUANT_TOOL_SERVER_PORT`,
`QUESTDB_HTTP_URL`, `QUESTDB_ILP_ADDR`, `QUESTDB_ILP_ADDR_INTERNAL`, `REDIS_URL`,
`RUST_TOOL_SERVER_URL`, `STRATAI_HTTP_BASE_URL`, `STRATAI_SERVER_HOST`, `STRATAI_WS_BASE_URL`,
`WEBSOCKET_PORT`.

`REDIS_URL` and `QUESTDB_ILP_ADDR` become secrets the moment they carry inline credentials or point
at a publicly routable host. Re-classify them if that changes.

### 3.3 Structural findings

- **`KITE_REQUEST_TOKEN` should not be in `.env`.** It is single-use and consumed immediately during
  login. Persisting it serves no purpose and widens the blast radius of a `.env` disclosure.
- **`QUESTDB_POSTGRES_URL` embeds `QUESTDB_PASSWORD`.** Two places to forget. Compose it at runtime
  from its parts instead of storing it.
- **No managed secret store is in use.** Every credential is a plaintext file on disk. Migrating to
  one (Doppler, AWS Secrets Manager, or Vault) is the P7 follow-up that makes this runbook
  maintainable rather than aspirational. Until then, rotation is a manual, error-prone process on
  every host.
- The desktop app already has an encrypted local store — the Tauri **Stronghold** plugin, used for
  user-supplied API keys via `save_api_key` / `hydrate_key_cache`. Service-side credentials do not
  use it and cannot, since they are needed before any user session exists.

---

## 4. Git history: why we are not rewriting it (yet)

The two leaked files remain reachable in git history. This is a **deliberate, recorded decision**, not
an oversight.

**Rationale.** Rewriting history with `git filter-repo` changes every commit hash from the rewrite
point forward. It requires a force-push and a coordinated re-clone by every collaborator and every CI
runner. Critically, **it does not revoke anything** — every existing clone, fork, and CI cache still
holds the original blobs. The security value is close to zero when weighed against the coordination
cost and the risk of losing commits.

**What actually closes the exposure is §1 and §3 — rotation and review.** Prioritise those.

**When to rewrite.** Do it as one deliberate operation, ideally before the repository is shared with
external parties (a broker's technical due diligence, an acquirer, or a CSCRF auditor who is given
repository access). Recorded here so it does not have to be re-derived:

```bash
# Coordinate first: every collaborator must re-clone afterwards.
pip install git-filter-repo
git filter-repo --invert-paths \
  --path bedrock-api-key.txt \
  --path scripts/powershell/auth/keys/private.pem \
  --path scripts/powershell/auth/keys/public.pem
git push --force --all && git push --force --tags
```

After any rewrite, GitHub retains unreferenced blobs until garbage collection — open a support
request to expedite it, and treat the credentials as exposed regardless.

---

## 5. Incident-reporting trigger **[COUNSEL]**

Rotation is remediation. It is **not** a substitute for reporting where reporting is required.

Escalate immediately, before remediating, if any of the following is true:

- A **Kite** credential (`KITE_API_SECRET`, or a non-expired `KITE_ACCESS_TOKEN`) was exposed outside
  the team — this is a broker credential and the most likely reportable case.
- `keys/tauri-updater.key` was exposed in any form.
- There is evidence of **use** rather than mere exposure: unexpected API calls, unrecognised orders,
  unfamiliar CloudTrail activity, or an unexplained deploy.
- Any client personal data was reachable using the exposed credential (DPDP Act 2023 breach
  notification is a separate obligation with its own timeline).

Counsel must confirm the CSCRF reporting window and the responsible filer before an incident occurs.
Determining this mid-incident is how deadlines get missed. **This section is unresolved until counsel
signs off.**

---

## 6. Rotation log

Every rotation gets a row. An empty cell is an open item, and this table is the artefact an auditor
will ask for.

| Date | Credential | Reason | Rotated by | Old value revoked? | Verified by |
| --- | --- | --- | --- | --- | --- |
| 2026-08-17 | `bedrock-api-key.txt` | Untracked from git; committed `4aeceb2` | | n/a — STS token expired 2026-06-12 | |
| 2026-08-17 | `scripts/powershell/auth/keys/*.pem` | Untracked from git; committed `a37ce32` | | Pending §1.2 fingerprint check | |
| | `KITE_API_SECRET` | Precautionary — plaintext on disk, no managed store | | | |
| | `LLM_API_KEY` | Precautionary | | | |
| | `FINNHUB_API_KEY` | Precautionary | | | |
| | `NEWSDATA_API_KEY` | Precautionary | | | |
| | `QUESTDB_PASSWORD` + `QUESTDB_POSTGRES_URL` | Default credentials | | | |
| | `keys/stratai_deploy` | Precautionary | | | |

---

## 7. Verification

Run before every release, and on any change to `.gitignore`:

```bash
# 1. No key material is tracked. Must print nothing.
git ls-files | grep -Ei '\.pem$|\.key$|bedrock|id_rsa|\.p12$|\.pfx$'

# 2. Sensitive paths are ignored. Every line must resolve to a .gitignore rule.
git check-ignore -v .env keys/stratai_deploy keys/tauri-updater.key \
  bedrock-api-key.txt scripts/powershell/auth/keys/private.pem

# 3. No secret-shaped literals in staged changes, before committing.
git diff --cached | grep -nE 'BEGIN [A-Z ]*PRIVATE KEY|sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}'
```

Check 1 and check 2 currently pass. Check 3 is a pre-commit discipline; wiring it into a
`pre-commit` hook or into `ci.yml` is the mechanical enforcement this runbook should eventually rely
on instead of memory.

---

## 8. Open items

1. **No managed secret store.** The single largest structural gap. Until it exists, §3 rotation is
   manual on every host.
2. **§5 reporting triggers are unconfirmed.** Blocked on counsel.
3. **No automated secret scanning** in `ci.yml`. Check 3 above should run on every PR.
4. **`tauri-updater.key` has no rotation plan.** Needs a staged-migration design *before* it is ever
   needed, since reactive rotation orphans shipped clients.
5. **Owner unassigned.** This runbook needs a named Compliance Officer, which is itself a Phase-0
   deliverable in `PLAN_OF_ACTION.md`.
