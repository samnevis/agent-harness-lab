"""Aggregate persisted runs into a comparison report.

Reads every ``result.json`` under a runs directory and produces:
  - a per-task comparison (one row per agent/model),
  - a leaderboard ranked by success rate then cost-per-success.

Output is available both as a rich console table and as portable Markdown.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class RunRow:
    task_id: str
    agent: str
    model: str
    success: bool
    steps: int
    cost_usd: float
    duration_sec: float
    files_changed: int
    deny_violations: int
    allow_violations: int
    loop_detected: bool
    reasons: List[str]
    run_id: str = ""
    trace_path: str = ""
    secret_leaks: int = 0
    budget_violations: int = 0


def load_runs(runs_dir: Path) -> List[RunRow]:
    runs_dir = Path(runs_dir)
    rows: List[RunRow] = []
    for result_file in sorted(runs_dir.glob("*/*/*/result.json")):
        try:
            data = json.loads(result_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        loop = _loop_from_trace(result_file.parent / "trace.jsonl")
        grade = data.get("grade", {})
        rows.append(
            RunRow(
                task_id=data.get("task_id", "?"),
                agent=data.get("agent_name", "?"),
                model=data.get("model", "") or "default",
                success=bool(grade.get("success")),
                steps=int(data.get("agent", {}).get("steps", 0)),
                cost_usd=float(data.get("agent", {}).get("usage", {}).get("cost_usd", 0.0)),
                duration_sec=float(data.get("duration_sec", 0.0)),
                files_changed=len(grade.get("files_changed", [])),
                deny_violations=len(grade.get("deny_violations", [])),
                allow_violations=len(grade.get("allow_violations", [])),
                loop_detected=loop,
                reasons=list(grade.get("reasons", [])),
                run_id=data.get("run_id", ""),
                trace_path=str(result_file.parent / "trace.jsonl"),
                secret_leaks=len(grade.get("secret_leaks", [])),
                budget_violations=len(grade.get("budget_violations", [])),
            )
        )
    return rows


def _loop_from_trace(trace_path: Path) -> bool:
    if not trace_path.exists():
        return False
    try:
        first = trace_path.read_text().splitlines()[0]
        return bool(json.loads(first).get("loop_detected"))
    except (IndexError, json.JSONDecodeError, OSError):
        return False


@dataclass
class LeaderEntry:
    key: str  # agent/model
    runs: int
    successes: int
    avg_steps: float
    cost_per_success: Optional[float]
    avg_duration: float

    @property
    def success_rate(self) -> float:
        return self.successes / self.runs if self.runs else 0.0


def leaderboard(rows: List[RunRow]) -> List[LeaderEntry]:
    by_key: Dict[str, List[RunRow]] = defaultdict(list)
    for r in rows:
        by_key[f"{r.agent} / {r.model}"].append(r)

    entries: List[LeaderEntry] = []
    for key, group in by_key.items():
        successes = [r for r in group if r.success]
        cost_per_success = (
            round(sum(r.cost_usd for r in successes) / len(successes), 4)
            if successes
            else None
        )
        entries.append(
            LeaderEntry(
                key=key,
                runs=len(group),
                successes=len(successes),
                avg_steps=round(sum(r.steps for r in group) / len(group), 1),
                cost_per_success=cost_per_success,
                avg_duration=round(sum(r.duration_sec for r in group) / len(group), 2),
            )
        )
    # Rank: highest success rate first, then cheapest successful run.
    entries.sort(
        key=lambda e: (-e.success_rate, e.cost_per_success if e.cost_per_success is not None else 1e9)
    )
    return entries


@dataclass
class FlakeStat:
    task_id: str
    agent_key: str
    attempts: int
    passes: int

    @property
    def pass_rate(self) -> float:
        return self.passes / self.attempts if self.attempts else 0.0

    @property
    def is_flaky(self) -> bool:
        return 0 < self.passes < self.attempts


def flakiness(rows: List[RunRow]) -> List[FlakeStat]:
    """Group runs by (task, agent/model) and measure pass rate across attempts."""
    by_cell: Dict[str, List[RunRow]] = defaultdict(list)
    for r in rows:
        by_cell[f"{r.task_id}::{r.agent}/{r.model}"].append(r)
    stats: List[FlakeStat] = []
    for group in by_cell.values():
        first = group[0]
        stats.append(
            FlakeStat(
                task_id=first.task_id,
                agent_key=f"{first.agent}/{first.model}",
                attempts=len(group),
                passes=sum(1 for r in group if r.success),
            )
        )
    # Flaky cells first, then by lowest pass rate.
    stats.sort(key=lambda s: (not s.is_flaky, s.pass_rate))
    return stats


def to_markdown(rows: List[RunRow]) -> str:
    lines: List[str] = ["# Harness Lab — Scoreboard", ""]

    board = leaderboard(rows)
    lines += ["## Leaderboard", ""]
    lines += ["| Agent / Model | Success | Avg steps | Cost/success | Avg time (s) |"]
    lines += ["|---|---|---|---|---|"]
    for e in board:
        cps = f"${e.cost_per_success}" if e.cost_per_success is not None else "—"
        lines.append(
            f"| {e.key} | {e.successes}/{e.runs} ({e.success_rate*100:.0f}%) "
            f"| {e.avg_steps} | {cps} | {e.avg_duration} |"
        )

    lines += ["", "## Runs by task", ""]
    by_task: Dict[str, List[RunRow]] = defaultdict(list)
    for r in rows:
        by_task[r.task_id].append(r)
    for task_id in sorted(by_task):
        lines += [f"### {task_id}", ""]
        lines += ["| Agent/Model | Result | Steps | Cost | Files | Loop | Notes |"]
        lines += ["|---|---|---|---|---|---|---|"]
        for r in by_task[task_id]:
            result = "PASS" if r.success else "FAIL"
            flags = []
            if r.deny_violations:
                flags.append(f"{r.deny_violations} forbidden")
            if r.allow_violations:
                flags.append(f"{r.allow_violations} out-of-scope")
            notes = "; ".join(r.reasons) if r.reasons else ("; ".join(flags) or "")
            lines.append(
                f"| {r.agent}/{r.model} | {result} | {r.steps} | ${r.cost_usd} "
                f"| {r.files_changed} | {'yes' if r.loop_detected else 'no'} | {notes} |"
            )
        lines.append("")
    return "\n".join(lines)
