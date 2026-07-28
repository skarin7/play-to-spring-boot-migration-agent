"""legacy_logic.unmappable_framework_packages: flags 'package X does not exist'
errors for packages (Pekko/Akka) with zero Spring/Jakarta equivalent, so a
stuck compile-fix loop can be told apart from one that just needs more
attempts. See agent/nodes/slice_pipeline.py's _manual_intervention_note."""

from agent.legacy_logic import unmappable_framework_packages


def test_flags_pekko_package():
    errors = [{"file": "HomeController.java", "line": 3, "message": "package org.apache.pekko does not exist"}]
    assert unmappable_framework_packages(errors) == {"HomeController.java": {"org.apache.pekko"}}


def test_flags_akka_package():
    errors = [{"file": "A.java", "line": 1, "message": "package akka.actor does not exist"}]
    assert unmappable_framework_packages(errors) == {"A.java": {"akka.actor"}}


def test_ignores_mappable_missing_dependency_package():
    errors = [{"file": "A.java", "line": 1, "message": "package com.google.common does not exist"}]
    assert unmappable_framework_packages(errors) == {}


def test_ignores_ordinary_symbol_errors():
    errors = [{"file": "A.java", "line": 1, "message": "cannot find symbol X"}]
    assert unmappable_framework_packages(errors) == {}


def test_groups_multiple_packages_per_file():
    errors = [
        {"file": "A.java", "line": 1, "message": "package org.apache.pekko does not exist"},
        {"file": "A.java", "line": 2, "message": "package org.apache.pekko.stream does not exist"},
    ]
    assert unmappable_framework_packages(errors) == {"A.java": {"org.apache.pekko", "org.apache.pekko.stream"}}


def test_no_errors_returns_empty():
    assert unmappable_framework_packages([]) == {}
