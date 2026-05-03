"""
Enrichment retrieval subgraph.

One of two parallel sub-subgraphs inside soft_requirements_subgraph.
Retrieves and ranks a pool of career-aligned enrichment courses from beyond
the approved technical elective pool, then groups them into prereq chains.

Graph topology (sequential):
    START
      ↓
    enrichment_courses_retrieval_node   (RAG — ChromaDB bi-encoder, top 50)
      ↓
    remove_taken_courses_node           (Python — dedup against student history)
      ↓
    enrichment_rerank_node              (LLM — Gemini Flash, structured output, top 10)
      ↓
    find_missing_prereqs_node           (Python + RAG — recursive prereq chain expansion)
      ↓
    END → scored_enrichment: list[list[ScoredCourse]]

State: EnrichmentState (extends SoftRequirementsState → AdvisorState).
Shared keys (read from parent, written back):
    career_goal, student_state, elective_courses  ← inputs
    scored_enrichment                             ← output
Private intermediate keys (never written back to parent):
    temp_retrieved_enrichment, retrieved_enrichment, scored_enrichment_flat

Because EnrichmentState shares keys with the parent SoftRequirementsState,
the compiled subgraph is passed directly to add_node — no wrapper needed.
LangGraph maps shared keys automatically; private keys stay internal.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from models.pipeline_state import EnrichmentState
from nodes.soft_requirements_nodes import (
    enrichment_courses_retrieval_node,
    enrichment_rerank_node,
    find_missing_prereqs_node,
    remove_taken_courses_node,
)


def _build() -> StateGraph:
    builder = StateGraph(EnrichmentState)

    builder.add_node("enrichment_courses_retrieval_node", enrichment_courses_retrieval_node)
    builder.add_node("remove_taken_courses_node", remove_taken_courses_node)
    builder.add_node("enrichment_rerank_node", enrichment_rerank_node)
    builder.add_node("find_missing_prereqs_node", find_missing_prereqs_node)

    builder.add_edge(START, "enrichment_courses_retrieval_node")
    builder.add_edge("enrichment_courses_retrieval_node", "remove_taken_courses_node")
    builder.add_edge("remove_taken_courses_node", "enrichment_rerank_node")
    builder.add_edge("enrichment_rerank_node", "find_missing_prereqs_node")
    builder.add_edge("find_missing_prereqs_node", END)

    return builder.compile()


enrichment_subgraph = _build()
