"""Orchestration: tie sandbox + agent + tracer + grader together and persist.

This is the engine. Everything else is a component it composes.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional

from .agents.base import Agent
from .grader import enforce_budget, grade
from .models import RunResult
from .sandbox import Sandbox
from .tasks import TaskSpec
from .tracer import Tracer


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in text) or "x"


def run_task(
    task: TaskSpec,
    agent: Agent,
    runs_dir: Path,
    keep_sandbox: bool = False,
) -> RunResult:
    """Execute one (task, agent) pair end to end and write the result to disk."""
    runs_dir = Path(runs_dir)
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    label = f"{agent.name}__{_slug(agent.model) or 'default'}"
    run_dir = runs_dir / task.id / label / run_id
    sandbox_dir = run_dir / "sandbox"

    tracer = Tracer()
    started = time.time()

    if task.repo_path is not None and (task.repo_path / ".git").exists():
        sandbox = Sandbox.from_git_worktree(task.repo_path, task.base_ref, sandbox_dir)
    else:
        sandbox = Sandbox.from_template(task.repo_path, sandbox_dir)

    try:
        # Setup commands run before the agent and are part of the recorded trace,
        # but they are the harness's doing, not the agent's.
        for cmd in task.setup:
            tracer.run_command(cmd, cwd=sandbox.workdir, timeout=task.timeout_sec)
        steps_before_agent = tracer.steps

        agent_result = agent.run(task, sandbox.workdir, tracer)
        # Recompute steps to exclude setup commands from the agent's step count.
        agent_result.steps = max(tracer.steps - steps_before_agent, 0)

        grade_result = grade(task, sandbox, tracer)
        if task.budget.is_set:
            enforce_budget(
                grade_result,
                task.budget,
                cost_usd=agent_result.usage.cost_usd,
                steps=agent_result.steps,
            )
        ended = time.time()

        trace_path = tracer.finalize(run_dir / "trace.jsonl")

        result = RunResult(
            run_id=run_id,
            task_id=task.id,
            agent_name=agent.name,
            model=agent.model,
            started_at=started,
            ended_at=ended,
            duration_sec=round(ended - started, 3),
            agent=agent_result,
            grade=grade_result,
            sandbox_path=str(sandbox.workdir) if keep_sandbox else "",
            trace_path=str(trace_path),
        )
        (run_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
        return result
    finally:
        if not keep_sandbox:
            sandbox.cleanup()
