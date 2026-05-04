"""
Nodes for the schedule_validator_loop subgraph.

Node inventory (implementation order):
  reconstruct_timeline_node   — Python: group courses_taken into CompletedSemester list
  schedule_construction_node  — LLM (Sonnet): produce DraftSchedule
  merge_schedule_node         — Python: combine timeline + draft → PlanJSON + remarks
  validator_node              — Python: 5 hard checks → ValidationResult
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from pydantic import BaseModel, Field

from models.pipeline_state import CourseNode, ScoredCourse, SchedulerState
from models.plan import (
    CompletedSemester,
    DraftSchedule,
    SemesterSlot,
    ValidationResult,
    Violation,
)
from models.student_state import CourseRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Semester sort helpers (shared by reconstruct_timeline_node and merge_schedule_node)
# ---------------------------------------------------------------------------

# MVP: only Fall and Spring. Spring precedes Fall within the same year.
_SEASON_ORDER: dict[str, int] = {"Spring": 0, "Fall": 1}


def _semester_sort_key(label: str) -> tuple[int, int]:
    """Parse "Fall 2024" → (2024, 1) for chronological sorting."""
    parts = label.split()
    season = parts[0] if len(parts) >= 2 else ""
    year = int(parts[-1]) if parts and parts[-1].isdigit() else 0
    return (year, _SEASON_ORDER.get(season, 2))


# ---------------------------------------------------------------------------
# reconstruct_timeline_node
# ---------------------------------------------------------------------------

def reconstruct_timeline_node(state: SchedulerState) -> dict[str, Any]:
    """Group student_state.courses_taken into a chronologically ordered
    list of CompletedSemester objects.

    All courses in courses_taken are included — the STARS parser already
    determined what belongs there, including in-progress courses (>IP flag).
    In-progress courses are treated optimistically as completed for planning.

    merge_schedule_node later converts CourseRecord entries into PlannedCourse
    objects and sets is_completed per course based on the >IP flag.
    """
    courses: list[CourseRecord] = state["student_state"].courses_taken

    by_semester: defaultdict[str, list[CourseRecord]] = defaultdict(list)
    for course in courses:
        by_semester[course.semester_label].append(course)

    completed_timeline = [
        CompletedSemester(
            label=label,
            courses=semester_courses,
            total_units=sum(c.units for c in semester_courses),
        )
        for label, semester_courses in sorted(
            by_semester.items(),
            key=lambda kv: _semester_sort_key(kv[0]),
        )
    ]

    return {"completed_timeline": completed_timeline}


# ---------------------------------------------------------------------------
# schedule_construction_node — private LLM output schemas
# ---------------------------------------------------------------------------

class _CoursePlacement(BaseModel):
    """Minimal course reference in the LLM draft output.

    The LLM only outputs course_code (and optionally name for novel courses
    added via additional_requirements). All other CourseNode fields are
    looked up from the input course lists by _to_draft_schedule.
    """
    course_code: str
    name: str = ""


class _SemesterDraft(BaseModel):
    label: str                           # "Fall 2026"
    courses: list[_CoursePlacement]


class _ScheduleDraft(BaseModel):
    future_semesters: list[_SemesterDraft]
    remarks: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# schedule_construction_node — module-level lazy LLM
# ---------------------------------------------------------------------------

_llm_structured: Any = None


def _get_llm() -> Any:
    global _llm_structured
    if _llm_structured is None:
        from config import settings
        from langchain_anthropic import ChatAnthropic
        _llm_structured = ChatAnthropic(
            model="claude-sonnet-4-6",
            api_key=settings.anthropic_api_key,
            temperature=0,
        ).with_structured_output(_ScheduleDraft)
    return _llm_structured


# ---------------------------------------------------------------------------
# schedule_construction_node — helpers
# ---------------------------------------------------------------------------

def _build_course_lookup(
    hard_courses: list[CourseNode],
    scored_electives: list[ScoredCourse],
    scored_enrichment: list[list[ScoredCourse]],
) -> dict[str, CourseNode]:
    """Build a course_code → CourseNode index from all known input courses."""
    lookup: dict[str, CourseNode] = {}
    for c in hard_courses:
        lookup[c.course_code] = c
    for sc in scored_electives:
        lookup[sc.course.course_code] = sc.course
    for chain in scored_enrichment:
        for sc in chain:
            lookup[sc.course.course_code] = sc.course
    return lookup


def _to_draft_schedule(
    llm_output: _ScheduleDraft,
    course_lookup: dict[str, CourseNode],
) -> DraftSchedule:
    """Convert slim LLM output into a DraftSchedule with full CourseNode objects.

    Resolution order per course_code:
      1. GE placeholder (starts with "GE-") → synthetic 4-unit CourseNode
      2. Known course in lookup → full enriched CourseNode from inputs
      3. Unknown (novel via additional_requirements) → minimal stub CourseNode
    """
    future_semesters: list[SemesterSlot] = []

    for sem in llm_output.future_semesters:
        courses: list[CourseNode] = []
        for placement in sem.courses:
            code = placement.course_code
            if code.startswith("GE-"):
                node = CourseNode(
                    course_code=code,
                    name=placement.name or code,
                    units=4.0,
                )
            elif code in course_lookup:
                node = course_lookup[code]
            else:
                # Novel course injected via additional_requirements
                logger.warning("schedule_construction_node: unknown course %s — creating stub", code)
                node = CourseNode(course_code=code, name=placement.name)
            courses.append(node)

        future_semesters.append(SemesterSlot(
            label=sem.label,
            courses=courses,
            total_units=sum(c.units for c in courses),
        ))

    return DraftSchedule(
        future_semesters=future_semesters,
        remarks=llm_output.remarks,
    )


def _format_context(
    completed_timeline: list[CompletedSemester],
    hard_courses: list[CourseNode],
    ge_placeholders_remaining: list[str],
    scored_electives: list[ScoredCourse],
    scored_enrichment: list[list[ScoredCourse]],
    career_goal: str,
    additional_requirements: str,
    current_plan: Any,
    violations: list[Violation],
) -> str:
    """Serialize all scheduling inputs into a structured context string.

    Embedded verbatim into both Mode 1 and Mode 2 prompts. Each section
    is clearly labeled so the LLM can reference it by name.
    """
    lines: list[str] = []

    lines.append(f"Career goal: {career_goal}")

    lines.append("\n### Completed semesters (locked — do not reproduce in future plan)")
    if completed_timeline:
        for sem in completed_timeline:
            codes = ", ".join(c.course_code for c in sem.courses)
            lines.append(f"  {sem.label}: {codes} ({sem.total_units} units)")
    else:
        lines.append("  (none)")

    lines.append("\n### Required courses (all must appear in future plan)")
    for c in hard_courses:
        prereq_parts = []
        for group in c.prereqs:
            if isinstance(group, str):
                prereq_parts.append(group)
            else:
                prereq_parts.append(" or ".join(group))
        prereq_str = f" | prereqs: {', '.join(prereq_parts)}" if prereq_parts else ""
        lines.append(f"  {c.course_code} — {c.name} ({c.units} units){prereq_str}")

    lines.append(f"\n### GE placeholders to distribute: {ge_placeholders_remaining or '(none)'}")

    lines.append("\n### Elective pool (ranked by career alignment, descending)")
    for sc in scored_electives:
        lines.append(
            f"  [{sc.score:.2f}] {sc.course.course_code} — {sc.course.name} ({sc.course.units} units)"
        )

    lines.append("\n### Enrichment options (grouped as prereq chains; score = primary course score)")
    for chain in scored_enrichment:
        primary = chain[-1]
        if len(chain) == 1:
            lines.append(
                f"  [{primary.score:.2f}] {primary.course.course_code} — {primary.course.name} ({primary.course.units} units)"
            )
        else:
            prereq_codes = " → ".join(sc.course.course_code for sc in chain[:-1])
            chain_units = sum(sc.course.units for sc in chain)
            lines.append(
                f"  [{primary.score:.2f}] {prereq_codes} → {primary.course.course_code} — {primary.course.name}"
                f" (chain: {chain_units} units total)"
            )

    if violations:
        lines.append("\n### Violations to fix")
        for v in violations:
            loc = f" [{v.semester or v.course_code}]" if (v.semester or v.course_code) else ""
            lines.append(f"  -{loc} {v.detail}")

    if additional_requirements:
        lines.append(f"\n### Additional requirements\n{additional_requirements}")

    if current_plan:
        lines.append("\n### Current plan (future semesters only)")
        for sem in current_plan.semesters:
            if not sem.is_completed:
                codes = ", ".join(c.course_code for c in sem.courses)
                lines.append(f"  {sem.label}: {codes} ({sem.total_units} units)")

    return "\n".join(lines)


# Prompt templates — fill in the instruction text before the context block.
# {context} is replaced at call time with the output of _format_context().

_MODE1_PROMPT = """\
TODO: Fill in Mode 1 (first generation) instructions here.

