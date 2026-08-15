"""tools/signature_diff.py tests: run_signature_scan (scripted subprocess) and
diff_signatures (pure). JAR contract verified against the pinned release --
see docs/superpowers/plans/2026-08-15-plugin-parity-hardening.md, Task 1."""

import json
from pathlib import Path

from agent.tools import signature_diff


def fake_runner_writing_report(report_content):
    """Simulates `java -jar <jar> signature <root> -o <out>`: writes
    report_content to the path following -o in argv."""
    calls = []

    def runner(argv, cwd, dry_run):
        calls.append(argv)
        if report_content is not None and not dry_run:
            out_idx = argv.index("-o") + 1
            Path(argv[out_idx]).write_text(json.dumps(report_content))

        class Result:
            stdout = ""
            stderr = ""

        return Result()

    return runner, calls


def test_run_signature_scan_uses_dash_o_not_report_flag(tmp_path):
    """The signature subcommand takes -o/--output, not --report like inventory --
    a real bug this test would have caught if the wrong flag were used."""
    report_path = tmp_path / "sig.json"
    runner, calls = fake_runner_writing_report({"root": "x", "files": {}})

    signature_diff.run_signature_scan(tmp_path, Path("dev-toolkit.jar"), report_path, runner=runner)

    argv = calls[0]
    assert "-o" in argv
    assert "--report" not in argv
    assert argv[argv.index("-o") + 1] == str(report_path)


def test_run_signature_scan_reads_report(tmp_path):
    report_path = tmp_path / "sig.json"
    content = {"root": "x", "files": {"Foo.java": {"path": "Foo.java", "class": "Foo", "methods": []}}}
    runner, _ = fake_runner_writing_report(content)

    report = signature_diff.run_signature_scan(tmp_path, Path("dev-toolkit.jar"), report_path, runner=runner)

    assert report == content


def test_run_signature_scan_dry_run_returns_none(tmp_path):
    report_path = tmp_path / "sig.json"
    runner, calls = fake_runner_writing_report({"root": "x", "files": {}})

    report = signature_diff.run_signature_scan(
        tmp_path, Path("dev-toolkit.jar"), report_path, dry_run=True, runner=runner
    )

    assert report is None
    assert not report_path.exists()


def test_run_signature_scan_missing_report_returns_none(tmp_path):
    report_path = tmp_path / "sig.json"
    runner, _ = fake_runner_writing_report(None)  # runner does not write the file

    report = signature_diff.run_signature_scan(tmp_path, Path("dev-toolkit.jar"), report_path, runner=runner)

    assert report is None


def test_run_signature_scan_unparseable_report_returns_none(tmp_path):
    report_path = tmp_path / "sig.json"
    report_path.write_text("not json{{{")

    def runner(argv, cwd, dry_run):
        class Result:
            stdout = ""
            stderr = ""

        return Result()

    report = signature_diff.run_signature_scan(tmp_path, Path("dev-toolkit.jar"), report_path, runner=runner)

    assert report is None


def _method(name, arity=0, visibility="public", returns="reference", statements=1):
    return {"name": name, "arity": arity, "visibility": visibility, "returns": returns, "statements": statements}


def _file(cls, methods, path=None):
    return {"path": path or f"{cls}.java", "class": cls, "methods": methods, "fields": []}


def test_diff_method_missing_is_blocker():
    play = {"files": {"Foo.java": _file("Foo", [_method("bar"), _method("baz")])}}
    spring = {"files": {"Foo.java": _file("Foo", [_method("bar")])}}

    diff = signature_diff.diff_signatures(play, spring)

    assert len(diff["method_missing"]) == 1
    assert diff["method_missing"][0]["method"] == "baz"
    assert diff["signature_changed"] == []


