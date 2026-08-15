"""tools/gaps.py tests: append-only NDJSON recording, closed kind set,
read-back. Mirrors test_journal.py's discipline (same append-only design)."""

import json

from agent.tools import gaps


def test_record_gap_writes_entry(tmp_path):
    err = gaps.record_gap(tmp_path, "unhandled_idiom", "akka.actor.UntypedActor", "hand-ported to @Async")

    assert err is None
    entries = gaps.read_gaps(tmp_path)
    assert len(entries) == 1
    assert entries[0]["kind"] == "unhandled_idiom"
    assert entries[0]["subject"] == "akka.actor.UntypedActor"
    assert entries[0]["what_i_did"] == "hand-ported to @Async"
    assert entries[0]["role"] == "dev"  # default


def test_record_gap_rejects_unknown_kind(tmp_path):
    err = gaps.record_gap(tmp_path, "made_up_kind", "x", "y")

    assert err is not None
    assert "made_up_kind" in err
    assert gaps.read_gaps(tmp_path) == []  # nothing written on rejection


def test_record_gap_all_closed_set_kinds_accepted(tmp_path):
    for kind in gaps.GAP_KINDS:
        err = gaps.record_gap(tmp_path, kind, "subject", "did something")
        assert err is None, f"{kind} should be accepted"

    assert len(gaps.read_gaps(tmp_path)) == len(gaps.GAP_KINDS)


def test_record_gap_optional_fields_omitted_when_not_given(tmp_path):
    gaps.record_gap(tmp_path, "tool_error", "migrate-app", "crashed")

    entry = gaps.read_gaps(tmp_path)[0]
    assert "blind_tier" not in entry
    assert "layer" not in entry


def test_record_gap_optional_fields_included_when_given(tmp_path):
    gaps.record_gap(tmp_path, "tier_blind_spot", "x", "y", blind_tier="T2", layer="service")

    entry = gaps.read_gaps(tmp_path)[0]
    assert entry["blind_tier"] == "T2"
    assert entry["layer"] == "service"


def test_record_gap_role_override(tmp_path):
    gaps.record_gap(tmp_path, "layout_surprise", "x", "y", role="architect")

    assert gaps.read_gaps(tmp_path)[0]["role"] == "architect"


def test_read_gaps_missing_file_returns_empty(tmp_path):
    assert gaps.read_gaps(tmp_path) == []


def test_read_gaps_accumulates_across_calls(tmp_path):
    gaps.record_gap(tmp_path, "unmapped_dependency", "com.acme:foo:1.0", "left unmapped")
    gaps.record_gap(tmp_path, "boot_failure", "spring-boot:run", "never started")

    entries = gaps.read_gaps(tmp_path)
    assert len(entries) == 2
    assert {e["kind"] for e in entries} == {"unmapped_dependency", "boot_failure"}


def test_gaps_path_under_migration_dir(tmp_path):
    path = gaps.gaps_path(tmp_path)
    assert path == tmp_path / ".migration" / "gaps.jsonl"


def test_read_gaps_skips_truncated_final_line(tmp_path):
    path = gaps.gaps_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    complete = json.dumps({"kind": "tool_error", "subject": "x", "what_i_did": "y", "role": "dev"})
    truncated = '{"kind": "boot_fail'
    path.write_text(f"\n{complete}\n{truncated}")

    entries = gaps.read_gaps(tmp_path)

    assert len(entries) == 1
    assert entries[0]["kind"] == "tool_error"


def test_read_gaps_ignores_entries_without_kind(tmp_path):
    path = gaps.gaps_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n" + json.dumps({"not_a_gap": True}))

    assert gaps.read_gaps(tmp_path) == []
