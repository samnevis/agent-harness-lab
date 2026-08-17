"""LangChain-backed adapter for external coding-agent commands.

This adapter keeps Harness Lab's sandbox, tracing, grading, and scoreboard as the
source of truth while letting users execute a command-agent path through a
LangChain Runnable. It is intentionally thin: LangChain handles the runner
boundary, and the existing CommandAgent preserves the battle-tested command,
usage, and trace ingestion behavior.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import AgentResult, TaskSpec
from ..tracer import Tracer
from .base import Agent
from .command import CommandAgent


def _runnable_lambda() -> Any:
    try:
        from langchain_core.runnables import RunnableLambda
    except ImportError as exc:
        raise ValueError(
            "LangChainAgent requires langchain-core. "
            'Install it with: pip install -e ".[langchain]"'
        ) from exc
    return RunnableLambda


class LangChainAgent(Agent):
    """Run an external coding-agent command through a LangChain Runnable."""

    name = "langchain"

    def run(self, task: TaskSpec, workdir: Path, tracer: Tracer) -> AgentResult:
        RunnableLambda = _runnable_lambda()
        delegate = CommandAgent(model=self.model, config=self.config)

        def invoke_command(inputs: dict) -> AgentResult:
            return delegate.run(
                inputs["task"],
                inputs["workdir"],
                inputs["tracer"],
            )

        tracer.note("LangChainAgent dispatching command via RunnableLambda", model=self.model)
        runner = RunnableLambda(invoke_command)
        return runner.invoke({"task": task, "workdir": workdir, "tracer": tracer})
