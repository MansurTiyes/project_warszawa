"""
Soft requirements subgraph.

Called as a node inside the pipeline graph (POST /api/pipeline).
Pre-answers all ranking and retrieval questions so the Scheduler only
has to optimize — not search.

Graph topology (parallel fan-out, no fan-in):
    START
      ├── score_electives_node    (RAG — ChromaDB bi-encoder query)   → END
      └── enrichment_subgraph     (compiled — 4-node sequential chain) → END

Both branches write to disjoint AdvisorState keys and have no shared
downstream dependency, so they flow directly to END independently.
LangGraph runs them in the same superstep.

State: SoftRequirementsState (extends AdvisorState, no private keys).
Reads from parent:  elective_courses, career_goal, student_state
Writes to parent:   scored_electives, scored_enrichment

Because SoftRequirementsState shares keys with AdvisorState (the pipeline
graph's state), the compiled subgraph is passed directly to add_node in
the pipeline graph — no wrapper function needed.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from graphs.subgraphs.soft_requirements.enrichment_retrieval import enrichment_subgraph
from models.pipeline_state import SoftRequirementsState
from nodes.soft_requirements_nodes import score_electives_node


def _build() -> StateGraph:
    builder = StateGraph(SoftRequirementsState)

    # score_electives_node is a single node — no wrapper subgraph needed
    builder.add_node("score_electives_node", score_electives_node)

    # enrichment_subgraph is a compiled CompiledStateGraph; shared keys are
    # mapped automatically by LangGraph — pass directly to add_node
    builder.add_node("enrichment_subgraph", enrichment_subgraph)

    # Fan-out: both start in the same superstep
    builder.add_edge(START, "score_electives_node")
    builder.add_edge(START, "enrichment_subgraph")

    # Both write to disjoint keys — flow directly to END independently
    builder.add_edge("score_electives_node", END)
    builder.add_edge("enrichment_subgraph", END)

    return builder.compile()


soft_requirements_subgraph = _build()
