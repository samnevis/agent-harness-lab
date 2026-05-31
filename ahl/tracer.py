"""The black-box recorder.

The tracer is handed to an agent adapter. Every shell command an agent runs
should go through :meth:`Tracer.run_command`, which executes it, times it,
captures truncated output, and appends a structured event. Agents can also record
tool/MCP calls, free-form notes, and token usage.

At the end of a run the event stream is written to ``trace.jsonl`` -- a replay of
exactly what the agent did.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

from .models import TraceEvent, Usage

_MAX_OUTPUT_CHARS = 4000


class Tracer:
    def __init__(self) -> None:
        self.events: List[TraceEvent] = []
        self.usage = Usage()
        self.started_at = time.time()
        self.ended_at: Optional[float] = None

    # -- recording primitives -------------------------------------------------

    def _emit(self, type_: str, data: Dict) -> TraceEvent:
        ev = TraceEvent(ts=time.time(), type=type_, data=data)
        self.events.append(ev)
        return ev

    def note(self, message: str, **extra) -> None:
        self._emit("note", {"message": message, **extra})

    def tool_call(self, name: str, args: Optional[Dict] = None, result: str = "") -> None:
        self._emit(
            "tool_call",
            {"name": name, "args": args or {}, "result": _truncate(result)},
        )

    def record_usage(self, usage: Usage) -> None:
        self.usage.add(usage)
        self._emit(
            "usage",
            {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cost_usd": usage.cost_usd,
            },
        )

    # -- the workhorse ---------------------------------------------------------

    def run_command(
        self,
        cmd: str,
        cwd: Path,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> subprocess.CompletedProcess:
        """Run a shell command, record it as a trace event, and return the result."""
        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        t0 = time.time()
        timed_out = False
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=str(cwd),
                env=full_env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            exit_code = proc.returncode
            stdout, stderr = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as e:
            timed_out = True
            exit_code = 124
            stdout = e.stdout or ""
            stderr = (e.stderr or "") + f"\n[ahl] command timed out after {timeout}s"
            proc = subprocess.CompletedProcess(cmd, exit_code, stdout, stderr)
        duration = time.time() - t0

        self._emit(
            "command",
            {
                "cmd": cmd,
                "exit_code": exit_code,
                "duration_sec": round(duration, 3),
                "timed_out": timed_out,
                "stdout": _truncate(stdout if isinstance(stdout, str) else ""),
                "stderr": _truncate(stderr if isinstance(stderr, str) else ""),
            },
        )
        return proc

    # -- counters & finalize ---------------------------------------------------

    @property
    def steps(self) -> int:
        """A 'step' is an action the agent took: a command or a tool call."""
        return sum(1 for e in self.events if e.type in ("command", "tool_call"))

    def command_outputs(self) -> List[str]:
        """All recorded command stdout/stderr, for secret scanning of the trace."""
        out: List[str] = []
        for e in self.events:
            if e.type == "command":
                out.append((e.data.get("stdout") or "") + "\n" + (e.data.get("stderr") or ""))
        return out

    def detect_loops(self, window: int = 2) -> bool:
        """Heuristic: did the agent run the same command back-to-back repeatedly?"""
        cmds = [e.data.get("cmd") for e in self.events if e.type == "command"]
        for i in range(len(cmds) - window * 2 + 1):
            if cmds[i : i + window] == cmds[i + window : i + window * 2]:
                return True
        return False

    def finalize(self, path: Path) -> Path:
        self.ended_at = time.time()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            header = {
                "type": "meta",
                "started_at": self.started_at,
                "ended_at": self.ended_at,
                "steps": self.steps,
                "loop_detected": self.detect_loops(),
            }
            f.write(json.dumps(header) + "\n")
            for ev in self.events:
                f.write(json.dumps(dataclasses.asdict(ev)) + "\n")
        return path


def _truncate(text: str) -> str:
    if text is None:
        return ""
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    half = _MAX_OUTPUT_CHARS // 2
    return text[:half] + "\n...[truncated]...\n" + text[-half:]
