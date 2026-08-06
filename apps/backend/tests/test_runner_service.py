"""
Tests for app.services.runner_service.execute_submission.

These exercise the real runner subprocess (runner/runner.py) end to end —
no mocking of subprocess or the runner script — since the whole point of
this milestone is confidence that the subprocess protocol actually works,
not just that the service layer calls subprocess.run correctly.
"""

from app.services.runner_service import execute_submission

SHORT_TIMEOUT = 1.0  # keep the timeout test fast


def test_correct_function_returns_success():
    result = execute_submission(
        source_code="def add_all(xs):\n    return sum(xs)\n",
        function_name="add_all",
        input_=[1, 2, 3, 4],
    )
    assert result.status == "success"
    assert result.return_value == 10
    assert result.exception_type is None
    assert result.exception_message is None
    assert result.runtime_ms is not None
    assert result.runtime_ms >= 0


def test_syntax_error_is_reported():
    result = execute_submission(
        source_code="def broken(xs:\n    return xs\n",
        function_name="broken",
        input_=[1, 2, 3],
    )
    assert result.status == "syntax_error"
    assert result.exception_type == "SyntaxError"
    assert result.exception_message  # non-empty, some message present
    assert result.return_value is None


def test_runtime_exception_is_reported():
    result = execute_submission(
        source_code="def crashes(xs):\n    return xs[999]\n",
        function_name="crashes",
        input_=[1, 2, 3],
    )
    assert result.status == "runtime_error"
    assert result.exception_type == "IndexError"
    assert result.return_value is None


def test_infinite_loop_times_out():
    result = execute_submission(
        source_code="def spins(xs):\n    while True:\n        pass\n",
        function_name="spins",
        input_=[1],
        timeout_seconds=SHORT_TIMEOUT,
    )
    assert result.status == "timeout"
    assert result.exception_type == "TimeoutError"


def test_function_not_found_is_load_error():
    result = execute_submission(
        source_code="def some_other_name(xs):\n    return xs\n",
        function_name="expected_name",
        input_=[1, 2, 3],
    )
    assert result.status == "load_error"
    assert result.exception_type == "FunctionNotFoundError"
    assert "expected_name" in result.exception_message


def test_import_error_is_load_error_not_syntax_error():
    """Module-level failures (bad imports, NameErrors at top level) compile
    fine but fail while executing — a different category from a syntax
    error, which never gets that far."""
    result = execute_submission(
        source_code="import this_module_does_not_exist_xyz\n\ndef foo(xs):\n    return xs\n",
        function_name="foo",
        input_=[1],
    )
    assert result.status == "load_error"
    assert result.exception_type == "ModuleNotFoundError"


def test_non_serializable_return_value():
    result = execute_submission(
        source_code="def foo(xs):\n    return set(xs)\n",
        function_name="foo",
        input_=[1, 2, 3],
    )
    assert result.status == "unserializable_output"
    assert result.exception_type == "set"
    assert result.return_value is None


def test_printed_output_is_captured():
    result = execute_submission(
        source_code=(
            "def foo(xs):\n"
            "    print('debug:', xs)\n"
            "    return len(xs)\n"
        ),
        function_name="foo",
        input_=[1, 2, 3],
    )
    assert result.status == "success"
    assert "debug:" in result.stdout


def test_empty_list_input():
    result = execute_submission(
        source_code="def foo(xs):\n    return len(xs)\n",
        function_name="foo",
        input_=[],
    )
    assert result.status == "success"
    assert result.return_value == 0


def test_negative_integers_input():
    result = execute_submission(
        source_code="def foo(xs):\n    return sum(xs)\n",
        function_name="foo",
        input_=[-10, -1, 5],
    )
    assert result.status == "success"
    assert result.return_value == -6


# --- Additional coverage: safety properties beyond the required list -------


def test_huge_printed_output_is_truncated():
    result = execute_submission(
        source_code=(
            "def foo(xs):\n"
            "    for _ in range(200000):\n"
            "        print('x' * 100)\n"
            "    return 1\n"
        ),
        function_name="foo",
        input_=[1],
    )
    assert result.status == "success"
    assert len(result.stdout) < 5_000
    assert "truncated" in result.stdout


def test_exception_message_does_not_leak_host_paths():
    result = execute_submission(
        source_code="def foo(xs):\n    raise ValueError('boom: ' + str(xs))\n",
        function_name="foo",
        input_=[1, 2],
    )
    assert result.status == "runtime_error"
    assert "/home/" not in result.exception_message
    assert "runner.py" not in result.exception_message


def test_not_callable_attribute_is_load_error():
    result = execute_submission(
        source_code="foo = 42\n",
        function_name="foo",
        input_=[1],
    )
    assert result.status == "load_error"
    assert result.exception_type == "NotCallableError"


def test_infinity_return_value_is_unserializable():
    result = execute_submission(
        source_code="def foo(xs):\n    return float('inf')\n",
        function_name="foo",
        input_=[1],
    )
    assert result.status == "unserializable_output"


