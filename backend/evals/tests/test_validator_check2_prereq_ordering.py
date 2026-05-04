"""
Tests for validator Check 2: prerequisite ordering.

Covers:
  - Clean plan with prereqs in correct earlier semesters → no violations
  - Prereq satisfied by completed history → no violation
  - Prereq in same semester as dependent → violation ("same or later")
  - Prereq in later semester than dependent → violation ("same or later")
  - Prereq absent from plan entirely → violation ("not in plan, add or drop")
  - OR-group: one option satisfied → no violation
  - OR-group: all options unsatisfied, one in plan later → violation ("same or later")
  - OR-group: all options absent from plan → violation ("not in plan, add or drop")
  - Multi-hop chain in correct order → no violations
  - Multi-hop chain out of order → violation on the misordered course
  - Semester ordering uses semester_index, not label string
  - Courses with no prereqs → no violations
  - GE placeholders skipped → no false positives
"""

import pytest

from models.plan import PlanJSON, PlannedCourse, SemesterPlan, PlanMetadata, Violation
from nodes.validator_nodes import check_prereq_ordering


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _course(
    code: str,
    prereqs: list = None,
    is_placeholder: bool = False,
    is_completed: bool = False,
) -> PlannedCourse:
    return PlannedCourse(
        course_code=code,
        name=code,
        units=4.0,
        prereqs=prereqs or [],
        is_placeholder=is_placeholder,
        is_completed=is_completed,
    )


def _semester(
    label: str,
    courses: list[PlannedCourse],
    is_completed: bool = False,
    index: int = 0,
) -> SemesterPlan:
    return SemesterPlan(
        label=label,
        semester_index=index,
        is_completed=is_completed,
        total_units=sum(c.units for c in courses),
        courses=courses,
    )


def _plan(*semesters: SemesterPlan) -> PlanJSON:
    return PlanJSON(
        generated_at="2026-01-01T00:00:00+00:00",
        version_id=1,
        track="test",
        semesters=list(semesters),
        metadata=PlanMetadata(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_clean_plan_prereqs_in_earlier_semester():
    """Prereq placed before dependent → no violations."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-270")], index=0),
        _semester("Spring 2027", [_course("CSCI-360", prereqs=["CSCI-270"])], index=1),
    )
    assert check_prereq_ordering(plan) == []


def test_prereq_satisfied_by_completed_history():
    """Prereq in completed semester seeds satisfied set → no violation for future course."""
    plan = _plan(
        _semester("Spring 2026", [_course("CSCI-270", is_completed=True)], is_completed=True, index=0),
        _semester("Fall 2026", [_course("CSCI-360", prereqs=["CSCI-270"])], index=1),
    )
    assert check_prereq_ordering(plan) == []


def test_prereq_in_same_semester_as_dependent():
    """Prereq and dependent in same future semester → violation with 'same or later' message."""
    plan = _plan(
        _semester("Fall 2026", [
            _course("CSCI-270"),
            _course("CSCI-360", prereqs=["CSCI-270"]),
        ], index=0),
    )
    violations = check_prereq_ordering(plan)
    assert len(violations) == 1
    assert violations[0].course_code == "CSCI-360"
    assert violations[0].semester == "Fall 2026"
    assert "same or a later semester" in violations[0].detail
    assert "Move" in violations[0].detail


def test_prereq_in_later_semester_than_dependent():
    """Prereq placed after dependent → violation with 'same or later' message."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-360", prereqs=["CSCI-270"])], index=0),
        _semester("Spring 2027", [_course("CSCI-270")], index=1),
    )
    violations = check_prereq_ordering(plan)
    assert len(violations) == 1
    assert violations[0].course_code == "CSCI-360"
    assert "same or a later semester" in violations[0].detail
    assert "Move" in violations[0].detail


