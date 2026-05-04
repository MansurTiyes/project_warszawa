"""
Tests for validator Check 4: total units sufficient.

Covers:
  - Future units exactly equal to needed → no violations (boundary: only < triggers)
  - Future units greater than needed → no violations
  - Future units less than needed → violation with correct message
  - GE placeholder units count toward the total
  - Completed semester units are excluded
  - Zero future units with non-zero needed → violation
  - units_still_needed of 0 → no violations (degree already complete)
  - Violation message contains both actual and needed unit counts
"""

import pytest

from models.pipeline_state import OtherRequirements
from models.plan import PlanJSON, PlannedCourse, SemesterPlan, PlanMetadata
from nodes.validator_nodes import check_total_units


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _other_must_reqs(units_still_needed: float) -> OtherRequirements:
    return OtherRequirements(
        units_total_required=128.0,
        units_still_needed=units_still_needed,
        upper_division_required=32.0,
        upper_division_still_needed=0.0,
        residency_satisfied=True,
        writing_requirement_satisfied=True,
        ge_seminar_satisfied=True,
    )


def _course(code: str, units: float = 4.0, is_placeholder: bool = False, is_completed: bool = False) -> PlannedCourse:
    return PlannedCourse(
        course_code=code,
        name=code,
        units=units,
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

def test_future_units_exactly_equal_to_needed():
    """Boundary: future == needed → no violation (only strict < triggers)."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-310"), _course("CSCI-401")], index=0),
        # 2 courses × 4 units = 8 units
    )
    assert check_total_units(plan, _other_must_reqs(units_still_needed=8.0)) == []


def test_future_units_exceed_needed():
    """Future units above needed → no violation."""
    plan = _plan(
        _semester("Fall 2026", [
            _course("CSCI-310"), _course("CSCI-401"),
            _course("WRIT-340"), _course("CSCI-485"),
        ], index=0),
        # 4 × 4 = 16 units
    )
    assert check_total_units(plan, _other_must_reqs(units_still_needed=12.0)) == []


def test_future_units_below_needed():
    """Future units below needed → one violation."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-310"), _course("CSCI-401")], index=0),
        # 8 units planned, 16 needed
    )
    violations = check_total_units(plan, _other_must_reqs(units_still_needed=16.0))
    assert len(violations) == 1
    assert violations[0].course_code is None
    assert violations[0].semester is None
    assert "8" in violations[0].detail
    assert "16" in violations[0].detail
    assert "still needed" in violations[0].detail


def test_ge_placeholder_units_count():
    """GE placeholder slots (4 units each) count toward the future unit total."""
    plan = _plan(
        _semester("Fall 2026", [
            _course("CSCI-310"),
            _course("GE-C", is_placeholder=True),
        ], index=0),
        # 4 + 4 = 8 units including GE slot
    )
    assert check_total_units(plan, _other_must_reqs(units_still_needed=8.0)) == []


def test_ge_placeholder_units_needed_to_meet_threshold():
    """Without GE placeholder units, plan falls short; with them, it meets the threshold."""
    plan = _plan(
        _semester("Fall 2026", [
            _course("CSCI-310"),                         # 4 units
            _course("GE-C", is_placeholder=True),        # 4 units
            _course("GE-G", is_placeholder=True),        # 4 units
        ], index=0),
        # 12 units total
    )
    # Exactly meets threshold with GE units included
    assert check_total_units(plan, _other_must_reqs(units_still_needed=12.0)) == []
    # Would fall short if GE slots were excluded (only 4 units of real courses)
    # This test confirms GE units ARE counted


def test_completed_semester_units_excluded():
    """Completed semester units must not count — only future semesters matter."""
    plan = _plan(
        _semester("Spring 2026", [
            _course("CSCI-103", is_completed=True),
            _course("CSCI-104", is_completed=True),
            _course("CSCI-170", is_completed=True),
            _course("CSCI-201", is_completed=True),
        ], is_completed=True, index=0),
        # 16 completed units — must not count
        _semester("Fall 2026", [_course("CSCI-310")], index=1),
        # 4 future units
    )
    violations = check_total_units(plan, _other_must_reqs(units_still_needed=8.0))
    assert len(violations) == 1
    assert "4" in violations[0].detail   # only future units counted
    assert "8" in violations[0].detail


def test_zero_future_units_nonzero_needed():
    """No future semesters → 0 future units → violation if needed > 0."""
    plan = _plan(
        _semester("Spring 2026", [_course("CSCI-103", is_completed=True)], is_completed=True, index=0),
    )
    violations = check_total_units(plan, _other_must_reqs(units_still_needed=16.0))
    assert len(violations) == 1
    assert "0" in violations[0].detail


def test_units_still_needed_zero():
    """units_still_needed of 0 → degree complete, no violation regardless of future units."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-310")], index=0),
    )
    assert check_total_units(plan, _other_must_reqs(units_still_needed=0.0)) == []


def test_violation_message_contains_both_values():
    """Violation detail must contain both the actual future units and the needed amount."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-310")], index=0),
        # 4 future units
    )
    violations = check_total_units(plan, _other_must_reqs(units_still_needed=18.0))
    assert len(violations) == 1
    detail = violations[0].detail
    assert "4" in detail
    assert "18" in detail


def test_fractional_units():
    """Units are floats — fractional unit courses handled correctly."""
    plan = _plan(
        _semester("Fall 2026", [
            _course("CSCI-490", units=1.0),
            _course("CSCI-310", units=4.0),
        ], index=0),
        # 5.0 future units
    )
    assert check_total_units(plan, _other_must_reqs(units_still_needed=5.0)) == []
    violations = check_total_units(plan, _other_must_reqs(units_still_needed=6.0))
    assert len(violations) == 1
