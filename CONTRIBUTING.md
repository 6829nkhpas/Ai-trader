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

GitHub branch protection is **not currently active** on this repository — it is
private on a free plan, and the protection API returns:

```
403 Upgrade to GitHub Pro or make this repository public to enable this feature.
```

So the rules above are **policy, not physics.** Two things partially compensate:

- [`branch-guard.yml`](.github/workflows/branch-guard.yml) fails on any direct
  (non-merge) push to `main` or `staging`, and on any PR into `main` that does
  not come from `staging` or a `hotfix/*` branch. It reports the violation after
  the fact; it cannot prevent it.
- Review discipline. Until protection is available, this is the real gate.

### Turning on real protection

Once the repo is on GitHub Pro (or made public), run these to enforce the model
properly:

```bash
REPO=6829nkhpas/Ai-trader

# main — production: PR-only, 1 approval, no force-push, no deletion
gh api -X PUT "repos/$REPO/branches/main/protection" \
  --input - <<'JSON'
{
  "required_status_checks": null,
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
  "required_status_checks": null,
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

After enabling protection, `branch-guard.yml` becomes a redundant second layer —
harmless to keep as defence in depth.

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
