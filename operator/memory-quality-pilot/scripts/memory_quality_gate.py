#!/usr/bin/env python3
"""Read-only operational health and offline memory-quality gates.

Uses Python standard library only. Never uploads documents or invokes model APIs.
Does not implement automatic memory capture or modify a knowledge source.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
NIGHTLY_KV = re.compile(r"(?<!\S)([A-Za-z][A-Za-z0-9_]*)=([^\s]+)")
VALID_STATES = {"candidate", "verified", "superseded", "retracted"}
EVENT_TYPES = {"decision", "correction", "error", "lesson", "discovery", "handoff"}


def run_readonly(argv: list[str], *, cwd: Path | None = None, timeout: int = 60,
                 env: dict[str, str] | None = None) -> dict[str, Any]:
    """Return the exit status and captured text; never invoke a shell."""
    safe_env = dict(os.environ)
    safe_env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
    if env:
        safe_env.update(env)
    try:
        proc = subprocess.run(argv, cwd=str(cwd) if cwd else None,
                              capture_output=True, text=True, timeout=timeout,
                              check=False, env=safe_env)
        return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        # No untrusted subprocess output in structured reports; no command contents leaked.
        return {"returncode": None, "stdout": "", "stderr": type(exc).__name__}


def parse_nightly(text: str) -> dict[str, Any]:
    """Summarize the final recorded run; never emit raw log content."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return {"status": "unknown", "reason": "empty_status_file"}
    line = lines[-1]
    marker = re.search(r"\b(SUCCESS|SUCCEEDED|PASS|PASSED|FAILED|FAIL|WARN|WARNING)\b", line, re.I)
    label = marker.group(1).upper() if marker else None
    kv = dict(NIGHTLY_KV.findall(line))
    numeric: dict[str, int] = {}
    for name in ("lint_errors", "blocked"):
        if name in kv and kv[name].isdigit():
            numeric[name] = int(kv[name])
    step_segment = re.search(r"(?<!\S)failed_steps=(.*?)(?=\s+[A-Za-z][A-Za-z0-9_]*=|$)", line)
    failed_steps = step_segment.group(1) if step_segment else ""
    count = sum(int(num) for num in re.findall(r":(\d+)(?:\b|$)", failed_steps))
    bad = label in {"FAILED", "FAIL"} or count > 0 or numeric.get("lint_errors", 0) > 0
    warn = label in {"WARN", "WARNING"} or kv.get("index_health", "").upper() in {"WARN", "FAILED", "DEGRADED"}
    return {
        "status": "fail" if bad else "attention" if warn else "pass" if label in {"SUCCESS", "SUCCEEDED", "PASS", "PASSED"} else "unknown",
        "failed_step_count": count,
        "lint_errors": numeric.get("lint_errors"),
        "index_health": kv.get("index_health", "unknown").upper()[:24],
    }


def parse_source_status(text: str, *, source: str, min_embed_percent: int = 95) -> dict[str, Any]:
    """Parse one row of human-readable `gbrain sources status <source>`.

    Unrecognized formats are UNKNOWN, not a green pass. Indentation and
    thousands separators are permitted; page names are not returned.
    """
    row: list[str] | None = None
    for line in text.splitlines():
        fields = line.strip().split()
        if fields and fields[0] == source and len(fields) >= 7:
            row = fields
            break
    if row is None:
        return {"status": "unknown", "reason": "source_row_not_found"}
    embed = row[2]
    if not re.fullmatch(r"\d+(?:\.\d+)?%", embed):
        return {"status": "unknown", "reason": "embedding_percentage_unrecognized"}
    coverage = float(embed[:-1])
    try:
        failures = int(row[4].replace(",", ""))
        queue = int(row[5].replace(",", ""))
        pages = int(row[6].replace(",", ""))
    except ValueError:
        return {"status": "unknown", "reason": "counts_unrecognized"}
    if not 0 <= coverage <= 100 or min(failures, queue, pages) < 0:
        return {"status": "unknown", "reason": "invalid_counts"}
    return {
        "status": "attention" if coverage < min_embed_percent or failures > 0 else "pass",
        "embed_percent": coverage,
        "embedding_policy_threshold_percent": min_embed_percent,
        "failed_items": failures,
        "queued_items": queue,
        "pages": pages,
        "backfill_state": row[3][:32],
    }


