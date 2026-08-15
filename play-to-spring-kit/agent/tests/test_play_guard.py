"""tools/play_guard.py tests: git-mode / manifest-mode detection, and the two
bugs the plugin's guard.py fixed that this module deliberately avoids --
a non-git Play repo must never read as "clean" via empty git stdout, and a
missing baseline must be "error", never "clean"."""

import subprocess

import pytest

from agent.tools import play_guard


def _real_run_cmd(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=30)


def _git_init(repo):
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def _git_commit_all(repo, msg="commit"):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)


# ----------------------------------------------------------------------
# 1. Non-git repo -> manifest mode, never mistaken for git-clean.
# ----------------------------------------------------------------------


def test_non_git_repo_uses_manifest_mode_not_git(tmp_path):
    play_repo = tmp_path / "play"
    play_repo.mkdir()
    (play_repo / "app.conf").write_text("x=1")
    baseline_path = tmp_path / "baseline.json"

    play_guard.capture_baseline(play_repo, baseline_path, run_cmd=_real_run_cmd)

    import json

    baseline = json.loads(baseline_path.read_text())
    assert baseline["mode"] == "manifest"
    assert "app.conf" in baseline["manifest"]


def test_non_git_repo_clean_after_capture(tmp_path):
    play_repo = tmp_path / "play"
    play_repo.mkdir()
    (play_repo / "app.conf").write_text("x=1")
    baseline_path = tmp_path / "baseline.json"

    play_guard.capture_baseline(play_repo, baseline_path, run_cmd=_real_run_cmd)
    status = play_guard.check(play_repo, baseline_path, run_cmd=_real_run_cmd)

    assert status == "clean"


def test_non_git_repo_tampered_after_edit(tmp_path):
    play_repo = tmp_path / "play"
    play_repo.mkdir()
    target = play_repo / "app.conf"
    target.write_text("x=1")
    baseline_path = tmp_path / "baseline.json"

    play_guard.capture_baseline(play_repo, baseline_path, run_cmd=_real_run_cmd)
    target.write_text("x=2")  # simulate an unjailed writer touching Play
    status = play_guard.check(play_repo, baseline_path, run_cmd=_real_run_cmd)

    assert status == "tampered"


def test_non_git_repo_catches_gitignored_style_new_file(tmp_path):
    """Manifest mode has no gitignore semantics -- a new file is tamper
    regardless of what a .gitignore in the repo would say."""
    play_repo = tmp_path / "play"
    play_repo.mkdir()
    (play_repo / "app.conf").write_text("x=1")
    (play_repo / ".gitignore").write_text("secret.env\n")
    baseline_path = tmp_path / "baseline.json"

    play_guard.capture_baseline(play_repo, baseline_path, run_cmd=_real_run_cmd)
    (play_repo / "secret.env").write_text("SECRET=1")  # would be gitignored, still caught
    status = play_guard.check(play_repo, baseline_path, run_cmd=_real_run_cmd)

    assert status == "tampered"


# ----------------------------------------------------------------------
# 2. The specific bug: a non-git repo must never be read as clean via
#    git's own "exit 128, empty stdout" behavior.
# ----------------------------------------------------------------------


def test_non_git_repo_git_status_would_lie_but_manifest_mode_avoids_it(tmp_path):
    """Directly exercises the failure mode the plugin's guard.py fixed: git
    status --porcelain on a non-repo exits 128 with EMPTY stdout, which a
    naive 'empty means clean' check would misread as clean. This module must
    never take that path for a non-git repo -- it must resolve to manifest
    mode before ever calling git status."""
    play_repo = tmp_path / "play"
    play_repo.mkdir()

    result = _real_run_cmd(["git", "-C", str(play_repo), "status", "--porcelain"])
    assert result.returncode != 0
    assert result.stdout.strip() == ""  # the trap: this LOOKS clean

    baseline_path = tmp_path / "baseline.json"
    play_guard.capture_baseline(play_repo, baseline_path, run_cmd=_real_run_cmd)
    import json

    assert json.loads(baseline_path.read_text())["mode"] == "manifest"


