# Contributing to Ai-trader

## Branching model

Three long-lived branches. Work flows in one direction only:

```
feature/*  fix/*  chore/*
    │
    │  PR + review
    ▼
develop ──────────► staging ──────────► main
          PR                  PR          │
     (integration)      (pre-prod)        └─► production
```

| Branch | Purpose | Who writes to it | Deploys to |
|---|---|---|---|
| **`main`** | **Production.** Always releasable. | **Nobody directly** — approved PRs from `staging` only | Droplet backend on push; desktop app on `v*` tag |
| **`staging`** | Pre-production verification against real infra | Approved PRs from `develop` | Staging environment (when configured) |
| **`develop`** | Day-to-day integration of feature work | Approved PRs from `feature/*` etc. | Nothing automatic |

### Rules

1. **Never push directly to `main`.** It accepts approved pull requests from
   `staging` only. A CI guard flags direct pushes — see
   [Enforcement](#enforcement) below.
2. **Never push directly to `staging`.** It accepts PRs from `develop`.
3. **Branch off `develop`**, not `main`, for all new work.
4. **Don't skip a rung.** A `feature/*` branch does not go straight to `staging`
   or `main`. The one exception is a hotfix — see below.
5. **Keep history linear where you can.** Rebase your feature branch on
   `develop` before opening the PR rather than merging `develop` into it
   repeatedly.

### Branch naming

| Prefix | For |
|---|---|
| `feature/` | new functionality — `feature/options-greeks-panel` |
| `fix/` | bug fixes — `fix/ghost-line-double-buffer` |
| `chore/` | deps, tooling, config, cleanup |
| `refactor/` | behaviour-preserving restructuring |
| `docs/` | documentation only |
| `hotfix/` | urgent production fix — see below |

## Everyday workflow

```bash
# 1. Start from an up-to-date develop
git checkout develop
git pull

# 2. Branch
git checkout -b feature/my-thing

# 3. Work, committing as you go. Then verify (see below), push, open a PR.
git push -u origin feature/my-thing
gh pr create --base develop
```

### Promoting develop → staging

```bash
gh pr create --base staging --head develop \
  --title "release: promote develop to staging"
```

### Promoting staging → main (production)

```bash
gh pr create --base main --head staging \
  --title "release: promote staging to production"
```

Merging to `main` triggers [`deploy-server.yml`](.github/workflows/deploy-server.yml),
which SSHes into the droplet and runs `redeploy.sh`. Treat every merge to `main`
as a production deploy.

## Hotfixes

For a production incident that cannot wait for the full ladder:

```bash
git checkout -b hotfix/describe-it main
# fix, verify
gh pr create --base main --head hotfix/describe-it
```

After it merges to `main`, **back-merge immediately** so the fix isn't lost:

```bash
git checkout staging && git merge main && git push
git checkout develop && git merge staging && git push
```

A hotfix that skips the back-merge will be silently reverted by the next
ordinary `staging → main` promotion.

## Before you open a PR

Run the checks that cover what you touched. From the repo root:

| Changed | Command |
|---|---|
| Rust (Tauri backend) | `cd frontend/src-tauri && cargo build --lib && cargo test --lib` |
| Rust (a service crate) | `cd <crate> && cargo check && cargo test` |
| Frontend types | `cd frontend && npx tsc --noEmit` |
| Frontend tests | `cd frontend && npx vitest run` |
| Charting (largest suite) | `cd frontend && npx vitest run src/charting` |
| Python agent | `cd agents/deep-quant-loop && python -m pytest` |

Known pre-existing failures that are **not** your fault — they fail on a clean
tree too: `frontend/src/components/fno/__tests__/selectors.bounding.property.test.ts`,
`scopeBoundary.test.ts`, and `tsc` errors in `WatchlistPanel.tsx` plus the
TradingView codegen assets. `tools/load_tester` also does not currently compile.

## Pull requests

- Fill in the template — what changed, why, how you verified it.
- Keep PRs scoped. A 40-file PR that does three unrelated things is three PRs.
- Link the issue or tracker row if there is one (`docs/tasks/*.csv`).
- **At least one approving review** before merge. This is the gate that matters
  most, since GitHub cannot currently enforce it for us.
- CI must be green.

## Enforcement

Server-side enforcement is **not available on this repository.** It is private
under a Free organization, and *both* mechanisms are gated behind a paid plan:

| Attempted | Result |
|-----------|--------|
| `gh api repos/thestratai/Ai-trader/branches/main/protection` | `403 Upgrade to GitHub Pro or make this repository public` |
| `gh api repos/thestratai/Ai-trader/rulesets` (GET and POST) | `403 Upgrade to GitHub Pro or make this repository public` |
| `gh api orgs/thestratai/rulesets` | needs `admin:org`; org-level rulesets require **Team** regardless |

Per GitHub's docs, rulesets and protected branches cover private repos only on
**Pro / Team / Enterprise**; org-wide rulesets need **Team**. `thestratai` is on
`free`. So the rules above are **policy, not physics.** Three things compensate:

1. **[`.githooks/pre-push`](.githooks/pre-push) — the only real prevention.**
   Refuses to push (or delete) `main` and `staging` from your machine, before
   anything reaches GitHub. Install it once per clone:

   ```bash
   git config core.hooksPath .githooks
   ```

   It is per-clone and bypassable (`--no-verify`, or the explicit
   `ALLOW_PROTECTED_PUSH=1`). It is a seatbelt against the accidental push on
   the wrong branch, not a security control — someone who wants to push to
   `main` still can.

2. **[`branch-guard.yml`](.github/workflows/branch-guard.yml)** — reports a
   direct push to `main`/`staging`, and fails any PR into `main` not from
   `staging` or `hotfix/*`. It runs *after* the push lands, so it documents a
   violation rather than stopping it.

3. **Review discipline.** Until the plan allows protection, this is the real
   gate. Nothing above can stop a determined `git push`.

### Turning on real protection

Once the org is on **Team** (or the repo is made public), enforce the model for
real. A **ruleset** is the current mechanism and is preferred over classic
branch protection — it is evaluated as a unit, can target several branches at
once, and reports which rule rejected a push.

```bash
REPO=thestratai/Ai-trader

# One ruleset covering both protected branches: PR-only, no force-push,
# no deletion, and CI must be green. The status context is "CI" — the
# `name:` of the ci-ok job, which aggregates every other CI job. Point at
# that one rather than each job, so path-filtered skips don't wedge a merge.
gh api -X POST "repos/$REPO/rulesets" --input - <<'JSON'
{
  "name": "protected-branches",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": { "include": ["refs/heads/main", "refs/heads/staging"], "exclude": [] }
  },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews_on_push": true,
        "require_last_push_approval": true,
        "require_code_owner_review": false,
        "required_review_thread_resolution": false,
        "allowed_merge_methods": ["merge", "squash", "rebase"]
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [{ "context": "CI" }]
      }
    }
  ],
  "bypass_actors": []
}
JSON

# Verify it took effect, then confirm a direct push is actually refused:
gh api "repos/$REPO/rulesets" --jq '.[] | "\(.id) \(.name) \(.enforcement)"'
gh api "repos/$REPO/rules/branches/main" --jq '.[].type'
```

`bypass_actors: []` means the rules apply to admins too. To let repo admins
bypass in a genuine emergency, add
`{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}`.

<details>
<summary>Classic branch protection (alternative, if you prefer it)</summary>

```bash
REPO=thestratai/Ai-trader

# main — production: PR-only, 1 approval, no force-push, no deletion
gh api -X PUT "repos/$REPO/branches/main/protection" \
  --input - <<'JSON'
{
  "required_status_checks": { "strict": true, "contexts": ["CI"] },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true,
    "require_last_push_approval": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_linear_history": true
}
JSON

# staging — same, but approvals optional
gh api -X PUT "repos/$REPO/branches/staging/protection" \
  --input - <<'JSON'
{
  "required_status_checks": { "strict": true, "contexts": ["CI"] },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 0,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

</details>

⚠️ Do **not** enable *Automatically delete head branches* on this repo. In this
model the head of a promotion PR is `develop` or `staging` — long-lived branches
that must survive the merge. It is currently off; leave it off.

After enabling either mechanism, `branch-guard.yml` becomes a redundant second
layer — harmless to keep as defence in depth.

## Commit messages

Conventional Commits, matching existing history:

```
feat(fno): add options greeks panel
fix(charting): stop scroll-back cache from dropping merged bars
chore(deps): drop unused three.js
refactor(quant): extract consensus engine into quant-core
docs(readme): document the branch model
```

## A note on the external auth API

Authentication, broker credentials, credit, and payments are **not in this
repository** — they are a separate deployment behind
`NEXT_PUBLIC_API_BASE_URL` (production: `https://api-web.stratai.live`). Changes
to login or payment behaviour usually belong in that repository, not this one.
See [`frontend/src/store/useAuthStore.ts`](frontend/src/store/useAuthStore.ts).
