"""tools/setup_ops.py tests with a scripted fake subprocess runner (no real Maven/bash)."""

from pathlib import Path

from agent.tools import setup_ops


def fake_runner(returncode=0, stdout="", stderr=""):
    calls = []

    def runner(argv, cwd, dry_run):
        calls.append(argv)

        class Result:
            pass

        r = Result()
        r.returncode = returncode
        r.stdout = stdout
        r.stderr = stderr
        return r

    return runner, calls


def test_ensure_jar_skip_build_uses_existing_jar(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_ops, "kit_root", lambda: tmp_path)
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "dev-toolkit-1.0.0.jar").write_text("x")
    ok, msg = setup_ops.ensure_jar_in_kit_lib(skip_build=True, toolkit_root=tmp_path, dry_run=False)
    assert ok
    assert "dev-toolkit-1.0.0.jar" in msg


def test_ensure_jar_skip_build_fails_without_existing_jar(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_ops, "kit_root", lambda: tmp_path)
    ok, msg = setup_ops.ensure_jar_in_kit_lib(skip_build=True, toolkit_root=tmp_path, dry_run=False)
    assert not ok
    assert "no JAR found" in msg


def test_ensure_jar_fetches_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_ops, "kit_root", lambda: tmp_path / "kit")
    fetched = []

    def fake_fetch(release_file, cache_dir):
        fetched.append((release_file, cache_dir))
        jar = cache_dir / "dev-toolkit-1.0.0.jar"
        jar.parent.mkdir(parents=True, exist_ok=True)
        jar.write_text("jar-bytes")
        return jar

    ok, msg = setup_ops.ensure_jar_in_kit_lib(
        skip_build=False, toolkit_root=tmp_path / "unused", dry_run=False, fetch=fake_fetch
    )
    assert ok
    assert "Fetched" in msg
    assert fetched == [(tmp_path / "kit" / "toolkit-release.json", tmp_path / "kit" / "lib")]


def test_ensure_jar_fetch_failure_propagates(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_ops, "kit_root", lambda: tmp_path / "kit")

    def fake_fetch(release_file, cache_dir):
        raise SystemExit("ERROR: sha256 mismatch")

    ok, msg = setup_ops.ensure_jar_in_kit_lib(
        skip_build=False, toolkit_root=tmp_path / "unused", dry_run=False, fetch=fake_fetch
    )
    assert not ok
    assert "sha256 mismatch" in msg


def test_ensure_jar_dry_run_skips_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_ops, "kit_root", lambda: tmp_path / "kit")

    def fake_fetch(release_file, cache_dir):
        raise AssertionError("fetch should not be called during a dry run")

    ok, msg = setup_ops.ensure_jar_in_kit_lib(
        skip_build=False, toolkit_root=tmp_path / "unused", dry_run=True, fetch=fake_fetch
    )
    assert ok
    assert "dry-run" in msg


def test_ensure_jar_missing_toolkit_root_fails(tmp_path):
    ok, msg = setup_ops.ensure_jar_in_kit_lib(
        skip_build=False, toolkit_root=tmp_path / "nope", dry_run=False, build_from_source=True
    )
    assert not ok
    assert "not found" in msg


def test_ensure_jar_builds_and_copies(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_ops, "kit_root", lambda: tmp_path / "kit")
    toolkit_root = tmp_path / "java-dev-toolkit"
    (toolkit_root / "target").mkdir(parents=True)
    (toolkit_root / "pom.xml").write_text("<project/>")
    (toolkit_root / "target" / "dev-toolkit-1.0.0.jar").write_text("jar-bytes")
    runner, calls = fake_runner(returncode=0)
    ok, msg = setup_ops.ensure_jar_in_kit_lib(
        skip_build=False,
        toolkit_root=toolkit_root,
        dry_run=False,
        build_from_source=True,
        runner=runner,
    )
    assert ok
    assert (tmp_path / "kit" / "lib" / "dev-toolkit-1.0.0.jar").is_file()
    assert calls[0][:2] == ["mvn", "package"]


def test_ensure_jar_build_failure(tmp_path):
    toolkit_root = tmp_path / "java-dev-toolkit"
    toolkit_root.mkdir()
    (toolkit_root / "pom.xml").write_text("<project/>")
    runner, _ = fake_runner(returncode=1)
    ok, msg = setup_ops.ensure_jar_in_kit_lib(
        skip_build=False,
        toolkit_root=toolkit_root,
        dry_run=False,
        build_from_source=True,
        runner=runner,
    )
    assert not ok
    assert "mvn package failed" in msg


def test_run_setup_sh_missing_script(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_ops, "scripts_dir", lambda: tmp_path / "no-scripts")
    ok, msg = setup_ops.run_setup_sh(tmp_path, tmp_path, None, False)
    assert not ok
    assert "missing" in msg


def test_run_setup_sh_success(tmp_path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "setup.sh").write_text("#!/bin/bash\n")
    monkeypatch.setattr(setup_ops, "scripts_dir", lambda: tmp_path / "scripts")
    monkeypatch.setattr(setup_ops, "kit_root", lambda: tmp_path)
    runner, calls = fake_runner(returncode=0, stdout="ok\n")
    ok, msg = setup_ops.run_setup_sh(tmp_path, tmp_path, "my-spring", False, runner=runner)
    assert ok
    assert "my-spring" in calls[0]


def test_run_export_play_conf_skips_when_no_conf(tmp_path):
    ok, msg = setup_ops.run_export_play_conf(tmp_path, tmp_path, [], False)
    assert ok
    assert "skip" in msg


def test_setup_ops_class_skips_when_no_play_repo(tmp_path):
    class Cfg:
        play_repo = None
        export_play_conf = False

    ops = setup_ops.SetupOps()
    assert ops.ensure_jar(Cfg())[0]
    assert ops.install(Cfg())[0]
    assert ops.export_conf(Cfg())[0]
