from pathlib import Path

import pytest

from agent.tools.fs import FsJail


@pytest.fixture
def repos(tmp_path):
    spring = tmp_path / "spring"
    play = tmp_path / "play"
    (spring / "src" / "main" / "java").mkdir(parents=True)
    (play / "app").mkdir(parents=True)
    (spring / "pom.xml").write_text("<project/>")
    (spring / "src" / "main" / "java" / "A.java").write_text("class A {}")
    (play / "app" / "B.java").write_text("class B {}")
    (tmp_path / "secret.txt").write_text("nope")
    return spring, play


def get_tool(jail: FsJail, name: str):
    return next(t for t in jail.build_tools() if t.name == name)


def test_read_spring_and_play(repos):
    spring, play = repos
    jail = FsJail(spring, play)
    read = get_tool(jail, "read_file")
    assert "class A" in read.invoke({"path": "src/main/java/A.java"})
    assert "class B" in read.invoke({"path": str(play / "app" / "B.java")})


def test_read_outside_roots_blocked(repos):
    spring, play = repos
    jail = FsJail(spring, play)
    read = get_tool(jail, "read_file")
    with pytest.raises(PermissionError):
        read.invoke({"path": str(spring.parent / "secret.txt")})


def test_write_jail(repos):
    spring, play = repos
    jail = FsJail(spring, play)
    write = get_tool(jail, "write_file")
    # allowed: src/** and pom.xml
    write.invoke({"path": "src/main/java/New.java", "content": "class New {}"})
    write.invoke({"path": "pom.xml", "content": "<project></project>"})
    # blocked: play repo, spring repo root, outside
    with pytest.raises(PermissionError):
        write.invoke({"path": str(play / "app" / "B.java"), "content": "x"})
    with pytest.raises(PermissionError):
        write.invoke({"path": "README.md", "content": "x"})
    assert len(jail.edited_files) == 2


def test_write_traversal_blocked(repos):
    spring, play = repos
    jail = FsJail(spring, play)
    write = get_tool(jail, "write_file")
    with pytest.raises(PermissionError):
        write.invoke({"path": "src/../../escape.java", "content": "x"})


def test_str_replace_uniqueness(repos):
    spring, play = repos
    jail = FsJail(spring, play)
    replace = get_tool(jail, "str_replace")
    target = spring / "src" / "main" / "java" / "A.java"
    target.write_text("aa bb aa")
    assert "error" in replace.invoke({"path": str(target), "old": "aa", "new": "cc"})
    assert "edited" in replace.invoke({"path": str(target), "old": "bb", "new": "cc"})
    assert target.read_text() == "aa cc aa"


def test_str_replace_ambiguous_reports_line_numbers(repos):
    """M6 Task 3: the model gets line numbers, not just a bare occurs-N-times
    message, so it can disambiguate with real context instead of guessing."""
    spring, play = repos
    jail = FsJail(spring, play)
    replace = get_tool(jail, "str_replace")
    target = spring / "src" / "main" / "java" / "A.java"
    target.write_text("line1\nfoo\nline3\nfoo\nline5")
    result = replace.invoke({"path": str(target), "old": "foo", "new": "bar"})
    assert "lines 2, 4" in result


def test_dry_run_write_file_does_not_touch_disk(repos):
    spring, play = repos
    jail = FsJail(spring, play, dry_run=True)
    write = get_tool(jail, "write_file")
    target = spring / "src" / "main" / "java" / "New.java"
    result = write.invoke({"path": "src/main/java/New.java", "content": "class New {}"})
    assert "dry-run" in result
    assert not target.exists()
    assert jail.edited_files  # still recorded, so the caller sees intent


def test_dry_run_str_replace_does_not_touch_disk(repos):
    spring, play = repos
    jail = FsJail(spring, play, dry_run=True)
    replace = get_tool(jail, "str_replace")
    target = spring / "src" / "main" / "java" / "A.java"
    original = target.read_text()
    result = replace.invoke({"path": str(target), "old": "class A", "new": "class Z"})
    assert "dry-run" in result
    assert target.read_text() == original