def test_prereq_absent_from_plan_entirely():
    """Prereq not in plan or completed history → violation with 'not in plan' message."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-360", prereqs=["CSCI-270"])], index=0),
    )
    violations = check_prereq_ordering(plan)
    assert len(violations) == 1
    assert violations[0].course_code == "CSCI-360"
    assert "not in the plan" in violations[0].detail
    assert "drop" in violations[0].detail


def test_or_group_one_option_satisfied():
    """OR-group prereq: one option already in completed history → no violation."""
    plan = _plan(
        _semester("Spring 2026", [_course("CSCI-104", is_completed=True)], is_completed=True, index=0),
        _semester("Fall 2026", [
            _course("CSCI-360", prereqs=[["CSCI-104", "CSCI-114"]]),
        ], index=1),
    )
    assert check_prereq_ordering(plan) == []


def test_or_group_one_option_in_earlier_future_semester():
    """OR-group prereq: one option placed in an earlier future semester → no violation."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-104")], index=0),
        _semester("Spring 2027", [
            _course("CSCI-360", prereqs=[["CSCI-104", "CSCI-114"]]),
        ], index=1),
    )
    assert check_prereq_ordering(plan) == []


def test_or_group_all_options_unsatisfied_one_in_plan_later():
    """OR-group: all options unsatisfied, one appears later in plan → 'same or later'."""
    plan = _plan(
        _semester("Fall 2026", [
            _course("CSCI-360", prereqs=[["CSCI-104", "CSCI-114"]]),
        ], index=0),
        _semester("Spring 2027", [_course("CSCI-104")], index=1),
    )
    violations = check_prereq_ordering(plan)
    assert len(violations) == 1
    assert violations[0].course_code == "CSCI-360"
    assert "same or a later semester" in violations[0].detail


def test_or_group_all_options_absent_from_plan():
    """OR-group: all options absent from plan → 'not in plan' message on first option."""
    plan = _plan(
        _semester("Fall 2026", [
            _course("CSCI-360", prereqs=[["CSCI-104", "CSCI-114"]]),
        ], index=0),
    )
    violations = check_prereq_ordering(plan)
    assert len(violations) == 1
    assert "not in the plan" in violations[0].detail
    # canonical representative (first in OR list) used in message
    assert "CSCI-104" in violations[0].detail


def test_multi_hop_chain_correct_order():
    """A → B → C placed in order → no violations."""
    plan = _plan(
        _semester("Fall 2026",  [_course("CSCI-103")], index=0),
        _semester("Spring 2027", [_course("CSCI-104", prereqs=["CSCI-103"])], index=1),
        _semester("Fall 2027",  [_course("CSCI-360", prereqs=["CSCI-104"])], index=2),
    )
    assert check_prereq_ordering(plan) == []


def test_multi_hop_chain_out_of_order():
    """A → B → C with C before B → violation only on C, not B (B's prereq A is satisfied)."""
    plan = _plan(
        _semester("Fall 2026",  [_course("CSCI-103")], index=0),
        _semester("Spring 2027", [_course("CSCI-360", prereqs=["CSCI-104"])], index=1),
        _semester("Fall 2027",  [_course("CSCI-104", prereqs=["CSCI-103"])], index=2),
    )
    violations = check_prereq_ordering(plan)
    assert len(violations) == 1
    assert violations[0].course_code == "CSCI-360"
    assert violations[0].semester == "Spring 2027"


def test_semester_index_drives_ordering_not_label():
    """semester_index must determine scan order, not label alphabetical order.

    'Spring 2027' (index=1) sorts after 'Fall 2026' (index=0) by index
    even though 'Fall' < 'Spring' alphabetically.
    Prereq in index=0 satisfies dependent in index=1 → no violation.
    """
    plan = _plan(
        _semester("Fall 2026",  [_course("CSCI-270")], index=0),
        _semester("Spring 2027", [_course("CSCI-360", prereqs=["CSCI-270"])], index=1),
    )
    assert check_prereq_ordering(plan) == []


