"""Regression tracking.

A *baseline* is a snapshot of how each (task, agent/model) pair did: pass/fail,
steps, and cost. After a new suite run we compare against the baseline to find:

- **regressions**: was passing, now failing (the thing that fails a CI gate),
- **fixes**: was failing, now passing,
- **cost regressions**: still passing but materially more expensive / more steps,
- **new** / **removed** cells.

Baselines are plain JSON so they live in git next to the code and act as the
"saved failed runs you re-check later".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .scoreboard import RunRow, load_runs

# A run is a "cost regression" if cost grows by more than this fraction AND by
# more than a small absolute floor (so $0.001 -> $0.002 doesn't trip it).
COST_REGRESSION_FRACTION = 0.25
COST_REGRESSION_FLOOR_USD = 0.01


@dataclass
class Cell:
    task_id: str
    agent_key: str
    success: bool
    steps: int
    cost_usd: float

    @property
    def key(self) -> str:
        return f"{self.task_id}::{self.agent_key}"


def _rows_to_cells(rows: List[RunRow]) -> Dict[str, Cell]:
    """Collapse possibly-many runs per cell into one (latest wins via best/last).

    We keep the *most representative* run: prefer a success, then lowest cost.
    """
    by_key: Dict[str, List[RunRow]] = {}
    for r in rows:
        key = f"{r.task_id}::{r.agent}/{r.model}"
        by_key.setdefault(key, []).append(r)

    cells: Dict[str, Cell] = {}
    for key, group in by_key.items():
        successes = [r for r in group if r.success]
        chosen = min(successes, key=lambda r: r.cost_usd) if successes else group[-1]
        cells[key] = Cell(
            task_id=chosen.task_id,
            agent_key=f"{chosen.agent}/{chosen.model}",
            success=chosen.success,
            steps=chosen.steps,
            cost_usd=chosen.cost_usd,
        )
    return cells


def build_baseline(runs_dir: Path) -> Dict[str, dict]:
    cells = _rows_to_cells(load_runs(runs_dir))
    return {
        key: {
            "task_id": c.task_id,
            "agent_key": c.agent_key,
            "success": c.success,
            "steps": c.steps,
            "cost_usd": c.cost_usd,
        }
        for key, c in cells.items()
    }


def save_baseline(runs_dir: Path, path: Path) -> Path:
    path = Path(path)
    path.write_text(json.dumps(build_baseline(runs_dir), indent=2, sort_keys=True))
    return path


def load_baseline(path: Path) -> Dict[str, Cell]:
    path = Path(path)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {
        k: Cell(
            task_id=v["task_id"],
            agent_key=v["agent_key"],
            success=bool(v["success"]),
            steps=int(v.get("steps", 0)),
            cost_usd=float(v.get("cost_usd", 0.0)),
        )
        for k, v in raw.items()
    }


@dataclass
class Diff:
    regressions: List[str] = field(default_factory=list)
    fixes: List[str] = field(default_factory=list)
    cost_regressions: List[str] = field(default_factory=list)
    new_cells: List[str] = field(default_factory=list)
    removed_cells: List[str] = field(default_factory=list)

    @property
    def has_regressions(self) -> bool:
        return bool(self.regressions or self.cost_regressions)

    def summary(self) -> str:
        parts = [
            f"{len(self.regressions)} regression(s)",
            f"{len(self.fixes)} fix(es)",
            f"{len(self.cost_regressions)} cost regression(s)",
        ]
        if self.new_cells:
            parts.append(f"{len(self.new_cells)} new")
        if self.removed_cells:
            parts.append(f"{len(self.removed_cells)} removed")
        return ", ".join(parts)


def compare(baseline: Dict[str, Cell], runs_dir: Path) -> Diff:
    current = _rows_to_cells(load_runs(runs_dir))
    diff = Diff()

    for key, cur in current.items():
        base = baseline.get(key)
        if base is None:
            diff.new_cells.append(key)
            continue
        if base.success and not cur.success:
            diff.regressions.append(key)
        elif not base.success and cur.success:
            diff.fixes.append(key)
        elif base.success and cur.success and _is_cost_regression(base, cur):
            diff.cost_regressions.append(
                f"{key} (${base.cost_usd} -> ${cur.cost_usd})"
            )

    for key in baseline:
        if key not in current:
            diff.removed_cells.append(key)

    return diff


def _is_cost_regression(base: Cell, cur: Cell) -> bool:
    delta = cur.cost_usd - base.cost_usd
    if delta <= COST_REGRESSION_FLOOR_USD:
        return False
    if base.cost_usd <= 0:
        return True
    return (delta / base.cost_usd) > COST_REGRESSION_FRACTION
