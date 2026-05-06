from __future__ import annotations

from pydantic import BaseModel

from models.course_nodes import CourseNode, OtherRequirements, ScoredCourse
from models.plan import PlanJSON
from models.requirements_map import RequirementsMap
from models.student_state import StudentState


class ScheduleModifyRequest(BaseModel):
    modify_intent: str              # chat agent's precise instruction — passed as additional_requirements

    # Current plan being modified — versions[currentIndex].plan from localStorage
    current_plan: PlanJSON

    # Full pipeline state from localStorage
    student_state: StudentState
    requirements_map: RequirementsMap
    career_goal: str
    hard_courses: list[CourseNode]
    other_must_reqs: OtherRequirements
    ge_placeholders_remaining: list[str]
    scored_electives: list[ScoredCourse]
    scored_enrichment: list[list[ScoredCourse]]


class ScheduleModifyResponse(BaseModel):
    new_plan: PlanJSON              # always present — scheduler always returns a plan
    diff_label: str                 # "Updated plan — Apr 7" — generated at request time
    schedule_remarks: list[str]