def test_read_file_head_tail_truncation(repos):
    """A single hard cut drops whichever half didn't fit -- verify both ends
    of a large file survive truncation, not just the head."""
    spring, play = repos
    jail = FsJail(spring, play)
    read = get_tool(jail, "read_file")
    target = spring / "src" / "main" / "java" / "Big.java"
    target.write_text("HEAD_MARKER\n" + ("x" * 100_000) + "\nTAIL_MARKER")
    result = read.invoke({"path": "src/main/java/Big.java"})
    assert "HEAD_MARKER" in result
    assert "TAIL_MARKER" in result
    assert "omitted" in result


def test_search_finds_matches_with_line_numbers(repos):
    spring, play = repos
    jail = FsJail(spring, play)
    search = get_tool(jail, "search")
    (spring / "src" / "main" / "java" / "A.java").write_text("class A {\n    void foo() {}\n}\n")
    result = search.invoke({"pattern": r"void foo", "path": "src/main/java"})
    assert "A.java:2:" in result
    assert "void foo" in result


def test_search_no_matches(repos):
    spring, play = repos
    jail = FsJail(spring, play)
    search = get_tool(jail, "search")
    result = search.invoke({"pattern": "NoSuchSymbolAnywhere", "path": "src/main/java"})
    assert result == "no matches"


def test_search_invalid_regex(repos):
    spring, play = repos
    jail = FsJail(spring, play)
    search = get_tool(jail, "search")
    result = search.invoke({"pattern": "(unclosed", "path": "src/main/java"})
    assert "error" in result


def test_search_respects_max_results(repos):
    spring, play = repos
    jail = FsJail(spring, play)
    search = get_tool(jail, "search")
    java_dir = spring / "src" / "main" / "java"
    for i in range(5):
        (java_dir / f"F{i}.java").write_text("MATCH_ME\n")
    result = search.invoke({"pattern": "MATCH_ME", "path": "src/main/java", "max_results": 2})
    match_lines = [line for line in result.splitlines() if "MATCH_ME" in line and ".java:" in line]
    assert len(match_lines) == 2
    assert "truncated" in result


def test_search_reaches_play_repo_readonly(repos):
    spring, play = repos
    jail = FsJail(spring, play)
    search = get_tool(jail, "search")
    result = search.invoke({"pattern": "class B", "path": str(play / "app")})
    assert "B.java" in result


def test_search_outside_roots_blocked(repos):
    spring, play = repos
    jail = FsJail(spring, play)
    search = get_tool(jail, "search")
    with pytest.raises(PermissionError):
        search.invoke({"pattern": "x", "path": str(spring.parent)})


def test_search_skips_target_and_git_dirs(repos):
    spring, play = repos
    jail = FsJail(spring, play)
    search = get_tool(jail, "search")
    skip_dir = spring / "target" / "classes"
    skip_dir.mkdir(parents=True)
    (skip_dir / "Generated.java").write_text("SKIP_ME_MARKER\n")
    result = search.invoke({"pattern": "SKIP_ME_MARKER", "path": "."})
    assert result == "no matches"


# ----------------------------------------------------------------------
# M6 Task 8: record_gap and flag_for_manual_review's gap side-effect.
# ----------------------------------------------------------------------


def test_record_gap_writes_to_gaps_jsonl(repos):
    from agent.tools.gaps import read_gaps

    spring, play = repos
    jail = FsJail(spring, play)
    record_gap = get_tool(jail, "record_gap")

    result = record_gap.invoke(
        {"kind": "unhandled_idiom", "subject": "akka.actor.UntypedActor", "what_i_did": "hand-ported to @Async"}
    )

    assert "recorded gap" in result
    entries = read_gaps(spring)
    assert len(entries) == 1
    assert entries[0]["subject"] == "akka.actor.UntypedActor"


def test_record_gap_invalid_kind_returns_error_not_written(repos):
    from agent.tools.gaps import read_gaps

    spring, play = repos
    jail = FsJail(spring, play)
    record_gap = get_tool(jail, "record_gap")

    result = record_gap.invoke({"kind": "not_a_real_kind", "subject": "x", "what_i_did": "y"})

    assert "error" in result
    assert read_gaps(spring) == []


def test_record_gap_never_stops_the_loop(repos):
    """Unlike flag_for_manual_review, record_gap's output must never start
    with MANUAL_REVIEW_PREFIX -- run_tool_loop only stops early on that
    prefix, and this tool must not accidentally trigger it."""
    from agent.tools.fs import MANUAL_REVIEW_PREFIX

    spring, play = repos
    jail = FsJail(spring, play)
    record_gap = get_tool(jail, "record_gap")

    result = record_gap.invoke({"kind": "tool_error", "subject": "x", "what_i_did": "y"})

    assert not result.startswith(MANUAL_REVIEW_PREFIX)


