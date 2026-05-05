"""
Pipeline graph — top-level orchestrator for POST /api/pipeline.

Sequences three compiled subgraphs as nodes in a linear chain:
    START → hard_requirements → soft_requirements → scheduler → END

State: AdvisorState (shared by all three subgraphs via key-name mapping).
LangGraph maps keys between parent and subgraph by name automatically when
a compiled subgraph is passed to add_node.

Inputs (provided at invoke / ainvoke time):
    student_state: StudentState
    requirements_map: RequirementsMap
    career_goal: str

Note: additional_requirements is intentionally absent from pipeline inputs.
The scheduler reads it via state.get("additional_requirements", "") which
returns "" when the key is not present. The key is only populated when the
scheduler subgraph is invoked from /api/schedule/modify (a separate graph
with its own state initialization).

Key writes by subgraph, accumulated in AdvisorState:
    hard_requirements  → hard_courses, elective_courses (internal),
                         other_must_reqs, ge_placeholders_remaining
    soft_requirements  → scored_electives, scored_enrichment
    scheduler          → current_plan, remarks

Final AdvisorState outputs consumed by /api/pipeline response:
    hard_courses: list[CourseNode]
    ge_placeholders_remaining: list[str]
    other_must_reqs: OtherRequirements
    scored_electives: list[ScoredCourse]
    scored_enrichment: list[list[ScoredCourse]]
    current_plan: PlanJSON
    remarks: list[str]
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from graphs.subgraphs.hard_requirements import hard_requirements_subgraph
from graphs.subgraphs.scheduler import schedule_validator_loop
from graphs.subgraphs.soft_requirements.subgraph import soft_requirements_subgraph
from models.pipeline_state import AdvisorState


def _build() -> StateGraph:
    builder = StateGraph(AdvisorState)

    builder.add_node("hard_requirements", hard_requirements_subgraph)
    builder.add_node("soft_requirements", soft_requirements_subgraph)
    builder.add_node("scheduler", schedule_validator_loop)

    builder.add_edge(START, "hard_requirements")
    builder.add_edge("hard_requirements", "soft_requirements")
    builder.add_edge("soft_requirements", "scheduler")
    builder.add_edge("scheduler", END)

    return builder.compile()


pipeline_graph = _build()
