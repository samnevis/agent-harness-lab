"""Machine-readable exports for CI and dashboards.

- JUnit XML: so existing CI dashboards (GitHub, GitLab, Jenkins) render each run
  as a test case, with failure reasons in the message.
- JSON: the leaderboard + per-run rows, for custom tooling.
- SVG badge: a shields-style "agent pass NN%" badge you can embed in a README.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List
from xml.sax.saxutils import escape, quoteattr

from .scoreboard import RunRow, leaderboard


def to_junit_xml(rows: List[RunRow]) -> str:
    by_task: Dict[str, List[RunRow]] = defaultdict(list)
    for r in rows:
        by_task[r.task_id].append(r)

    total = len(rows)
    failures = sum(1 for r in rows if not r.success)
    out: List[str] = ['<?xml version="1.0" encoding="UTF-8"?>']
    out.append(f'<testsuites tests="{total}" failures="{failures}">')
    for task_id, group in sorted(by_task.items()):
        tf = sum(1 for r in group if not r.success)
        out.append(
            f'  <testsuite name={quoteattr(task_id)} tests="{len(group)}" failures="{tf}">'
        )
        for r in group:
            name = quoteattr(f"{r.agent}/{r.model}")
            out.append(
                f'    <testcase classname={quoteattr(task_id)} name={name} time="{r.duration_sec}">'
            )
            if not r.success:
                msg = quoteattr("; ".join(r.reasons) or "failed")
                out.append(f'      <failure message={msg}>{escape("; ".join(r.reasons))}</failure>')
            out.append("    </testcase>")
        out.append("  </testsuite>")
    out.append("</testsuites>")
    return "\n".join(out)


def to_json(rows: List[RunRow]) -> str:
    board = [
        {
            "key": e.key,
            "runs": e.runs,
            "successes": e.successes,
            "success_rate": round(e.success_rate, 4),
            "avg_steps": e.avg_steps,
            "cost_per_success": e.cost_per_success,
            "avg_duration": e.avg_duration,
        }
        for e in leaderboard(rows)
    ]
    runs = [
        {
            "task_id": r.task_id,
            "agent": r.agent,
            "model": r.model,
            "success": r.success,
            "steps": r.steps,
            "cost_usd": r.cost_usd,
            "duration_sec": r.duration_sec,
            "files_changed": r.files_changed,
            "deny_violations": r.deny_violations,
            "allow_violations": r.allow_violations,
            "loop_detected": r.loop_detected,
            "reasons": r.reasons,
        }
        for r in rows
    ]
    return json.dumps({"leaderboard": board, "runs": runs}, indent=2)


def _color(pass_rate: float) -> str:
    if pass_rate >= 0.9:
        return "#4c1"
    if pass_rate >= 0.7:
        return "#a3c51c"
    if pass_rate >= 0.5:
        return "#dfb317"
    if pass_rate >= 0.3:
        return "#fe7d37"
    return "#e05d44"


def to_badge_svg(rows: List[RunRow], label: str = "agent pass") -> str:
    total = len(rows)
    passed = sum(1 for r in rows if r.success)
    rate = passed / total if total else 0.0
    value = f"{rate*100:.0f}% ({passed}/{total})"
    color = _color(rate)

    # Rough width estimate (6px/char + padding) so the badge isn't clipped.
    lw = 6 * len(label) + 10
    vw = 6 * len(value) + 10
    total_w = lw + vw
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="20" role="img" aria-label="{label}: {value}">
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <rect rx="3" width="{total_w}" height="20" fill="#555"/>
  <rect rx="3" x="{lw}" width="{vw}" height="20" fill="{color}"/>
  <rect rx="3" width="{total_w}" height="20" fill="url(#s)"/>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,sans-serif" font-size="11">
    <text x="{lw/2}" y="14">{escape(label)}</text>
    <text x="{lw + vw/2}" y="14">{escape(value)}</text>
  </g>
</svg>"""