{context}
"""

_MODE2_PROMPT = """\
TODO: Fill in Mode 2 (revision — violations or change request) instructions here.

{context}
"""


# ---------------------------------------------------------------------------
# schedule_construction_node
# ---------------------------------------------------------------------------

def schedule_construction_node(state: SchedulerState) -> dict[str, Any]:
    """Core generative node. Calls Claude Sonnet to produce a DraftSchedule.

    Mode 1 (first generation): violations empty, no additional_requirements,
    no current_plan. Builds a full plan from scratch.

    Mode 2 (revision): violations present, OR additional_requirements set, OR
    current_plan present. Makes minimum changes to fix violations / apply changes.
    """
    hard_courses: list[CourseNode]           = state.get("hard_courses", [])
    ge_placeholders_remaining: list[str]     = state.get("ge_placeholders_remaining", [])
    scored_electives: list[ScoredCourse]     = state.get("scored_electives", [])
    scored_enrichment: list[list[ScoredCourse]] = state.get("scored_enrichment", [])
    career_goal: str                         = state.get("career_goal", "")
    additional_requirements: str             = state.get("additional_requirements", "")
    current_plan                             = state.get("current_plan")
    completed_timeline: list[CompletedSemester] = state.get("completed_timeline", [])

    validation_result: ValidationResult | None = state.get("validation_result")
    violations = validation_result.violations if validation_result else []

    is_revision = bool(violations) or bool(additional_requirements) or current_plan is not None

    context = _format_context(
        completed_timeline=completed_timeline,
        hard_courses=hard_courses,
        ge_placeholders_remaining=ge_placeholders_remaining,
        scored_electives=scored_electives,
        scored_enrichment=scored_enrichment,
        career_goal=career_goal,
        additional_requirements=additional_requirements,
        current_plan=current_plan,
        violations=violations,
    )

    prompt = (_MODE2_PROMPT if is_revision else _MODE1_PROMPT).format(context=context)

    course_lookup = _build_course_lookup(hard_courses, scored_electives, scored_enrichment)

    try:
        llm_output: _ScheduleDraft = _get_llm().invoke(prompt)
        draft_schedule = _to_draft_schedule(llm_output, course_lookup)
    except Exception:
        logger.exception("schedule_construction_node LLM call failed — returning empty draft")
        draft_schedule = DraftSchedule(
            future_semesters=[],
            remarks=["Schedule generation failed — please try again."],
        )

    return {"draft_schedule": draft_schedule}
