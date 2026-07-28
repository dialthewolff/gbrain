# GBrain Fork Operations Status

**Updated:** 2026-07-28T05:25:59Z  
**Operational repository:** [`dialthewolff/gbrain`](https://github.com/dialthewolff/gbrain)  
**Companion GBrain page:** `projects/gbrain/status` (`gbrain get projects/gbrain/status`)

## Current state

The local installation and `dialthewolff/gbrain:master` are at `00ac8070`. The local `master` tracks `wolff/master`; `garrytan/gbrain` remains the upstream remote.

Integrated fixes:

- `2ed8dea0` — remove the broken tracked `node_modules` symlink and add a regression guard (upstream PR [#3463](https://github.com/garrytan/gbrain/pull/3463)).
- `811fbdc1` — reconcile GitHub Actions cost controls and use four consistent test shards.
- `771d4330` — reserve autopilot worker capacity and prevent fan-out deadlocks (upstream PR [#3469](https://github.com/garrytan/gbrain/pull/3469)).
- `942a3484` — close federated secondary-read and code-intelligence source-isolation gaps (upstream PR [#3470](https://github.com/garrytan/gbrain/pull/3470)).
- `00ac8070` — reconcile completed backlog entries (upstream PR [#3471](https://github.com/garrytan/gbrain/pull/3471)).

## Verification

- Local targeted suite: 133 passed, 0 failed.
- Local `bun run verify`: 32/32 passed.
- Local Actionlint and diff checks: passed.
- HTTP service: healthy on `localhost:3131`, version `0.42.66.1`, Postgres engine.
- Migrations: current through schema version 125.
- Extraction: 2,383 pages processed; 0 missing links or timeline entries created.
- Embeddings: 0 stale chunks; Pexoro reports 100% coverage.
- Skillpack check: healthy.
- Hosted fork Actions [run 30330111107](https://github.com/dialthewolff/gbrain/actions/runs/30330111107): verify, serial tests, gitleaks, and dedicated slow tests passed. All four broad unit-test shards reached the configured 22-minute job timeout and were cancelled; the aggregate `test-status` therefore failed. No individual assertion failure was reported before cancellation.

## Waiting / next actions

1. Adjust the fork's broad unit-test CI budget or partitioning and rerun until the hosted `Test` workflow is green.
2. Upstream maintainer may approve and merge PRs #3463, #3469, #3470, and #3471. This is desirable but no longer blocks the local installation.
3. Separate brain-content health debt remains (stale full-cycle timestamps and schema/type proliferation); it is not a runtime/code regression and requires policy or credential decisions.

## Related Pexoro hardening

Pexoro PR [#740](https://github.com/dialthewolff/pexoro/pull/740) committed the three DB-only pages re-exported by sync. The follow-up sync imported them as file-backed pages with no stale embeddings.
