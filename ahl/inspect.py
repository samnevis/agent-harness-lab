"""Load and read back persisted runs for the ``trace`` and ``show`` commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def find_run_dir(runs_dir: Path, run_id: str) -> Path:
    """Resolve a run by id (or a direct path to its run directory)."""
    p = Path(run_id)
    if p.is_dir() and (p / "result.json").exists():
        return p
    runs_dir = Path(runs_dir)
    for result_file in runs_dir.glob("*/*/*/result.json"):
        try:
            data = json.loads(result_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("run_id") == run_id:
            return result_file.parent
    raise KeyError(f"Run '{run_id}' not found under {runs_dir}")


def latest_run_dir(runs_dir: Path, task_id: Optional[str] = None) -> Path:
    runs_dir = Path(runs_dir)
    candidates: List[Tuple[float, Path]] = []
    for result_file in runs_dir.glob("*/*/*/result.json"):
        try:
            data = json.loads(result_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if task_id and data.get("task_id") != task_id:
            continue
        candidates.append((float(data.get("started_at", 0)), result_file.parent))
    if not candidates:
        raise KeyError(f"No runs found under {runs_dir}")
    return max(candidates, key=lambda c: c[0])[1]


def load_result(run_dir: Path) -> Dict:
    return json.loads((Path(run_dir) / "result.json").read_text())


def read_trace(trace_path: Path) -> Tuple[Dict, List[Dict]]:
    """Return (meta, events) from a trace.jsonl file."""
    trace_path = Path(trace_path)
    meta: Dict = {}
    events: List[Dict] = []
    if not trace_path.exists():
        return meta, events
    for i, line in enumerate(trace_path.read_text().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if i == 0 and rec.get("type") == "meta":
            meta = rec
        else:
            events.append(rec)
    return meta, events
