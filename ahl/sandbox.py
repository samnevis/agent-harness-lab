"""Isolated, throwaway execution environments.

The default sandbox copies a task's seed repo into a fresh directory and turns it
into a git repo. That gives us two things for free:

1. Isolation -- the agent works on a disposable copy, never the real source.
2. A precise record of what changed -- we diff the working tree against the
   initial commit to know exactly which files the agent touched.

``GitWorktreeSandbox`` is the variant for pointing the harness at an *existing*
real repository (e.g. Agent CI on a PR): it uses ``git worktree`` so multiple runs
can share one clone without stepping on each other.

A Docker-backed sandbox would implement the same ``Sandbox`` interface; it is left
as a documented extension point since Docker is not available in every environment.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


def _git(args: List[str], cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # Deterministic, hermetic git identity so commits work without user config.
    env.setdefault("GIT_AUTHOR_NAME", "ahl")
    env.setdefault("GIT_AUTHOR_EMAIL", "ahl@harness.local")
    env.setdefault("GIT_COMMITTER_NAME", "ahl")
    env.setdefault("GIT_COMMITTER_EMAIL", "ahl@harness.local")
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )


@dataclass
class Sandbox:
    """A disposable working directory backed by git for diffing."""

    workdir: Path
    base_commit: str
    _owns_dir: bool = True

    @classmethod
    def from_template(cls, seed_dir: Optional[Path], dest: Path) -> "Sandbox":
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        if seed_dir is not None:
            seed_dir = Path(seed_dir)
            if not seed_dir.exists():
                raise FileNotFoundError(f"Seed repo not found: {seed_dir}")
            for item in seed_dir.iterdir():
                target = dest / item.name
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, target)

        _git(["init", "-q"], dest)
        _git(["add", "-A"], dest)
        _git(["commit", "-q", "-m", "ahl: base", "--allow-empty"], dest)
        rev = _git(["rev-parse", "HEAD"], dest).stdout.strip()
        return cls(workdir=dest, base_commit=rev)

    @classmethod
    def from_git_worktree(cls, repo: Path, base_ref: str, dest: Path) -> "Sandbox":
        repo = Path(repo).resolve()
        dest = Path(dest)
        rev = _git(["rev-parse", base_ref], repo).stdout.strip()
        if not rev:
            raise ValueError(f"Cannot resolve ref '{base_ref}' in {repo}")
        res = _git(["worktree", "add", "--detach", str(dest), rev], repo)
        if res.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {res.stderr}")
        sb = cls(workdir=dest, base_commit=rev, _owns_dir=False)
        sb._origin_repo = repo  # type: ignore[attr-defined]
        return sb

    def _numstat(self) -> List[Tuple[int, int, str]]:
        # Stage everything so new/untracked files show up in the diff too.
        _git(["add", "-A"], self.workdir)
        res = _git(["diff", "--numstat", "--cached", self.base_commit], self.workdir)
        rows: List[Tuple[int, int, str]] = []
        for line in res.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            added, removed, path = parts
            a = int(added) if added.isdigit() else 0
            r = int(removed) if removed.isdigit() else 0
            rows.append((a, r, path))
        return rows

    def changed_files(self) -> List[str]:
        return [path for _, _, path in self._numstat()]

    def diff_stats(self) -> Tuple[List[str], int, int]:
        rows = self._numstat()
        files = [p for _, _, p in rows]
        added = sum(a for a, _, _ in rows)
        removed = sum(r for _, r, _ in rows)
        return files, added, removed

    def cleanup(self) -> None:
        origin = getattr(self, "_origin_repo", None)
        if origin is not None:
            _git(["worktree", "remove", "--force", str(self.workdir)], Path(origin))
            return
        if self._owns_dir and self.workdir.exists():
            shutil.rmtree(self.workdir, ignore_errors=True)


def make_temp_sandbox(seed_dir: Optional[Path]) -> Sandbox:
    """Convenience for tests: a template sandbox in a temp dir."""
    dest = Path(tempfile.mkdtemp(prefix="ahl-sandbox-"))
    return Sandbox.from_template(seed_dir, dest)


# -- PR-grading git plumbing ---------------------------------------------------
# These operate on an *existing* repo to compare two refs (a PR head vs its base),
# which is the Agent CI use case rather than running an agent in a fresh sandbox.


def resolve_sha(repo: Path, ref: str) -> str:
    sha = _git(["rev-parse", ref], Path(repo)).stdout.strip()
    if not sha:
        raise ValueError(f"Cannot resolve ref '{ref}' in {repo}")
    return sha


def merge_base(repo: Path, base: str, head: str) -> str:
    """The common ancestor, so we diff only what the PR actually introduced."""
    res = _git(["merge-base", base, head], Path(repo))
    mb = res.stdout.strip()
    return mb or resolve_sha(repo, base)


def diff_numstat_between(repo: Path, base: str, head: str) -> Tuple[List[str], int, int]:
    repo = Path(repo)
    res = _git(["diff", "--numstat", f"{base}..{head}"], repo)
    files: List[str] = []
    added = removed = 0
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        a, r, path = parts
        added += int(a) if a.isdigit() else 0
        removed += int(r) if r.isdigit() else 0
        files.append(path)
    return files, added, removed


def add_worktree_at(repo: Path, sha: str, dest: Path) -> Path:
    res = _git(["worktree", "add", "--detach", str(dest), sha], Path(repo))
    if res.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {res.stderr}")
    return Path(dest)


def remove_worktree(repo: Path, dest: Path) -> None:
    _git(["worktree", "remove", "--force", str(dest)], Path(repo))
