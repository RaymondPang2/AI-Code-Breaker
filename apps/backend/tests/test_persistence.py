"""
Tests for database persistence: the repository layer, the persistence
service, and the GET endpoints. All run against the isolated SQLite test
database configured in conftest.py.
"""

import uuid

import pytest

from app.repositories import analysis_repository as repo
from app.schemas.submission import SubmissionAnalysisResponse, SubmissionRequest
from app.services import persistence_service


# --- Repository layer -------------------------------------------------------


def test_create_and_get_submission(db_session):
    submission = repo.create_submission(
        db_session,
        function_name="f",
        specification="does a thing",
        candidate_code="def f(xs):\n    return xs\n",
        reference_code="def f(xs):\n    return xs\n",
    )
    db_session.commit()

    fetched = repo.get_submission(db_session, submission.id)
    assert fetched is not None
    assert fetched.function_name == "f"
    assert isinstance(fetched.id, uuid.UUID)
    assert fetched.created_at is not None


def test_get_missing_submission_returns_none(db_session):
    assert repo.get_submission(db_session, uuid.uuid4()) is None


def test_execution_role_uniqueness_is_enforced(db_session):
    submission = repo.create_submission(
        db_session, function_name="f", specification="s",
        candidate_code="c", reference_code="r",
    )
    run = repo.create_analysis_run(
        db_session, submission_id=submission.id, status="completed",
        total_tests=1, passed_tests=0, failed_tests=1,
        elapsed_seconds=None, seed=None, configuration={},
    )
    tc = repo.create_test_case(
        db_session, submission_id=submission.id, input_values=[1],
        category="manual", source="manual", reason="r",
    )
    repo.create_execution(
        db_session, analysis_run_id=run.id, test_case_id=tc.id, role="candidate",
        normalized_result={"status": "success"}, runtime_ms=1.0, timed_out=False,
    )
    repo.create_execution(
        db_session, analysis_run_id=run.id, test_case_id=tc.id, role="candidate",
        normalized_result={"status": "success"}, runtime_ms=1.0, timed_out=False,
    )
    with pytest.raises(Exception):  # IntegrityError from the unique constraint
        db_session.commit()


def test_execution_role_check_constraint_rejects_invalid_value(db_session):
    """Regression: the DB must reject any role other than
    candidate/reference, independent of application-level correctness."""
    submission = repo.create_submission(
        db_session, function_name="f", specification="s",
        candidate_code="c", reference_code="r",
    )
    run = repo.create_analysis_run(
        db_session, submission_id=submission.id, status="completed",
        total_tests=1, passed_tests=0, failed_tests=1,
        elapsed_seconds=None, seed=None, configuration={},
    )
    tc = repo.create_test_case(
        db_session, submission_id=submission.id, input_values=[1],
        category="manual", source="manual", reason="r",
    )
    repo.create_execution(
        db_session, analysis_run_id=run.id, test_case_id=tc.id, role="banana",
        normalized_result={"status": "success"}, runtime_ms=1.0, timed_out=False,
    )
    with pytest.raises(Exception):  # IntegrityError from ck_execution_role_valid
        db_session.commit()


