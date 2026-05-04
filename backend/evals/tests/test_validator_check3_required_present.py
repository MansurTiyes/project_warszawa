"""
Tests for validator Check 3: hard courses present + GE placeholders present.

check_required_present covers:
  - All hard courses in future plan → no violations
  - Hard course already in completed history → no violation
  - Hard course missing from plan and history → violation
  - Hard course in omitted_hard_courses → no violation (exempt)
  - Hard course in omitted_hard_courses AND completed history → no violation
  - Multiple missing hard courses → one violation each
  - GE placeholder slots not counted as planned non-placeholder courses
  - Empty hard_courses → no violations

check_ge_placeholders covers:
  - All GE categories have slots → no violations
  - Single GE category missing → violation
  - Double-dipped slot ("GE-C / GE-G") satisfies both categories
  - One category satisfied by double-dip, another standalone missing → violation only for missing
  - Empty ge_placeholders_remaining → no violations
  - Multiple missing GE categories → one violation each
"""

import pytest

from models.pipeline_state import CourseNode
from models.plan import PlanJSON, PlannedCourse, SemesterPlan, PlanMetadata
from nodes.validator_nodes import check_required_present, check_ge_placeholders


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _course(
    code: str,
    is_placeholder: bool = False,
    is_completed: bool = False,
) -> PlannedCourse:
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


def _hard(code: str) -> CourseNode:
    return CourseNode(course_code=code, name=code, units=4.0)


# ---------------------------------------------------------------------------
# check_required_present tests
# ---------------------------------------------------------------------------

def test_all_hard_courses_in_future_plan():
    """All hard courses appear in future semesters → no violations."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-310"), _course("CSCI-401")], index=0),
    )
    hard = [_hard("CSCI-310"), _hard("CSCI-401")]
    assert check_required_present(plan, hard, []) == []


def test_hard_course_in_completed_history():
    """Hard course already in a completed semester → satisfied, no violation."""
    plan = _plan(
        _semester("Spring 2026", [_course("CSCI-270", is_completed=True)], is_completed=True, index=0),
        _semester("Fall 2026", [_course("CSCI-310")], index=1),
    )
    hard = [_hard("CSCI-270"), _hard("CSCI-310")]
    assert check_required_present(plan, hard, []) == []


def test_hard_course_missing_from_plan():
    """Hard course absent from plan and history → violation."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-310")], index=0),
    )
    hard = [_hard("CSCI-310"), _hard("CSCI-401")]
    violations = check_required_present(plan, hard, [])
    assert len(violations) == 1
    assert violations[0].course_code == "CSCI-401"
    assert violations[0].semester is None
    assert "required for graduation" in violations[0].detail
    assert "not in the plan" in violations[0].detail


def test_hard_course_in_omitted_hard_courses():
    """Hard course explicitly omitted by LLM → exempt, no violation."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-310")], index=0),
    )
    hard = [_hard("CSCI-310"), _hard("MATH-125")]
    # MATH-125 omitted because downstream dependents are all completed
    assert check_required_present(plan, hard, omitted_hard_courses=["MATH-125"]) == []


def test_hard_course_in_omitted_and_completed():
    """Hard course listed in both omitted_hard_courses and completed history → no violation."""
    plan = _plan(
        _semester("Spring 2026", [_course("MATH-125", is_completed=True)], is_completed=True, index=0),
        _semester("Fall 2026", [_course("CSCI-310")], index=1),
    )
    hard = [_hard("CSCI-310"), _hard("MATH-125")]
    assert check_required_present(plan, hard, omitted_hard_courses=["MATH-125"]) == []


def test_multiple_missing_hard_courses():
    """Two hard courses missing → two violations."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-310")], index=0),
    )
    hard = [_hard("CSCI-310"), _hard("CSCI-401"), _hard("WRIT-340")]
    violations = check_required_present(plan, hard, [])
    assert len(violations) == 2
    missing_codes = {v.course_code for v in violations}
    assert missing_codes == {"CSCI-401", "WRIT-340"}