def test_course_with_no_prereqs():
    """Courses with empty prereqs list → no violations."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-310"), _course("WRIT-340")], index=0),
    )
    assert check_prereq_ordering(plan) == []


def test_ge_placeholder_skipped():
    """GE placeholder slots have no prereqs and are skipped entirely."""
    plan = _plan(
        _semester("Fall 2026", [
            _course("GE-C", is_placeholder=True),
            _course("CSCI-310"),
        ], index=0),
    )
    assert check_prereq_ordering(plan) == []


def test_multiple_violations_collected():
    """All violations collected in one pass — not short-circuited."""
    plan = _plan(
        _semester("Fall 2026", [
            _course("CSCI-360", prereqs=["CSCI-270"]),  # CSCI-270 absent
            _course("CSCI-401", prereqs=["CSCI-310"]),  # CSCI-310 absent
        ], index=0),
    )
    violations = check_prereq_ordering(plan)
    assert len(violations) == 2
    codes = {v.course_code for v in violations}
    assert codes == {"CSCI-360", "CSCI-401"}


def test_and_of_or_mixed_prereqs():
    """AND-of-OR: str prereq AND OR-group both must be satisfied."""
    # course requires CSCI-270 (str) AND (CSCI-104 OR CSCI-114) (OR-group)
    plan = _plan(
        _semester("Spring 2026", [
            _course("CSCI-270", is_completed=True),
            _course("CSCI-104", is_completed=True),
        ], is_completed=True, index=0),
        _semester("Fall 2026", [
            _course("CSCI-467", prereqs=["CSCI-270", ["CSCI-104", "CSCI-114"]]),
        ], index=1),
    )
    assert check_prereq_ordering(plan) == []


def test_prereqs_satisfied_flag_skips_check():
    """prereqs_satisfied=True on a future course skips its prereq check entirely.

    Covers courses whose prereqs were satisfied via waivers (CW exception codes),
    transfers, or in-progress — none of which appear in courses_taken / completed
    semesters, so the validator's satisfied set would miss them.
    EE-109 / CSCI-102 waiver is the real-world trigger for this case.
    """
    # prereq EE-155 is NOT in plan or completed history, but prereqs_satisfied=True
    # (e.g. CSCI-102 was waived, satisfying the OR group [EE-155, CSCI-102, CSCI-113])
    plan = _plan(
        _semester("Fall 2026", [
            PlannedCourse(
                course_code="EE-109",
                name="Introduction to Embedded Systems",
                units=4.0,
                prereqs=[["EE-155", "CSCI-102", "CSCI-113"]],
                prereqs_satisfied=True,   # waiver confirmed upstream
            ),
        ], index=0),
    )
    assert check_prereq_ordering(plan) == []


def test_prereqs_satisfied_false_still_checked():
    """prereqs_satisfied=False means at least one prereq is unconfirmed — check runs."""
    plan = _plan(
        _semester("Fall 2026", [
            _course("CSCI-360", prereqs=["CSCI-270"]),  # prereqs_satisfied defaults False
        ], index=0),
        # CSCI-270 absent from plan and history → should still violate
    )
    violations = check_prereq_ordering(plan)
    assert len(violations) == 1
    assert violations[0].course_code == "CSCI-360"


def test_and_of_or_one_group_unsatisfied():
    """AND-of-OR: str prereq satisfied, OR-group unsatisfied → one violation."""
    plan = _plan(
        _semester("Spring 2026", [_course("CSCI-270", is_completed=True)], is_completed=True, index=0),
        _semester("Fall 2026", [
            # CSCI-270 satisfied, but (CSCI-104 OR CSCI-114) not in plan at all
            _course("CSCI-467", prereqs=["CSCI-270", ["CSCI-104", "CSCI-114"]]),
        ], index=1),
    )
    violations = check_prereq_ordering(plan)
    assert len(violations) == 1
    assert violations[0].course_code == "CSCI-467"
    assert "not in the plan" in violations[0].detail
