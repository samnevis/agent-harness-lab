"""Command-line interface for Harness Lab.

    ahl tasks                          list available tasks
    ahl run  --task ID --agent NAME    run one task with one agent
    ahl report                         build the scoreboard from past runs
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.table import Table

from . import __version__
from .agents import build_agent
from .ci import evaluate_gate
from .config import load_config
from .exports import to_badge_svg, to_json, to_junit_xml
from .github import gh_available, upsert_pr_comment
from .inspect import find_run_dir, latest_run_dir, load_result, read_trace
from .mcp_doctor import analyze_runs
from .models import Check, FileRules, RunResult
from .pr import grade_pr
from .regression import save_baseline
from .report_html import write_html, write_report
from .runner import run_task
from .scoreboard import flakiness, leaderboard, load_runs, to_markdown
from .suite import AgentSpec, resolve_tasks, run_suite
from .tasks import discover_tasks, filter_tasks, find_task

console = Console()

DEFAULT_TASKS_DIR = Path("tasks")
DEFAULT_RUNS_DIR = Path("runs")


def _cmd_tasks(args: argparse.Namespace) -> int:
    tasks = discover_tasks(Path(args.tasks_dir))
    tasks = filter_tasks(tasks, tags=args.tag or None, difficulty=args.difficulty or None)
    if not tasks:
        console.print(f"[yellow]No tasks found under {args.tasks_dir}[/yellow]")
        return 0
    table = Table(title="Tasks", show_lines=False)
    table.add_column("id", style="cyan")
    table.add_column("name")
    table.add_column("diff")
    table.add_column("tags")
    table.add_column("budget")
    table.add_column("prompt")
    for t in tasks:
        budget = []
        if t.budget.max_steps is not None:
            budget.append(f"≤{t.budget.max_steps} steps")
        if t.budget.max_cost_usd is not None:
            budget.append(f"≤${t.budget.max_cost_usd}")
        table.add_row(
            t.id, t.name, t.difficulty, ", ".join(t.tags),
            ", ".join(budget) or "—", _short(t.prompt, 48),
        )
    console.print(table)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    task = find_task(Path(args.tasks_dir), args.task)
    config = {}
    if args.cmd:
        config["cmd"] = args.cmd
    agent = build_agent(args.agent, model=args.model or "", config=config)

    console.print(f"[bold]Running[/bold] task [cyan]{task.id}[/cyan] "
                  f"with agent [magenta]{agent.name}[/magenta]"
                  + (f" (model={agent.model})" if agent.model else ""))
    result = run_task(task, agent, Path(args.runs_dir), keep_sandbox=args.keep_sandbox)
    _print_run_result(result)
    return 0 if result.grade.success else 1


def _cmd_report(args: argparse.Namespace) -> int:
    rows = load_runs(Path(args.runs_dir))
    if not rows:
        console.print(f"[yellow]No runs found under {args.runs_dir}[/yellow]")
        return 0

    board = leaderboard(rows)
    lb = Table(title="Leaderboard")
    for col in ["Agent / Model", "Success", "Avg steps", "Cost/success", "Avg time (s)"]:
        lb.add_column(col)
    for e in board:
        cps = f"${e.cost_per_success}" if e.cost_per_success is not None else "—"
        lb.add_row(
            e.key,
            f"{e.successes}/{e.runs} ({e.success_rate*100:.0f}%)",
            str(e.avg_steps),
            cps,
            str(e.avg_duration),
        )
    console.print(lb)

    detail = Table(title="Runs")
    for col in ["Task", "Agent/Model", "Result", "Steps", "Cost", "Files", "Loop", "Notes"]:
        detail.add_column(col)
    for r in rows:
        result = "[green]PASS[/green]" if r.success else "[red]FAIL[/red]"
        detail.add_row(
            r.task_id,
            f"{r.agent}/{r.model}",
            result,
            str(r.steps),
            f"${r.cost_usd}",
            str(r.files_changed),
            "yes" if r.loop_detected else "no",
            _short("; ".join(r.reasons), 50),
        )
    console.print(detail)

    flakes = [f for f in flakiness(rows) if f.attempts > 1]
    if flakes:
        ft = Table(title="Flakiness (pass@k)")
        for col in ["Task", "Agent/Model", "Pass rate", "Flaky?"]:
            ft.add_column(col)
        for f in flakes:
            ft.add_row(
                f.task_id, f.agent_key,
                f"{f.passes}/{f.attempts} ({f.pass_rate*100:.0f}%)",
                "[red]FLAKY[/red]" if f.is_flaky else "stable",
            )
        console.print(ft)

    if args.out:
        Path(args.out).write_text(to_markdown(rows))
        console.print(f"[green]Wrote Markdown report to {args.out}[/green]")
    if args.html:
        write_html(rows, Path(args.html))
        console.print(f"[green]Wrote HTML report to {args.html}[/green]")
    if args.report_dir:
        index = write_report(rows, Path(args.report_dir))
        console.print(f"[green]Wrote multi-page HTML report to {index}[/green]")
    if args.json:
        Path(args.json).write_text(to_json(rows))
        console.print(f"[green]Wrote JSON report to {args.json}[/green]")
    if args.junit:
        Path(args.junit).write_text(to_junit_xml(rows))
        console.print(f"[green]Wrote JUnit XML to {args.junit}[/green]")
    if args.badge:
        Path(args.badge).write_text(to_badge_svg(rows))
        console.print(f"[green]Wrote SVG badge to {args.badge}[/green]")
    return 0


def _parse_agents(specs: List[str]) -> List[AgentSpec]:
    """Parse 'agent:model' tokens into AgentSpec list."""
    out: List[AgentSpec] = []
    for token in specs:
        token = token.strip()
        if not token:
            continue
        if ":" in token:
            agent, model = token.split(":", 1)
        else:
            agent, model = token, ""
        out.append(AgentSpec(agent=agent.strip(), model=model.strip()))
    return out


def _cmd_bench(args: argparse.Namespace) -> int:
    tasks_dir = Path(args.tasks_dir)
    runs_dir = Path(args.runs_dir)
    repeat = args.repeat
    task_ids = args.tasks or None
    tags = args.tag or None
    difficulty = args.difficulty or None

    # A config + suite name overrides the loose CLI agent flags.
    if args.config:
        cfg = load_config(Path(args.config))
        suite = cfg.get_suite(args.suite)
        agents = suite.agents
        repeat = args.repeat if args.repeat != 1 else suite.repeat
        tasks_dir = Path(args.tasks_dir) if args.tasks_dir != str(DEFAULT_TASKS_DIR) else Path(cfg.tasks_dir)
        runs_dir = Path(args.runs_dir) if args.runs_dir != str(DEFAULT_RUNS_DIR) else Path(cfg.runs_dir)
        task_ids = task_ids or (suite.tasks or None)
        tags = tags or (suite.tags or None)
        difficulty = difficulty or suite.difficulty
    else:
        agents = _parse_agents(args.agents.split(","))

    tasks = resolve_tasks(tasks_dir, task_ids)
    tasks = filter_tasks(tasks, tags=tags, difficulty=difficulty)
    if not tasks:
        console.print("[yellow]No tasks match the given filters.[/yellow]")
        return 0
    console.print(
        f"[bold]Benchmark:[/bold] {len(tasks)} task(s) x {len(agents)} agent(s)"
        + (f" x {repeat} attempts" if repeat > 1 else "")
    )

    def _on_result(r: RunResult):
        mark = "[green]PASS[/green]" if r.grade.success else "[red]FAIL[/red]"
        console.print(f"  {r.task_id} / {r.agent_name}/{r.model or 'default'}: {mark}")

    report = run_suite(tasks, agents, runs_dir, on_result=_on_result, repeat=repeat)
    console.print(f"\n[bold]{report.passed}/{report.total} passing[/bold]")
    return 0 if report.failed == 0 else 1


def _cmd_baseline(args: argparse.Namespace) -> int:
    path = save_baseline(Path(args.runs_dir), Path(args.out))
    console.print(f"[green]Saved baseline to {path}[/green]")
    return 0


def _cmd_grade_pr(args: argparse.Namespace) -> int:
    checks = [Check(name=f"check{i+1}", cmd=c) for i, c in enumerate(args.check or [])]
    rules = FileRules(allow=list(args.allow or []), deny=list(args.deny or []))
    console.print(
        f"[bold]Grading PR[/bold] head=[cyan]{args.head}[/cyan] base=[cyan]{args.base}[/cyan] "
        f"in {args.repo_path}"
    )
    result = grade_pr(
        repo=Path(args.repo_path),
        runs_dir=Path(args.runs_dir),
        base=args.base,
        head=args.head,
        checks=checks,
        file_rules=rules,
        task_id=args.task_id,
        author=args.author,
        model=args.model,
        timeout_sec=args.timeout,
        keep_worktree=args.keep_worktree,
    )
    _print_run_result(result)
    return 0 if result.grade.success else 1


def _cmd_ci(args: argparse.Namespace) -> int:
    # Optionally run the suite first; otherwise grade whatever is in runs-dir.
    if args.agents:
        tasks = resolve_tasks(Path(args.tasks_dir), args.tasks or None)
        agents = _parse_agents(args.agents.split(","))
        console.print(f"[bold]Agent CI:[/bold] running {len(tasks)} task(s) x {len(agents)} agent(s)")
        run_suite(tasks, agents, Path(args.runs_dir))

    baseline = Path(args.baseline) if args.baseline else None
    gate = evaluate_gate(
        Path(args.runs_dir),
        baseline_path=baseline,
        min_pass_rate=args.min_pass_rate,
        fail_on_security=not args.no_security_gate,
    )
    console.print(gate.markdown)
    if args.comment:
        Path(args.comment).write_text(gate.markdown)
        console.print(f"\n[green]Wrote PR comment to {args.comment}[/green]")
    if args.html:
        write_html(load_runs(Path(args.runs_dir)), Path(args.html), title="Agent CI", diff=gate.diff)
        console.print(f"[green]Wrote HTML report to {args.html}[/green]")
    if args.pr:
        if not gh_available():
            console.print("[yellow]gh CLI not found; skipping PR comment.[/yellow]")
        elif upsert_pr_comment(args.pr, gate.markdown, repo=args.gh_repo or None):
            console.print(f"[green]Posted Agent CI comment to PR {args.pr}[/green]")
        else:
            console.print(f"[red]Failed to post comment to PR {args.pr}[/red]")
    status = "[green]GATE PASSED[/green]" if gate.ok else "[red]GATE FAILED[/red]"
    console.print(f"\n{status}")
    return gate.exit_code


def _cmd_mcp(args: argparse.Namespace) -> int:
    reports = analyze_runs(Path(args.runs_dir), task_id=args.task or None)
    if not reports:
        console.print("[yellow]No traces found.[/yellow]")
        return 0
    for key, rep in reports.items():
        table = Table(title=f"MCP Doctor — {key}  (health {rep.health_score()}/100)")
        for col in ["Tool", "Calls", "Errors", "Err %", "Redundant", "Avg result"]:
            table.add_column(col)
        for name in sorted(rep.tools):
            t = rep.tools[name]
            table.add_row(
                name, str(t.calls), str(t.errors), f"{t.error_rate*100:.0f}%",
                str(t.redundant), str(t.avg_result_chars),
            )
        console.print(table)
        console.print(
            f"  total calls={rep.total_calls}  wasted={rep.wasted_calls} "
            f"(errors={rep.total_errors}, redundant={rep.total_redundant})\n"
        )
    return 0


def _resolve_run_dir(args: argparse.Namespace) -> Path:
    if args.run:
        return find_run_dir(Path(args.runs_dir), args.run)
    return latest_run_dir(Path(args.runs_dir), task_id=args.task or None)


def _cmd_trace(args: argparse.Namespace) -> int:
    run_dir = _resolve_run_dir(args)
    result = load_result(run_dir)
    meta, events = read_trace(run_dir / "trace.jsonl")
    console.print(
        f"[bold]Trace[/bold] {result.get('task_id')} · "
        f"{result.get('agent_name')}/{result.get('model') or 'default'} "
        f"([cyan]{result.get('run_id')}[/cyan])"
    )
    console.print(f"steps={meta.get('steps')}  loop_detected={meta.get('loop_detected')}\n")
    start = meta.get("started_at") or (events[0]["ts"] if events else 0)
    for ev in events:
        t = ev.get("ts", 0) - start
        etype = ev.get("type")
        data = ev.get("data", {})
        prefix = f"[dim]+{t:6.2f}s[/dim] "
        if etype == "command":
            ok = "[green]✓[/green]" if data.get("exit_code") == 0 else "[red]✗[/red]"
            console.print(f"{prefix}{ok} [blue]$[/blue] {data.get('cmd')}")
        elif etype == "tool_call":
            console.print(f"{prefix}[magenta]⚙[/magenta] {data.get('name')} {data.get('args')}")
        elif etype == "usage":
            console.print(f"{prefix}[green]$[/green] +{data.get('input_tokens',0)}in/"
                          f"{data.get('output_tokens',0)}out  ${data.get('cost_usd',0)}")
        elif etype == "note":
            console.print(f"{prefix}[yellow]•[/yellow] {_short(data.get('message',''), 100)}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    run_dir = _resolve_run_dir(args)
    result = load_result(run_dir)
    g = result.get("grade", {})
    agent = result.get("agent", {})
    status = "[green]PASS[/green]" if g.get("success") else "[red]FAIL[/red]"
    console.print(f"[bold]{result.get('task_id')}[/bold] · "
                  f"{result.get('agent_name')}/{result.get('model') or 'default'}  {status}")
    console.print(f"  run_id   : {result.get('run_id')}")
    console.print(f"  steps    : {agent.get('steps')}")
    console.print(f"  cost     : ${agent.get('usage', {}).get('cost_usd')}")
    console.print(f"  duration : {result.get('duration_sec')}s")
    console.print(f"  files    : {', '.join(g.get('files_changed', [])) or '—'}")
    for c in g.get("checks", []):
        mark = "[green]ok[/green]" if c.get("passed") else "[red]fail[/red]"
        console.print(f"    check {c.get('name')}: {mark}")
    if g.get("secret_leaks"):
        for s in g["secret_leaks"]:
            console.print(f"  [red]secret leak[/red]: {s.get('kind')} in {s.get('where')} ({s.get('preview')})")
    if g.get("reasons"):
        console.print(f"  reasons  : {'; '.join(g['reasons'])}")
    console.print(f"  run dir  : {run_dir}")
    return 0


def _cmd_badge(args: argparse.Namespace) -> int:
    rows = load_runs(Path(args.runs_dir))
    Path(args.out).write_text(to_badge_svg(rows, label=args.label))
    passed = sum(1 for r in rows if r.success)
    console.print(f"[green]Wrote badge ({passed}/{len(rows)} passing) to {args.out}[/green]")
    return 0


def _print_run_result(result: RunResult) -> None:
    g = result.grade
    status = "[green]PASS[/green]" if g.success else "[red]FAIL[/red]"
    console.print(f"\nResult: {status}")
    console.print(f"  steps      : {result.agent.steps}")
    console.print(f"  cost       : ${result.agent.usage.cost_usd} "
                  f"({result.agent.usage.total_tokens} tokens)")
    console.print(f"  duration   : {result.duration_sec}s")
    console.print(f"  files      : {len(g.files_changed)} changed "
                  f"(+{g.lines_added}/-{g.lines_removed})")
    for c in g.checks:
        mark = "[green]ok[/green]" if c.passed else "[red]fail[/red]"
        console.print(f"    check {c.name}: {mark}")
    if g.deny_violations:
        console.print(f"  [red]forbidden files touched:[/red] {', '.join(g.deny_violations)}")
    if g.allow_violations:
        console.print(f"  [yellow]out-of-scope files:[/yellow] {', '.join(g.allow_violations)}")
    for s in g.secret_leaks:
        console.print(f"  [red]secret leak:[/red] {s.kind} in {s.where} ({s.preview})")
    if g.budget_violations:
        console.print(f"  [red]budget:[/red] {'; '.join(g.budget_violations)}")
    if g.reasons:
        console.print(f"  reasons    : {'; '.join(g.reasons)}")
    console.print(f"  trace      : {result.trace_path}")


def _short(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ahl", description="Harness Lab")
    p.add_argument("--version", action="version", version=f"ahl {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    pt = sub.add_parser("tasks", help="list available tasks")
    pt.add_argument("--tasks-dir", default=str(DEFAULT_TASKS_DIR))
    pt.add_argument("--tag", action="append", help="filter by tag (repeatable, AND)")
    pt.add_argument("--difficulty", default="", help="filter by difficulty")
    pt.set_defaults(func=_cmd_tasks)

    pr = sub.add_parser("run", help="run a task with an agent")
    pr.add_argument("--task", required=True)
    pr.add_argument("--agent", default="mock", help="mock | noop | command | langchain")
    pr.add_argument("--model", default="", help="model label for the scoreboard")
    pr.add_argument("--cmd", default="", help="command template for the 'command' agent")
    pr.add_argument("--tasks-dir", default=str(DEFAULT_TASKS_DIR))
    pr.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    pr.add_argument("--keep-sandbox", action="store_true", help="don't delete the sandbox")
    pr.set_defaults(func=_cmd_run)

    prep = sub.add_parser("report", help="build the scoreboard")
    prep.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    prep.add_argument("--out", default="", help="also write a Markdown report to this path")
    prep.add_argument("--html", default="", help="write a single-page HTML report")
    prep.add_argument("--report-dir", default="", help="write a multi-page HTML report (per-run trace pages)")
    prep.add_argument("--json", default="", help="write a JSON report")
    prep.add_argument("--junit", default="", help="write a JUnit XML report")
    prep.add_argument("--badge", default="", help="write an SVG pass-rate badge")
    prep.set_defaults(func=_cmd_report)

    pb = sub.add_parser("bench", help="run a matrix of tasks x agents")
    pb.add_argument("--agents", default="mock:default",
                    help="comma-separated agent:model pairs, e.g. mock:claude,noop:gpt")
    pb.add_argument("--tasks", nargs="*", help="task ids (default: all)")
    pb.add_argument("--tasks-dir", default=str(DEFAULT_TASKS_DIR))
    pb.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    pb.add_argument("--repeat", type=int, default=1, help="attempts per cell (pass@k)")
    pb.add_argument("--config", default="", help="ahl.yaml config file defining suites")
    pb.add_argument("--suite", default="smoke", help="suite name within the config")
    pb.add_argument("--tag", action="append", help="filter tasks by tag (repeatable, AND)")
    pb.add_argument("--difficulty", default="", help="filter tasks by difficulty")
    pb.set_defaults(func=_cmd_bench)

    pbl = sub.add_parser("baseline", help="snapshot current runs as a regression baseline")
    pbl.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    pbl.add_argument("--out", default="baseline.json")
    pbl.set_defaults(func=_cmd_baseline)

    pc = sub.add_parser("ci", help="run the suite as a CI gate (regressions + security)")
    pc.add_argument("--agents", default="",
                    help="agent:model pairs to run first; omit to grade existing runs")
    pc.add_argument("--tasks", nargs="*")
    pc.add_argument("--tasks-dir", default=str(DEFAULT_TASKS_DIR))
    pc.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    pc.add_argument("--baseline", default="", help="baseline.json to compare against")
    pc.add_argument("--min-pass-rate", type=float, default=0.0,
                    help="fail the gate below this pass rate (0-1)")
    pc.add_argument("--no-security-gate", action="store_true",
                    help="do not fail on forbidden-file access")
    pc.add_argument("--comment", default="", help="write the PR-comment Markdown here")
    pc.add_argument("--html", default="", help="write an HTML artifact here")
    pc.add_argument("--pr", default="", help="PR number/URL to post the summary to (via gh)")
    pc.add_argument("--gh-repo", default="", help="OWNER/REPO for gh (default: inferred)")
    pc.set_defaults(func=_cmd_ci)

    pp = sub.add_parser("grade-pr", help="grade an existing PR diff (Agent CI core)")
    pp.add_argument("--repo-path", default=".", help="path to the git repo to grade")
    pp.add_argument("--base", default="origin/main", help="target branch ref")
    pp.add_argument("--head", default="HEAD", help="PR head ref")
    pp.add_argument("--check", action="append", help="check command (repeatable)")
    pp.add_argument("--allow", action="append", help="allowed-path glob (repeatable)")
    pp.add_argument("--deny", action="append", help="forbidden-path glob (repeatable)")
    pp.add_argument("--task-id", default="pr", help="logical id for the scoreboard")
    pp.add_argument("--author", default="pr", help="agent/author label for the scoreboard")
    pp.add_argument("--model", default="", help="model label for the scoreboard")
    pp.add_argument("--timeout", type=int, default=1800)
    pp.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    pp.add_argument("--keep-worktree", action="store_true")
    pp.set_defaults(func=_cmd_grade_pr)

    pm = sub.add_parser("mcp", help="grade tool/MCP call quality from traces")
    pm.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    pm.add_argument("--task", default="", help="restrict to one task id")
    pm.set_defaults(func=_cmd_mcp)

    ptr = sub.add_parser("trace", help="print a run's recorded trace as a timeline")
    ptr.add_argument("--run", default="", help="run id or run directory (default: latest)")
    ptr.add_argument("--task", default="", help="latest run for this task")
    ptr.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    ptr.set_defaults(func=_cmd_trace)

    psh = sub.add_parser("show", help="show a run's result detail")
    psh.add_argument("--run", default="", help="run id or run directory (default: latest)")
    psh.add_argument("--task", default="", help="latest run for this task")
    psh.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    psh.set_defaults(func=_cmd_show)

    pbd = sub.add_parser("badge", help="write an SVG pass-rate badge")
    pbd.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    pbd.add_argument("--out", default="badge.svg")
    pbd.add_argument("--label", default="agent pass")
    pbd.set_defaults(func=_cmd_badge)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (KeyError, FileNotFoundError, ValueError) as e:
        console.print(f"[red]Error:[/red] {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
