"""tools/toolkit_inventory.py tests with a scripted fake subprocess runner (no real JAR/java)."""

import json
from pathlib import Path

from agent.tools import toolkit_inventory


def fake_runner_writing_report(report_content):
    """Simulates the JAR: writes report_content as JSON to the path following --report in argv."""
    calls = []

    def runner(argv, cwd, dry_run):
        calls.append(argv)
        if report_content is not None:
            report_idx = argv.index("--report") + 1
            Path(argv[report_idx]).write_text(json.dumps(report_content))

        class Result:
            stdout = ""
            stderr = ""

        return Result()

    return runner, calls


def test_run_inventory_scan_reads_report(tmp_path):
    report_path = tmp_path / "inventory-report.json"
    content = {
        "filesScanned": 2,
        "knownCount": 3,
        "unknownCount": 0,
        "paradigmCount": 1,
        "coveragePercent": 75.0,
        "touchpoints": [{"construct": "akka.actor.UntypedActor", "classification": "PARADIGM"}],
    }
    runner, calls = fake_runner_writing_report(content)

    report = toolkit_inventory.run_inventory_scan(
        tmp_path, Path("dev-toolkit.jar"), report_path, dry_run=False, runner=runner
    )

    assert report == content
    assert "inventory" in calls[0]
    assert "--source" in calls[0] and str(tmp_path) in calls[0]
    assert "--report" in calls[0] and str(report_path) in calls[0]


def test_run_inventory_scan_dry_run_returns_none(tmp_path):
    report_path = tmp_path / "inventory-report.json"
    runner, calls = fake_runner_writing_report(None)

    report = toolkit_inventory.run_inventory_scan(
        tmp_path, Path("j.jar"), report_path, dry_run=True, runner=runner
    )

    assert report is None
    assert len(calls) == 1


def test_run_inventory_scan_missing_report_returns_none(tmp_path):
    report_path = tmp_path / "does-not-exist.json"
    runner, _ = fake_runner_writing_report(None)  # runner "succeeds" but writes nothing

    report = toolkit_inventory.run_inventory_scan(
        tmp_path, Path("j.jar"), report_path, dry_run=False, runner=runner
    )

    assert report is None
