<!-- Target the right base branch:
       feature/fix/chore  ->  develop
       develop            ->  staging
       staging            ->  main      (production — needs an approving review)
     See CONTRIBUTING.md. -->

## What

<!-- What changed, in a sentence or two. -->

## Why

<!-- The problem this solves. Link the issue or tracker row if there is one. -->

## How it was verified

<!-- The commands you actually ran and their outcome. Delete rows that don't apply. -->

- [ ] `cd frontend && npx tsc --noEmit`
- [ ] `cd frontend && npx vitest run`
- [ ] `cd frontend/src-tauri && cargo build --lib && cargo test --lib`
- [ ] `cd <crate> && cargo check && cargo test`
- [ ] `cd agents/deep-quant-loop && python -m pytest`
- [ ] Manually exercised in the running app

<!-- Paste relevant output, or say plainly what you could not verify and why.
     "Not verified — needs a live Kite session" is a useful answer. -->

## Risk

<!-- What could break, and what to watch after deploy. For a PR into main, say
     whether this touches live trading, order placement, or auth. -->

- [ ] Touches order placement / paper trading
- [ ] Touches auth or credential handling
- [ ] Changes a database schema or migration
- [ ] Changes a public IPC command or WS contract
- [ ] None of the above

## Checklist

- [ ] Branched off `develop` (or `main` for a `hotfix/*`)
- [ ] Scoped to one concern
- [ ] No secrets, tokens, or `.env` values in the diff
- [ ] No debug logging or commented-out code left behind
- [ ] Docs updated if behaviour or setup changed
