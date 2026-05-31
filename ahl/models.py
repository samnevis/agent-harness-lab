"""Core data models shared across the harness.

Everything that crosses a component boundary (task -> agent -> tracer -> grader ->
scoreboard) is one of these dataclasses so the pieces stay decoupled and the
on-disk JSON format is stable.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Check:
    """A single success criterion. The command passes if it exits with code 0."""

    name: str
    cmd: str


@dataclass
class FileRules:
    """Globs describing which files an agent is allowed / forbidden to modify.

    - ``allow``: if non-empty, every changed file MUST match one of these globs.
    - ``deny``: a changed file matching any of these globs is a hard violation
      (e.g. secrets, lockfiles, CI config) regardless of ``allow``.
    """

    allow: List[str] = field(default_factory=list)
    deny: List[str] = field(default_factory=list)


@dataclass
class Budget:
    """Optional resource ceilings for a task.

    A run that exceeds either ceiling is failed by the grader even if the checks
    pass -- catching agents that brute-force their way to a green build by burning
    unbounded steps or money.
    """

    max_cost_usd: Optional[float] = None
    max_steps: Optional[int] = None

    @property
    def is_set(self) -> bool:
        return self.max_cost_usd is not None or self.max_steps is not None


@dataclass
class TaskSpec:
    """A single benchmark task."""

    id: str
    name: str
    prompt: str
    description: str = ""
    repo: Optional[str] = None  # path to a seed repo template, relative to task dir
    base_ref: str = "HEAD"  # only used when repo is an existing git repo
    setup: List[str] = field(default_factory=list)
    checks: List[Check] = field(default_factory=list)
    solution: List[str] = field(default_factory=list)  # used by the mock agent
    file_rules: FileRules = field(default_factory=FileRules)
    timeout_sec: int = 600
    tags: List[str] = field(default_factory=list)
    difficulty: str = "medium"  # easy | medium | hard
    budget: Budget = field(default_factory=Budget)
    scan_secrets: bool = True  # scan changed files + trace for leaked secrets
    path: Optional[Path] = None  # task directory on disk

    @property
    def repo_path(self) -> Optional[Path]:
        if self.repo is None:
            return None
        if self.path is None:
            return Path(self.repo)
        return (self.path / self.repo).resolve()


@dataclass
class TraceEvent:
    """One recorded thing the agent did."""

    ts: float
    type: str  # "command" | "tool_call" | "file_change" | "note" | "usage" | "result"
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Usage:
    """Token / cost accounting for a run."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_usd += other.cost_usd


@dataclass
class AgentResult:
    """What an agent adapter returns after working on a task."""

    exit_code: int = 0
    usage: Usage = field(default_factory=Usage)
    steps: int = 0
    summary: str = ""


@dataclass
class CheckResult:
    name: str
    passed: bool
    exit_code: int
    output: str = ""


@dataclass
class SecretLeak:
    """A secret-looking string found in a changed file or trace output."""

    where: str  # file path or "trace"
    kind: str  # e.g. "aws_access_key", "private_key"
    preview: str  # redacted snippet


@dataclass
class GradeResult:
    success: bool
    checks: List[CheckResult] = field(default_factory=list)
    files_changed: List[str] = field(default_factory=list)
    lines_added: int = 0
    lines_removed: int = 0
    allow_violations: List[str] = field(default_factory=list)
    deny_violations: List[str] = field(default_factory=list)
    secret_leaks: List[SecretLeak] = field(default_factory=list)
    budget_violations: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)  # why it failed, if it did


@dataclass
class RunResult:
    """The full record of one (task, agent) execution. Persisted as result.json."""

    run_id: str
    task_id: str
    agent_name: str
    model: str
    started_at: float
    ended_at: float
    duration_sec: float
    agent: AgentResult
    grade: GradeResult
    sandbox_path: str = ""
    trace_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return _to_jsonable(self)


def _to_jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj
