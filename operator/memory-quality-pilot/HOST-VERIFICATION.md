# Host acceptance checklist — read-only pilot

This is a **manual operator handoff**, not permission for remote execution, deployment, service restart, index rebuild, new memory capture, or a production repair. Work against the currently installed GBrain CLI; do not install the version of the public fork in place of it. Use only a permitted, non-client development source when exercising retrieval.

## 0. Confirm identity and boundaries

1. Identify the actual vault path, its mounted storage, and the currently running GBrain/Hermes/Hindsight versions. The GitHub branch containing this pilot may be older than the machine installation.
2. Confirm the expected source and active brain with `gbrain sources current --json` (only if supported by that local CLI). Never infer the source from the current directory alone.
3. Review local memory policy, bank permissions, externally routed model/embedding destinations, and the provenance of the existing scope check before enabling integrations or testing the Hindsight write path.
4. No data from client matters, legal sources, credentials, or raw notes belongs in a public GitHub issue, CI artifact, or external benchmark export. Never publish the full status output or changed filenames.

## 1. Obtain the baseline, without modifying the vault

From the directory containing `scripts/memory_quality_gate.py`, run in a local shell with paths appropriate to the *actual* host (these are example parameters, not a discovery mechanism):

```bash
python3 --version
command -v gbrain

gbrain --version
gbrain sources current --json

python3 scripts/memory_quality_gate.py audit \
  --vault "$VAULT_PATH" \
  --source "$SOURCE_ID" \
  --nightly-status "$NIGHTLY_STATUS_FILE" \
  --json
```

Set `VAULT_PATH`, `SOURCE_ID`, and `NIGHTLY_STATUS_FILE` before running. This first pass deliberately omits `--scope-script` and `--preflight-frontmatter` until their read-only behaviors are confirmed against the actual installation.

Inspect aggregated checks: failed nightly job, embedding coverage, failed/queued ingestion work, active backfill, dirty/untracked vault files, unknown source-output formats. The audit does not claim that Git changes are failed sync, that a model-generated memory is authenticated, or that the installed CLI itself cannot maintain caches.

After reviewing the exact installed validator's help and non-fixing behavior on disposable synthetic Markdown, optionally run a second pass with `--preflight-frontmatter`. Only pass a `--scope-script` that is independently inspected and known to be read-only with `--check`. These opt-ins may execute more commands and are not required for the first baseline.

## 2. Capture a source-backed retrieval baseline

Use a non-client, permitted development source. Prepare held-out, synthetic/opaque gold IDs that correspond to exact permitted source notes. Run the currently authorized search/recall tool separately and export **only** note IDs, allowed scope IDs, source state, and supporting opaque evidence IDs; do not export raw queries or documents. Fill the JSONL contracts in the pilot README and run:

```bash
python3 scripts/memory_quality_gate.py evaluate \
  --gold /path/to/authorized-gold.jsonl \
  --results /path/to/authorized-results.jsonl \
  --json
```

The evaluator requires the expected note in the top five, authorized scopes, nonempty evidence references, and every note used by a `current` answer to be a returned verified note. Failures require review, not automatic changes. A green score on the included synthetic files does not prove production retrieval accuracy.

## 3. Fresh-session continuity proof

Use two **separate** test sessions in one approved development bank, with a synthetic decision containing a reason that is not derivable from source code. Inspect the local Hindsight policy and authorized inference destinations before permitting any memory write. In the first session, capture using the already approved memory lifecycle (no new hook); in the second, retrieve through the same configured provider. Record a matching bank/project ID, source or receipt IDs, observed answer, and evidence of correct scope. Attempt a deliberately unauthorized scope read using synthetic notes and require denial. Do not create a cross-client shared bank. If any step is unavailable, report **UNMEASURED**, not PASS.

## 4. Evidence required for acceptance

- Host identifier generalized to an approved internal environment label; installed GBrain version and source resolution; no secrets or private paths in shared outputs.
- Reproduced nightly and source health with timestamps and observed diagnostic status; no claim of sync recovery from worktree cleanliness.
- Exact non-client gold IDs, authorized retrieval IDs and metrics; stale/as-current, unauthorized-scope, citation and missing-result results.
- Two separate sessions with verified memory write/readback references and a denied cross-scope attempt; no use of remembered text as current repository, court, or business authority.
- Independent reviewer checks the execution provenance and confirms no changed vault files, config, schedules, providers, bank grants, or external inference routing attributable to this pilot.

## 5. Release boundary

Keep the existing draft PR unmerged until the independent review, CI diagnosis and host acceptance are documented. Do not enable automated hooks, add providers, install scheduled jobs, run `gbrain ... --fix`, `sync`, `remediate`, or migrate existing knowledge based only on this pilot. Rollback is simply stopping use of the standalone tool and closing the PR; production state is untouched.
