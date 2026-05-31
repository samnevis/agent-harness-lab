from __future__ import annotations

from pathlib import Path

from ahl.ci import evaluate_gate
from ahl.mcp_doctor import analyze_runs, analyze_trace
from ahl.regression import Cell, compare, load_baseline, save_baseline
from ahl.report_html import render_html
from ahl.scoreboard import load_runs
from ahl.suite import AgentSpec, resolve_tasks, run_suite
from ahl.tracer import Tracer

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = REPO_ROOT / "tasks"


def _run_basic_suite(runs_dir: Path, agents=None):
    tasks = resolve_tasks(TASKS_DIR, ["fix-add-bug"])
    agents = agents or [AgentSpec("mock", "claude"), AgentSpec("noop", "gpt")]
    return run_suite(tasks, agents, runs_dir)


# ---- suite ---------------------------------------------------------------

def test_suite_counts(tmp_path):
    report = _run_basic_suite(tmp_path / "runs")
    assert report.total == 2
    assert report.passed == 1
    assert report.failed == 1


# ---- regression ----------------------------------------------------------

def test_regression_detects_regression(tmp_path):
    runs = tmp_path / "runs"
    _run_basic_suite(runs, [AgentSpec("mock", "claude")])
    baseline_path = tmp_path / "baseline.json"
    save_baseline(runs, baseline_path)
    baseline = load_baseline(baseline_path)
    assert any(c.success for c in baseline.values())

    # Flip the baseline cell to "was passing"; now simulate it failing by
    # pointing compare at a fresh runs dir where the same cell fails.
    runs2 = tmp_path / "runs2"
    _run_basic_suite(runs2, [AgentSpec("noop", "claude")])  # noop with same model label
    # Build a baseline that says mock/claude... mismatch keys, so craft directly:
    crafted = {
        "fix-add-bug::noop/claude": Cell("fix-add-bug", "noop/claude", True, 1, 0.01),
    }
    diff = compare(crafted, runs2)
    assert "fix-add-bug::noop/claude" in diff.regressions
    assert diff.has_regressions


def test_regression_detects_fix(tmp_path):
    runs = tmp_path / "runs"
    _run_basic_suite(runs, [AgentSpec("mock", "claude")])
    crafted = {
        "fix-add-bug::mock/claude": Cell("fix-add-bug", "mock/claude", False, 1, 0.01),
    }
    diff = compare(crafted, runs)
    assert "fix-add-bug::mock/claude" in diff.fixes


def test_cost_regression(tmp_path):
    runs = tmp_path / "runs"
    _run_basic_suite(runs, [AgentSpec("mock", "claude")])
    rows = load_runs(runs)
    real_cost = next(r.cost_usd for r in rows if r.success)
    crafted = {
        "fix-add-bug::mock/claude": Cell("fix-add-bug", "mock/claude", True, 1, real_cost / 10),
    }
    diff = compare(crafted, runs)
    assert any("fix-add-bug::mock/claude" in c for c in diff.cost_regressions)


# ---- CI gate -------------------------------------------------------------

def test_ci_gate_fails_on_security(tmp_path):
    runs = tmp_path / "runs"
    tasks = resolve_tasks(TASKS_DIR, ["fix-add-bug"])
    run_suite(tasks, [AgentSpec("reckless", "rogue")], runs)
    gate = evaluate_gate(runs)
    assert gate.ok is False
    assert any("security" in r for r in gate.reasons)
    assert gate.exit_code == 1


def test_ci_gate_passes_clean(tmp_path):
    runs = tmp_path / "runs"
    tasks = resolve_tasks(TASKS_DIR, ["fix-add-bug"])
    run_suite(tasks, [AgentSpec("mock", "claude")], runs)
    gate = evaluate_gate(runs)
    assert gate.ok is True
    assert gate.exit_code == 0


def test_ci_gate_min_pass_rate(tmp_path):
    runs = tmp_path / "runs"
    _run_basic_suite(runs)  # 1 pass, 1 fail -> 50%
    gate = evaluate_gate(runs, min_pass_rate=0.9)
    assert gate.ok is False
    assert any("pass rate" in r for r in gate.reasons)


# ---- MCP Doctor ----------------------------------------------------------

def test_mcp_doctor_flags_errors_and_redundancy(tmp_path):
    tr = Tracer()
    tr.tool_call("search", {"q": "x"}, result="ok")
    tr.tool_call("search", {"q": "x"}, result="ok")  # redundant (same sig back to back)
    tr.tool_call("fetch", {"u": "y"}, result="Error: not found")  # error
    trace_path = tr.finalize(tmp_path / "trace.jsonl")

    rep = analyze_trace(trace_path)
    assert rep.tools["search"].redundant == 1
    assert rep.tools["fetch"].errors == 1
    assert rep.wasted_calls == 2
    assert rep.health_score() < 100


def test_mcp_doctor_over_runs(tmp_path):
    runs = tmp_path / "runs"
    _run_basic_suite(runs, [AgentSpec("mock", "claude")])
    reports = analyze_runs(runs, task_id="fix-add-bug")
    assert "mock / claude" in reports
    assert reports["mock / claude"].total_calls > 0


# ---- HTML ----------------------------------------------------------------

def test_html_renders(tmp_path):
    runs = tmp_path / "runs"
    _run_basic_suite(runs)
    html = render_html(load_runs(runs))
    assert "<html>" in html and "Leaderboard" in html
    assert "PASS" in html and "FAIL" in html
