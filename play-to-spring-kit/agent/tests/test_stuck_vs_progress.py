"""legacy_logic.py:stuck_vs_progress_reason tests (M6 Task 12): classifies
the SAME comparison is_looping makes, for logging/report purposes only.
Every case here is cross-checked against is_looping's own bool where the
two functions should agree on stuck-vs-not."""

from agent.legacy_logic import is_looping, stuck_vs_progress_reason


def test_no_history():
    assert stuck_vs_progress_reason(["a"], []) == "no_history"
    assert is_looping(["a"], []) is False


def test_identical_to_last():
    fp = ["A.java:1:x"]
    assert stuck_vs_progress_reason(fp, [fp]) == "identical_to_last"
    assert is_looping(fp, [fp]) is True


def test_oscillating():
    fp_a = ["A.java:1:x"]
    fp_b = ["B.java:2:y"]
    # current == history[-2] (two rounds back), history[-1] is different
    assert stuck_vs_progress_reason(fp_a, [fp_a, fp_b]) == "oscillating"
    assert is_looping(fp_a, [fp_a, fp_b]) is True


def test_progressing_fewer_errors():
    prev = ["A.java:1:x", "B.java:2:y", "C.java:3:z"]
    current = ["A.java:1:x"]
    assert stuck_vs_progress_reason(current, [prev]) == "progressing"
    assert is_looping(current, [prev]) is False


def test_error_count_spike():
    prev = ["A.java:1:x"]
    # spike_threshold = max(int(1*1.5+0.999), 1+5) = max(2, 6) = 6
    current = [f"F{i}.java:1:x" for i in range(10)]
    assert stuck_vs_progress_reason(current, [prev]) == "error_count_spike"
    assert is_looping(current, [prev]) is True


def test_different_error_set_same_or_slightly_more_errors():
    """The exact case the plugin's observation is about: a fix landed and
    exposed a DIFFERENT set of errors, not necessarily more of the same
    problem -- must be distinguishable from a genuine stuck loop, and
    is_looping must NOT classify this as looping either."""
    prev = ["A.java:1:x"]
    current = ["B.java:2:y"]  # same count, totally different error
    assert stuck_vs_progress_reason(current, [prev]) == "different_error_set"
    assert is_looping(current, [prev]) is False


def test_different_error_set_at_exact_spike_threshold_boundary():
    prev = ["A.java:1:x"]
    # spike_threshold = 6 -- exactly at threshold is NOT a spike (> not >=)
    current = [f"F{i}.java:1:x" for i in range(6)]
    assert stuck_vs_progress_reason(current, [prev]) == "different_error_set"
    assert is_looping(current, [prev]) is False


def test_identical_when_both_empty():
    """Two empty error lists ARE identical (current == history[-1] fires
    first, matching is_looping's own real behavior) -- this is not the
    prev_n == 0 branch, since that's only reached when current != history[-1]."""
    assert stuck_vs_progress_reason([], [[]]) == "identical_to_last"
    assert is_looping([], [[]]) is True


def test_different_error_set_when_prev_empty_but_current_not():
    assert stuck_vs_progress_reason(["A.java:1:x"], [[]]) == "different_error_set"
    assert is_looping(["A.java:1:x"], [[]]) is False


def test_classification_agrees_with_is_looping_across_many_cases():
    """Property-style cross-check: whenever is_looping says True, the
    classification must be one of the two "stuck" reasons, and whenever it
    says False, the classification must be a "not stuck" reason."""
    stuck_reasons = {"identical_to_last", "oscillating", "error_count_spike"}
    not_stuck_reasons = {"no_history", "progressing", "different_error_set"}

    cases = [
        (["a"], []),
        (["a"], [["a"]]),
        (["a"], [["a"], ["b"]]),
        (["a", "b"], [["a"]]),
        (["a"], [["a", "b", "c"]]),
        ([f"e{i}" for i in range(20)], [["e0"]]),
        (["x"], [[]]),
        ([], [[]]),
    ]
    for current, history in cases:
        looping = is_looping(current, history)
        reason = stuck_vs_progress_reason(current, history)
        if looping:
            assert reason in stuck_reasons, f"{current!r}, {history!r} -> looping=True but reason={reason!r}"
        else:
            assert reason in not_stuck_reasons, f"{current!r}, {history!r} -> looping=False but reason={reason!r}"
