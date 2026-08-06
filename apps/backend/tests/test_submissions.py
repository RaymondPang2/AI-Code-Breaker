"""
Tests for POST /submissions/validate and the SubmissionRequest contract.

No code execution happens anywhere in this project yet, so these tests are
entirely about validation and normalization: does the API accept
well-formed submissions, reject malformed ones, and return useful errors?
"""

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.submission import (
    MAX_FUNCTION_NAME_LENGTH,
    MAX_INPUT_LIST_SIZE,
    MAX_SOURCE_CODE_LENGTH,
    MAX_SPECIFICATION_LENGTH,
    MAX_TEST_CASES,
)

client = TestClient(app)

VALID_PAYLOAD = {
    "function_name": "double_all",
    "specification": "Return a new list with every element doubled.",
    "candidate_code": "def double_all(xs):\n    return [x * 2 for x in xs]\n",
    "reference_code": "def double_all(xs):\n    return [x * 2 for x in xs]\n",
    "test_inputs": [[1, 2, 3], [], [-5, 0, 5]],
}


def submit(payload: dict):
    return client.post("/submissions/validate", json=payload)


# --- Valid submissions ----------------------------------------------------


def test_valid_submission_returns_200():
    response = submit(VALID_PAYLOAD)
    assert response.status_code == 200


def test_valid_submission_echoes_normalized_body():
    response = submit(VALID_PAYLOAD)
    body = response.json()
    assert body["function_name"] == "double_all"
    assert body["test_inputs"] == [[1, 2, 3], [], [-5, 0, 5]]


def test_valid_submission_with_no_test_inputs_defaults_to_empty_list():
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "test_inputs"}
    response = submit(payload)
    assert response.status_code == 200
    assert response.json()["test_inputs"] == []


def test_valid_submission_with_negative_and_zero_values():
    payload = {**VALID_PAYLOAD, "test_inputs": [[0], [-1, -2, -3], [0, 0, 0]]}
    response = submit(payload)
    assert response.status_code == 200


def test_function_name_with_underscores_and_digits_is_valid():
    payload = {**VALID_PAYLOAD, "function_name": "_helper_v2"}
    response = submit(payload)
    assert response.status_code == 200


def test_whitespace_is_trimmed_from_string_fields():
    payload = {
        **VALID_PAYLOAD,
        "function_name": "  double_all  ",
        "specification": "  Doubles every element.  ",
    }
    response = submit(payload)
    assert response.status_code == 200
    body = response.json()
    assert body["function_name"] == "double_all"
    assert body["specification"] == "Doubles every element."


def test_max_length_specification_is_accepted():
    payload = {**VALID_PAYLOAD, "specification": "x" * MAX_SPECIFICATION_LENGTH}
    response = submit(payload)
    assert response.status_code == 200


def test_max_test_cases_is_accepted():
    payload = {**VALID_PAYLOAD, "test_inputs": [[1]] * MAX_TEST_CASES}
    response = submit(payload)
    assert response.status_code == 200


def test_max_input_list_size_is_accepted():
    payload = {**VALID_PAYLOAD, "test_inputs": [list(range(MAX_INPUT_LIST_SIZE))]}
    response = submit(payload)
    assert response.status_code == 200


# --- Invalid function_name -------------------------------------------------


def test_function_name_with_space_is_rejected():
    payload = {**VALID_PAYLOAD, "function_name": "double all"}
    response = submit(payload)
    assert response.status_code == 422


def test_function_name_starting_with_digit_is_rejected():
    payload = {**VALID_PAYLOAD, "function_name": "1double"}
    response = submit(payload)
    assert response.status_code == 422


def test_function_name_with_hyphen_is_rejected():
    payload = {**VALID_PAYLOAD, "function_name": "double-all"}
    response = submit(payload)
    assert response.status_code == 422


def test_function_name_that_is_a_keyword_is_rejected():
    payload = {**VALID_PAYLOAD, "function_name": "class"}
    response = submit(payload)
    assert response.status_code == 422


def test_empty_function_name_is_rejected():
    payload = {**VALID_PAYLOAD, "function_name": ""}
    response = submit(payload)
    assert response.status_code == 422