def test_execution_role_check_constraint_allows_valid_values(db_session):
    submission = repo.create_submission(
        db_session, function_name="f", specification="s",
        candidate_code="c", reference_code="r",
    )
    run = repo.create_analysis_run(
        db_session, submission_id=submission.id, status="completed",
        total_tests=1, passed_tests=0, failed_tests=1,
        elapsed_seconds=None, seed=None, configuration={},
    )
    tc = repo.create_test_case(
        db_session, submission_id=submission.id, input_values=[1],
        category="manual", source="manual", reason="r",
    )
    repo.create_execution(
        db_session, analysis_run_id=run.id, test_case_id=tc.id, role="candidate",
        normalized_result={"status": "success"}, runtime_ms=1.0, timed_out=False,
    )
    repo.create_execution(
        db_session, analysis_run_id=run.id, test_case_id=tc.id, role="reference",
        normalized_result={"status": "success"}, runtime_ms=1.0, timed_out=False,
    )
    db_session.commit()  # must not raise
    stored = repo.get_analysis_run(db_session, run.id)
    assert {e.role for e in stored.executions} == {"candidate", "reference"}
    sub_a = repo.create_submission(
        db_session, function_name="a", specification="s", candidate_code="c", reference_code="r"
    )
    sub_b = repo.create_submission(
        db_session, function_name="b", specification="s", candidate_code="c", reference_code="r"
    )
    run_a = repo.create_analysis_run(
        db_session, submission_id=sub_a.id, status="completed",
        total_tests=0, passed_tests=0, failed_tests=0,
        elapsed_seconds=None, seed=None, configuration={},
    )
    db_session.commit()

    # run_a belongs to sub_a, not sub_b.
    assert repo.get_analysis_run_for_submission(db_session, sub_a.id, run_a.id) is not None
    assert repo.get_analysis_run_for_submission(db_session, sub_b.id, run_a.id) is None


# --- Persistence service ----------------------------------------------------


def _analysis_with_bug() -> tuple[SubmissionRequest, SubmissionAnalysisResponse]:
    from app.schemas.submission import FunctionExecutionResult, TestComparisonResult

    request = SubmissionRequest(
        function_name="second_largest",
        specification="second largest distinct value",
        candidate_code="def second_largest(v):\n    return sorted(v)[-2]\n",
        reference_code="def second_largest(v):\n    return sorted(set(v))[-2]\n",
        test_inputs=[[1, 2, 3], [5, 5, 5]],
    )
    passing = TestComparisonResult(
        input=[1, 2, 3], source="manual", category="manual", reason="r",
        candidate=FunctionExecutionResult(status="success", returned_value=2),
        reference=FunctionExecutionResult(status="success", returned_value=2),
        match=True, internal_error=False,
    )
    failing = TestComparisonResult(
        input=[5, 5, 5], source="manual", category="manual", reason="r",
        candidate=FunctionExecutionResult(status="success", returned_value=5),
        reference=FunctionExecutionResult(status="runtime_error", exception_type="IndexError"),
        match=False, internal_error=False,
    )
    analysis = SubmissionAnalysisResponse(
        function_name="second_largest",
        total_tests=2, passed_tests=1, failed_tests=1,
        comparisons=[passing, failing],
        first_failing_input=[5, 5, 5],
    )
    return request, analysis


def test_persist_analysis_writes_full_graph(db_session):
    request, analysis = _analysis_with_bug()
    submission_id, run_id = persistence_service.persist_analysis(db_session, request, analysis)
    db_session.commit()

    run = repo.get_analysis_run(db_session, run_id)
    assert run is not None
    assert run.submission_id == submission_id
    assert run.total_tests == 2
    assert run.passed_tests == 1
    assert run.failed_tests == 1
    # Two test cases * (candidate + reference) = 4 executions.
    assert len(run.executions) == 4
    # One counterexample for the single confirmed failure.
    assert len(run.counterexamples) == 1
    ce = run.counterexamples[0]
    assert ce.original_input == [5, 5, 5]
    assert ce.minimized_input is None
    assert ce.explanation is None  # reserved for later


def test_persist_analysis_without_bug_stores_no_counterexample(db_session):
    from app.schemas.submission import FunctionExecutionResult, TestComparisonResult

    request = SubmissionRequest(
        function_name="f", specification="s",
        candidate_code="def f(xs):\n    return xs\n",
        reference_code="def f(xs):\n    return xs\n",
        test_inputs=[[1]],
    )
    analysis = SubmissionAnalysisResponse(
        function_name="f", total_tests=1, passed_tests=1, failed_tests=0,
        comparisons=[
            TestComparisonResult(
                input=[1], source="manual", category="manual", reason="r",
                candidate=FunctionExecutionResult(status="success", returned_value=[1]),
                reference=FunctionExecutionResult(status="success", returned_value=[1]),
                match=True, internal_error=False,
            )
        ],
        first_failing_input=None,
    )
    _, run_id = persistence_service.persist_analysis(db_session, request, analysis)
    db_session.commit()

    run = repo.get_analysis_run(db_session, run_id)
    assert len(run.counterexamples) == 0
    assert len(run.executions) == 2


