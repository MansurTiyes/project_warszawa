"""
Schedule validator loop subgraph.

Called as a node inside the pipeline graph (POST /api/pipeline).
Produces current_plan (PlanJSON) and remarks from all scheduler inputs.

Graph topology:
    START
      ↓
    reconstruct_timeline_node   (Python) — runs once; builds completed_timeline
      ↓
    schedule_construction_node  (LLM — Sonnet) ←──────────────────────────┐
      ↓                                                                    │
    merge_schedule_node         (Python) — combines timeline + draft       │
      ↓                                   into PlanJSON + annotations      │
    validator_node              (Python) — 5 hard checks → ValidationResult│
      ↓                                                                    │
    _route_after_validator ─── is_valid or MAX_ITERATIONS? → END          │
                           └── violations remain, iterations left? ────────┘
                               (skips reconstruct — completed_timeline unchanged)

State: SchedulerState (extends AdvisorState, total=False).
Private intermediate keys (never written back to parent pipeline state):
  completed_timeline, draft_schedule, validation_result,
  iteration_count, omitted_hard_courses

Shared keys written back to AdvisorState:
  current_plan  — updated on every merge_schedule_node pass
  remarks       — overwritten on every pass; only final iteration reaches user
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from config import settings
from models.pipeline_state import SchedulerState
from nodes.scheduler_nodes import (
    merge_schedule_node,
    reconstruct_timeline_node,
    schedule_construction_node,
)
from nodes.validator_nodes import validator_node


def _route_after_validator(state: SchedulerState) -> str:
    """Conditional edge after validator_node.

    Returns END if the plan is valid or MAX_ITERATIONS has been reached.
    Returns 'schedule_construction_node' to loop for another revision pass.
    reconstruct_timeline_node is skipped on re-entry — completed_timeline
    is unchanged between iterations and remains in state from the first pass.
    """
    result = state["validation_result"]
    if result.is_valid:
        return END
    if state.get("iteration_count", 0) >= settings.max_validator_iterations:
        # Surface unresolved violations in remarks rather than looping forever.
        # merge_schedule_node already wrote the best-effort plan to current_plan.
        return END
    return "schedule_construction_node"


def _build() -> StateGraph:
    builder = StateGraph(SchedulerState)

    builder.add_node("reconstruct_timeline_node", reconstruct_timeline_node)
    builder.add_node("schedule_construction_node", schedule_construction_node)
    builder.add_node("merge_schedule_node", merge_schedule_node)
    builder.add_node("validator_node", validator_node)

    # Linear chain — first pass
    builder.add_edge(START, "reconstruct_timeline_node")
    builder.add_edge("reconstruct_timeline_node", "schedule_construction_node")
    builder.add_edge("schedule_construction_node", "merge_schedule_node")
    builder.add_edge("merge_schedule_node", "validator_node")

    # Conditional loop: valid or exhausted → END; violations remain → revise
    builder.add_conditional_edges("validator_node", _route_after_validator)

    return builder.compile()


schedule_validator_loop = _build()