def test_flag_for_manual_review_also_records_a_gap(repos):
    """A stop is never a silent loss of the diagnosis -- the agent's own
    explanation must land in gaps.jsonl too, not only in the transient
    manual_review_reason."""
    from agent.tools.gaps import read_gaps

    spring, play = repos
    jail = FsJail(spring, play)
    flag = get_tool(jail, "flag_for_manual_review")

    flag.invoke({"reason": "Akka actor with no Spring equivalent"})

    entries = read_gaps(spring)
    assert len(entries) == 1
    assert entries[0]["kind"] == "agent_improvised"
    assert entries[0]["what_i_did"] == "Akka actor with no Spring equivalent"


def test_flag_for_manual_review_still_returns_manual_review_prefix(repos):
    from agent.tools.fs import MANUAL_REVIEW_PREFIX

    spring, play = repos
    jail = FsJail(spring, play)
    flag = get_tool(jail, "flag_for_manual_review")

    result = flag.invoke({"reason": "no equivalent"})

    assert result.startswith(MANUAL_REVIEW_PREFIX)


# ----------------------------------------------------------------------
# M6 Task 9: per-phase tool scoping. Capability removal over prompt
# instruction -- a phase that never needs write_file doesn't get the tool.
# ----------------------------------------------------------------------


def _tool_names(jail: FsJail, phase: str) -> set[str]:
    return {t.name for t in jail.build_tools(phase=phase)}


def test_default_phase_gets_every_tool(repos):
    spring, play = repos
    jail = FsJail(spring, play)
    names = _tool_names(jail, "")
    assert names == {
        "read_file", "write_file", "str_replace", "list_dir", "search", "record_gap", "flag_for_manual_review",
    }


def test_unrecognized_phase_falls_back_to_every_tool(repos):
    """Matches pre-Task-9 behavior: an unknown phase name must not silently
    narrow capability in a way nobody asked for."""
    spring, play = repos
    jail = FsJail(spring, play)
    assert _tool_names(jail, "some_future_phase") == _tool_names(jail, "")


def test_compile_fix_has_no_write_file():
    """The plan's own steer: whole-file rewrite is both the expensive and
    the lossy path -- compile_fix gets str_replace, never write_file."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        jail = FsJail(Path(d))
        names = _tool_names(jail, "compile_fix")
        assert "write_file" not in names
        assert "str_replace" in names


def test_routes_has_no_manual_review_or_gap_tools():
    """Routes only ever adds an annotation line -- never a redesign, so it
    gets no diagnosis/learning tools."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        jail = FsJail(Path(d))
        names = _tool_names(jail, "routes")
        assert "flag_for_manual_review" not in names
        assert "record_gap" not in names
        assert "write_file" not in names


def test_bootstrap_and_architect_get_write_file_not_str_replace():
    """Creating fresh files from scratch -- write_file is the natural tool,
    there's nothing existing yet to str_replace into."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        jail = FsJail(Path(d))
        for phase in ("bootstrap", "architect"):
            names = _tool_names(jail, phase)
            assert "write_file" in names
            assert "str_replace" not in names


def test_config_mapping_keeps_write_file_and_str_replace():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        jail = FsJail(Path(d))
        names = _tool_names(jail, "config_mapping")
        assert "write_file" in names
        assert "str_replace" in names


def test_runtime_wiring_has_manual_review_and_gap_tools():
    """Same rationale as compile_fix -- both hit library/pattern mismatches
    with no clean mapping."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        jail = FsJail(Path(d))
        names = _tool_names(jail, "runtime_wiring")
        assert "flag_for_manual_review" in names
        assert "record_gap" in names
        assert "write_file" not in names


def test_all_scoped_phases_keep_read_file_list_dir_search():
    """Every phase, however narrow, keeps the read-only tools -- scoping
    removes write capability, never the ability to see what's there."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        jail = FsJail(Path(d))
        for phase in ("bootstrap", "architect", "compile_fix", "routes", "config_mapping", "runtime_wiring"):
            names = _tool_names(jail, phase)
            assert {"read_file", "list_dir", "search"} <= names
