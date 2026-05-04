"""
Tests for validator Check 1: no duplicate courses in future plan.

Covers:
  - Clean plan → no violations
  - Duplicate in the same future semester → violation
  - Duplicate across two future semesters → violation
  - Completed semesters are not checked (duplicates there are ignored)
  - GE placeholders are not checked (same category code is legal once per slot)
  - First occurrence is not flagged; only the repeat is
  - Multiple distinct duplicates → one violation each
"""

import pytest

from models.plan import PlanJSON, PlannedCourse, SemesterPlan, PlanMetadata, ValidationResult
from nodes.validator_nodes import check_no_duplicates


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _course(code: str, is_placeholder: bool = False, is_completed: bool = False) -> PlannedCourse:
    return PlannedCourse(
        course_code=code,
        name=code,
        units=4.0,
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
# Check 1 tests
# ---------------------------------------------------------------------------

def test_no_duplicates_clean_plan():
    """All distinct course codes → no violations."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-310"), _course("CSCI-401")], index=0),
        _semester("Spring 2027", [_course("WRIT-340"), _course("CSCI-485")], index=1),
    )
    assert check_no_duplicates(plan) == []


def test_duplicate_within_same_semester():
    """Same code twice in one future semester → one violation on that semester."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-310"), _course("CSCI-310")], index=0),
    )
    violations = check_no_duplicates(plan)
    assert len(violations) == 1
    assert violations[0].course_code == "CSCI-310"
    assert violations[0].semester == "Fall 2026"
    assert "more than once" in violations[0].detail


def test_duplicate_across_two_future_semesters():
    """Same code in two different future semesters → one violation on the second semester."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-310")], index=0),
        _semester("Spring 2027", [_course("CSCI-310")], index=1),
    )
    violations = check_no_duplicates(plan)
    assert len(violations) == 1
    assert violations[0].course_code == "CSCI-310"
    assert violations[0].semester == "Spring 2027"


def test_first_occurrence_not_flagged():
    """Only the repeat is flagged, never the first placement."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-310"), _course("CSCI-401")], index=0),
        _semester("Spring 2027", [_course("CSCI-310")], index=1),
    )
    violations = check_no_duplicates(plan)
    assert len(violations) == 1
    assert violations[0].semester == "Spring 2027"


def test_completed_semesters_skipped():
    """Duplicates in completed semesters are not flagged; only future semesters matter."""
    plan = _plan(
        _semester("Spring 2026", [_course("CSCI-310", is_completed=True)], is_completed=True, index=0),
        _semester("Fall 2026", [_course("CSCI-310")], index=1),
    )
    # CSCI-310 appears in both completed and future — should not flag
    assert check_no_duplicates(plan) == []


def test_ge_placeholders_skipped():
    """GE placeholder slots (is_placeholder=True) are not checked for duplicates."""
    plan = _plan(
        _semester(
            "Fall 2026",
            [_course("GE-C", is_placeholder=True), _course("GE-C", is_placeholder=True)],
            index=0,
        ),
    )
    # Two GE-C slots would be a scheduling mistake, but not a duplicate-course violation
    assert check_no_duplicates(plan) == []


def test_multiple_distinct_duplicates():
    """Two different courses each duplicated → two violations."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-310"), _course("CSCI-401")], index=0),
        _semester("Spring 2027", [_course("CSCI-310"), _course("CSCI-401")], index=1),
    )
    violations = check_no_duplicates(plan)
    assert len(violations) == 2
    duplicate_codes = {v.course_code for v in violations}
    assert duplicate_codes == {"CSCI-310", "CSCI-401"}
    # Both flagged on the second semester
    assert all(v.semester == "Spring 2027" for v in violations)


def test_empty_plan():
    """Plan with no future semesters → no violations."""
    plan = _plan(
        _semester("Spring 2026", [_course("CSCI-103", is_completed=True)], is_completed=True, index=0),
    )
    assert check_no_duplicates(plan) == []


def test_single_future_semester_no_duplicates():
    """Single future semester with all distinct codes → no violations."""
    plan = _plan(
        _semester(
            "Fall 2026",
            [_course("CSCI-310"), _course("WRIT-340"), _course("GE-A", is_placeholder=True)],
            index=0,
        ),
    )
    assert check_no_duplicates(plan) == []
