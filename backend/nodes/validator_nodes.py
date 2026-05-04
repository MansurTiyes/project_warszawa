"""
Nodes for the schedule_validator_loop subgraph — validator side.

Node inventory:
  validator_node — Python: 5 hard checks → ValidationResult + iteration_count increment

Check inventory (all run every pass, collect all violations at once):
  1. check_no_duplicates          — no course appears twice in future plan
  2. check_prereq_ordering        — every prereq appears in a strictly earlier semester
  3. check_required_present       — every hard course appears in plan or omitted_hard_courses
     check_ge_placeholders        — every ge_placeholders_remaining category has a slot
  4. check_total_units            — future units >= other_must_reqs.units_still_needed
  5. check_semester_unit_cap      — no future semester exceeds 18 units
"""

from __future__ import annotations

import logging
from typing import Any

from models.pipeline_state import CourseNode, OtherRequirements, SchedulerState
from models.plan import PlanJSON, ValidationResult, Violation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Check 1: No duplicate courses
# ---------------------------------------------------------------------------

def check_no_duplicates(plan: PlanJSON) -> list[Violation]:
    """No course code may appear more than once across all future semesters.

    Skips completed semesters (locked — duplicates there are a STARS data issue,
    not a scheduler error). Skips GE placeholders (is_placeholder=True) since
    the same GE category code can legally appear as a double-dip in one slot only,
    and distinct category codes are always distinct strings.
    """
    seen: set[str] = set()
    violations: list[Violation] = []

    for semester in plan.semesters:
        if semester.is_completed:
            continue
        for course in semester.courses:
            if course.is_placeholder:
                continue
            if course.course_code in seen:
                violations.append(Violation(
                    course_code=course.course_code,
                    semester=semester.label,
                    detail=f"{course.course_code} appears more than once in the future plan",
                ))
            seen.add(course.course_code)

    return violations


# ---------------------------------------------------------------------------
# Check 2: Prerequisite ordering
# ---------------------------------------------------------------------------

def _prereq_detail(dependent: str, prereq: str, satisfied: set[str], plan: PlanJSON) -> str:
    """Return an actionable violation message for a missing or misordered prereq.

    When called, prereq is guaranteed not to be in `satisfied`. The message
    distinguishes two cases:
      - prereq is in the future plan but in the wrong order → "same or later, move earlier"
      - prereq is absent from both plan and completed history → "not in plan, add or drop"
    """
    plan_codes = {
        c.course_code
        for s in plan.semesters if not s.is_completed
        for c in s.courses
    }
    if prereq not in plan_codes and prereq not in satisfied:
        return (
            f"{dependent} requires {prereq}, which is not in the plan. "
            f"Add {prereq} in an earlier semester, or drop {dependent} if it is optional enrichment."
        )
    return (
        f"{dependent} requires {prereq}, which appears in the same or a later semester. "
        f"Move {prereq} earlier."
    )


def check_prereq_ordering(plan: PlanJSON) -> list[Violation]:
    """Every prereq must appear in a strictly earlier semester than its dependent.

    Forward-scan approach: seed `satisfied` from completed semesters, iterate
    future semesters in semester_index order, check each AND-of-OR prereq group,
    then add the semester's courses to `satisfied` before moving to the next.

    PrereqItem is Union[str, list[str]]:
      str        → single AND-prereq (must be in satisfied)
      list[str]  → OR-group (any one option satisfies this AND slot)
    """
    satisfied: set[str] = {
        c.course_code
        for s in plan.semesters if s.is_completed
        for c in s.courses
    }

    violations: list[Violation] = []
    future = sorted(
        [s for s in plan.semesters if not s.is_completed],
        key=lambda s: s.semester_index,
    )

    for semester in future:
        for course in semester.courses:
            if course.is_placeholder:
                continue
            if course.prereqs_satisfied:
                # All prereqs confirmed satisfied by student history (courses_taken,
                # waivers, transfers). Trust the upstream computation — no ordering
                # check needed. Without this, CW-waived prereqs trigger false violations
                # because waived courses are not in courses_taken.
                continue
            for group in course.prereqs:
                if isinstance(group, str):
                    if group not in satisfied:
                        violations.append(Violation(
                            course_code=course.course_code,
                            semester=semester.label,
                            detail=_prereq_detail(course.course_code, group, satisfied, plan),
                        ))
                else:
                    # OR-group: at least one option must be satisfied
                    if not any(p in satisfied for p in group):
                        best = group[0]  # canonical representative for the error message
                        violations.append(Violation(
                            course_code=course.course_code,
                            semester=semester.label,
                            detail=_prereq_detail(course.course_code, best, satisfied, plan),
                        ))

        # This semester's courses are now available as prereqs for subsequent semesters
        for course in semester.courses:
            satisfied.add(course.course_code)

    return violations


# ---------------------------------------------------------------------------
# Check 3: Hard courses present + GE placeholders present
# ---------------------------------------------------------------------------

