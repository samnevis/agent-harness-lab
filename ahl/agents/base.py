"""The agent adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Optional

from ..models import AgentResult, TaskSpec
from ..tracer import Tracer


class Agent(ABC):
    #: human-readable adapter name, e.g. "mock" / "command"
    name: str = "agent"

    def __init__(self, model: str = "", config: Optional[Dict] = None) -> None:
        self.model = model
        self.config = config or {}

    @abstractmethod
    def run(self, task: TaskSpec, workdir: Path, tracer: Tracer) -> AgentResult:
        """Do the task inside ``workdir``, recording actions via ``tracer``."""
        raise NotImplementedError