def parse_git_porcelain(text: str) -> dict[str, Any]:
    """Aggregate porcelain output without displaying confidential paths.

    NUL-separated records safely account for pathnames containing newlines and
    extra rename/copy path entries. git status itself does not prove sync.
    """
    if not text:
        return {"status": "pass", "modified_or_staged": 0, "untracked": 0, "renames": 0}
    rows = text.split("\0")
    changes = Counter()
    i = 0
    while i < len(rows):
        record = rows[i]
        i += 1
        if not record:
            continue
        if len(record) < 3 or record[2] != " ":
            return {"status": "unknown", "reason": "unexpected_git_porcelain"}
        xy = record[:2]
        if xy == "??":
            changes["untracked"] += 1
        else:
            changes["modified_or_staged"] += 1
        if "R" in xy or "C" in xy:
            changes["renames"] += 1
            if i >= len(rows) or not rows[i]:
                return {"status": "unknown", "reason": "incomplete_rename_record"}
            i += 1
    return {
        "status": "attention" if changes["modified_or_staged"] or changes["untracked"] else "pass",
        "modified_or_staged": changes["modified_or_staged"],
        "untracked": changes["untracked"],
        "renames": changes["renames"],
        "note": "Dirty Git state is not by itself evidence of failed indexing or failed sync.",
    }


def check_changed_frontmatter(vault: Path, *, timeout: int, max_files: int = 250) -> dict[str, Any]:
    """Run non-fixing GBrain frontmatter validator on changed Markdown only.

    Uses NUL-delimited Git file listing so unexpected filenames cannot become
    shell syntax. Symlinks or paths outside the vault are skipped. No raw
    document name, body or validator stderr is emitted.
    """
    changed = run_readonly(["git", "diff", "--name-only", "-z", "--diff-filter=ACMRT", "HEAD", "--"],
                           cwd=vault, timeout=timeout)
    untracked = run_readonly(["git", "ls-files", "--others", "--exclude-standard", "-z", "--"],
                             cwd=vault, timeout=timeout)
    if changed["returncode"] != 0 or untracked["returncode"] != 0:
        return {"status": "unknown", "reason": "changed_file_listing_unavailable"}
    names = set(filter(None, changed["stdout"].split("\0") + untracked["stdout"].split("\0")))
    files: list[Path] = []
    root = vault.resolve()
    for name in names:
        path = vault / name
        try:
            resolved = path.resolve(strict=True)
            if resolved.is_file() and resolved.suffix.lower() == ".md" and not path.is_symlink() and resolved.is_relative_to(root):
                files.append(resolved)
        except (OSError, RuntimeError, ValueError):
            return {"status": "unknown", "reason": "changed_path_cannot_be_verified"}
    if len(files) > max_files:
        return {"status": "unknown", "reason": "frontmatter_file_cap_exceeded", "markdown_files": len(files)}
    failed = 0
    unverified = 0
    for path in sorted(files):
        result = run_readonly(["gbrain", "frontmatter", "validate", str(path)], cwd=vault, timeout=timeout)
        if result["returncode"] is None:
            unverified += 1
        elif result["returncode"] != 0:
            failed += 1
    return {"status": "fail" if failed else "unknown" if unverified else "pass",
            "markdown_files": len(files), "invalid_markdown_files": failed,
            "unverified_files": unverified, "non_fixing_validation": True}