def check_required_present(
    plan: PlanJSON,
    hard_courses: list[CourseNode],
    omitted_hard_courses: list[str],
) -> list[Violation]:
    """Every hard course must appear in completed history, the future plan, or omitted_hard_courses.

    omitted_hard_courses contains codes the LLM explicitly exempted (redundancy argument).
    Any hard course absent from all three sources triggers a violation.
    GE placeholder slots (is_placeholder=True) are excluded from the planned set —
    they don't satisfy a hard course requirement.
    """
    required = {c.course_code for c in hard_courses}
    satisfied = {
        c.course_code
        for s in plan.semesters if s.is_completed
        for c in s.courses
    }
    still_needed = required - satisfied - set(omitted_hard_courses)
    planned = {
        c.course_code
        for s in plan.semesters if not s.is_completed
        for c in s.courses
        if not c.is_placeholder
    }
    return [
        Violation(
            course_code=code,
            semester=None,
            detail=f"{code} is required for graduation but is not in the plan",
        )
        for code in still_needed - planned
    ]


def check_ge_placeholders(
    plan: PlanJSON,
    ge_placeholders_remaining: list[str],
) -> list[Violation]:
    """Every GE category in ge_placeholders_remaining must have a placeholder slot in the plan.

    Double-dipped slots use a combined course_code e.g. "GE-C / GE-G". Split on " / "
    to collect all satisfied categories from a single slot.
    Iterates all semesters (completed semesters never have is_placeholder=True in practice).
    """
    satisfied_ge: set[str] = set()
    for s in plan.semesters:
        for c in s.courses:
            if c.is_placeholder:
                satisfied_ge.update(c.course_code.split(" / "))

    return [
        Violation(
            course_code=code,
            semester=None,
            detail=f"GE category {code} has no placeholder slot in the plan",
        )
        for code in ge_placeholders_remaining
        if code not in satisfied_ge
    ]


# ---------------------------------------------------------------------------
# Check 4: Total units sufficient
# ---------------------------------------------------------------------------

def check_total_units(
    plan: PlanJSON,
    other_must_reqs: OtherRequirements,
) -> list[Violation]:
    """Future unit total must meet the units still needed for graduation.

    GE placeholder units count (they are 4-unit courses, just unspecified).
    Completed semester units are excluded — only future units matter here.
    """
    future_units = sum(
        c.units
        for s in plan.semesters if not s.is_completed
        for c in s.courses
    )
    needed = other_must_reqs.units_still_needed
    if future_units < needed:
        return [Violation(
            course_code=None,
            semester=None,
            detail=f"Plan covers {future_units} future units — {needed} still needed for graduation",
        )]
    return []


# ---------------------------------------------------------------------------
# Check 5: Per-semester unit cap
# ---------------------------------------------------------------------------

def check_semester_unit_cap(plan: PlanJSON, cap: float = 18.0) -> list[Violation]:
    """No future semester may exceed the per-semester unit cap (default 18).

    Uses SemesterPlan.total_units — the sum already computed by merge_schedule_node.
    Completed semesters are not checked.
    """
    return [
        Violation(
            course_code=None,
            semester=s.label,
            detail=f"{s.label} has {s.total_units} units — exceeds {cap} unit cap",
        )
        for s in plan.semesters
        if not s.is_completed and s.total_units > cap
    ]


# ---------------------------------------------------------------------------
# validator_node
# ---------------------------------------------------------------------------

def validator_node(state: SchedulerState) -> dict[str, Any]:
    """Run all hard checks against current_plan. Increment iteration_count.

    All checks run even if an earlier one fails — violations are collected in
    one pass so the scheduler can fix multiple issues per loop iteration.

    Writes:
      validation_result  — ValidationResult with is_valid and violations list
      iteration_count    — incremented by 1 (drives MAX_ITERATIONS routing)
    """
    current_plan: PlanJSON                  = state["current_plan"]
    hard_courses: list[CourseNode]          = state.get("hard_courses", [])
    other_must_reqs: OtherRequirements | None = state.get("other_must_reqs")
    ge_placeholders_remaining: list[str]    = state.get("ge_placeholders_remaining", [])
    omitted_hard_courses: list[str]         = state.get("omitted_hard_courses", [])
    iteration_count: int                    = state.get("iteration_count", 0)

    violations: list[Violation] = []

    # Check 1
    violations.extend(check_no_duplicates(current_plan))

    # Check 2
    violations.extend(check_prereq_ordering(current_plan))

    # Check 3 (hard courses + GE placeholders)
    violations.extend(check_required_present(current_plan, hard_courses, omitted_hard_courses))
    violations.extend(check_ge_placeholders(current_plan, ge_placeholders_remaining))

    # Check 4
    violations.extend(check_total_units(current_plan, other_must_reqs))

    # Check 5
    violations.extend(check_semester_unit_cap(current_plan))

    is_valid = len(violations) == 0
    result = ValidationResult(is_valid=is_valid, violations=violations)

    if violations:
        logger.warning(
            "validator_node iteration %d: %d violation(s)",
            iteration_count + 1,
            len(violations),
        )
        for v in violations:
            logger.warning("  • [%s] %s", v.course_code or v.semester or "plan", v.detail)
    else:
        logger.info("validator_node iteration %d: plan is valid", iteration_count + 1)

    return {
        "validation_result": result,
        "iteration_count": iteration_count + 1,
    }