def test_function_name_exceeding_max_length_is_rejected():
    payload = {**VALID_PAYLOAD, "function_name": "f" * (MAX_FUNCTION_NAME_LENGTH + 1)}
    response = submit(payload)
    assert response.status_code == 422


# --- Invalid / missing required fields -------------------------------------


def test_missing_candidate_code_is_rejected():
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "candidate_code"}
    response = submit(payload)
    assert response.status_code == 422


def test_empty_reference_code_is_rejected():
    payload = {**VALID_PAYLOAD, "reference_code": ""}
    response = submit(payload)
    assert response.status_code == 422


def test_empty_specification_is_rejected():
    payload = {**VALID_PAYLOAD, "specification": ""}
    response = submit(payload)
    assert response.status_code == 422


def test_specification_exceeding_max_length_is_rejected():
    payload = {**VALID_PAYLOAD, "specification": "x" * (MAX_SPECIFICATION_LENGTH + 1)}
    response = submit(payload)
    assert response.status_code == 422


def test_candidate_code_exceeding_max_length_is_rejected():
    payload = {**VALID_PAYLOAD, "candidate_code": "x" * (MAX_SOURCE_CODE_LENGTH + 1)}
    response = submit(payload)
    assert response.status_code == 422


# --- Invalid test_inputs ----------------------------------------------------


def test_too_many_test_cases_is_rejected():
    payload = {**VALID_PAYLOAD, "test_inputs": [[1]] * (MAX_TEST_CASES + 1)}
    response = submit(payload)
    assert response.status_code == 422


def test_input_list_exceeding_max_size_is_rejected():
    payload = {**VALID_PAYLOAD, "test_inputs": [list(range(MAX_INPUT_LIST_SIZE + 1))]}
    response = submit(payload)
    assert response.status_code == 422


def test_test_inputs_not_a_list_of_lists_is_rejected():
    payload = {**VALID_PAYLOAD, "test_inputs": [1, 2, 3]}
    response = submit(payload)
    assert response.status_code == 422


def test_test_inputs_with_float_values_is_rejected():
    payload = {**VALID_PAYLOAD, "test_inputs": [[1, 2.5, 3]]}
    response = submit(payload)
    assert response.status_code == 422


def test_test_inputs_with_string_values_is_rejected():
    payload = {**VALID_PAYLOAD, "test_inputs": [[1, "2", 3]]}
    response = submit(payload)
    assert response.status_code == 422


def test_test_inputs_with_boolean_values_is_rejected():
    # bool is a subclass of int in Python; this must not be silently
    # coerced to 0/1.
    payload = {**VALID_PAYLOAD, "test_inputs": [[1, True, 3]]}
    response = submit(payload)
    assert response.status_code == 422


def test_test_inputs_with_null_entry_is_rejected():
    payload = {**VALID_PAYLOAD, "test_inputs": [[1, None, 3]]}
    response = submit(payload)
    assert response.status_code == 422


def test_test_inputs_as_flat_array_is_rejected():
    payload = {**VALID_PAYLOAD, "test_inputs": [[1, 2, 3], 4]}
    response = submit(payload)
    assert response.status_code == 422


# --- Error response quality -------------------------------------------------


def test_validation_error_response_identifies_offending_field():
    payload = {**VALID_PAYLOAD, "function_name": "not valid!"}
    response = submit(payload)
    body = response.json()
    assert "detail" in body
    locations = [".".join(str(p) for p in err["loc"]) for err in body["detail"]]
    assert any("function_name" in loc for loc in locations)


def test_validation_error_message_is_descriptive():
    payload = {**VALID_PAYLOAD, "function_name": "class"}
    response = submit(payload)
    body = response.json()
    messages = " ".join(err["msg"] for err in body["detail"])
    assert "keyword" in messages.lower()


def test_multiple_errors_are_all_reported():
    payload = {
        **VALID_PAYLOAD,
        "function_name": "not valid!",
        "specification": "",
    }
    response = submit(payload)
    body = response.json()
    locations = [".".join(str(p) for p in err["loc"]) for err in body["detail"]]
    assert any("function_name" in loc for loc in locations)
    assert any("specification" in loc for loc in locations)
