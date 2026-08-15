"""scripts/gap_report.py tests: framework-symbol passthrough, per-install
salt persistence, redaction of non-framework subjects, render output shape.
No network I/O anywhere in this module -- nothing here should ever attempt
one; these tests exist to keep that true, not just to check the happy path.
"""

from agent.scripts import gap_report
from agent.tools import gaps


def test_is_framework_symbol_recognizes_known_prefixes():
    assert gap_report.is_framework_symbol("play.libs.Akka.system")
    assert gap_report.is_framework_symbol("org.springframework.web.bind.annotation.RestController")
    assert gap_report.is_framework_symbol("akka.actor.UntypedActor")
    assert gap_report.is_framework_symbol("jakarta.persistence.Entity")


def test_is_framework_symbol_rejects_user_code():
    assert not gap_report.is_framework_symbol("com.acme.Pricing")
    assert not gap_report.is_framework_symbol("controllers.UserController")


def test_redact_subject_framework_symbol_passes_through():
    result = gap_report.redact_subject("play.libs.Akka.system", "some-salt")
    assert result == "play.libs.Akka.system"


def test_redact_subject_user_code_is_hashed():
    result = gap_report.redact_subject("com.acme.Pricing", "some-salt")
    assert result != "com.acme.Pricing"
    assert result.startswith("<class:")
    assert result.endswith(">")


def test_redact_subject_same_salt_same_subject_same_hash():
    a = gap_report.redact_subject("com.acme.Pricing", "salt-1")
    b = gap_report.redact_subject("com.acme.Pricing", "salt-1")
    assert a == b


def test_redact_subject_different_salt_different_hash():
    """Different installs never collide into one identity."""
    a = gap_report.redact_subject("com.acme.Pricing", "salt-1")
    b = gap_report.redact_subject("com.acme.Pricing", "salt-2")
    assert a != b


def test_get_or_create_salt_persists_across_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    first = gap_report.get_or_create_salt()
    second = gap_report.get_or_create_salt()
    assert first == second


def test_get_or_create_salt_different_config_homes_different_salts(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "install-a"))
    a = gap_report.get_or_create_salt()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "install-b"))
    b = gap_report.get_or_create_salt()
    assert a != b


def test_redact_gap_replaces_subject_and_matching_text_in_what_i_did(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    salt = gap_report.get_or_create_salt()
    gap = {
        "kind": "unhandled_idiom",
        "subject": "com.acme.PricingEngine",
        "what_i_did": "hand-ported com.acme.PricingEngine to a Spring @Service",
        "role": "dev",
    }

    redacted = gap_report.redact_gap(gap, salt)

    assert redacted["subject"] != "com.acme.PricingEngine"
    assert "com.acme.PricingEngine" not in redacted["what_i_did"]
    assert redacted["subject"] in redacted["what_i_did"]


def test_redact_gap_framework_subject_untouched_in_what_i_did(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    salt = gap_report.get_or_create_salt()
    gap = {
        "kind": "unhandled_idiom",
        "subject": "akka.actor.UntypedActor",
        "what_i_did": "hand-ported akka.actor.UntypedActor to @Async",
        "role": "dev",
    }

    redacted = gap_report.redact_gap(gap, salt)

    assert redacted["subject"] == "akka.actor.UntypedActor"
    assert "akka.actor.UntypedActor" in redacted["what_i_did"]


def test_render_no_gaps():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        report = gap_report.render(Path(d))

    assert "No gaps recorded" in report


def test_render_includes_kind_counts_and_detail(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    spring_repo = tmp_path / "spring"
    spring_repo.mkdir()
    gaps.record_gap(spring_repo, "unhandled_idiom", "akka.actor.UntypedActor", "hand-ported to @Async")
    gaps.record_gap(spring_repo, "unmapped_dependency", "com.acme:internal-lib:1.0", "left unmapped")

    report = gap_report.render(spring_repo)

    assert "unhandled_idiom" in report
    assert "unmapped_dependency" in report
    assert "akka.actor.UntypedActor" in report  # framework symbol, passed through
    assert "com.acme:internal-lib:1.0" not in report  # not a recognized framework prefix, must be redacted


def test_render_never_touches_network(tmp_path, monkeypatch):
    """Belt-and-suspenders: patch socket.socket to raise if gap_report ever
    tries to open a connection during render."""
    import socket

    def _raise(*a, **k):
        raise AssertionError("gap_report.render must never touch the network")

    monkeypatch.setattr(socket, "socket", _raise)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    spring_repo = tmp_path / "spring"
    spring_repo.mkdir()
    gaps.record_gap(spring_repo, "tool_error", "x", "y")

    gap_report.render(spring_repo)  # must not raise
