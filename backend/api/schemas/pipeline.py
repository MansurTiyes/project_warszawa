from __future__ import annotations

from pydantic import BaseModel

from models.course_nodes import CourseNode, OtherRequirements, ScoredCourse
from models.plan import PlanJSON
from models.requirements_map import RequirementsMap
from models.student_state import StudentState


class PipelineRequest(BaseModel):
    career_goal: str
    student_state: StudentState
    requirements_map: RequirementsMap


class PipelineResponse(BaseModel):
    current_plan: PlanJSON
    schedule_remarks: list[str]
    diff_label: str                          # "Initial plan — Apr 7" — generated at request time
    hard_courses: list[CourseNode]
    other_must_reqs: OtherRequirements
    ge_placeholders_remaining: list[str]
    scored_electives: list[ScoredCourse]
    scored_enrichment: list[list[ScoredCourse]]
