from __future__ import annotations

from pathlib import Path

import pytest

from ahl.agents import build_agent
from ahl.grader import grade
from ahl.models import Check, FileRules, TaskSpec
from ahl.runner import run_task
from ahl.sandbox import make_temp_sandbox
from ahl.scoreboard import leaderboard, load_runs
from ahl.tasks import find_task
from ahl.tracer import Tracer

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = REPO_ROOT / "tasks"


# ---- sandbox -------------------------------------------------------------

def test_sandbox_tracks_changes(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "a.txt").write_text("hello\n")
    sb = make_temp_sandbox(seed)
    try:
        (Path(sb.workdir) / "a.txt").write_text("hello\nworld\n")
        (Path(sb.workdir) / "new.txt").write_text("new\n")
        files, added, removed = sb.diff_stats()
        assert "a.txt" in files
        assert "new.txt" in files
        assert added >= 2
    finally:
        sb.cleanup()


# ---- grader --------------------------------------------------------------

def _task_with(tmp_path, checks, rules):
    return TaskSpec(
        id="t", name="t", prompt="p", checks=checks, file_rules=rules, path=tmp_path
    )


def test_grader_deny_violation(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "code.py").write_text("x = 1\n")
    (seed / ".env").write_text("SECRET=1\n")
    sb = make_temp_sandbox(seed)
    try:
        # Agent touches the forbidden secret file.
        (Path(sb.workdir) / ".env").write_text("SECRET=2\n")
        task = _task_with(tmp_path, [], FileRules(allow=["code.py"], deny=["*.env", ".env"]))
        result = grade(task, sb, Tracer())
        assert ".env" in result.deny_violations
        assert result.success is False
    finally:
        sb.cleanup()


def test_grader_passes_clean_change(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "code.py").write_text("x = 1\n")
    sb = make_temp_sandbox(seed)
    try:
        (Path(sb.workdir) / "code.py").write_text("x = 2\n")
        task = _task_with(
            tmp_path,
            [Check(name="ok", cmd="true")],
            FileRules(allow=["code.py"], deny=["*.env"]),
        )
        result = grade(task, sb, Tracer())
        assert result.success is True
        assert result.deny_violations == []
    finally:
        sb.cleanup()


def test_grader_fails_when_check_fails(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "code.py").write_text("x = 1\n")
    sb = make_temp_sandbox(seed)
    try:
        (Path(sb.workdir) / "code.py").write_text("x = 2\n")
        task = _task_with(tmp_path, [Check(name="no", cmd="false")], FileRules())
        result = grade(task, sb, Tracer())
        assert result.success is False
        assert any("failing checks" in r for r in result.reasons)
    finally:
        sb.cleanup()


# ---- tracer --------------------------------------------------------------

def test_tracer_loop_detection(tmp_path):
    tr = Tracer()
    for _ in range(4):
        tr.run_command("true", cwd=tmp_path)
    assert tr.detect_loops() is True


def test_tracer_records_steps(tmp_path):
    tr = Tracer()
    tr.run_command("true", cwd=tmp_path)
    tr.tool_call("read_file", {"path": "x"}, "ok")
    tr.note("thinking")  # notes are not steps
    assert tr.steps == 2


# ---- end to end ----------------------------------------------------------

def test_mock_agent_solves_task(tmp_path):
    task = find_task(TASKS_DIR, "fix-add-bug")
    agent = build_agent("mock", model="test")
    result = run_task(task, agent, tmp_path / "runs")
    assert result.grade.success is True
    # list_dir + read_file tool calls + 1 solution command
    assert result.agent.steps == 3
    assert result.agent.usage.cost_usd > 0


def test_noop_agent_fails_task(tmp_path):
    task = find_task(TASKS_DIR, "fix-add-bug")
    agent = build_agent("noop", model="control")
    result = run_task(task, agent, tmp_path / "runs")
    assert result.grade.success is False
    assert "no files were changed" in "; ".join(result.grade.reasons)


def test_scoreboard_ranks_mock_above_noop(tmp_path):
    runs = tmp_path / "runs"
    run_task(find_task(TASKS_DIR, "fix-add-bug"), build_agent("mock", model="m"), runs)
    run_task(find_task(TASKS_DIR, "fix-add-bug"), build_agent("noop", model="n"), runs)
    rows = load_runs(runs)
    board = leaderboard(rows)
    assert board[0].key.startswith("mock")
    assert board[0].success_rate == 1.0
    assert board[-1].success_rate == 0.0
