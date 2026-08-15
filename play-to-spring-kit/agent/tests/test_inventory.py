"""Inventory discovery + plausibility-check tests (ported from migration_orchestrator.py)."""

from pathlib import Path

from agent import inventory


def _touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("class X {}", encoding="utf-8")


def test_classify_layer():
    assert inventory.classify_layer("app/controllers/Foo.java") == "controller"
    assert inventory.classify_layer("app/services/Foo.java") == "service"
    assert inventory.classify_layer("app/models/Foo.java") == "model"
    assert inventory.classify_layer("app/db/Foo.java") == "manager"
    assert inventory.classify_layer("app/repositories/Foo.java") == "repository"
    assert inventory.classify_layer("app/misc/Foo.java") == "other"


def test_scan_play_java_counts_by_layer(tmp_path):
    _touch(tmp_path / "app" / "controllers" / "A.java")
    _touch(tmp_path / "app" / "services" / "B.java")
    inv = inventory.scan_play_java(tmp_path)
    assert inv["total_java_files"] == 2
    assert inv["by_layer"]["controller"] == 1
    assert inv["by_layer"]["service"] == 1


def test_discover_migration_units_controllers_anchor(tmp_path):
    _touch(tmp_path / "app" / "controllers" / "A.java")
    _touch(tmp_path / "app" / "services" / "B.java")
    units = inventory.discover_migration_units(tmp_path)
    assert {u["discovered_by"] for u in units} == {"controllers_anchor"}
    prefixes = {u["path_prefix"] for u in units}
    assert prefixes == {"controllers", "services"}


def test_discover_migration_units_empty_app_falls_back(tmp_path):
    units = inventory.discover_migration_units(tmp_path)
    assert len(units) == 1
    assert units[0]["discovered_by"] == "empty_app"


def test_merge_discovered_migration_units_preserves_progress():
    existing = [
        {**inventory.default_unit_entry("a", "a", 3), "status": "done", "retry_count": 2},
    ]
    discovered = [
        {"id": "a", "path_prefix": "a", "java_file_count": 5, "discovered_by": "controllers_anchor"},
        {"id": "b", "path_prefix": "b", "java_file_count": 1, "discovered_by": "controllers_anchor"},
    ]
    merged = inventory.merge_discovered_migration_units(existing, discovered)
    by_id = {u["id"]: u for u in merged}
    assert by_id["a"]["status"] == "done"  # progress preserved
    assert by_id["a"]["java_file_count"] == 5  # refreshed from disk
    assert by_id["b"]["status"] == "pending"


def test_merge_discovered_migration_units_keeps_done_unit_no_longer_discovered():
    existing = [{**inventory.default_unit_entry("gone", "gone", 3), "status": "done"}]
    merged = inventory.merge_discovered_migration_units(existing, [])
    assert len(merged) == 1
    assert merged[0]["id"] == "gone"


def test_migration_output_plausible_true_when_expected_zero(tmp_path):
    unit = inventory.default_unit_entry("a", "a", 0)
    plausible, _ = inventory.migration_output_plausible(
        dry_run=False, label="a", unit=unit, source_inventory=None, spring_repo=tmp_path, n_add=0
    )
    assert plausible


def test_migration_output_plausible_false_on_empty_output(tmp_path):
    unit = inventory.default_unit_entry("a", "a", 10)
    plausible, reason = inventory.migration_output_plausible(
        dry_run=False, label="a", unit=unit, source_inventory=None, spring_repo=tmp_path, n_add=0
    )
    assert not plausible
    assert "expects ~10" in reason


def test_migration_output_plausible_true_when_files_migrated(tmp_path):
    unit = inventory.default_unit_entry("a", "a", 10)
    plausible, _ = inventory.migration_output_plausible(
        dry_run=False, label="a", unit=unit, source_inventory=None, spring_repo=tmp_path, n_add=3
    )
    assert plausible
