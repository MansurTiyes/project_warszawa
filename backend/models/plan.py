"""
Plan domain models and scheduler-internal intermediates.

PlanJSON is the canonical schedule artifact — stored in localStorage,
rendered as the visual grid, and passed back to schedule_construction_node
on re-entry.

DraftSchedule / SemesterSlot / CompletedSemester are private to
SchedulerState and are never written back to the parent pipeline state.

ValidationResult / Violation are outputs of validator_node, stored as
private state in SchedulerState and read by schedule_construction_node
on loop re-entry.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from models.course_nodes import CourseNode
from models.student_state import CourseRecord


# ---------------------------------------------------------------------------
# Scheduler private intermediates
# ---------------------------------------------------------------------------

class CompletedSemester(BaseModel):
    """One completed semester reconstructed from student_state.courses_taken.

    Produced by reconstruct_timeline_node. Consumed by merge_schedule_node.
    """
    label: str
    courses: list[CourseRecord]
    total_units: float


class SemesterSlot(BaseModel):
    """One future semester as produced by schedule_construction_node (LLM output).

    courses contains sparse CourseNode objects — the LLM is only required to
    populate course_code. merge_schedule_node looks up full details (units,
    prereqs, description) from the input course lists and promotes each
    CourseNode to a PlannedCourse with annotation fields populated.
    """
    label: str
    courses: list[CourseNode]
    total_units: float


class DraftSchedule(BaseModel):
    """Raw structured output of schedule_construction_node.

    Contains only future semesters. merge_schedule_node combines this with
    completed_timeline to produce the final PlanJSON.
    """
    future_semesters: list[SemesterSlot]
    remarks: list[str]
    omitted_hard_courses: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Validator output
# ---------------------------------------------------------------------------

class Violation(BaseModel):
    course_code: str | None = None
    semester: str | None = None
    detail: str  # precise, actionable — passed verbatim into the scheduler prompt


class ValidationResult(BaseModel):
    """Output of validator_node. Drives the scheduler loop conditional edge."""
    is_valid: bool = True
    violations: list[Violation] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Final plan artifact
# ---------------------------------------------------------------------------

class PlannedCourse(CourseNode):
    """
    A course in the final PlanJSON.

    Inherits all CourseNode fields (course_code, name, units, prereqs,
    prereqs_satisfied, description) so the validator can access prereqs
    directly without unwrapping a wrapper type.

    Annotation fields are populated by merge_schedule_node from context —
    never by the LLM.
    """
    category: str = ""
    # "core" | "additional_required" | "capstone" | "math_foundation" |
    # "science_foundation" | "writing" | "technical_elective" |
    # "enrichment" | "ge_placeholder"
    is_placeholder: bool = False   # True for GE slots (course_code = "GE-C" etc.)
    is_completed: bool = False     # True for courses in courses_taken (past semesters)
    is_enabler: bool = False       # True if placed purely as prereq for an enrichment course


class SemesterPlan(BaseModel):
    """One semester in the final PlanJSON."""
    label: str           # "Fall 2024", "Spring 2026"
    semester_index: int  # 0-based stable sort key — use this, not string parsing
    is_completed: bool   # True for past semesters reconstructed from courses_taken
    total_units: float
    courses: list[PlannedCourse]


class PlanMetadata(BaseModel):
    elective_units_placed: float = 0.0
    enrichment_count: int = 0
    ge_placeholders_placed: int = 0
    unresolved_remarks: list[str] = Field(default_factory=list)
    # e.g. "No space found for GE-G placeholder" — surfaces in UI as schedule remarks


class PlanJSON(BaseModel):
    """
    Canonical schedule artifact.

    Stored in localStorage after pipeline completion, rendered as the
    visual grid, and passed back to schedule_construction_node on re-entry
    (as current_plan in AdvisorState / SchedulerState).
    """
    generated_at: str        # ISO timestamp
    version_id: int = 1      # incremented by the API on each accepted update
    track: str               # career_goal string e.g. "machine learning engineer"

    total_units_planned: float = 0.0   # sum of all future course units (including placeholders)
    total_semesters: int = 0           # completed + future

    semesters: list[SemesterPlan] = Field(default_factory=list)
    metadata: PlanMetadata = Field(default_factory=PlanMetadata)
