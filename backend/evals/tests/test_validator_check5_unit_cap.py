"""
Tests for validator Check 5: per-semester unit cap.

Covers:
  - All semesters within cap → no violations
  - Semester exactly at cap → no violation (only strict > triggers)
  - One semester exceeds cap → violation with correct label and unit count
  - Multiple semesters exceeding cap → one violation each
  - Completed semesters not checked, even if over cap
  - Violation has semester label set and course_code=None
  - Custom cap parameter respected
  - Violation message contains semester label and unit count
"""

import pytest

from models.plan import PlanJSON, PlannedCourse, SemesterPlan, PlanMetadata
from nodes.validator_nodes import check_semester_unit_cap


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _course(code: str, units: float = 4.0, is_completed: bool = False) -> PlannedCourse:
    return PlannedCourse(
        course_code=code,
        name=code,
        units=units,
        is_completed=is_completed,
    )


def _semester(
    label: str,
    total_units: float,
    is_completed: bool = False,
    index: int = 0,
) -> SemesterPlan:
    # Build a minimal semester with total_units set directly — check only uses total_units
    return SemesterPlan(
        label=label,
        semester_index=index,
        is_completed=is_completed,
        total_units=total_units,
        courses=[],
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

def test_all_semesters_within_cap():
    """All future semesters at or below 18 units → no violations."""
    plan = _plan(
        _semester("Fall 2026", total_units=16.0, index=0),
        _semester("Spring 2027", total_units=18.0, index=1),
    )
    assert check_semester_unit_cap(plan) == []


def test_semester_exactly_at_cap():
    """Boundary: exactly 18 units → no violation (only strict > triggers)."""
    plan = _plan(
        _semester("Fall 2026", total_units=18.0, index=0),
    )
    assert check_semester_unit_cap(plan) == []


def test_one_semester_exceeds_cap():
    """One future semester over 18 units → one violation."""
    plan = _plan(
        _semester("Fall 2026", total_units=20.0, index=0),
        _semester("Spring 2027", total_units=16.0, index=1),
    )
    violations = check_semester_unit_cap(plan)
    assert len(violations) == 1
    assert violations[0].semester == "Fall 2026"
    assert violations[0].course_code is None


def test_multiple_semesters_exceed_cap():
    """Two future semesters over cap → two violations."""
    plan = _plan(
        _semester("Fall 2026", total_units=20.0, index=0),
        _semester("Spring 2027", total_units=22.0, index=1),
        _semester("Fall 2027", total_units=16.0, index=2),
    )
    violations = check_semester_unit_cap(plan)
    assert len(violations) == 2
    labels = {v.semester for v in violations}
    assert labels == {"Fall 2026", "Spring 2027"}


def test_completed_semesters_not_checked():
    """Completed semesters are skipped even if their total_units exceeds the cap."""
    plan = _plan(
        _semester("Spring 2026", total_units=22.0, is_completed=True, index=0),
        _semester("Fall 2026", total_units=16.0, index=1),
    )
    assert check_semester_unit_cap(plan) == []


def test_violation_fields():
    """Violation has semester label, no course_code, actionable detail."""
    plan = _plan(
        _semester("Fall 2026", total_units=20.0, index=0),
    )
    violations = check_semester_unit_cap(plan)
    assert len(violations) == 1
    v = violations[0]
    assert v.course_code is None
    assert v.semester == "Fall 2026"
    assert "Fall 2026" in v.detail
    assert "20" in v.detail
    assert "18" in v.detail
    assert "exceeds" in v.detail


def test_violation_message_includes_actual_and_cap():
    """Detail string contains both the actual unit count and the cap value."""
    plan = _plan(
        _semester("Spring 2027", total_units=19.0, index=0),
    )
    violations = check_semester_unit_cap(plan)
    detail = violations[0].detail
    assert "19" in detail
    assert "18" in detail


def test_custom_cap_parameter():
    """Custom cap value is respected — violation only above the provided cap."""
    plan = _plan(
        _semester("Fall 2026", total_units=20.0, index=0),
        _semester("Spring 2027", total_units=22.0, index=1),
    )
    # With cap=21, only Spring 2027 (22 units) should trigger
    violations = check_semester_unit_cap(plan, cap=21.0)
    assert len(violations) == 1
    assert violations[0].semester == "Spring 2027"
    assert "21" in violations[0].detail


def test_fractional_overage():
    """Fractional unit total exceeding cap triggers violation."""
    plan = _plan(
        _semester("Fall 2026", total_units=18.5, index=0),
    )
    violations = check_semester_unit_cap(plan)
    assert len(violations) == 1
    assert "18.5" in violations[0].detail


def test_empty_plan():
    """No semesters → no violations."""
    plan = _plan()
    assert check_semester_unit_cap(plan) == []


def test_only_completed_semesters():
    """Plan with only completed semesters → no violations regardless of units."""
    plan = _plan(
        _semester("Spring 2026", total_units=24.0, is_completed=True, index=0),
    )
    assert check_semester_unit_cap(plan) == []