# --- GET endpoints ----------------------------------------------------------


def _post_analyze(client, generate_tests=False):
    """Create a submission and run an analysis via the async flow (which
    completes inline in tests, QUEUE_EAGER=1). Returns a lightweight object
    exposing `submission_id` / `analysis_run_id` so the GET-endpoint tests
    below read the same as they did against the old /analyze response."""
    content = {
        "function_name": "second_largest",
        "specification": "Return the second largest distinct value.",
        "candidate_code": "def second_largest(values):\n    return sorted(values)[-2]\n",
        "reference_code": (
            "def second_largest(values):\n"
            "    unique = sorted(set(values))\n"
            "    if len(unique) < 2:\n"
            "        raise ValueError('need two distinct')\n"
            "    return unique[-2]\n"
        ),
    }
    created = client.post("/submissions", json=content)
    assert created.status_code == 201, created.text
    submission_id = created.json()["submission_id"]

    options = {
        "test_inputs": [[3, 1, 2], [5, 5, 5]],
        "generate_tests": generate_tests,
    }
    started = client.post(f"/submissions/{submission_id}/analyses", json=options)
    assert started.status_code == 202, started.text
    analysis_id = started.json()["analysis_id"]

    return _AnalyzeResult(submission_id, analysis_id)


class _AnalyzeResult:
    """Adapter exposing the two ids the GET-endpoint tests need, with a
    .json() shim matching the fields those tests read."""

    def __init__(self, submission_id, analysis_id):
        self.status_code = 200
        self._submission_id = submission_id
        self._analysis_id = analysis_id

    def json(self):
        return {
            "submission_id": self._submission_id,
            "analysis_run_id": self._analysis_id,
        }


def test_analyze_persists_and_returns_ids(client):
    response = _post_analyze(client)
    body = response.json()
    assert body["submission_id"] is not None
    assert body["analysis_run_id"] is not None


