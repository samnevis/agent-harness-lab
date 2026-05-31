"""Loading and discovering task specs from disk.

A task lives in its own directory containing ``task.yaml`` and (optionally) a
``repo/`` template that seeds the sandbox.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import yaml

from .models import Budget, Check, FileRules, TaskSpec


def load_task(task_dir: Path) -> TaskSpec:
    task_dir = Path(task_dir).resolve()
    spec_file = task_dir / "task.yaml"
    if not spec_file.exists():
        raise FileNotFoundError(f"No task.yaml in {task_dir}")
    with spec_file.open() as f:
        raw = yaml.safe_load(f) or {}

    checks = [Check(name=c["name"], cmd=c["cmd"]) for c in raw.get("checks", [])]
    fr = raw.get("file_rules", {}) or {}
    file_rules = FileRules(allow=list(fr.get("allow", [])), deny=list(fr.get("deny", [])))

    bg = raw.get("budget", {}) or {}
    budget = Budget(
        max_cost_usd=bg.get("max_cost_usd"),
        max_steps=bg.get("max_steps"),
    )

    return TaskSpec(
        id=raw.get("id", task_dir.name),
        name=raw.get("name", task_dir.name),
        prompt=raw["prompt"],
        description=raw.get("description", ""),
        repo=raw.get("repo"),
        base_ref=raw.get("base_ref", "HEAD"),
        setup=list(raw.get("setup", [])),
        checks=checks,
        solution=list(raw.get("solution", [])),
        file_rules=file_rules,
        timeout_sec=int(raw.get("timeout_sec", 600)),
        tags=list(raw.get("tags", [])),
        difficulty=raw.get("difficulty", "medium"),
        budget=budget,
        scan_secrets=bool(raw.get("scan_secrets", True)),
        path=task_dir,
    )


def discover_tasks(tasks_root: Path) -> List[TaskSpec]:
    tasks_root = Path(tasks_root)
    tasks: List[TaskSpec] = []
    if not tasks_root.exists():
        return tasks
    for spec_file in sorted(tasks_root.glob("*/task.yaml")):
        tasks.append(load_task(spec_file.parent))
    return tasks


def find_task(tasks_root: Path, task_id: str) -> TaskSpec:
    for task in discover_tasks(tasks_root):
        if task.id == task_id:
            return task
    raise KeyError(f"Task '{task_id}' not found under {tasks_root}")


def filter_tasks(
    tasks: List[TaskSpec],
    tags: Optional[List[str]] = None,
    difficulty: Optional[str] = None,
) -> List[TaskSpec]:
    """Keep tasks matching ALL given tags and (if set) the difficulty."""
    out = tasks
    if tags:
        wanted = set(tags)
        out = [t for t in out if wanted.issubset(set(t.tags))]
    if difficulty:
        out = [t for t in out if t.difficulty == difficulty]
    return out