# ----------------------------------------------------------------------
# 3. Missing baseline is "error", never "clean".
# ----------------------------------------------------------------------


def test_missing_baseline_is_error_not_clean(tmp_path):
    play_repo = tmp_path / "play"
    play_repo.mkdir()
    baseline_path = tmp_path / "does-not-exist.json"

    status = play_guard.check(play_repo, baseline_path, run_cmd=_real_run_cmd)

    assert status == "error"


def test_unparseable_baseline_is_error(tmp_path):
    play_repo = tmp_path / "play"
    play_repo.mkdir()
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text("not json{{{")

    status = play_guard.check(play_repo, baseline_path, run_cmd=_real_run_cmd)

    assert status == "error"


def test_unknown_mode_in_baseline_is_error(tmp_path):
    play_repo = tmp_path / "play"
    play_repo.mkdir()
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text('{"mode": "bogus"}')

    status = play_guard.check(play_repo, baseline_path, run_cmd=_real_run_cmd)

    assert status == "error"


# ----------------------------------------------------------------------
# 4. Git-mode repo: clean/tampered via git status, and the git-repo-deleted
#    edge case (was git at capture, isn't now) is "error", not silently
#    falling back to a clean-looking manifest check.
# ----------------------------------------------------------------------


@pytest.mark.skipif(subprocess.run(["which", "git"], capture_output=True).returncode != 0, reason="git not installed")
def test_git_repo_clean(tmp_path):
    play_repo = tmp_path / "play"
    play_repo.mkdir()
    _git_init(play_repo)
    (play_repo / "app.conf").write_text("x=1")
    _git_commit_all(play_repo)
    baseline_path = tmp_path / "baseline.json"

    play_guard.capture_baseline(play_repo, baseline_path, run_cmd=_real_run_cmd)
    import json

    assert json.loads(baseline_path.read_text())["mode"] == "git"
    assert play_guard.check(play_repo, baseline_path, run_cmd=_real_run_cmd) == "clean"


def test_git_repo_tampered_by_dirty_worktree(tmp_path):
    play_repo = tmp_path / "play"
    play_repo.mkdir()
    _git_init(play_repo)
    target = play_repo / "app.conf"
    target.write_text("x=1")
    _git_commit_all(play_repo)
    baseline_path = tmp_path / "baseline.json"

    play_guard.capture_baseline(play_repo, baseline_path, run_cmd=_real_run_cmd)
    target.write_text("x=2")  # unjailed writer touches Play
    status = play_guard.check(play_repo, baseline_path, run_cmd=_real_run_cmd)

    assert status == "tampered"


def test_git_repo_deleted_between_capture_and_check_is_error(tmp_path):
    """Baseline says 'git' but the repo isn't git anymore -- this is itself
    tamper-shaped and must not be silently re-resolved to a fresh manifest
    comparison that would read as clean."""
    play_repo = tmp_path / "play"
    play_repo.mkdir()
    _git_init(play_repo)
    (play_repo / "app.conf").write_text("x=1")
    _git_commit_all(play_repo)
    baseline_path = tmp_path / "baseline.json"
    play_guard.capture_baseline(play_repo, baseline_path, run_cmd=_real_run_cmd)

    import shutil

    shutil.rmtree(play_repo / ".git")
    status = play_guard.check(play_repo, baseline_path, run_cmd=_real_run_cmd)

    assert status == "error"


# ----------------------------------------------------------------------
# 5. Idempotent capture: recapturing an unchanged repo produces the same
#    manifest content.
# ----------------------------------------------------------------------


def test_capture_baseline_idempotent(tmp_path):
    play_repo = tmp_path / "play"
    play_repo.mkdir()
    (play_repo / "app.conf").write_text("x=1")
    baseline_path = tmp_path / "baseline.json"

    play_guard.capture_baseline(play_repo, baseline_path, run_cmd=_real_run_cmd)
    first = baseline_path.read_text()
    play_guard.capture_baseline(play_repo, baseline_path, run_cmd=_real_run_cmd)
    second = baseline_path.read_text()

    assert first == second
