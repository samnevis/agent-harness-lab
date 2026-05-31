"""Agent adapters.

An adapter knows how to take a task + a sandbox working directory and make an
agent do work in it, routing all actions through the tracer.
"""

from __future__ import annotations

from typing import Dict, Optional

from .base import Agent
from .command import CommandAgent
from .mock import MockAgent, NoopAgent, RecklessAgent
from .synthetic import FlakyAgent, LooperAgent, ScriptedAgent

_BUILTINS = {
    "mock": MockAgent,
    "noop": NoopAgent,
    "reckless": RecklessAgent,
    "flaky": FlakyAgent,
    "looper": LooperAgent,
    "scripted": ScriptedAgent,
    "command": CommandAgent,
}


def build_agent(name: str, model: str = "", config: Optional[Dict] = None) -> Agent:
    config = config or {}
    if name not in _BUILTINS:
        raise KeyError(f"Unknown agent '{name}'. Available: {', '.join(_BUILTINS)}")
    return _BUILTINS[name](model=model, config=config)


__all__ = [
    "Agent",
    "MockAgent",
    "NoopAgent",
    "RecklessAgent",
    "FlakyAgent",
    "LooperAgent",
    "ScriptedAgent",
    "CommandAgent",
    "build_agent",
]
