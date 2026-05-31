"""Run a matrix of (task x agent) and collect the results.

This is the batch layer above ``runner.run_task``. Agent CI and the regression
checker both run a suite and then act on the aggregate.

An agent spec is a small dict so a suite can mix the built-in mock/noop agents
with real external CLI agents:

    {"agent": "command", "model": "claude-code", "config": {"cmd": "claude -p ..."}}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .agents import build_agent
from .models import RunResult
from .runner import run_task
from .tasks import TaskSpec, discover_tasks, find_task


@dataclass
class AgentSpec:
    agent: str
    model: str = ""
    config: Dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.agent} / {self.model or 'default'}"


@dataclass
class SuiteReport:
    results: List[RunResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.grade.success)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    def for_agent(self, key: str) -> List[RunResult]:
        return [r for r in self.results if _agent_key(r) == key]


def _agent_key(r: RunResult) -> str:
    return f"{r.agent_name} / {r.model or 'default'}"


def resolve_tasks(
    tasks_dir: Path, task_ids: Optional[List[str]] = None
) -> List[TaskSpec]:
    if task_ids:
        return [find_task(tasks_dir, tid) for tid in task_ids]
    return discover_tasks(tasks_dir)


def run_suite(
    tasks: List[TaskSpec],
    agents: List[AgentSpec],
    runs_dir: Path,
    keep_sandbox: bool = False,
    on_result=None,
    repeat: int = 1,
) -> SuiteReport:
    """Run every (task, agent) pair ``repeat`` times.

    ``on_result`` is an optional callback(RunResult). Repeats get a distinct
    ``attempt`` injected into the agent config so non-deterministic agents (e.g.
    the flaky demo agent) actually vary across attempts -- this is what powers
    pass@k / flake-rate reporting.
    """
    report = SuiteReport()
    repeat = max(1, repeat)
    for task in tasks:
        for spec in agents:
            for attempt in range(repeat):
                config = dict(spec.config)
                config["attempt"] = attempt
                agent = build_agent(spec.agent, model=spec.model, config=config)
                result = run_task(task, agent, runs_dir, keep_sandbox=keep_sandbox)
                report.results.append(result)
                if on_result is not None:
                    on_result(result)
    return report
