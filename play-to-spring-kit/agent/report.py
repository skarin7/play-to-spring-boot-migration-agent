"""Run report (M6 Task 11): the engine's terminal artifact was a JSON blob
and an exit code -- this renders <spring-repo>/.migration/report.html from
the same sources cli.py already writes/reads (migration-status.json via
status_v2.py) plus the M6 artifacts: T2 signature findings (Task 1), cost
totals (Task 5), gaps (Task 8), T4/T5 findings (Task 10).

Console output stays terse on purpose: only blocker-severity findings, the
run outcome, and total cost print to the terminal (console_summary below);
everything else is a pointer to the rendered file. Matches the plugin's own
split -- "failed_layers, and qa_findings at blocker severity only" go to
chat, "clean layers, major/minor findings" stay in the report.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .config import AgentConfig
from .state import MigrationState
from .status_v2 import state_to_status_v2

SEVERITY_ORDER = {"blocker": 0, "major": 1, "minor": 2}


def _all_findings(state: MigrationState) -> list[dict[str, Any]]:
    """Merges the two finding lists this engine has (see state.py's comment
    on why they're separate): T2's signature_findings (Task 1, predates the
    general shape) and the general findings list (Task 10/11, T4/T5)."""
    return list(state.get("signature_findings") or []) + list(state.get("findings") or [])


def blocker_findings(state: MigrationState) -> list[dict[str, Any]]:
    return [f for f in _all_findings(state) if f.get("severity") == "blocker"]


def console_summary(state: MigrationState, config: AgentConfig) -> str:
    """Short text for the terminal: run outcome, cost, blocker findings only.
    Everything else is a pointer to the report file."""
    lines = [
        f"run_outcome={state.get('run_outcome', 'unknown')} "
        f"total_cost_usd={round(state.get('total_cost_usd', 0.0), 4)}",
    ]
    blockers = blocker_findings(state)
    if blockers:
        lines.append(f"{len(blockers)} blocker finding(s):")
        for f in blockers:
            tier = f.get("tier", "?")
            category = f.get("category", "?")
            subject = f.get("subject") or f.get("path") or f.get("class") or ""
            lines.append(f"  - [{tier}] {category}: {subject}")
    else:
        lines.append("0 blocker findings")
    report_path = config.spring_repo / ".migration" / "report.html"
    lines.append(f"full report: {report_path}")
    return "\n".join(lines)


def _esc(value: Any) -> str:
    return html.escape(str(value))


def _findings_table(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "<p>No findings.</p>"
    findings = sorted(findings, key=lambda f: SEVERITY_ORDER.get(f.get("severity", "minor"), 9))
    rows = []
    for f in findings:
        rows.append(
            "<tr>"
            f"<td>{_esc(f.get('tier', ''))}</td>"
            f"<td>{_esc(f.get('severity', ''))}</td>"
            f"<td>{_esc(f.get('category', ''))}</td>"
            f"<td>{_esc(f.get('scope', ''))}</td>"
            f"<td>{_esc(json.dumps({k: v for k, v in f.items() if k not in ('tier', 'severity', 'category', 'scope')}, default=str))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Tier</th><th>Severity</th><th>Category</th>"
        "<th>Scope</th><th>Detail</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _gaps_section(config: AgentConfig) -> str:
    from .tools.gaps import read_gaps

    gaps = read_gaps(config.spring_repo)
    if not gaps:
        return "<p>No gaps recorded.</p>"
    rows = []
    for g in gaps:
        rows.append(
            "<tr>"
            f"<td>{_esc(g.get('kind', ''))}</td>"
            f"<td>{_esc(g.get('subject', ''))}</td>"
            f"<td>{_esc(g.get('what_i_did', ''))}</td>"
            f"<td>{_esc(g.get('role', ''))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Kind</th><th>Subject</th><th>What happened</th>"
        "<th>Role</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        "<p><em>Run gap_report.py render for a shareable, redacted version of this section.</em></p>"
    )


def render_html(state: MigrationState, config: AgentConfig) -> str:
    status = state_to_status_v2(state, config)
    findings = _all_findings(state)
    blockers = [f for f in findings if f.get("severity") == "blocker"]
    majors_minors = [f for f in findings if f.get("severity") != "blocker"]

    units = state.get("migration_units") or []
    unit_rows = "".join(
        f"<tr><td>{_esc(u.get('id', ''))}</td><td>{_esc(u.get('status', ''))}</td>"
        f"<td>{_esc(u.get('files_migrated', 0))}</td>"
        # M6 Task 12: which case actually fired when status is loop_detected
        # -- "identical_to_last"/"oscillating" reads as genuinely stuck;
        # "error_count_spike"/"different_error_set" often means a fix
        # landed and exposed a different problem underneath, the exact
        # case a pure error-count heuristic misjudges as still stuck.
        f"<td>{_esc(u.get('stuck_vs_progress_reason', ''))}</td></tr>"
        for u in units
    )

    test_result = state.get("test_result") or {}
    endpoint = state.get("endpoint_verification") or {}
    autonomous = status.get("autonomous", {})

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Migration Report</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; }}
table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; vertical-align: top; }}
th {{ background: #f0f0f0; }}
.blocker {{ color: #b00; font-weight: bold; }}
</style></head>
<body>
<h1>Migration Report</h1>
<p>run_outcome: <strong>{_esc(state.get('run_outcome', 'unknown'))}</strong>
(exit {_esc(state.get('run_exit_code', ''))})</p>

<h2>Cost</h2>
<p>total_llm_calls: {_esc(autonomous.get('total_llm_calls', 0))} / {_esc(autonomous.get('max_total_llm_calls', ''))}<br>
total_cost_usd: {_esc(round(state.get('total_cost_usd', 0.0), 4))}</p>

<h2>Blocker findings ({len(blockers)})</h2>
{_findings_table(blockers)}

<h2>Other findings ({len(majors_minors)})</h2>
{_findings_table(majors_minors)}

<h2>T4: mvn test</h2>
<p>passed={_esc(test_result.get('passed', '-'))} failed={_esc(test_result.get('failed', '-'))}
errors={_esc(test_result.get('errors', '-'))} skipped={_esc(test_result.get('skipped', '-'))}</p>

<h2>T5: endpoint parity</h2>
<p>status={_esc(endpoint.get('status', 'not_attempted'))}
probes_compared={_esc(endpoint.get('probes_compared', 0))}
differences={_esc(endpoint.get('differences', 0))}
not_captured_after={_esc(endpoint.get('not_captured_after', 0))}</p>

<h2>Migration units</h2>
<table><thead><tr><th>Unit</th><th>Status</th><th>Files migrated</th><th>Stuck-vs-progress</th></tr></thead>
<tbody>{unit_rows}</tbody></table>

<h2>Gaps</h2>
{_gaps_section(config)}

</body></html>
"""


def write_report(state: MigrationState, config: AgentConfig) -> Path:
    path = config.spring_repo / ".migration" / "report.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(state, config), encoding="utf-8")
    return path


def report_only(config: AgentConfig) -> Path:
    """--report-only: regenerate report.html from an existing
    migration-status.json without re-running anything or touching migration
    state. Raises FileNotFoundError if no status file exists yet."""
    from .status_v2 import status_v2_to_state

    status_path = config.status_path or (config.spring_repo / "migration-status.json")
    if not status_path.is_file():
        raise FileNotFoundError(f"no migration-status.json found at {status_path}")
    raw = json.loads(status_path.read_text(encoding="utf-8"))
    state = status_v2_to_state(raw)
    # status_v2_to_state only adopts the fields the graph itself reads back
    # in (see its own docstring) -- findings/cost/etc. for the report are
    # read straight from the raw status dict here instead, since the report
    # wants everything status_v2 wrote, not just what a resumed run needs.
    state.setdefault("run_outcome", raw.get("run_outcome"))
    state.setdefault("run_exit_code", raw.get("run_exit_code"))
    state.setdefault("signature_findings", raw.get("signature_findings", []))
    state.setdefault("findings", raw.get("findings", []))
    state.setdefault("test_result", raw.get("test_result"))
    state.setdefault("endpoint_verification", raw.get("endpoint_verification"))
    autonomous = raw.get("autonomous") or {}
    state.setdefault("total_cost_usd", autonomous.get("total_cost_usd", 0.0))
    return write_report(state, config)