def test_get_submission_returns_stored_submission(client):
    analyze_body = _post_analyze(client).json()
    submission_id = analyze_body["submission_id"]

    response = client.get(f"/submissions/{submission_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == submission_id
    assert body["function_name"] == "second_largest"
    assert "candidate_code" in body
    assert "created_at" in body


def test_get_submission_404_for_unknown_id(client):
    response = client.get(f"/submissions/{uuid.uuid4()}")
    assert response.status_code == 404


def test_get_submission_422_for_non_uuid(client):
    response = client.get("/submissions/not-a-uuid")
    assert response.status_code == 422


def test_get_analysis_run_returns_full_result(client):
    analyze_body = _post_analyze(client).json()
    submission_id = analyze_body["submission_id"]
    analysis_id = analyze_body["analysis_run_id"]

    response = client.get(f"/submissions/{submission_id}/analyses/{analysis_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == analysis_id
    assert body["submission_id"] == submission_id
    assert body["total_tests"] == 2
    assert body["failed_tests"] == 1
    # 2 test cases * 2 roles = 4 executions.
    assert len(body["executions"]) == 4
    # The [5,5,5] failure is recorded as a counterexample.
    assert len(body["counterexamples"]) == 1
    assert body["counterexamples"][0]["original_input"] == [5, 5, 5]


def test_get_analysis_run_404_for_wrong_submission(client):
    analyze_body = _post_analyze(client).json()
    analysis_id = analyze_body["analysis_run_id"]
    other_submission = uuid.uuid4()

    # Correct analysis id, but under a submission it doesn't belong to.
    response = client.get(f"/submissions/{other_submission}/analyses/{analysis_id}")
    assert response.status_code == 404


def test_get_analysis_run_404_for_unknown_analysis(client):
    analyze_body = _post_analyze(client).json()
    submission_id = analyze_body["submission_id"]

    response = client.get(f"/submissions/{submission_id}/analyses/{uuid.uuid4()}")
    assert response.status_code == 404


def test_stored_execution_results_do_not_leak_sensitive_fields(client):
    analyze_body = _post_analyze(client).json()
    submission_id = analyze_body["submission_id"]
    analysis_id = analyze_body["analysis_run_id"]

    body = client.get(f"/submissions/{submission_id}/analyses/{analysis_id}").json()
    serialized = str(body)
    # None of these host-internal markers should ever appear in the output.
    assert "/home/" not in serialized
    assert "/mnt/" not in serialized
    assert "acb-runner-" not in serialized  # container name prefix
    for execution in body["executions"]:
        # normalized_result must only carry the sanitized public fields.
        assert set(execution["normalized_result"].keys()) <= {
            "status", "returned_value", "exception_type",
            "exception_message", "stdout", "stderr", "runtime_ms",
        }


# --- Explanation persistence (stored alongside, never overwriting results) --


def test_persist_analysis_stores_counterexample_explanation(db_session):
    request, analysis = _analysis_with_bug()
    explanation = {
        "source": "deterministic",
        "ai_generated": False,
        "summary": "On [5,5,5] candidate returned 5 but reference raised.",
        "root_cause": "deterministic fallback",
        "walkthrough": ["ran both", "they differ"],
        "suspected_lines": [],
        "suggested_fix": "",
        "suggested_fix_verified": False,
        "suggested_patch": None,
        "confidence": "low",
    }
    submission_id, run_id = persistence_service.persist_analysis(
        db_session, request, analysis, counterexample_explanation=explanation
    )
    db_session.commit()

    run = repo.get_analysis_run(db_session, run_id)
    assert len(run.counterexamples) == 1
    ce = run.counterexamples[0]
    # Explanation stored...
    assert ce.explanation is not None
    assert ce.explanation["source"] == "deterministic"
    assert ce.explanation["ai_generated"] is False
    # ...and the verified execution results are untouched by it.
    assert ce.candidate_result["status"] == "success"
    assert ce.reference_result["status"] == "runtime_error"


def test_explanation_is_returned_by_get_endpoint(client):
    # This submission has a confirmed bug and requests an explanation.
    content = {
        "function_name": "second_largest",
        "specification": "Return the second largest distinct value.",
        "candidate_code": "def second_largest(values):\n    return sorted(values)[-2]\n",
        "reference_code": (
            "def second_largest(values):\n"
            "    unique = sorted(set(values))\n"
            "    if len(unique) < 2:\n"
            "        raise ValueError('need two distinct')\n"
            "    return unique[-2]\n"
        ),
    }
    submission_id = client.post("/submissions", json=content).json()["submission_id"]
    started = client.post(
        f"/submissions/{submission_id}/analyses",
        json={"test_inputs": [[5, 5, 5]], "explain_counterexamples": True},
    )
    analysis_id = started.json()["analysis_id"]

    # The run completes inline (QUEUE_EAGER). AI isn't configured in tests, so
    # a deterministic explanation is stored. Verify it via the GET endpoint.
    body = client.get(f"/submissions/{submission_id}/analyses/{analysis_id}").json()
    assert len(body["counterexamples"]) == 1
    explanation = body["counterexamples"][0]["explanation"]
    assert explanation is not None
    assert explanation["source"] == "deterministic"
    assert explanation["ai_generated"] is False
    assert body["counterexamples"][0]["explanation"]["ai_generated"] is False
    # Verified results still present and correct alongside the explanation.
    assert body["counterexamples"][0]["candidate_result"]["status"] == "success"
