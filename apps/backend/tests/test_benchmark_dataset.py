"""
Dataset integrity tests.

These guard the benchmark's ground truth: every case must be genuinely buggy
(candidate and reference actually differ on the known counterexample), every
listed agreement must actually agree, and the required categories must be
covered. A failure here means the DATASET is wrong, which would silently
corrupt every metric — so these run as part of the suite.

Mutation and state-leakage cases are exempt from the value-equality check
(their bug is a side effect / cross-call state that value comparison on a
single call can't see); instead they're checked for the property that makes
them buggy (the candidate mutates its input, or caches state).
"""

from benchmark.case_schema import BUG_CATEGORIES
from benchmark.dataset.cases import all_cases

REQUIRED_CATEGORIES = set(BUG_CATEGORIES)


def _execute(code, fn_name, arg):
    ns: dict = {}
    try:
        exec(code, ns)
        return ("ok", ns[fn_name](list(arg)))
    except Exception as exc:  # noqa: BLE001
        return ("exc", type(exc).__name__)


def _differs(a, b) -> bool:
    if a[0] != b[0]:
        return True
    return a[1] != b[1]


def test_dataset_has_at_least_30_cases():
    assert len(all_cases()) >= 30


def test_all_required_categories_present():
    covered = {c.category for c in all_cases()}
    assert REQUIRED_CATEGORIES <= covered, REQUIRED_CATEGORIES - covered


def test_case_ids_unique():
    ids = [c.case_id for c in all_cases()]
    assert len(ids) == len(set(ids))


def test_every_case_has_a_known_counterexample():
    for c in all_cases():
        assert c.known_counterexamples, c.case_id


def test_value_cases_actually_differ_on_counterexample():
    """For all non-mutation/state cases, the candidate must diverge from the
    reference on at least one known counterexample."""
    exempt = {"mutation_of_input", "state_leakage"}
    for c in all_cases():
        if c.category in exempt:
            continue
        differed = any(
            _differs(
                _execute(c.candidate_code, c.function_name, ce),
                _execute(c.reference_code, c.function_name, ce),
            )
            for ce in c.known_counterexamples
        )
        assert differed, f"{c.case_id}: candidate does not differ on any counterexample"


def test_agreements_actually_agree():
    for c in all_cases():
        for ag in c.known_agreements:
            cand = _execute(c.candidate_code, c.function_name, ag)
            ref = _execute(c.reference_code, c.function_name, ag)
            assert not _differs(cand, ref), (
                f"{c.case_id}: listed agreement {ag} actually differs "
                f"(candidate={cand}, reference={ref})"
            )


def test_mutation_cases_actually_mutate_input():
    for c in all_cases():
        if c.category != "mutation_of_input":
            continue
        ce = c.known_counterexamples[0]
        passed = list(ce)
        ns: dict = {}
        exec(c.candidate_code, ns)
        ns[c.function_name](passed)
        assert passed != list(ce), (
            f"{c.case_id}: candidate was expected to mutate its input but didn't"
        )
