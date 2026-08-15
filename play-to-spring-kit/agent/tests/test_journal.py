"""tools/journal.py tests: append-only NDJSON, idempotent folding via
journal_offsets, torn-line salvage. Lifted discipline from the
play-to-springboot plugin's journal design -- see the module docstring for
the two bugs (double-counting on re-fold, welded torn lines) this avoids."""

import json

from agent.tools import journal


def test_write_entry_creates_journal_file(tmp_path):
    journal.write_entry(tmp_path, "service", {"unit": "service", "action": "migrated", "count": 3})

    path = journal.journal_path(tmp_path, "service")
    assert path.is_file()
    assert "migrated" in path.read_text()


def test_journal_path_sanitizes_slashes(tmp_path):
    path = journal.journal_path(tmp_path, "controllers/admin")
    assert "/" not in path.name
    assert path.name == "controllers_admin.ndjson"


def test_journal_path_empty_unit_id_defaults(tmp_path):
    path = journal.journal_path(tmp_path, "")
    assert path.name == "default.ndjson"


def test_fold_journal_returns_all_entries_first_time(tmp_path):
    journal.write_entry(tmp_path, "service", {"unit": "service", "action": "migrated", "count": 3})
    journal.write_entry(tmp_path, "service", {"unit": "service", "action": "migrated", "count": 2})

    new_entries, offsets = journal.fold_journal(tmp_path, "service", {})

    assert len(new_entries) == 2
    assert journal.fold_migrated_count(new_entries) == 5
    assert offsets["service.ndjson"] == 2


def test_fold_journal_idempotent_second_fold_returns_only_new_entries(tmp_path):
    """The plugin's bug: a re-dispatched three-file layer reported nine files
    migrated because a naive re-fold recounted lines already folded. Here:
    fold once, persist the offset, write more, fold again -- the second
    fold must return ONLY the newly-appended entries."""
    journal.write_entry(tmp_path, "service", {"unit": "service", "action": "migrated", "count": 3})
    first_entries, offsets = journal.fold_journal(tmp_path, "service", {})
    assert journal.fold_migrated_count(first_entries) == 3

    journal.write_entry(tmp_path, "service", {"unit": "service", "action": "migrated", "count": 2})
    second_entries, offsets = journal.fold_journal(tmp_path, "service", offsets)

    assert len(second_entries) == 1
    assert journal.fold_migrated_count(second_entries) == 2
    assert journal.fold_migrated_count(first_entries + second_entries) == 5


def test_fold_journal_not_mutating_input_offsets_dict(tmp_path):
    journal.write_entry(tmp_path, "service", {"unit": "service", "action": "migrated", "count": 1})
    original = {}
    journal.fold_journal(tmp_path, "service", original)

    assert original == {}  # caller's dict untouched; must persist the RETURNED one


def test_fold_journal_repeated_fold_without_persisting_offset_is_idempotent(tmp_path):
    journal.write_entry(tmp_path, "service", {"unit": "service", "action": "migrated", "count": 3})
    entries_a, _ = journal.fold_journal(tmp_path, "service", {})
    entries_b, _ = journal.fold_journal(tmp_path, "service", {})  # same input offsets both times

    assert entries_a == entries_b


def test_fold_journal_shorter_than_offset_replays_from_top(tmp_path):
    """A journal shorter than its recorded offset cannot be the file that
    offset was measured against (fresh/reset journal reusing the same unit
    id) -- must replay from the top rather than returning nothing."""
    journal.write_entry(tmp_path, "service", {"unit": "service", "action": "migrated", "count": 1})
    journal.write_entry(tmp_path, "service", {"unit": "service", "action": "migrated", "count": 1})
    _entries, offsets = journal.fold_journal(tmp_path, "service", {})
    assert offsets["service.ndjson"] == 2

    # Simulate a reset journal (fewer entries than the offset claims).
    path = journal.journal_path(tmp_path, "service")
    path.write_text("\n" + json.dumps({"unit": "service", "action": "migrated", "count": 5}))

    entries, new_offsets = journal.fold_journal(tmp_path, "service", offsets)

    assert len(entries) == 1
    assert journal.fold_migrated_count(entries) == 5


def test_fold_journal_missing_file_returns_empty(tmp_path):
    entries, offsets = journal.fold_journal(tmp_path, "nonexistent", {})
    assert entries == []
    assert offsets["nonexistent.ndjson"] == 0


def test_truncated_final_line_is_skipped_not_counted(tmp_path):
    path = journal.journal_path(tmp_path, "service")
    path.parent.mkdir(parents=True, exist_ok=True)
    complete = json.dumps({"unit": "service", "action": "migrated", "count": 3})
    truncated = '{"unit": "service", "action": "mig'
    path.write_text(f"\n{complete}\n{truncated}")

    entries, _ = journal.fold_journal(tmp_path, "service", {})

    assert len(entries) == 1
    assert journal.fold_migrated_count(entries) == 3


def test_welded_line_salvages_trailing_entry(tmp_path):
    """A torn write immediately followed by a new append welds two entries
    onto one line with no separator. The salvage must recover the second
    (trailing) entry, not silently drop both."""
    path = journal.journal_path(tmp_path, "service")
    path.parent.mkdir(parents=True, exist_ok=True)
    tail_of_torn = '"count": 1}'  # fragment of a torn-but-otherwise-complete-looking write
    second_entry = json.dumps({"unit": "service", "action": "migrated", "count": 4})
    welded = tail_of_torn + second_entry
    path.write_text(f"\n{welded}")

    entries, _ = journal.fold_journal(tmp_path, "service", {})

    assert len(entries) == 1
    assert entries[0]["count"] == 4


def test_welded_line_nested_object_not_mistaken_for_second_entry(tmp_path):
    """A nested object inside one well-formed entry (no "action" key at that
    nesting level) must never be salvaged as if it were a second entry."""
    path = journal.journal_path(tmp_path, "service")
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = json.dumps({"unit": "service", "action": "migrated", "count": 3, "detail": {"file": "Foo.java"}})
    path.write_text(f"\n{entry}")

    entries, _ = journal.fold_journal(tmp_path, "service", {})

    assert len(entries) == 1
    assert entries[0]["count"] == 3


def test_blank_lines_ignored(tmp_path):
    path = journal.journal_path(tmp_path, "service")
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = json.dumps({"unit": "service", "action": "migrated", "count": 1})
    path.write_text(f"\n\n{entry}\n\n")

    entries, _ = journal.fold_journal(tmp_path, "service", {})

    assert len(entries) == 1


def test_fold_migrated_count_ignores_non_migrated_actions(tmp_path):
    journal.write_entry(tmp_path, "service", {"unit": "service", "action": "migrated", "count": 3})
    journal.write_entry(tmp_path, "service", {"unit": "service", "action": "compiled", "error_count": 7})
    journal.write_entry(tmp_path, "service", {"unit": "service", "action": "failed", "file": "X.java"})

    entries, _ = journal.fold_journal(tmp_path, "service", {})

    assert journal.fold_migrated_count(entries) == 3
