"""Deterministic agents used for testing the harness itself and for demos.

These let the entire pipeline (sandbox -> trace -> grade -> scoreboard) run with
no API keys or network, so the harness is verifiable in CI.

- :class:`MockAgent` "solves" a task by running the shell commands listed under
  the task's ``solution`` field. It also synthesizes a fake token/cost figure so
  the cost columns in the scoreboard are populated.
- :class:`NoopAgent` does nothing -- it models an agent that *claims* success but
  doesn't actually fix anything, which the grader must catch.
"""

from __future__ import annotations

from pathlib import Path

from ..models import AgentResult, TaskSpec, Usage
from ..tracer import Tracer
from .base import Agent


class MockAgent(Agent):
    name = "mock"

    def run(self, task: TaskSpec, workdir: Path, tracer: Tracer) -> AgentResult:
        tracer.note(f"MockAgent received prompt: {task.prompt!r}")
        # Model a sensible tool-use pattern: inspect the workspace before editing.
        tracer.tool_call("list_dir", {"path": "."}, result="ok")
        for cmd in task.solution:
            tracer.tool_call("read_file", {"path": "target"}, result="ok")
            tracer.run_command(cmd, cwd=workdir, timeout=task.timeout_sec)

        # Synthesize plausible usage so cost/token reporting is exercised.
        # Scale loosely with how much "work" (steps) was done.
        per_step_in, per_step_out = 1500, 600
        steps = max(tracer.steps, 1)
        usage = Usage(
            input_tokens=per_step_in * steps,
            output_tokens=per_step_out * steps,
            cost_usd=round((per_step_in * steps * 3e-6) + (per_step_out * steps * 15e-6), 4),
        )
        tracer.record_usage(usage)
        return AgentResult(
            exit_code=0,
            usage=usage,
            steps=tracer.steps,
            summary=f"Applied {len(task.solution)} solution step(s).",
        )


class NoopAgent(Agent):
    name = "noop"

    def run(self, task: TaskSpec, workdir: Path, tracer: Tracer) -> AgentResult:
        tracer.note("NoopAgent: claiming success without doing any work.")
        usage = Usage(input_tokens=800, output_tokens=120, cost_usd=0.004)
        tracer.record_usage(usage)
        return AgentResult(
            exit_code=0,
            usage=usage,
            steps=tracer.steps,
            summary="Did nothing (control agent).",
        )


class RecklessAgent(Agent):
    """Solves the task but also pokes at a secret file.

    Models the dangerous-but-effective agent: tests would pass, yet it violated a
    file rule. Used to demonstrate the security gate catching a "successful" run.
    """

    name = "reckless"

    def run(self, task: TaskSpec, workdir: Path, tracer: Tracer) -> AgentResult:
        tracer.note("RecklessAgent: applying solution and snooping around.")
        for cmd in task.solution:
            tracer.run_command(cmd, cwd=workdir, timeout=task.timeout_sec)
        # Touch a likely-forbidden file to trip deny rules.
        tracer.run_command("echo '# touched by agent' >> .env", cwd=workdir, timeout=task.timeout_sec)
        usage = Usage(input_tokens=3000, output_tokens=900, cost_usd=0.0225)
        tracer.record_usage(usage)
        return AgentResult(
            exit_code=0,
            usage=usage,
            steps=tracer.steps,
            summary="Solved the task but modified a forbidden file.",
        )
