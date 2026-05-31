"""Agent CI: the harness pointed at a pull request / commit.

This is the reliability-gate product. Given a set of tasks, a set of agents, and
optionally a baseline, it:

  1. runs the whole suite,
  2. compares against the baseline to find regressions,
  3. writes a PR-comment-ready Markdown summary (and optional HTML artifact),
  4. returns an exit code that fails the build when:
       - any run touched a forbidden (deny) file, or
       - a previously-passing cell now fails (regression), or
       - the overall pass rate drops below ``min_pass_rate``.

It deliberately reuses the same engine as local runs, so "what CI checks" and
"what I can reproduce locally" are identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .regression import Diff, compare, load_baseline
from .scoreboard import RunRow, leaderboard, load_runs
from .suite import SuiteReport


@dataclass
class GateResult:
    ok: bool
    exit_code: int
    reasons: List[str]
    markdown: str
    diff: Optional[Diff] = None


def _security_violations(rows: List[RunRow]) -> List[str]:
    out = []
    for r in rows:
        if r.deny_violations:
            out.append(f"{r.task_id} [{r.agent}/{r.model}]: {r.deny_violations} forbidden file(s)")
    return out


def evaluate_gate(
    runs_dir: Path,
    baseline_path: Optional[Path] = None,
    min_pass_rate: float = 0.0,
    fail_on_security: bool = True,
) -> GateResult:
    rows = load_runs(runs_dir)
    reasons: List[str] = []

    diff: Optional[Diff] = None
    if baseline_path is not None:
        baseline = load_baseline(baseline_path)
        if baseline:
            diff = compare(baseline, runs_dir)
            if diff.regressions:
                reasons.append(f"{len(diff.regressions)} regression(s): " + ", ".join(diff.regressions))
            if diff.cost_regressions:
                reasons.append(f"{len(diff.cost_regressions)} cost regression(s)")

    if fail_on_security:
        sec = _security_violations(rows)
        if sec:
            reasons.append("security: " + "; ".join(sec))

    total = len(rows)
    passed = sum(1 for r in rows if r.success)
    pass_rate = passed / total if total else 0.0
    if min_pass_rate > 0 and pass_rate < min_pass_rate:
        reasons.append(f"pass rate {pass_rate*100:.0f}% < required {min_pass_rate*100:.0f}%")

    ok = not reasons
    markdown = _gate_markdown(rows, reasons, diff, pass_rate, ok)
    return GateResult(ok=ok, exit_code=0 if ok else 1, reasons=reasons, markdown=markdown, diff=diff)


def _gate_markdown(rows, reasons, diff: Optional[Diff], pass_rate: float, ok: bool) -> str:
    status = "✅ PASS" if ok else "❌ FAIL"
    lines = [f"## Agent CI — {status}", ""]
    total = len(rows)
    passed = sum(1 for r in rows if r.success)
    lines.append(f"**{passed}/{total} runs passing** ({pass_rate*100:.0f}%)")
    if diff is not None:
        lines.append(f"Regression check: {diff.summary()}")
    lines.append("")

    if reasons:
        lines += ["### Why this gate failed", ""]
        for r in reasons:
            lines.append(f"- {r}")
        lines.append("")

    if diff is not None and diff.fixes:
        lines += ["### Newly fixed", ""]
        for f in diff.fixes:
            lines.append(f"- {f}")
        lines.append("")

    board = leaderboard(rows)
    lines += ["### Leaderboard", "",
              "| Agent / Model | Success | Avg steps | Cost/success |",
              "|---|---|---|---|"]
    for e in board:
        cps = f"${e.cost_per_success}" if e.cost_per_success is not None else "—"
        lines.append(
            f"| {e.key} | {e.successes}/{e.runs} ({e.success_rate*100:.0f}%) | {e.avg_steps} | {cps} |"
        )
    return "\n".join(lines)
