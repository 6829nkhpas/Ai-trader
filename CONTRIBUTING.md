# Contributing to Ai-trader

## Branching model

**One branch: `main`.** Work is committed and pushed to it directly.

```
main ──► push ──► CI + production deploy (concurrently)
```

`develop` and `staging` were removed, along with `branch-guard.yml` and the
`.githooks/pre-push` hook that enforced the old three-rung ladder. If you go
looking for them in the history: they were deleted deliberately, not lost.

| Branch | Purpose | Deploys to |
|---|---|---|
| **`main`** | The only long-lived branch. Everything lands here. | Droplet backend + the web app on every push that touches server or `frontend/` code |

Short-lived branches are still fine for work in progress — nothing stops you
opening a PR into `main` if a change deserves review. There is simply no longer a
promotion path you are required to walk.

### What this trades away

Worth being explicit, because it is a real cost and not a formality:

* **Every push to `main` is a production deploy.** There is no pre-production rung
  to catch a bad change first.
* **CI does not gate the deploy.** `ci.yml` and `deploy-server.yml` both fire on
  the same push event and run *concurrently*. A red CI tells you a broken commit
  is already live; it does not prevent it. Wiring a `workflow_run` dependency into
  `deploy-server.yml` would turn it into a real gate, at the cost of waiting for
  the Rust build on every deploy.
* **Nothing enforces review.** See [Enforcement](#enforcement).

So the checks below are the safety net. Run them *before* you push, because after
you push the site has already changed.

## Everyday workflow

```bash
git pull
# work, committing as you go
# verify (see below) — BEFORE pushing, since the push deploys
git push origin main
```

Watch the deploy:

```bash
gh run watch "$(gh run list --workflow=deploy-server --limit 1 --json databaseId -q '.[0].databaseId')"
```

Then confirm the site actually came back:

```bash
curl -sI https://app.stratai.live/ | head -1        # 200
curl -s  https://app.stratai.live/api/features      # {"enforced":true,…}
```

If a push broke production, the fastest honest fix is forward:

```bash
git revert <sha> && git push origin main
```

## Before you push

Run the checks that cover what you touched. From the repo root:

| Changed | Command |
|---|---|
| Rust (a service crate) | `cd <crate> && cargo check && cargo test` |
| Aggregator (option-chain math) | `cd aggregator && cargo test --bin aggregator` |
| Tool server | `cd tool-server && cargo build --release` |
| Frontend types | `cd frontend && npx tsc --noEmit` |
| Frontend tests | `cd frontend && npx vitest run` |
| Charting (largest suite) | `cd frontend && npx vitest run src/charting` |
| The web build itself | `cd frontend && npm run build:web` |
| Python agent | `cd agents/deep-quant-loop && python -m pytest` |

`npm run build:web` is worth running for anything that touches routing, config or
`app/api/`: a route can typecheck and test green and still fail to register in the
production build. That exact failure mode has bitten twice — once as a
`PageNotFoundError: /_document` that only appeared in the production build, and
once as an `/api/tools/*` 404 caused by a missing upstream path prefix.

Known pre-existing failures that are **not** your fault — they fail on a clean
tree too (9 tests across 4 files):
`frontend/src/components/fno/__tests__/selectors.bounding.property.test.ts`,
`frontend/src/components/chart/__tests__/SplitChartContainer.test.tsx`,
`frontend/src/components/layout/__tests__/TerminalLayout.modeSelector.test.tsx`,
`frontend/src/components/panels/__tests__/LeftPanel.search.test.tsx`.
`tools/load_tester` also does not currently compile. `tsc --noEmit` is clean.

## Commits

- Explain **why**, not just what. The diff already says what changed.
- Note anything you measured rather than assumed — a comment recording "404
  without the prefix, 405 with it" saves the next person the experiment.
- Call out deliberate trade-offs and known ceilings so they are not mistaken for
  oversights later.

## Pull requests

Optional now, but still the right call for a change that is large, risky, or
touches money / auth / compliance paths. If you open one:

- Fill in the template — what changed, why, how you verified it.
- Keep it scoped. A 40-file PR doing three unrelated things is three PRs.
- CI runs on PRs into `main` as well as on pushes to it.

## Enforcement

There is **none** server-side, and it is not currently purchasable on this plan.
The repo is private under a Free organization and *both* mechanisms are gated:

| Attempted | Result |
|-----------|--------|
| `gh api repos/thestratai/Ai-trader/branches/main/protection` | `403 Upgrade to GitHub Pro or make this repository public` |
| `gh api repos/thestratai/Ai-trader/rulesets` (GET and POST) | `403 Upgrade to GitHub Pro or make this repository public` |
| `gh api orgs/thestratai/rulesets` | needs `admin:org`; org-level rulesets require **Team** regardless |

Per GitHub's docs, rulesets and protected branches cover private repos only on
**Pro / Team / Enterprise**; org-wide rulesets need **Team**. `thestratai` is on
`free`. So there was never a server-side rule to switch off — the previous model
was policy, enforced by a CI reporter and a local hook, both now removed.

What remains is discipline: run the checks before you push, and read the deploy.

### If you later want a gate back

Two independent levers, in increasing strictness:

1. **Make CI block the deploy.** Add a `workflow_run` trigger to
   `deploy-server.yml` so it only runs after `ci` concludes successfully on the
   same SHA. Needs no paid plan, and is the highest-value change if a bad deploy
   ever bites.

2. **Require green CI on `main` at the git level.** Needs **Team** (or a public
   repo), then:

   ```bash
   gh api -X POST repos/thestratai/Ai-trader/rulesets --input - <<'JSON'
   {
     "name": "protect-main",
     "target": "branch",
     "enforcement": "active",
     "conditions": { "ref_name": { "include": ["refs/heads/main"], "exclude": [] } },
     "rules": [
       { "type": "deletion" },
       { "type": "non_fast_forward" },
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
   ```

   The status context is `CI` — the `name:` of the `ci-ok` job, which aggregates
   every other CI job. Point at that one rather than at each job individually, so
   a path-filtered skip cannot wedge a merge. This deliberately omits the
   `pull_request` rule: adding it would re-impose PR-only on `main`, which is the
   thing being removed here.
