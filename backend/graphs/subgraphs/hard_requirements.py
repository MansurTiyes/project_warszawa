"""
Hard requirements subgraph.

Called as a node inside the pipeline graph (POST /api/pipeline).
Normalises StudentState + RequirementsMap into enriched CourseNode lists
and scalar requirement flags consumed by all downstream subgraphs.

Graph topology:
    START
      ├── other_requirements_node    (Python, parallel)  → END
      ├── ge_placeholders_node       (Python, parallel)  → END
      ├── hard_courses_node          (Python, parallel)  ─┐
      └── elective_courses_node      (Python, parallel)  ─┘
                                                            ↓ fan-in
                                              course_details_retrieval_node  (RAG)
                                                            ↓
                                                           END

Fan-out: all four nodes start in the same superstep.
Fan-in:  course_details_retrieval_node waits for both stub-producing nodes before running.
other_requirements_node and ge_placeholders_node flow directly to END on their own paths.

State: HardRequirementsState (extends AdvisorState with two private intermediate keys).
When the compiled subgraph is added as a node to the pipeline graph, LangGraph maps the
shared AdvisorState keys automatically; the private stub keys stay internal.
Ref: https://docs.langchain.com/oss/python/langgraph/graph-api#multiple-schemas
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from models.pipeline_state import HardRequirementsState
from nodes.hard_requirements_nodes import (
    course_details_retrieval_node,
    elective_courses_node,
    ge_placeholders_node,
    hard_courses_node,
    other_requirements_node,
)


def _build() -> StateGraph:
    builder = StateGraph(HardRequirementsState)

    builder.add_node("other_requirements_node", other_requirements_node)
    builder.add_node("ge_placeholders_node", ge_placeholders_node)
    builder.add_node("hard_courses_node", hard_courses_node)
    builder.add_node("elective_courses_node", elective_courses_node)
    builder.add_node("course_details_retrieval_node", course_details_retrieval_node)

    # Fan-out: all four nodes start in the same superstep
    builder.add_edge(START, "other_requirements_node")
    builder.add_edge(START, "ge_placeholders_node")
    builder.add_edge(START, "hard_courses_node")
    builder.add_edge(START, "elective_courses_node")

    # other_requirements_node and ge_placeholders_node write to disjoint
    # AdvisorState keys and have no downstream dependency — flow directly to END
    builder.add_edge("other_requirements_node", END)
    builder.add_edge("ge_placeholders_node", END)

    # Fan-in: course_details_retrieval_node runs after both stub nodes complete
    builder.add_edge("hard_courses_node", "course_details_retrieval_node")
    builder.add_edge("elective_courses_node", "course_details_retrieval_node")
    builder.add_edge("course_details_retrieval_node", END)

    return builder.compile()


hard_requirements_subgraph = _build()
