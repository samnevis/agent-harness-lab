"""Self-contained HTML reports (no external assets).

``render_html`` builds the single-page scoreboard (used as a CI artifact / PR
attachment). ``write_report`` additionally emits a per-run detail page for every
run, with the recorded trace rendered as a timeline -- so you can click a run and
see exactly what the agent did, step by step.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Dict, List, Optional

from .inspect import read_trace
from .regression import Diff
from .scoreboard import RunRow, flakiness, leaderboard

_CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:2rem;color:#1b1f24;background:#fafbfc}
a{color:#0969da;text-decoration:none}a:hover{text-decoration:underline}
h1{margin-bottom:.2rem}h2{margin-top:2rem;border-bottom:1px solid #e1e4e8;padding-bottom:.3rem}
table{border-collapse:collapse;width:100%;margin:.5rem 0;background:#fff}
th,td{border:1px solid #e1e4e8;padding:.45rem .6rem;text-align:left;font-size:14px}
th{background:#f6f8fa}
.pass{color:#fff;background:#1a7f37;border-radius:4px;padding:1px 8px;font-size:12px;font-weight:600}
.fail{color:#fff;background:#cf222e;border-radius:4px;padding:1px 8px;font-size:12px;font-weight:600}
.muted{color:#656d76}.flag{color:#cf222e;font-weight:600}
.summary{display:flex;gap:1rem;margin:1rem 0;flex-wrap:wrap}
.card{background:#fff;border:1px solid #e1e4e8;border-radius:8px;padding:.8rem 1.2rem;min-width:110px}
.card .n{font-size:1.8rem;font-weight:700}.card .l{color:#656d76;font-size:13px}
.timeline{list-style:none;padding:0}
.ev{background:#fff;border:1px solid #e1e4e8;border-left:4px solid #d0d7de;border-radius:6px;padding:.5rem .8rem;margin:.4rem 0}
.ev.command{border-left-color:#0969da}.ev.tool_call{border-left-color:#8250df}
.ev.note{border-left-color:#9a6700}.ev.usage{border-left-color:#1a7f37}
.ev .k{font-weight:600;font-size:12px;text-transform:uppercase;color:#656d76}
pre{background:#f6f8fa;border-radius:6px;padding:.5rem;overflow:auto;font-size:12px;margin:.3rem 0 0}
"""


def _badge(success: bool) -> str:
    return '<span class="pass">PASS</span>' if success else '<span class="fail">FAIL</span>'


def _e(text) -> str:
    return html.escape(str(text))


def render_html(
    rows: List[RunRow],
    title: str = "Harness Lab",
    diff: Optional[Diff] = None,
    run_links: Optional[Dict[str, str]] = None,
) -> str:
    run_links = run_links or {}
    total = len(rows)
    passed = sum(1 for r in rows if r.success)
    leaks = sum(r.secret_leaks for r in rows)
    board = leaderboard(rows)

    parts: List[str] = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body>",
        f"<h1>{_e(title)}</h1>",
        "<div class='summary'>",
        f"<div class='card'><div class='n'>{passed}/{total}</div><div class='l'>passing</div></div>",
        f"<div class='card'><div class='n'>{leaks}</div><div class='l'>secret leaks</div></div>",
    ]
    if diff is not None:
        parts.append(
            f"<div class='card'><div class='n'>{len(diff.regressions)}</div>"
            f"<div class='l'>regressions</div></div>"
        )
        parts.append(
            f"<div class='card'><div class='n'>{len(diff.fixes)}</div>"
            f"<div class='l'>fixes</div></div>"
        )
    parts.append("</div>")

    if diff is not None and (diff.regressions or diff.cost_regressions):
        parts.append("<h2>Regressions</h2><ul>")
        for r in diff.regressions:
            parts.append(f"<li class='flag'>{_e(r)}</li>")
        for r in diff.cost_regressions:
            parts.append(f"<li class='flag'>cost: {_e(r)}</li>")
        parts.append("</ul>")

    parts.append("<h2>Leaderboard</h2><table><tr><th>Agent / Model</th><th>Success</th>"
                 "<th>Avg steps</th><th>Cost/success</th><th>Avg time (s)</th></tr>")
    for e in board:
        cps = f"${e.cost_per_success}" if e.cost_per_success is not None else "—"
        parts.append(
            f"<tr><td>{_e(e.key)}</td>"
            f"<td>{e.successes}/{e.runs} ({e.success_rate*100:.0f}%)</td>"
            f"<td>{e.avg_steps}</td><td>{cps}</td><td>{e.avg_duration}</td></tr>"
        )
    parts.append("</table>")

    # Flakiness only adds signal when some cell ran more than once.
    flakes = [f for f in flakiness(rows) if f.attempts > 1]
    if flakes:
        parts.append("<h2>Flakiness (pass@k)</h2><table><tr><th>Task</th>"
                     "<th>Agent/Model</th><th>Pass rate</th><th>Flaky?</th></tr>")
        for f in flakes:
            flaky = "<span class='flag'>FLAKY</span>" if f.is_flaky else "stable"
            parts.append(
                f"<tr><td>{_e(f.task_id)}</td><td>{_e(f.agent_key)}</td>"
                f"<td>{f.passes}/{f.attempts} ({f.pass_rate*100:.0f}%)</td><td>{flaky}</td></tr>"
            )
        parts.append("</table>")

    parts.append("<h2>Runs</h2><table><tr><th>Task</th><th>Agent/Model</th><th>Result</th>"
                 "<th>Steps</th><th>Cost</th><th>Files</th><th>Loop</th><th>Notes</th></tr>")
    for r in rows:
        notes = _e("; ".join(r.reasons))
        loop = "<span class='flag'>yes</span>" if r.loop_detected else "no"
        link = run_links.get(r.run_id)
        task_cell = f"<a href='{_e(link)}'>{_e(r.task_id)}</a>" if link else _e(r.task_id)
        parts.append(
            f"<tr><td>{task_cell}</td>"
            f"<td>{_e(r.agent)}/{_e(r.model)}</td>"
            f"<td>{_badge(r.success)}</td><td>{r.steps}</td><td>${r.cost_usd}</td>"
            f"<td>{r.files_changed}</td><td>{loop}</td><td class='muted'>{notes}</td></tr>"
        )
    parts.append("</table></body></html>")
    return "".join(parts)


