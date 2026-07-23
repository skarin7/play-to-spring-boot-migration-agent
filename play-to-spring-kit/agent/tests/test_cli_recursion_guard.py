"""Regression: hitting the recursion_limit backstop must exit cleanly, not crash."""

from langgraph.errors import GraphRecursionError

from agent import cli


class FakeState:
    next = ()


class ExplodingGraph:
    def get_state(self, config):
        return FakeState()

    def invoke(self, initial, config):
        raise GraphRecursionError("Recursion limit of 5 reached without hitting a stop condition.")


def test_recursion_error_is_caught_and_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "make_checkpointer", lambda config: object())

    class FakeCompiledGraph:
        def compile(self, checkpointer):
            return ExplodingGraph()

    monkeypatch.setattr(cli, "build_graph", lambda config: FakeCompiledGraph())

    exit_code = cli.main(["--spring-repo", str(tmp_path)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "recursion limit" in captured.err.lower()