# --- Regression: the find_max mismatch that showed up as INCONCLUSIVE --------
#
# Root cause was infrastructural: in the worker container the runner script
# wasn't on disk (runner/ wasn't copied into the image), so every execution
# returned internal_error and the analysis reported INCONCLUSIVE instead of a
# real FAIL. These tests run the REAL runner end to end, so they prove both
# implementations actually execute and that this exact input is a confirmed
# behavioral difference (candidate 0 vs reference -2).

_FIND_MAX_CANDIDATE = (
    "def find_max(values):\n"
    "    maximum = 0\n"
    "    for value in values:\n"
    "        if value > maximum:\n"
    "            maximum = value\n"
    "    return maximum\n"
)
_FIND_MAX_REFERENCE = (
    "def find_max(values):\n"
    "    if not values:\n"
    "        raise ValueError('List cannot be empty')\n"
    "    return max(values)\n"
)


def test_find_max_candidate_returns_zero_on_all_negatives():
    result = execute_submission(
        source_code=_FIND_MAX_CANDIDATE,
        function_name="find_max",
        input_=[-5, -2, -9],
    )
    assert result.status == "success"
    assert result.return_value == 0
    assert result.runtime_ms is not None


def test_find_max_reference_returns_actual_max_on_all_negatives():
    result = execute_submission(
        source_code=_FIND_MAX_REFERENCE,
        function_name="find_max",
        input_=[-5, -2, -9],
    )
    assert result.status == "success"
    assert result.return_value == -2
    assert result.runtime_ms is not None


def test_find_max_mismatch_is_a_confirmed_difference_not_inconclusive():
    # End to end through the comparison rules: this must be a real mismatch
    # (match=False) and NOT an internal error (is_internal_error=False), i.e.
    # a confirmed FAIL rather than INCONCLUSIVE.
    from app.services.comparison_rules import compare_execution_results

    candidate = execute_submission(
        source_code=_FIND_MAX_CANDIDATE, function_name="find_max", input_=[-5, -2, -9]
    )
    reference = execute_submission(
        source_code=_FIND_MAX_REFERENCE, function_name="find_max", input_=[-5, -2, -9]
    )
    from app.services.comparison_rules import to_execution_result

    match, is_internal_error = compare_execution_results(
        to_execution_result(candidate), to_execution_result(reference)
    )
    assert is_internal_error is False, "must not be an internal/harness error"
    assert match is False, "0 vs -2 must be a confirmed behavioral difference"


def test_missing_runner_script_surfaces_explicit_internal_error(monkeypatch):
    # If the runner script can't be located (the exact container failure mode:
    # runner/ not shipped in the image), execution must surface a SPECIFIC
    # internal_error with a diagnosable message — never a silent success or a
    # bare inconclusive with no detail.
    from pathlib import Path

    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(
        settings, "runner_script_path", Path("/nonexistent/runner.py"), raising=False
    )

    result = execute_submission(
        source_code=_FIND_MAX_CANDIDATE,
        function_name="find_max",
        input_=[-5, -2, -9],
    )
    assert result.status == "internal_error"
    # The message must be specific enough to diagnose (empty output + the
    # interpreter's stderr about the missing file), not a generic label.
    assert result.exception_type in {"EmptyRunnerOutput", "MalformedRunnerOutput"}
    assert result.exception_message
    assert result.return_value is None


# --- Regression: second_largest [5, 5, 3] end to end -------------------------
#
# The built-in example. Candidate sorted(values)[-2] returns 5 on [5, 5, 3];
# the deduping reference returns 3. Runs the REAL runner so this proves both
# implementations execute and the input is a genuine behavioral difference.

_SECOND_LARGEST_CANDIDATE = "def second_largest(values):\n    return sorted(values)[-2]\n"
_SECOND_LARGEST_REFERENCE = (
    "def second_largest(values):\n"
    "    unique = sorted(set(values))\n"
    "    if len(unique) < 2:\n"
    "        raise ValueError('Need at least two distinct values')\n"
    "    return unique[-2]\n"
)


def test_second_largest_candidate_returns_five_on_5_5_3():
    result = execute_submission(
        source_code=_SECOND_LARGEST_CANDIDATE,
        function_name="second_largest",
        input_=[5, 5, 3],
    )
    assert result.status == "success"
    assert result.return_value == 5


def test_second_largest_reference_returns_three_on_5_5_3():
    result = execute_submission(
        source_code=_SECOND_LARGEST_REFERENCE,
        function_name="second_largest",
        input_=[5, 5, 3],
    )
    assert result.status == "success"
    assert result.return_value == 3


def test_second_largest_5_5_3_is_confirmed_difference_end_to_end():
    from app.services.comparison_rules import (
        compare_execution_results,
        to_execution_result,
    )

    candidate = execute_submission(
        source_code=_SECOND_LARGEST_CANDIDATE,
        function_name="second_largest",
        input_=[5, 5, 3],
    )
    reference = execute_submission(
        source_code=_SECOND_LARGEST_REFERENCE,
        function_name="second_largest",
        input_=[5, 5, 3],
    )
    match, is_internal_error = compare_execution_results(
        to_execution_result(candidate), to_execution_result(reference)
    )
    assert is_internal_error is False
    assert match is False  # 5 vs 3 is a confirmed behavioral difference
