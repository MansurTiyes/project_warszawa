from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from models.course_nodes import CourseNode, OtherRequirements, ScoredCourse
from models.plan import PlanJSON
from models.requirements_map import RequirementsMap
from models.student_state import StudentState


class ChatRequest(BaseModel):
    message: str
    # Prior turn history — flat LangChain wire format: {"type": "human"/"ai", "content": "..."}
    # Deserialized in the route handler via langchain_core.messages.convert_to_messages.
    # Frontend must use "type" (not "role") as the discriminator key.
    # Note: messages_from_dict requires a nested {"type": ..., "data": {...}} shape and
    # is NOT used here — convert_to_messages accepts the flat form the frontend sends.
    messages: list[dict[str, Any]]

    # Pipeline state from localStorage — used to build ChatContext
    current_plan: PlanJSON | None
    student_state: StudentState
    requirements_map: RequirementsMap
    career_goal: str
    hard_courses: list[CourseNode]
    scored_electives: list[ScoredCourse]
    scored_enrichment: list[list[ScoredCourse]]
    other_must_reqs: OtherRequirements
    ge_placeholders_remaining: list[str]


class ChatResponse(BaseModel):
    message: str          # always present — text extracted from last AIMessage
    modify_intent: str | None  # non-null only when propose_schedule_change fired