def test_diff_classes_absent_from_spring_is_not_a_finding():
    """The partial-migration case: a Play class not migrated yet must not be
    reported the same way as a genuinely lost method -- later batches simply
    haven't landed, and gating a partial slice must stay safe by construction."""
    play = {"files": {"Foo.java": _file("Foo", [_method("bar")])}}
    spring = {"files": {}}

    diff = signature_diff.diff_signatures(play, spring)

    assert diff["method_missing"] == []
    assert len(diff["classes_absent_from_spring"]) == 1
    assert diff["classes_absent_from_spring"][0]["class"] == "Foo"


def test_diff_return_kind_change_is_signature_changed_not_blocker():
    play = {"files": {"Foo.java": _file("Foo", [_method("bar", returns="reference")])}}
    spring = {"files": {"Foo.java": _file("Foo", [_method("bar", returns="void")])}}

    diff = signature_diff.diff_signatures(play, spring)

    assert diff["method_missing"] == []
    assert len(diff["signature_changed"]) == 1
    assert diff["signature_changed"][0] == {
        "class": "Foo",
        "method": "bar",
        "arity": 0,
        "play_returns": "reference",
        "spring_returns": "void",
    }


def test_diff_overload_disambiguated_by_arity():
    """Two methods named `bar` with different arity are different keys --
    losing the 1-arg overload must not be masked by the 0-arg one surviving."""
    play = {"files": {"Foo.java": _file("Foo", [_method("bar", arity=0), _method("bar", arity=1)])}}
    spring = {"files": {"Foo.java": _file("Foo", [_method("bar", arity=0)])}}

    diff = signature_diff.diff_signatures(play, spring)

    assert len(diff["method_missing"]) == 1
    assert diff["method_missing"][0]["arity"] == 1


def test_diff_private_and_package_methods_ignored():
    play = {
        "files": {
            "Foo.java": _file(
                "Foo",
                [_method("bar", visibility="private"), _method("baz", visibility="package")],
            )
        }
    }
    spring = {"files": {"Foo.java": _file("Foo", [])}}

    diff = signature_diff.diff_signatures(play, spring)

    assert diff["method_missing"] == []


def test_diff_parse_error_excluded_not_treated_as_zero_methods():
    """A parse_error entry has no `methods` key -- it must never be read as a
    class with zero methods (which would look like every method vanished)."""
    play = {"files": {"Foo.java": {"path": "Foo.java", "parse_error": "could not parse"}}}
    spring = {"files": {"Foo.java": _file("Foo", [_method("bar")])}}

    diff = signature_diff.diff_signatures(play, spring)

    assert diff["method_missing"] == []
    assert diff["classes_absent_from_spring"] == []
    assert len(diff["parse_errors"]) == 1
    assert diff["parse_errors"][0]["side"] == "play"


def test_diff_spring_side_parse_error_also_reported():
    play = {"files": {"Foo.java": _file("Foo", [_method("bar")])}}
    spring = {"files": {"Foo.java": {"path": "Foo.java", "parse_error": "could not parse"}}}

    diff = signature_diff.diff_signatures(play, spring)

    # class never matched (spring side excluded from spring_by_class), so it
    # reads as absent, plus its own parse_error is recorded.
    assert len(diff["classes_absent_from_spring"]) == 1
    assert len(diff["parse_errors"]) == 1
    assert diff["parse_errors"][0]["side"] == "spring"


def test_diff_layer_prefix_scopes_to_slice():
    play = {
        "files": {
            "controllers/Foo.java": _file("Foo", [_method("bar")], path="controllers/Foo.java"),
            "service/Baz.java": _file("Baz", [_method("qux")], path="service/Baz.java"),
        }
    }
    spring = {"files": {}}

    diff = signature_diff.diff_signatures(play, spring, layer_prefix="controllers/")

    assert len(diff["classes_absent_from_spring"]) == 1
    assert diff["classes_absent_from_spring"][0]["class"] == "Foo"


def test_diff_empty_reports_yield_no_findings():
    diff = signature_diff.diff_signatures(None, None)

    assert diff == {
        "method_missing": [],
        "classes_absent_from_spring": [],
        "signature_changed": [],
        "parse_errors": [],
    }
