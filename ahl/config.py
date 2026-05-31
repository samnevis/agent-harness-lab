"""Optional ``ahl.yaml`` config so you don't hand-type long CLI flags.

Define named suites of agents once and run them by name::

    tasks_dir: tasks
    runs_dir: runs
    suites:
      smoke:
        agents:
          - { agent: mock, model: claude }
          - { agent: noop, model: gpt-baseline }
        repeat: 1
      robustness:
        agents:
          - { agent: flaky, model: claude, config: { success_ratio: 0.6, seed: x } }
        repeat: 10
        tags: [bugfix]

Then: ``ahl bench --config ahl.yaml --suite robustness``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .suite import AgentSpec


@dataclass
class SuiteConfig:
    agents: List[AgentSpec] = field(default_factory=list)
    repeat: int = 1
    tags: List[str] = field(default_factory=list)
    difficulty: Optional[str] = None
    tasks: List[str] = field(default_factory=list)


@dataclass
class Config:
    tasks_dir: str = "tasks"
    runs_dir: str = "runs"
    suites: Dict[str, SuiteConfig] = field(default_factory=dict)

    def get_suite(self, name: str) -> SuiteConfig:
        if name not in self.suites:
            raise KeyError(
                f"Suite '{name}' not in config. Available: {', '.join(self.suites) or '(none)'}"
            )
        return self.suites[name]


def _parse_agents(raw_agents: List[dict]) -> List[AgentSpec]:
    specs: List[AgentSpec] = []
    for a in raw_agents:
        specs.append(
            AgentSpec(
                agent=a["agent"],
                model=a.get("model", ""),
                config=dict(a.get("config", {})),
            )
        )
    return specs


def load_config(path: Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    raw = yaml.safe_load(path.read_text()) or {}

    suites: Dict[str, SuiteConfig] = {}
    for name, s in (raw.get("suites", {}) or {}).items():
        s = s or {}
        suites[name] = SuiteConfig(
            agents=_parse_agents(s.get("agents", []) or []),
            repeat=int(s.get("repeat", 1)),
            tags=list(s.get("tags", []) or []),
            difficulty=s.get("difficulty"),
            tasks=list(s.get("tasks", []) or []),
        )

    return Config(
        tasks_dir=raw.get("tasks_dir", "tasks"),
        runs_dir=raw.get("runs_dir", "runs"),
        suites=suites,
    )