def test_ge_placeholder_not_counted_as_planned_course():
    """A GE placeholder slot does not satisfy a hard course requirement."""
    plan = _plan(
        _semester("Fall 2026", [
            _course("GE-C", is_placeholder=True),
        ], index=0),
    )
    # CSCI-310 required but only a GE slot is in the plan
    hard = [_hard("CSCI-310")]
    violations = check_required_present(plan, hard, [])
    assert len(violations) == 1
    assert violations[0].course_code == "CSCI-310"


def test_empty_hard_courses():
    """No hard courses required → no violations."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-310")], index=0),
    )
    assert check_required_present(plan, [], []) == []


def test_silent_omission_triggers_violation():
    """Course absent from plan and NOT in omitted_hard_courses → violation (silent omissions loop)."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-310")], index=0),
    )
    hard = [_hard("CSCI-310"), _hard("MATH-125")]
    # MATH-125 not in omitted_hard_courses despite not being placed
    violations = check_required_present(plan, hard, omitted_hard_courses=[])
    assert len(violations) == 1
    assert violations[0].course_code == "MATH-125"


# ---------------------------------------------------------------------------
# check_ge_placeholders tests
# ---------------------------------------------------------------------------

def test_all_ge_categories_have_slots():
    """Every required GE category has a placeholder slot → no violations."""
    plan = _plan(
        _semester("Fall 2026", [
            _course("GE-C", is_placeholder=True),
            _course("GE-A", is_placeholder=True),
        ], index=0),
    )
    assert check_ge_placeholders(plan, ["GE-C", "GE-A"]) == []


def test_single_ge_category_missing():
    """One GE category has no slot → violation."""
    plan = _plan(
        _semester("Fall 2026", [_course("GE-C", is_placeholder=True)], index=0),
    )
    violations = check_ge_placeholders(plan, ["GE-C", "GE-G"])
    assert len(violations) == 1
    assert violations[0].course_code == "GE-G"
    assert violations[0].semester is None
    assert "no placeholder slot" in violations[0].detail


def test_double_dipped_slot_satisfies_both_categories():
    """'GE-C / GE-G' slot satisfies both GE-C and GE-G → no violations."""
    plan = _plan(
        _semester("Fall 2026", [_course("GE-C / GE-G", is_placeholder=True)], index=0),
    )
    assert check_ge_placeholders(plan, ["GE-C", "GE-G"]) == []


def test_double_dip_satisfies_one_other_missing():
    """Double-dip covers GE-C and GE-G; GE-A is separately required but absent → violation."""
    plan = _plan(
        _semester("Fall 2026", [_course("GE-C / GE-G", is_placeholder=True)], index=0),
    )
    violations = check_ge_placeholders(plan, ["GE-C", "GE-G", "GE-A"])
    assert len(violations) == 1
    assert violations[0].course_code == "GE-A"


def test_empty_ge_placeholders_remaining():
    """No GE categories required → no violations."""
    plan = _plan(
        _semester("Fall 2026", [_course("CSCI-310")], index=0),
    )
    assert check_ge_placeholders(plan, []) == []


def test_multiple_ge_categories_missing():
    """Two GE categories missing → two violations."""
    plan = _plan(
        _semester("Fall 2026", [_course("GE-A", is_placeholder=True)], index=0),
    )
    violations = check_ge_placeholders(plan, ["GE-A", "GE-C", "GE-G"])
    assert len(violations) == 2
    missing = {v.course_code for v in violations}
    assert missing == {"GE-C", "GE-G"}


def test_ge_non_placeholder_course_does_not_satisfy():
    """A non-placeholder course with code starting 'GE-' does not satisfy a GE requirement."""
    plan = _plan(
        # is_placeholder=False — would not happen in practice, but check defensively
        _semester("Fall 2026", [_course("GE-C", is_placeholder=False)], index=0),
    )
    violations = check_ge_placeholders(plan, ["GE-C"])
    assert len(violations) == 1
    assert violations[0].course_code == "GE-C"


def test_ge_slots_across_multiple_semesters():
    """GE slots spread across semesters all counted toward satisfied set."""
    plan = _plan(
        _semester("Fall 2026", [_course("GE-A", is_placeholder=True)], index=0),
        _semester("Spring 2027", [_course("GE-C", is_placeholder=True)], index=1),
    )
    assert check_ge_placeholders(plan, ["GE-A", "GE-C"]) == []
