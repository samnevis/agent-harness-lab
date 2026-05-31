"""Post the Agent CI summary to a GitHub PR via the ``gh`` CLI.

We keep a single "sticky" comment per PR (identified by a hidden marker) and edit
it in place on each run, instead of spamming a new comment every push.

Requires the ``gh`` CLI to be installed and authenticated (it is, on GitHub
Actions runners, via ``GH_TOKEN``). All functions degrade gracefully: if ``gh`` is
missing or the call fails, they return False rather than raising, so CI gating is
never blocked by comment posting.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import List, Optional

MARKER = "<!-- ahl-agent-ci -->"


def gh_available() -> bool:
    return shutil.which("gh") is not None


def _run(args: List[str], input_text: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args],
        input=input_text,
        capture_output=True,
        text=True,
    )


def _find_sticky_comment_id(pr: str, repo: Optional[str]) -> Optional[str]:
    args = ["pr", "view", pr, "--json", "comments"]
    if repo:
        args += ["--repo", repo]
    res = _run(args)
    if res.returncode != 0:
        return None
    try:
        comments = json.loads(res.stdout).get("comments", [])
    except json.JSONDecodeError:
        return None
    for c in reversed(comments):
        if MARKER in (c.get("body") or ""):
            # gh exposes a numeric id or a url; prefer url for `gh api` editing.
            return c.get("url") or str(c.get("id", "")) or None
    return None


def upsert_pr_comment(
    pr: str,
    body: str,
    repo: Optional[str] = None,
) -> bool:
    """Create or edit the sticky Agent CI comment on ``pr``.

    ``pr`` may be a PR number or URL. Returns True on success.
    """
    if not gh_available():
        return False
    full_body = f"{MARKER}\n{body}"

    existing = _find_sticky_comment_id(pr, repo)
    if existing:
        # Edit in place. `gh pr comment --edit-last` is the simplest portable path
        # when the last bot comment is ours; fall back to creating a new one.
        args = ["pr", "comment", pr, "--edit-last", "--body-file", "-"]
        if repo:
            args += ["--repo", repo]
        res = _run(args, input_text=full_body)
        if res.returncode == 0:
            return True

    args = ["pr", "comment", pr, "--body-file", "-"]
    if repo:
        args += ["--repo", repo]
    res = _run(args, input_text=full_body)
    return res.returncode == 0
