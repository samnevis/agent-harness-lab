# Contributing

Thanks for taking a look! This project is intentionally small, dependency-light,
and runnable with no API keys (thanks to the built-in synthetic agents), so it's
easy to hack on.

## Dev setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

## Adding a task

A task is a directory under `tasks/<id>/` with a `task.yaml` and an optional
`repo/` seed template. See `tasks/fix-add-bug/` for the simplest example and
`tasks/secret-trap/` for a security task. Keep tasks:

- **deterministically gradable** — a `check` command that exits 0 iff solved;
- **scoped** — use `file_rules.allow` / `deny` to define what's in bounds;
- **honest** — the `solution:` field is only for the built-in `mock` agent so the
  pipeline runs in CI without a real model.

## Adding an agent adapter

Subclass `ahl.agents.base.Agent` and implement `run(task, workdir, tracer)`.
Route shell work through `tracer.run_command(...)` and tool/MCP calls through
`tracer.tool_call(...)` so they show up in the trace and metrics. Register it in
`ahl/agents/__init__.py`.

## Conventions

- Python 3.9+, standard library + `PyYAML` + `rich` only (keep deps minimal).
- Add a test for new behavior in `tests/`.
- Run `pytest -q` before opening a PR. CI runs the suite on 3.9 / 3.11 / 3.12.

## Project layout

See the "Layout" section of the [README](README.md) and
[docs/DESIGN.md](docs/DESIGN.md) for the architecture and design rationale.
