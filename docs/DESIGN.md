# Agent Harness Lab — Design

## Goal

Make AI coding-agent behavior **visible and measurable**. Given a task and an
agent, produce a trustworthy answer to: *did it work, what did it cost, what did
it do, and why did it fail?* — and let you compare agents/models/prompts over time.

## Grading dimensions (why a run can fail)

A run passes only if **all** hold. Each dimension exists to catch a real failure
mode that a plain "run the tests" check would miss:

1. **Checks pass** — the task is actually solved (not just claimed).
2. **In scope** — `file_rules.allow` / `deny` keep the agent from editing things it
   was told not to (tests, CI config, lockfiles).
3. **No secret leak** (`secrets.py`) — the agent didn't hardcode a credential into
   a changed file or print one into its trace. This is checked *independently* of
   the deny list, because an agent can pass tests by pasting a literal key.
4. **Within budget** (`Budget`) — it didn't burn more than `max_cost_usd` /
   `max_steps`. Enforced in the runner where the agent's clean step/cost numbers
   are known, then folded back into the grade.

Keeping these as separate, composable signals (rather than one boolean) is what
lets the scoreboard, CI gate, and PR comment explain *why* something failed.

## Determinism & synthetic agents

The built-in agents (`mock`, `noop`, `reckless`, `flaky`, `looper`, `scripted`)
make the whole pipeline runnable and testable with no API keys or network. `flaky`
is seeded by `(model, seed, attempt)` so `--repeat N` produces a stable-but-mixed
record — which is what makes the **pass@k / flake-rate** numbers reproducible in
tests and CI. Real evaluation uses the `command` adapter; the synthetic agents are
for demonstrating and regression-testing the *harness itself*.

## Exports as a thin projection

`exports.py` (JUnit XML / JSON / SVG badge) and `report_html.py` are pure
functions of the persisted `RunRow`s — no recomputation, no second source of
truth. That's deliberate: the scoreboard, the PR comment, the HTML report, and the
JUnit file all derive from the same `result.json` files, so they can never
disagree about whether a run passed.

## The four parts (and where they live)

| Concept            | Module          | Responsibility |
|--------------------|-----------------|----------------|
| **Tasks**          | `tasks.py`, `tasks/` | Goal, setup, success checks, file rules. |
| **Isolated exec**  | `sandbox.py`    | Disposable, git-backed working dir per run. |
| **Tracing**        | `tracer.py`     | Record every command/tool call/cost/loop. |
| **Grading + board**| `grader.py`, `scoreboard.py` | Pass/fail + metrics, then compare. |

`runner.py` is the engine that composes them; `cli.py` is the user surface.

## Design decisions & rationale

### 1. Everything crosses boundaries as dataclasses (`models.py`)
Components never reach into each other's internals — they pass `TaskSpec`,
`AgentResult`, `GradeResult`, `RunResult`. This keeps the pieces swappable (you can
replace the grader or scoreboard without touching agents) and gives a stable
on-disk JSON format (`result.json`) that the scoreboard reads back later.

### 2. Sandbox = git, not just a temp dir
Each run copies the task's seed repo into a fresh directory and `git init`s it.
Git gives us **isolation** (a throwaway copy) *and* a **precise diff** for free —
we know exactly which files changed and by how many lines. This is what powers
file-rule enforcement and the "no files changed" failure detection.

- **Template mode** (`from_template`): for self-contained benchmark tasks.
- **Worktree mode** (`from_git_worktree`): point the harness at a *real* repo at a
  specific ref (this is the hook for Agent CI on a live PR).
- **Docker** is an intentional extension point, not a hard dependency: a
  `DockerSandbox` would implement the same `changed_files()` / `diff_stats()` /
  `cleanup()` surface. We default to git so the harness runs anywhere.

### 3. Tracing is honest about what it can and can't see
We can't perfectly intercept a black-box external agent. So tracing works at two
levels:
- **Always available:** every command routed through `Tracer.run_command` is timed
  and recorded; the git diff captures file changes; wall-clock and exit codes are
  captured.
- **Opt-in richer data:** an agent can append tool/MCP calls to `$AHL_TRACE` and
  write token/cost to `$AHL_USAGE`. If it doesn't, we degrade gracefully to
  command-level tracing instead of pretending we have data we don't.

A "step" is a command or tool call (not a note). Loop detection is a cheap
heuristic: the same command repeated back-to-back.

### 4. Grading is stricter than "the agent said done"
A run passes only if **all** hold:
1. every `check` command exits 0 (actually solved),
2. no changed file matches a `deny` glob (no secrets/forbidden files),
3. if `allow` is set, every change matches it (stayed in scope).

This is the whole point: it catches the agent that claims success but changed
nothing (`noop`), or solved the task by editing files it was told not to touch.
Failure `reasons` are recorded so the scoreboard can explain *why*.