def audit(vault: Path, source: str, nightly_status: Path | None,
          scope_script: Path | None, timeout: int, min_embed_percent: int,
          preflight_frontmatter: bool = False) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    if not SAFE_ID.fullmatch(source):
        return {"status": "unknown", "reason": "invalid_source_id", "checks": {}}
    if not vault.is_dir():
        return {"status": "unknown", "reason": "vault_not_accessible", "checks": {}}
    output = run_readonly(["gbrain", "sources", "status", source], cwd=vault, timeout=timeout)
    checks["source_health"] = (parse_source_status(output["stdout"], source=source,
                                                   min_embed_percent=min_embed_percent)
                               if output["returncode"] == 0 else
                               {"status": "unknown", "reason": "gbrain_command_unavailable_or_failed"})
    git = run_readonly(["git", "-c", "core.fsmonitor=false", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                       cwd=vault, timeout=timeout)
    checks["vault_worktree"] = (parse_git_porcelain(git["stdout"])
                                if git["returncode"] == 0 else
                                {"status": "unknown", "reason": "git_status_unavailable_or_failed"})
    if nightly_status is not None:
        try:
            checks["nightly_job"] = parse_nightly(nightly_status.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            checks["nightly_job"] = {"status": "unknown", "reason": "nightly_status_unavailable"}
    if scope_script is not None:
        if scope_script.is_file() and scope_script.suffix == ".py":
            res = run_readonly([sys.executable, str(scope_script), "--check"], cwd=vault, timeout=timeout)
            checks["scope_check"] = {"status": "pass" if res["returncode"] == 0 else "fail" if res["returncode"] is not None else "unknown"}
        else:
            checks["scope_check"] = {"status": "unknown", "reason": "scope_script_unavailable"}
    if preflight_frontmatter:
        checks["changed_frontmatter"] = check_changed_frontmatter(vault, timeout=timeout)
    states = [v["status"] for v in checks.values()]
    overall = "fail" if "fail" in states else "attention" if "attention" in states else "unknown" if "unknown" in states else "pass"
    return {"status": overall, "source": source, "checks": checks,
            "runtime_verified": all(s not in {"unknown"} for s in states),
            "no_mutations_requested": True}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSON object required on line {line_no}")
        out.append(value)
    return out


def _string(row: dict[str, Any], key: str) -> bool:
    return isinstance(row.get(key), str) and bool(row[key].strip())


def validate_observations(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate metadata-only receipts and flag active-key conflicts.

    Does not infer truth from model-generated content. Verified means the
    *record* claims verified and points to evidence, not that this gate
    authenticated the external source.
    """
    problems: list[dict[str, Any]] = []
    keyed: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    by_id: dict[str, dict[str, Any]] = {}

    def add(index: int, code: str) -> None:
        problems.append({"row": index, "code": code})

    for index, row in enumerate(rows, 1):
        required = ("id", "scope_id", "project_id", "agent_id", "event_type", "occurred_at", "rationale", "state")
        for name in required:
            if not _string(row, name):
                add(index, "missing_" + name)
        ident = row.get("id")
        if not isinstance(ident, str) or not SAFE_ID.fullmatch(ident):
            add(index, "unsafe_or_missing_id")
        elif ident in by_id:
            add(index, "duplicate_id")
        else:
            by_id[ident] = row
        if row.get("event_type") not in EVENT_TYPES:
            add(index, "invalid_event_type")
        if row.get("state") not in VALID_STATES:
            add(index, "invalid_state")
        timestamp = row.get("occurred_at")
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if dt.tzinfo is None or dt.utcoffset() is None:
                add(index, "timestamp_without_timezone")
        except (ValueError, TypeError, AttributeError):
            add(index, "invalid_timestamp")
        evidence = row.get("evidence_refs")
        if not isinstance(evidence, list) or (row.get("state") == "verified" and not evidence):
            add(index, "missing_evidence_refs")
        elif isinstance(evidence, list) and any(not isinstance(x, str) or not x.strip() for x in evidence):
            add(index, "invalid_evidence_ref")
        if row.get("event_type") in {"decision", "correction"} and not _string(row, "decision_key"):
            add(index, "missing_decision_key")
        if row.get("state") == "verified" and _string(row, "decision_key"):
            keyed[(str(row.get("scope_id")), str(row.get("project_id")), row["decision_key"])].append(row)

    for index, row in enumerate(rows, 1):
        prior = row.get("supersedes")
        if prior is None:
            continue
        if not isinstance(prior, str) or prior not in by_id:
            add(index, "supersedes_missing_record")
        else:
            old = by_id[prior]
            if old.get("scope_id") != row.get("scope_id") or old.get("project_id") != row.get("project_id"):
                add(index, "cross_scope_supersession")
            if prior == row.get("id"):
                add(index, "self_supersession")
            if old.get("state") == "verified" and row.get("state") == "verified":
                add(index, "superseded_record_still_verified")
        seen: set[str] = set()
        cursor: Any = row
        while isinstance(cursor, dict) and isinstance(cursor.get("supersedes"), str):
            ref = cursor["supersedes"]
            if ref in seen or ref == row.get("id"):
                add(index, "supersession_cycle")
                break
            seen.add(ref)
            cursor = by_id.get(ref)
    for group in keyed.values():
        if len(group) > 1:
            problems.append({"code": "multiple_verified_decisions_for_key", "count": len(group)})
    return {"status": "pass" if not problems else "fail", "records": len(rows),
            "issues": problems, "issue_counts": dict(Counter(p["code"] for p in problems)),
            "authority_not_verified_externally": True}


def evaluate_retrieval(gold: list[dict[str, Any]], results: list[dict[str, Any]], *, k: int = 5,
                       min_hit_rate: float = 0.95) -> dict[str, Any]:
    """Evaluate previously exported results without running or broadening search.

    Datasets should contain opaque synthetic IDs, not confidential prompt text.
    Unknown/missing results never count as a pass.
    """
    if not 0 <= min_hit_rate <= 1 or k <= 0:
        raise ValueError("invalid evaluation threshold")
    by_id = {}
    duplicate_results = 0
    for item in results:
        ident = item.get("id")
        if ident in by_id:
            duplicate_results += 1
        by_id[ident] = item
    measures = Counter()
    checked_ids = set()
    for case in gold:
        ident = case.get("id")
        if not isinstance(ident, str) or not ident or ident in checked_ids:
            measures["invalid_gold"] += 1
            continue
        checked_ids.add(ident)
        row = by_id.get(ident)
        if row is None or not isinstance(row.get("hits"), list):
            measures["missing_or_invalid_result"] += 1
            continue
        expected = case.get("expected_note_ids")
        allowed = case.get("allowed_scopes")
        if not isinstance(expected, list) or not expected or not isinstance(allowed, list) or not allowed:
            measures["invalid_gold"] += 1
            continue
        hits = row["hits"]
        if any(isinstance(hit, dict) and hit.get("note_id") in expected for hit in hits[:k]):
            measures["hit_at_k"] += 1
        for hit in hits:
            if not isinstance(hit, dict) or hit.get("scope_id") not in allowed:
                measures["unauthorized_hits"] += 1
        if row.get("answer_state") == "current":
            if not row.get("answer_evidence_refs"):
                measures["uncited_current_answers"] += 1
            # A stale hit must never support an answer presented as current.
            used = row.get("used_note_ids")
            if not isinstance(used, list):
                measures["missing_used_note_ids"] += 1
            else:
                for hit in hits:
                    if isinstance(hit, dict) and hit.get("note_id") in used and hit.get("state") in {"superseded", "retracted"}:
                        measures["stale_as_current"] += 1
        elif row.get("answer_state") not in {"unknown", "historical", "unavailable"}:
            measures["invalid_answer_state"] += 1
    measures["duplicate_results"] = duplicate_results
    measures["unexpected_results"] = len(set(by_id) - checked_ids)
    total = len(gold)
    rate = (measures["hit_at_k"] / total) if total else 0.0
    hard = ("unauthorized_hits", "stale_as_current", "uncited_current_answers", "missing_or_invalid_result",
            "missing_used_note_ids", "invalid_gold", "invalid_answer_state", "duplicate_results", "unexpected_results")
    passed = total > 0 and rate >= min_hit_rate and all(measures[k] == 0 for k in hard)
    return {"status": "pass" if passed else "fail", "cases": total,
            "hit_at_k": measures["hit_at_k"], "k": k, "hit_rate": round(rate, 4),
            "minimum_hit_rate": min_hit_rate, "violations": {x: measures[x] for x in hard},
            "results_are_offline_exports": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("audit", help="Inspect available local status without changing sources")
    a.add_argument("--vault", type=Path, required=True)
    a.add_argument("--source", required=True)
    a.add_argument("--nightly-status", type=Path)
    a.add_argument("--scope-script", type=Path)
    a.add_argument("--timeout", type=int, default=60)
    a.add_argument("--min-embed-percent", type=int, default=95)
    a.add_argument("--preflight-frontmatter", action="store_true")
    a.add_argument("--json", action="store_true")
    v = sub.add_parser("validate", help="Validate an exported observation JSONL file")
    v.add_argument("--observations", required=True, type=Path)
    v.add_argument("--json", action="store_true")
    e = sub.add_parser("evaluate", help="Score offline retrieval results against a gold set")
    e.add_argument("--gold", required=True, type=Path)
    e.add_argument("--results", required=True, type=Path)
    e.add_argument("--k", type=int, default=5)
    e.add_argument("--min-hit-rate", type=float, default=0.95)
    e.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "audit":
            if args.timeout <= 0 or not 0 <= args.min_embed_percent <= 100:
                parser.error("timeout and embedding threshold are out of range")
            report = audit(args.vault, args.source, args.nightly_status,
                           args.scope_script, args.timeout, args.min_embed_percent, args.preflight_frontmatter)
        elif args.command == "validate":
            report = validate_observations(read_jsonl(args.observations))
        else:
            report = evaluate_retrieval(read_jsonl(args.gold), read_jsonl(args.results),
                                        k=args.k, min_hit_rate=args.min_hit_rate)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        report = {"status": "unknown", "reason": type(exc).__name__}
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("STATUS:", report["status"].upper())
        for key, value in report.items():
            if key != "status":
                print(f"{key}: {json.dumps(value, sort_keys=True)}")
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
