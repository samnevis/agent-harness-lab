"""MCP Doctor: grade the *tools* an agent used, not the task outcome.

Where the grader answers "did the agent solve the task?", MCP Doctor answers
"were the tools/MCP calls the agent made any good?". It reads the ``tool_call``
and ``command`` events out of one or more ``trace.jsonl`` files and reports, per
tool:

  - call count
  - error rate (results that look like errors)
  - redundant/duplicate calls (same tool + args back-to-back)
  - average result size (a proxy for chattiness / wasted context)

It also surfaces overall health signals: wasted calls and a simple 0-100 score so
tools can be compared across agents/models the same way tasks are.

A "tool" here covers both explicit MCP/tool calls (``tool_call`` events) and raw
shell commands (the command's first token), so it works even for agents that only
shell out.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

_ERROR_MARKERS = ("error", "exception", "traceback", "failed", "not found", "denied")


@dataclass
class ToolStat:
    name: str
    calls: int = 0
    errors: int = 0
    redundant: int = 0
    total_result_chars: int = 0

    @property
    def error_rate(self) -> float:
        return self.errors / self.calls if self.calls else 0.0

    @property
    def avg_result_chars(self) -> int:
        return self.total_result_chars // self.calls if self.calls else 0


@dataclass
class McpReport:
    tools: Dict[str, ToolStat] = field(default_factory=dict)

    @property
    def total_calls(self) -> int:
        return sum(t.calls for t in self.tools.values())

    @property
    def total_errors(self) -> int:
        return sum(t.errors for t in self.tools.values())

    @property
    def total_redundant(self) -> int:
        return sum(t.redundant for t in self.tools.values())

    @property
    def wasted_calls(self) -> int:
        """Calls that returned an error or were redundant."""
        return self.total_errors + self.total_redundant

    def health_score(self) -> int:
        """0-100. Starts at 100, penalized for error and waste rates."""
        if self.total_calls == 0:
            return 100
        error_rate = self.total_errors / self.total_calls
        waste_rate = self.wasted_calls / self.total_calls
        score = 100 - (error_rate * 60) - (waste_rate * 40)
        return max(0, min(100, round(score)))


def _looks_like_error(result: str) -> bool:
    low = result.lower()
    return any(marker in low for marker in _ERROR_MARKERS)


def _iter_trace_events(trace_path: Path):
    if not trace_path.exists():
        return
    for line in trace_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") in ("tool_call", "command"):
            yield rec


def analyze_trace(trace_path: Path) -> McpReport:
    return analyze_traces([trace_path])


def analyze_traces(trace_paths: List[Path]) -> McpReport:
    report = McpReport()
    last_signature: Optional[str] = None

    for trace_path in trace_paths:
        for rec in _iter_trace_events(Path(trace_path)):
            etype = rec.get("type")
            data = rec.get("data", {})
            if etype == "tool_call":
                name = data.get("name", "tool")
                result = str(data.get("result", ""))
                args = data.get("args", {})
                is_error = _looks_like_error(result)
                signature = f"tool:{name}:{json.dumps(args, sort_keys=True)}"
            else:  # command
                cmd = data.get("cmd", "")
                name = cmd.strip().split()[0] if cmd.strip() else "command"
                result = str(data.get("stderr", ""))
                is_error = int(data.get("exit_code", 0)) != 0 or _looks_like_error(result)
                signature = f"cmd:{cmd}"

            stat = report.tools.setdefault(name, ToolStat(name=name))
            stat.calls += 1
            stat.total_result_chars += len(result)
            if is_error:
                stat.errors += 1
            if signature == last_signature:
                stat.redundant += 1
            last_signature = signature

    return report


def analyze_runs(runs_dir: Path, task_id: Optional[str] = None) -> Dict[str, McpReport]:
    """Per agent/model MCP report aggregated across that agent's runs."""
    runs_dir = Path(runs_dir)
    grouped: Dict[str, List[Path]] = defaultdict(list)
    for result_file in sorted(runs_dir.glob("*/*/*/result.json")):
        try:
            data = json.loads(result_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if task_id and data.get("task_id") != task_id:
            continue
        key = f"{data.get('agent_name', '?')} / {data.get('model') or 'default'}"
        trace = result_file.parent / "trace.jsonl"
        if trace.exists():
            grouped[key].append(trace)
    return {key: analyze_traces(paths) for key, paths in grouped.items()}
