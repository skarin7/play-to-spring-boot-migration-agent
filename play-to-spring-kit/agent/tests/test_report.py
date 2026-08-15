"""report.py tests (M6 Task 11): console_summary (blocker-only), render_html
shape, write_report file output, and report_only's regenerate-without-
re-running path."""

import json

from agent.config import AgentConfig
from agent.report import blocker_findings, console_summary, render_html, report_only, write_report
from agent.status_v2 import write_status_v2


def make_config(tmp_path, **kw) -> AgentConfig:
    return AgentConfig(spring_repo=tmp_path, **kw)


def _state(**overrides):
    base = {
        "run_outcome": "success",
        "run_exit_code": 0,
        "total_cost_usd": 1.23,
        "migration_units": [{"id": "a", "status": "done", "files_migrated": 3}],
        "findings": [],
        "signature_findings": [],
    }
    base.update(overrides)
    return base


# ----------------------------------------------------------------------
# blocker_findings / console_summary
# ----------------------------------------------------------------------


def test_blocker_findings_filters_by_severity():
    state = _state(
        findings=[
            {"tier": "T4", "severity": "major", "category": "test_failure"},
            {"tier": "T5", "severity": "blocker", "category": "endpoint_diff"},
        ]
    )
    result = blocker_findings(state)
    assert len(result) == 1
    assert result[0]["category"] == "endpoint_diff"


def test_blocker_findings_merges_signature_and_general_findings():
    state = _state(
        signature_findings=[{"tier": "T2", "severity": "blocker", "category": "method_missing"}],
        findings=[{"tier": "T5", "severity": "blocker", "category": "endpoint_diff"}],
    )
    result = blocker_findings(state)
    assert len(result) == 2


def test_console_summary_no_blockers():
    state = _state()
    cfg = make_config
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        cfg = make_config(Path(d))
        summary = console_summary(state, cfg)

    assert "0 blocker findings" in summary
    assert "run_outcome=success" in summary


def test_console_summary_lists_only_blockers_not_majors_minors():
    import tempfile
    from pathlib import Path

    state = _state(
        findings=[
            {"tier": "T4", "severity": "major", "category": "test_failure", "subject": "should not appear"},
            {"tier": "T2", "severity": "blocker", "category": "method_missing", "subject": "Foo.bar"},
        ]
    )
    with tempfile.TemporaryDirectory() as d:
        cfg = make_config(Path(d))
        summary = console_summary(state, cfg)

    assert "1 blocker finding" in summary
    assert "method_missing: Foo.bar" in summary
    assert "should not appear" not in summary


def test_console_summary_includes_cost_and_report_pointer(tmp_path):
    cfg = make_config(tmp_path)
    summary = console_summary(_state(total_cost_usd=4.5678), cfg)
    assert "total_cost_usd=4.5678" in summary
    assert str(tmp_path / ".migration" / "report.html") in summary


# ----------------------------------------------------------------------
# render_html
# ----------------------------------------------------------------------


def test_render_html_contains_run_outcome_and_units(tmp_path):
    cfg = make_config(tmp_path)
    html_out = render_html(_state(), cfg)
    assert "success" in html_out
    assert "<html>" in html_out
    assert "files_migrated" not in html_out  # label, not the literal state key -- table renders values
    assert ">a<" in html_out  # unit id rendered in the table


def test_render_html_escapes_html_in_finding_values(tmp_path):
    """A finding subject containing HTML-significant characters (e.g. a Java
    generic type Foo<Bar>) must not break the page or inject markup."""
    cfg = make_config(tmp_path)
    state = _state(
        findings=[
            {
                "tier": "T4",
                "severity": "blocker",
                "category": "test_failure",
                "subject": "<script>alert(1)</script>",
            }
        ]
    )
    html_out = render_html(state, cfg)
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_render_html_separates_blockers_from_others(tmp_path):
    cfg = make_config(tmp_path)
    state = _state(
        findings=[
            {"tier": "T4", "severity": "major", "category": "test_failure"},
            {"tier": "T5", "severity": "blocker", "category": "endpoint_diff"},
        ]
    )
    html_out = render_html(state, cfg)
    assert "Blocker findings (1)" in html_out
    assert "Other findings (1)" in html_out


def test_render_html_includes_gaps_section(tmp_path):
    from agent.tools.gaps import record_gap

    cfg = make_config(tmp_path)
    record_gap(cfg.spring_repo, "unhandled_idiom", "akka.actor.UntypedActor", "hand-ported to @Async")

    html_out = render_html(_state(), cfg)

    assert "akka.actor.UntypedActor" in html_out
    assert "hand-ported to @Async" in html_out


def test_render_html_no_gaps_says_so(tmp_path):
    cfg = make_config(tmp_path)
    html_out = render_html(_state(), cfg)
    assert "No gaps recorded" in html_out


# ----------------------------------------------------------------------
# write_report
# ----------------------------------------------------------------------


def test_write_report_creates_file_under_migration_dir(tmp_path):
    cfg = make_config(tmp_path)
    path = write_report(_state(), cfg)

    assert path == tmp_path / ".migration" / "report.html"
    assert path.is_file()
    assert "<html>" in path.read_text()


# ----------------------------------------------------------------------
# report_only: regenerate from migration-status.json without re-running
# ----------------------------------------------------------------------


def test_report_only_missing_status_file_raises(tmp_path):
    cfg = make_config(tmp_path)
    try:
        report_only(cfg)
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_report_only_regenerates_from_status_file(tmp_path):
    cfg = make_config(tmp_path)
    state = _state(
        findings=[{"tier": "T4", "severity": "blocker", "category": "test_failure", "subject": "X"}],
    )
    write_status_v2(state, cfg)

    path = report_only(cfg)

    assert path.is_file()
    content = path.read_text()
    assert "test_failure" in content
    assert "Blocker findings (1)" in content


def test_report_only_does_not_write_status_file(tmp_path):
    """--report-only must not touch migration state -- only reads the
    existing migration-status.json and writes report.html."""
    cfg = make_config(tmp_path)
    state = _state()
    write_status_v2(state, cfg)
    status_path = cfg.status_path
    before = status_path.read_text()

    report_only(cfg)

    assert status_path.read_text() == before
