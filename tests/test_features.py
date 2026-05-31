from __future__ import annotations

from pathlib import Path

from ahl.agents import build_agent
from ahl.config import load_config
from ahl.exports import to_badge_svg, to_json, to_junit_xml
from ahl.grader import enforce_budget
from ahl.inspect import find_run_dir, latest_run_dir, read_trace
from ahl.models import Budget, GradeResult
from ahl.runner import run_task
from ahl.scoreboard import flakiness, load_runs
from ahl.secrets import scan_files, scan_text
from ahl.suite import AgentSpec, resolve_tasks, run_suite
from ahl.tasks import filter_tasks, find_task

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = REPO_ROOT / "tasks"


# ---- secret scanning -----------------------------------------------------

def test_scan_detects_known_secret_shapes():
    leaks = scan_text("token = sk-abcdefghijklmnopqrstuvwxyz1234", where="x.py")
    assert any(l.kind == "openai_key" for l in leaks)
    assert all("***" in l.preview for l in leaks)


def test_scan_detects_aws_and_private_key():
    text = "AKIAIOSFODNN7EXAMPLE\n-----BEGIN RSA PRIVATE KEY-----"
    kinds = {l.kind for l in scan_text(text, where="x")}
    assert "aws_access_key" in kinds
    assert "private_key" in kinds


def test_scan_files_reads_changed_files(tmp_path):
    (tmp_path / "a.py").write_text('API_KEY = "ghp_' + "a" * 36 + '"\n')
    leaks = scan_files(tmp_path, ["a.py"])
    assert any(l.kind == "github_token" for l in leaks)


def test_clean_text_has_no_leaks():
    assert scan_text("def add(a, b):\n    return a + b\n", where="x") == []


# ---- secret trap task end to end -----------------------------------------

def test_secret_trap_caught_when_hardcoded(tmp_path):
    task = find_task(TASKS_DIR, "secret-trap")
    agent = build_agent("scripted", model="evil", config={
        "files": {"client.py": 'def get_api_key():\n    return "sk-' + "a" * 28 + '"\n'}
    })
    result = run_task(task, agent, tmp_path / "runs")
    assert result.grade.success is False
    assert result.grade.secret_leaks
    assert any("leaked secrets" in r for r in result.grade.reasons)


def test_secret_trap_passes_when_using_env(tmp_path):
    task = find_task(TASKS_DIR, "secret-trap")
    result = run_task(task, build_agent("mock", model="good"), tmp_path / "runs")
    assert result.grade.success is True
    assert result.grade.secret_leaks == []


# ---- budget --------------------------------------------------------------

def test_enforce_budget_fails_on_overspend():
    g = GradeResult(success=True)
    enforce_budget(g, Budget(max_cost_usd=0.01, max_steps=2), cost_usd=0.05, steps=7)
    assert g.success is False
    assert len(g.budget_violations) == 2


def test_looper_busts_budget(tmp_path):
    task = find_task(TASKS_DIR, "loop-bait")
    result = run_task(task, build_agent("looper", model="thrasher"), tmp_path / "runs")
    assert result.grade.success is False
    assert result.grade.budget_violations


def test_efficient_agent_within_budget(tmp_path):
    task = find_task(TASKS_DIR, "loop-bait")
    result = run_task(task, build_agent("mock", model="fast"), tmp_path / "runs")
    assert result.grade.success is True
    assert result.grade.budget_violations == []


# ---- flakiness / pass@k --------------------------------------------------

def test_flaky_agent_pass_at_k(tmp_path):
    tasks = resolve_tasks(TASKS_DIR, ["fix-add-bug"])
    agents = [AgentSpec("flaky", "claude", {"success_ratio": 0.5, "seed": "t"})]
    run_suite(tasks, agents, tmp_path / "runs", repeat=10)
    rows = load_runs(tmp_path / "runs")
    assert len(rows) == 10
    stats = flakiness(rows)
    assert stats[0].attempts == 10
    # Mixed pass/fail expected for a 0.5 ratio across 10 attempts.
    assert 0 < stats[0].passes < 10


# ---- task filtering ------------------------------------------------------

def test_filter_tasks_by_tag():
    from ahl.tasks import discover_tasks
    tasks = discover_tasks(TASKS_DIR)
    sec = filter_tasks(tasks, tags=["security"])
    assert [t.id for t in sec] == ["secret-trap"]


def test_filter_tasks_by_difficulty():
    from ahl.tasks import discover_tasks
    tasks = discover_tasks(TASKS_DIR)
    easy = filter_tasks(tasks, difficulty="easy")
    assert {t.id for t in easy} == {"fix-add-bug", "fix-fizzbuzz"}


# ---- config --------------------------------------------------------------

def test_load_config_suites():
    cfg = load_config(REPO_ROOT / "ahl.yaml")
    assert "smoke" in cfg.suites
    assert "robustness" in cfg.suites
    smoke = cfg.get_suite("smoke")
    assert any(a.agent == "reckless" for a in smoke.agents)
    assert cfg.get_suite("robustness").repeat == 10


# ---- exports -------------------------------------------------------------

def _rows(tmp_path):
    tasks = resolve_tasks(TASKS_DIR, ["fix-add-bug"])
    run_suite(tasks, [AgentSpec("mock", "claude"), AgentSpec("noop", "gpt")], tmp_path / "runs")
    return load_runs(tmp_path / "runs")


def test_junit_export(tmp_path):
    xml = to_junit_xml(_rows(tmp_path))
    assert "<testsuites" in xml and "<testcase" in xml
    assert "<failure" in xml  # the noop run failed


def test_json_export(tmp_path):
    import json
    data = json.loads(to_json(_rows(tmp_path)))
    assert "leaderboard" in data and "runs" in data
    assert len(data["runs"]) == 2


def test_badge_export(tmp_path):
    svg = to_badge_svg(_rows(tmp_path))
    assert svg.startswith("<svg")
    assert "agent pass" in svg


# ---- inspect -------------------------------------------------------------

def test_inspect_find_and_read_trace(tmp_path):
    tasks = resolve_tasks(TASKS_DIR, ["fix-add-bug"])
    rep = run_suite(tasks, [AgentSpec("mock", "claude")], tmp_path / "runs")
    run_id = rep.results[0].run_id
    run_dir = find_run_dir(tmp_path / "runs", run_id)
    assert (run_dir / "result.json").exists()
    meta, events = read_trace(run_dir / "trace.jsonl")
    assert meta.get("type") == "meta"
    assert any(e["type"] == "command" for e in events)
    # latest_run_dir should resolve to something valid too.
    assert (latest_run_dir(tmp_path / "runs") / "result.json").exists()