### 5. Scoreboard separates per-run facts from rankings
`load_runs` reads raw `result.json` files into flat `RunRow`s; `leaderboard`
aggregates them (success rate, avg steps, cost-per-success). Ranking is success
rate first, then cheapest successful run. Output is both a rich console table and
portable Markdown so it can be dropped into a PR comment or README.

## Honest limitations (and how they'd be addressed)

- **Secret *access* vs *modification*:** we currently detect files an agent
  *modifies*. Detecting reads/exfiltration of secrets needs syscall-level tracing
  (e.g. `strace`/eBPF) or a network-egress-blocked Docker sandbox — a natural next
  layer on the `Sandbox` interface.
- **Token/cost for external agents** is only as accurate as what the agent
  reports via `$AHL_USAGE`. Provider-side usage APIs would tighten this.
- **Mock agent** exists to make the *harness itself* testable without keys; it is
  not a real agent. Real evaluation uses the `command` adapter.

## The products built on the engine (now implemented)

This repo is the **core engine** plus thin product layers on top, all sharing one
codepath so CI and local reproduction never diverge.

- **Suite** (`suite.py`) — run a matrix of (task × agent) and aggregate. The batch
  primitive both CI and regression checks build on.
- **Agent CI** (`ci.py`) — the harness as a reliability gate. Runs a suite,
  compares against a baseline, writes a PR-comment-ready Markdown summary + HTML
  artifact, and returns a non-zero exit code when: a previously-passing cell now
  fails (regression), any run touched a `deny` file (security), or pass rate drops
  below a threshold. Point `from_git_worktree` at a PR ref to run it on a real PR.
- **Regression tracking** (`regression.py`) — a *baseline* is a JSON snapshot of
  each cell's pass/fail/steps/cost, committed next to the code. `compare` surfaces
  regressions, fixes, and **cost regressions** (cost up >25% and >$0.01). This is
  the "saved runs you re-check later".
- **MCP Doctor** (`mcp_doctor.py`) — specializes the tracer's `tool_call`/`command`
  stream to grade *tool/MCP quality*: per-tool call count, error rate, redundant
  calls, result size, and a 0–100 health score. Answers "were the tools any good?"
  rather than "did the task pass?".
- **Reliability gate (B2B)** = Agent CI sold as a paid quality gate for teams whose
  agents open PRs.

### Two run modes: *execute an agent* vs *grade a PR*

There are two ways work enters the harness, and both produce the same
`RunResult`/`trace.jsonl`/`result.json` so everything downstream is identical:

1. **Execute an agent on a task** (`runner.run_task`): fresh sandbox seeded from
   the task, agent runs, diff is agent-vs-base. Used by `run`, `bench`, `ci --agents`.
2. **Grade an existing PR** (`pr.grade_pr`): no agent runs. We check out the PR head
   in a detached `git worktree`, diff `merge-base(base, head)..head`, run the repo's
   checks there, and apply file rules. Used by `grade-pr` and the GitHub Action.

Mode 2 is the real Agent CI story: by the time CI sees the PR, the agent already
did its work — the deliverable *is* the diff. We grade the diff against the same
standard (checks pass, no `deny` files, in scope) rather than trusting the PR
description. `require_changes` is relaxed in this mode because an empty PR is a
no-op, not a failure. The shared scoring core (`grader.grade_changes`) is used by
both modes so the pass/fail rules can never drift between them.

### Design notes for the product layers

- **One engine, many entry points.** `bench`, `ci`, the local `run`, and `grade-pr`
  all funnel into the same scoring core and persisted format, so a green CI gate is
  exactly reproducible locally.
- **Comment posting can't block the gate.** `github.upsert_pr_comment` returns
  False (never raises) if `gh` is missing or fails, and the Action records the run
  with `continue-on-error` — the gate's exit code, not comment plumbing, decides
  the check. The comment is *sticky* (one per PR, edited in place via a hidden
  marker) so pushes update rather than spam.
- **The gate is a pure function of persisted runs.** `evaluate_gate` reads
  `result.json`/`trace.jsonl` from disk, so you can run the suite on one machine
  and gate on another (or re-gate historical runs with stricter rules).
- **Security is graded from the diff, not trust.** The `reckless` demo agent solves
  the task yet writes to `.env`; the gate fails it despite passing tests — exactly
  the failure mode that "the agent said it was done" misses.

## Data flow

```
load_task ─► run_task ─┬─► Sandbox.from_template / from_git_worktree
                       ├─► (setup commands, traced)
                       ├─► Agent.run(task, workdir, tracer)
                       ├─► grade(task, sandbox, tracer)
                       └─► persist result.json + trace.jsonl
load_runs ─► leaderboard ─► to_markdown / console table
```
