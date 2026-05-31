"""Grade an existing pull request diff.

This is Agent CI's real entry point: an agent already opened a PR, and we want to
know if that PR is mergeable *by the same standard the harness uses for tasks* --
do the checks pass, did it touch forbidden files, did it stay in scope?

Unlike a task run, no agent executes here. We check out the PR head in a detached
``git worktree``, compute the diff against the merge-base with the target branch,
run the repo's own checks there, and apply the file rules. The output is a normal
:class:`RunResult` persisted into the runs dir, so ``ahl report`` / ``ahl ci``
treat a graded PR exactly like any other run.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import List, Optional

from .grader import grade_changes, run_checks
from .models import AgentResult, Check, FileRules, GradeResult, RunResult, Usage
from .secrets import scan_files
from .sandbox import (
    add_worktree_at,
    diff_numstat_between,
    merge_base,
    remove_worktree,
    resolve_sha,
)
from .tracer import Tracer


def grade_pr(
    repo: Path,
    runs_dir: Path,
    base: str = "origin/main",
    head: str = "HEAD",
    checks: Optional[List[Check]] = None,
    file_rules: Optional[FileRules] = None,
    task_id: str = "pr",
    author: str = "pr",
    model: str = "",
    timeout_sec: int = 1800,
    keep_worktree: bool = False,
    scan_secrets: bool = True,
) -> RunResult:
    repo = Path(repo).resolve()
    checks = checks or []
    file_rules = file_rules or FileRules()

    base_sha = merge_base(repo, base, head)
    head_sha = resolve_sha(repo, head)

    runs_dir = Path(runs_dir)
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    label = f"{author}__{model or 'default'}"
    run_dir = runs_dir / task_id / label / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    # Must be absolute: `git worktree add` runs with cwd=repo and would otherwise
    # resolve a relative dest against the repo, not our process cwd.
    worktree = (run_dir / "worktree").resolve()

    tracer = Tracer()
    started = time.time()
    tracer.note(
        f"Grading PR: {head} ({head_sha[:8]}) vs base {base} (merge-base {base_sha[:8]})"
    )

    files_changed, lines_added, lines_removed = diff_numstat_between(repo, base_sha, head_sha)
    tracer.note(
        f"diff: {len(files_changed)} file(s), +{lines_added}/-{lines_removed}",
        files=files_changed,
    )

    add_worktree_at(repo, head_sha, worktree)
    try:
        secret_leaks = scan_files(worktree, files_changed) if scan_secrets else []
        check_results = run_checks(checks, worktree, tracer, timeout_sec)
    finally:
        if not keep_worktree:
            remove_worktree(repo, worktree)

    grade_result = grade_changes(
        files_changed=files_changed,
        lines_added=lines_added,
        lines_removed=lines_removed,
        check_results=check_results,
        file_rules=file_rules,
        require_changes=False,  # an empty PR is a no-op, not a failure
        secret_leaks=secret_leaks,
    )
    ended = time.time()
    trace_path = tracer.finalize(run_dir / "trace.jsonl")

    result = RunResult(
        run_id=run_id,
        task_id=task_id,
        agent_name=author,
        model=model,
        started_at=started,
        ended_at=ended,
        duration_sec=round(ended - started, 3),
        agent=AgentResult(
            exit_code=0 if grade_result.success else 1,
            usage=Usage(),
            steps=0,
            summary=f"Graded PR diff ({len(files_changed)} files).",
        ),
        grade=grade_result,
        sandbox_path="",
        trace_path=str(trace_path),
    )
    (run_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
    return result
