# Memory Quality Pilot — read-only operator harness

A standalone Python 3.12+ utility that **checks existing memory quality** without creating a new memory backend or changing GBrain, Hindsight, Hermes, Clio, or the underlying vault. It does not require a model key or additional package. The harness is deliberately separate from application runtime and does not imply a live integration.

## What is implemented

1. **Operational audit:** calls the installed `gbrain sources status <source>`, reads the existing nightly status file, runs an optional existing scope checker in `--check` mode, and counts Git worktree changes without reporting paths. Unknown command/schema/permissions are **unknown**, never successful.
2. **Changed-Markdown preflight:** optional `--preflight-frontmatter` validates the existing modified/untracked Markdown notes with the installed non-fixing `gbrain frontmatter validate`; filenames and document content stay out of reports, symlinks and out-of-vault paths are excluded, and a bounded cap prevents an unplanned full-vault sweep.
3. **Observation validation:** validates imported decision, correction, lesson and handoff metadata including owner scopes, project ownership, evidence references, time zone, supersession links, and contradictory verified decisions.
4. **Retrieval quality scoring:** evaluates exported known-answer retrieval sets with hit@5, scope leakage, stale-as-current, missing evidence, and missing result gates. It does **not** execute queries against confidential systems.
5. **Machine-readable health summary:** `--json` prints only aggregate metrics, issue codes and synthetic identifiers; it never prints note bodies, file paths, source queries, raw command output, or credentials.

## Use on a machine that already has GBrain and the vault

Run this standalone file from any location; it is **not** a replacement for your currently installed GBrain CLI and does not require installing the older fork from which it may have been staged.

```bash
python3 scripts/memory_quality_gate.py audit \
  --vault /path/to/your/vault \
  --source secondbrain \
  --nightly-status "$HOME/Library/Logs/secondbrain-nightly.status" \
  --scope-script /path/to/your/vault/scripts/gbrain_scope.py \
  --preflight-frontmatter \
  --json
```

No `--fix`, `sync`, `remediate`, token-bearing arguments, `git add`, or network/model calls are performed by this harness. The frontmatter preflight invokes the installed validator without `--fix`, but its behavior must be qualified against the installed CLI before enabling it on a production vault. The invoked pre-existing scope checker is trusted by the operator to be read-only with `--check`; do not supply an unreviewed script. The harness itself prints only aggregate diagnostics. A report is not written to disk unless the operator explicitly redirects stdout.

When the nightly file says `FAILED ... lint_errors=35` or the source shows 73% embeddings under the default 95% inspection threshold, a nonzero queue, or an active backfill, the report is not green. The 95% threshold is an initial *operator policy*, not evidence that every eligible source must be embedded. Missing files or incompatible CLI output yield `unknown`; an unclean Git worktree merits review but is **not** proof of a sync failure.

## Offline observation gate

```bash
python3 scripts/memory_quality_gate.py validate --observations examples/synthetic_observations.jsonl --json
```

Observation JSONL objects have `id`, `scope_id`, `project_id`, `agent_id`, `event_type`, `occurred_at` (offset-aware ISO 8601), `rationale`, `state`, and `evidence_refs`. Decisions and corrections also require a `decision_key`; corrections may have `supersedes` pointing to an existing ID in the same scope and project. Values are **metadata evidence claims**: this validator does not independently authenticate receipts or source contents. Do not export confidential rationale, private URLs, secrets, or case content into public artifacts. For live legal work, original systems and their authorization policies remain controlling.

## Offline retrieval scorecard

```bash
python3 scripts/memory_quality_gate.py evaluate \
  --gold examples/synthetic_gold.jsonl \
  --results examples/synthetic_results.jsonl --json
```

Gold JSONL fields: `id`, `expected_note_ids`, `allowed_scopes`. Result JSONL fields: `id`, `hits` (note ID, scope ID, state), `answer_state`, optional `answer_evidence_refs`, and `used_note_ids` for an answer asserted `current`. The result export **must originate from an authorized, separately executed search**; these fixtures are synthetic and demonstrate contracts, not real recall accuracy. The test passes at `hit@5 >= 95%` with zero unauthorized hits, zero stale-as-current, zero uncited current answers, zero unresolved or unverified notes used in current answers, and zero missing cases.

## Host qualification and CI

See `HOST-VERIFICATION.md` for the read-only Mac/desktop acceptance handoff. The PR also includes a dedicated GitHub Actions workflow for the **synthetic** Python tests; the main GBrain test workflow is separate. Neither a local test pass nor a green CI workflow constitutes live host acceptance.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

No external dependencies, calls, live data, filesystem modifications to a real vault, or model spending. Test fixtures are synthetic. Test completion **does not qualify** installed agent memory or real-world retrieval.

## What remains deliberately outside this pilot

- No Hermes hooks or automatic Hindsight lesson retention until the existing local `MEMORY-POLICY.md` and authorized inference destinations are inspected.
- No modifications to live GBrain source, embeddings, schedules, indexes, or previously uncommitted files.
- No live case data, SharePoint/Clio changes, or WLO Lykos–SecondBrain release expansion.
- No claim of complete remediation from a green synthetic test; real Mac-hosted health and authorized fresh-session A/B testing remain separate acceptance steps.

## Rollback

Delete or stop using this standalone utility. It does not install hooks, services, indexes, timers, or other persistent runtime artifacts. If staged as a GitHub draft branch, close the draft PR without merging.
