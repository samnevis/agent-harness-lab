"""Adapter for real, external agent CLIs (Cursor agent, Claude Code, Codex, etc.).

You give it a command template via config, e.g.::

    {"cmd": "cursor-agent -p {prompt_file} --output-format json"}

Supported placeholders:
  {prompt}       -> the task prompt, shell-quoted
  {prompt_file}  -> path to a file containing the prompt
  {workdir}      -> the sandbox working directory

The command runs inside the sandbox so any files it writes land in the diff.
Two optional, well-known files let an external agent report richer data back:

  $AHL_TRACE  -> a JSONL file the agent may append tool/MCP-call events to.
  $AHL_USAGE  -> a JSON file with {input_tokens, output_tokens, cost_usd}.

If present after the run, they are merged into the trace. Everything still works
if the agent reports nothing -- you just get coarser (command-level) tracing.
"""

from __future__ import annotations

import json
import shlex
import tempfile
from pathlib import Path

from ..models import AgentResult, TaskSpec, Usage
from ..tracer import Tracer
from .base import Agent


class CommandAgent(Agent):
    name = "command"

    def run(self, task: TaskSpec, workdir: Path, tracer: Tracer) -> AgentResult:
        template = self.config.get("cmd")
        if not template:
            raise ValueError(
                "CommandAgent requires config['cmd'] (the agent CLI command template)."
            )

        prompt_file = Path(workdir) / ".ahl_prompt.txt"
        prompt_file.write_text(task.prompt)
        trace_file = Path(tempfile.mkstemp(prefix="ahl-trace-", suffix=".jsonl")[1])
        usage_file = Path(tempfile.mkstemp(prefix="ahl-usage-", suffix=".json")[1])

        cmd = template.format(
            prompt=shlex.quote(task.prompt),
            prompt_file=shlex.quote(str(prompt_file)),
            workdir=shlex.quote(str(workdir)),
        )
        tracer.note(f"CommandAgent running: {cmd}", model=self.model)

        proc = tracer.run_command(
            cmd,
            cwd=workdir,
            env={"AHL_TRACE": str(trace_file), "AHL_USAGE": str(usage_file)},
            timeout=task.timeout_sec,
        )

        self._ingest_agent_trace(trace_file, tracer)
        usage = self._ingest_usage(usage_file)
        if usage is not None:
            tracer.record_usage(usage)

        return AgentResult(
            exit_code=proc.returncode,
            usage=tracer.usage,
            steps=tracer.steps,
            summary=f"External agent exited {proc.returncode}.",
        )

    @staticmethod
    def _ingest_agent_trace(trace_file: Path, tracer: Tracer) -> None:
        if not trace_file.exists():
            return
        for line in trace_file.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            tracer.tool_call(
                name=rec.get("name", rec.get("tool", "tool")),
                args=rec.get("args", {}),
                result=str(rec.get("result", "")),
            )

    @staticmethod
    def _ingest_usage(usage_file: Path):
        if not usage_file.exists():
            return None
        try:
            data = json.loads(usage_file.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        return Usage(
            input_tokens=int(data.get("input_tokens", 0)),
            output_tokens=int(data.get("output_tokens", 0)),
            cost_usd=float(data.get("cost_usd", 0.0)),
        )
