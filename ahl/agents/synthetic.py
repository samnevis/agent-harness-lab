"""More deterministic-ish demo agents that exercise specific failure modes.

These exist so the harness can *demonstrate* the behaviors it detects without
needing a real, paid agent:

- :class:`FlakyAgent`  -- succeeds only some of the time, to show pass@k / flake
  rate over repeated runs. Determinism is controlled by a seed so tests are stable.
- :class:`LooperAgent` -- repeats the same command many times, to trip loop
  detection (and a step budget, if one is set).
- :class:`ScriptedAgent` -- writes files from its ``config['files']`` mapping
  instead of running the task's built-in solution, so a suite/config file can
  drive arbitrary edits.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..models import AgentResult, TaskSpec, Usage
from ..tracer import Tracer
from .base import Agent


class FlakyAgent(Agent):
    """Applies the solution on 'good' runs and a no-op on 'bad' runs.

    Whether a given run is good is decided by hashing (model, seed, attempt) so a
    fixed ``config['success_ratio']`` produces a stable but mixed pass/fail record.
    """

    name = "flaky"

    def run(self, task: TaskSpec, workdir: Path, tracer: Tracer) -> AgentResult:
        ratio = float(self.config.get("success_ratio", 0.5))
        seed = str(self.config.get("seed", ""))
        attempt = str(self.config.get("attempt", tracer.started_at))
        h = hashlib.sha256(f"{self.model}:{seed}:{attempt}".encode()).hexdigest()
        roll = int(h[:8], 16) / 0xFFFFFFFF
        will_succeed = roll < ratio

        tracer.note(f"FlakyAgent roll={roll:.3f} ratio={ratio} -> {'apply' if will_succeed else 'skip'}")
        if will_succeed:
            for cmd in task.solution:
                tracer.run_command(cmd, cwd=workdir, timeout=task.timeout_sec)
        usage = Usage(input_tokens=1200, output_tokens=400, cost_usd=0.009)
        tracer.record_usage(usage)
        return AgentResult(
            exit_code=0,
            usage=usage,
            steps=tracer.steps,
            summary="Flaky run: " + ("applied solution" if will_succeed else "gave up"),
        )


class LooperAgent(Agent):
    """Runs a harmless command many times -- models an agent stuck in a loop."""

    name = "looper"

    def run(self, task: TaskSpec, workdir: Path, tracer: Tracer) -> AgentResult:
        reps = int(self.config.get("reps", 6))
        tracer.note(f"LooperAgent: repeating a no-op {reps} times")
        for _ in range(reps):
            tracer.run_command("echo thinking...", cwd=workdir, timeout=task.timeout_sec)
        # It does eventually apply the fix, but only after wasting many steps.
        for cmd in task.solution:
            tracer.run_command(cmd, cwd=workdir, timeout=task.timeout_sec)
        usage = Usage(
            input_tokens=1500 * (reps + 1),
            output_tokens=300 * (reps + 1),
            cost_usd=round((1500 * (reps + 1) * 3e-6) + (300 * (reps + 1) * 15e-6), 4),
        )
        tracer.record_usage(usage)
        return AgentResult(
            exit_code=0,
            usage=usage,
            steps=tracer.steps,
            summary=f"Looped {reps}x before solving.",
        )


class ScriptedAgent(Agent):
    """Writes files from config instead of using the task's solution.

    ``config['files']`` is a {relative_path: contents} mapping. Useful for driving
    specific edits (good or bad) from a suite/config file.
    """

    name = "scripted"

    def run(self, task: TaskSpec, workdir: Path, tracer: Tracer) -> AgentResult:
        files = self.config.get("files", {})
        tracer.note(f"ScriptedAgent: writing {len(files)} file(s)")
        for rel, contents in files.items():
            target = Path(workdir) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            tracer.tool_call("write_file", {"path": rel}, result="ok")
            target.write_text(contents)
        usage = Usage(input_tokens=1000, output_tokens=500, cost_usd=0.0105)
        tracer.record_usage(usage)
        return AgentResult(
            exit_code=0,
            usage=usage,
            steps=tracer.steps,
            summary=f"Wrote {len(files)} scripted file(s).",
        )
