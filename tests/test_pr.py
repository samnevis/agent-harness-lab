from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ahl import github
from ahl.ci import evaluate_gate
from ahl.models import Check, FileRules
from ahl.pr import grade_pr
from ahl.sandbox import diff_numstat_between, merge_base, resolve_sha


def _git(args, cwd):
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
    )
    return subprocess.run(["git", *args], cwd=str(cwd), env=env, capture_output=True, text=True)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], repo)
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / ".env").write_text("SECRET=keep\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "base"], repo)
    return repo


def _add_pr_branch(repo: Path, touch_secret: bool = False):
    _git(["checkout", "-q", "-b", "pr"], repo)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "test_calc.py").write_text("from calc import add\nassert add(1, 2) == 3\n")
    if touch_secret:
        (repo / ".env").write_text("SECRET=leaked\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "fix"], repo)


# ---- git plumbing --------------------------------------------------------

def test_diff_between_refs(tmp_path):
    repo = _make_repo(tmp_path)
    _add_pr_branch(repo)
    base = merge_base(repo, "main", "pr")
    head = resolve_sha(repo, "pr")
    files, added, removed = diff_numstat_between(repo, base, head)
    assert "calc.py" in files
    assert "test_calc.py" in files
    assert added >= 2


# ---- grade_pr ------------------------------------------------------------

def test_grade_pr_passes_clean(tmp_path):
    repo = _make_repo(tmp_path)
    _add_pr_branch(repo)
    result = grade_pr(
        repo=repo,
        runs_dir=tmp_path / "runs",
        base="main",
        head="pr",
        checks=[Check("tests", "python3 test_calc.py")],
        file_rules=FileRules(allow=["calc.py", "test_calc.py"], deny=["*.env"]),
        author="cursor",
        model="claude",
    )
    assert result.grade.success is True
    assert "calc.py" in result.grade.files_changed
    assert result.grade.deny_violations == []


def test_grade_pr_fails_on_secret(tmp_path):
    repo = _make_repo(tmp_path)
    _add_pr_branch(repo, touch_secret=True)
    result = grade_pr(
        repo=repo,
        runs_dir=tmp_path / "runs",
        base="main",
        head="pr",
        checks=[Check("noop", "true")],
        file_rules=FileRules(deny=["*.env"]),
        author="cursor",
        model="claude",
    )
    assert result.grade.success is False
    assert ".env" in result.grade.deny_violations

    # And the CI gate must fail on it.
    gate = evaluate_gate(tmp_path / "runs")
    assert gate.ok is False
    assert any("security" in r for r in gate.reasons)


def test_grade_pr_persists_run_for_scoreboard(tmp_path):
    repo = _make_repo(tmp_path)
    _add_pr_branch(repo)
    grade_pr(
        repo=repo,
        runs_dir=tmp_path / "runs",
        base="main",
        head="pr",
        checks=[Check("tests", "python3 test_calc.py")],
        file_rules=FileRules(deny=["*.env"]),
        author="cursor",
        model="claude",
    )
    results = list((tmp_path / "runs").glob("*/*/*/result.json"))
    assert len(results) == 1


# ---- github commenter (no network) --------------------------------------

def test_upsert_comment_without_gh_returns_false(monkeypatch):
    monkeypatch.setattr(github, "gh_available", lambda: False)
    assert github.upsert_pr_comment("123", "hello") is False


def test_upsert_comment_invokes_gh(monkeypatch):
    monkeypatch.setattr(github, "gh_available", lambda: True)
    monkeypatch.setattr(github, "_find_sticky_comment_id", lambda pr, repo: None)
    calls = {}

    def fake_run(args, input_text=None):
        calls["args"] = args
        calls["body"] = input_text
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(github, "_run", fake_run)
    ok = github.upsert_pr_comment("123", "summary body", repo="o/r")
    assert ok is True
    assert "pr" in calls["args"] and "comment" in calls["args"]
    assert github.MARKER in calls["body"]
    assert "o/r" in calls["args"]