def render_run_detail(result: Dict, meta: Dict, events: List[Dict], title: str) -> str:
    grade = result.get("grade", {})
    agent = result.get("agent", {})
    success = bool(grade.get("success"))

    parts: List[str] = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body>",
        "<p><a href='index.html'>&larr; back to scoreboard</a></p>",
        f"<h1>{_e(result.get('task_id'))} &middot; {_e(result.get('agent_name'))}/"
        f"{_e(result.get('model') or 'default')}</h1>",
        f"<p>{_badge(success)} &nbsp; run <code>{_e(result.get('run_id'))}</code></p>",
        "<div class='summary'>",
        f"<div class='card'><div class='n'>{agent.get('steps', 0)}</div><div class='l'>steps</div></div>",
        f"<div class='card'><div class='n'>${agent.get('usage', {}).get('cost_usd', 0)}</div>"
        f"<div class='l'>cost</div></div>",
        f"<div class='card'><div class='n'>{result.get('duration_sec', 0)}s</div>"
        f"<div class='l'>duration</div></div>",
        "</div>",
    ]

    if grade.get("reasons"):
        parts.append("<h2>Why it failed</h2><ul>")
        for r in grade["reasons"]:
            parts.append(f"<li class='flag'>{_e(r)}</li>")
        parts.append("</ul>")

    if grade.get("files_changed"):
        parts.append("<h2>Files changed</h2><pre>" + _e("\n".join(grade["files_changed"])) + "</pre>")

    parts.append("<h2>Trace timeline</h2>")
    parts.append(f"<p class='muted'>{len(events)} events &middot; "
                 f"loop_detected={meta.get('loop_detected')}</p>")
    parts.append("<ul class='timeline'>")
    for ev in events:
        etype = ev.get("type", "?")
        data = ev.get("data", {})
        parts.append(f"<li class='ev {_e(etype)}'><span class='k'>{_e(etype)}</span> ")
        if etype == "command":
            parts.append(f"<code>{_e(data.get('cmd', ''))}</code> "
                         f"<span class='muted'>(exit {data.get('exit_code')}, "
                         f"{data.get('duration_sec')}s)</span>")
            out = (data.get("stdout") or "") + (data.get("stderr") or "")
            if out.strip():
                parts.append(f"<pre>{_e(out[:1500])}</pre>")
        elif etype == "tool_call":
            parts.append(f"<code>{_e(data.get('name'))}</code> "
                         f"<span class='muted'>{_e(data.get('args'))}</span>")
        elif etype == "usage":
            parts.append(f"<span class='muted'>+{data.get('input_tokens',0)}in / "
                         f"{data.get('output_tokens',0)}out, ${data.get('cost_usd',0)}</span>")
        else:
            parts.append(f"<span class='muted'>{_e(data.get('message', data))}</span>")
        parts.append("</li>")
    parts.append("</ul></body></html>")
    return "".join(parts)


def write_html(rows: List[RunRow], path: Path, title: str = "Harness Lab", diff: Optional[Diff] = None) -> Path:
    path = Path(path)
    path.write_text(render_html(rows, title=title, diff=diff))
    return path


def write_report(
    rows: List[RunRow],
    out_dir: Path,
    title: str = "Harness Lab",
    diff: Optional[Diff] = None,
) -> Path:
    """Write index.html + a detail page per run into ``out_dir``. Returns index path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    run_links: Dict[str, str] = {}
    for r in rows:
        if not r.run_id:
            continue
        fname = f"run-{r.run_id}.html"
        run_links[r.run_id] = fname
        run_dir = Path(r.trace_path).parent
        try:
            result = json.loads((run_dir / "result.json").read_text())
        except (OSError, ValueError):
            continue
        meta, events = read_trace(Path(r.trace_path))
        (out_dir / fname).write_text(
            render_run_detail(result, meta, events, title=f"{r.task_id} · {r.agent}/{r.model}")
        )

    index = out_dir / "index.html"
    index.write_text(render_html(rows, title=title, diff=diff, run_links=run_links))
    return index
