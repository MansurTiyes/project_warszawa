"""
STARS Parser graph — parallel extraction of StudentState and RequirementsMap.

Called via .ainvoke() from POST /api/stars. Not part of the pipeline graph —
the pipeline accepts the results of this graph as inputs to /api/pipeline.

Graph topology:
    START → student_state_node  ─┐
    START → requirements_map_node ┘→ END

Both nodes execute in the same superstep (LangGraph parallel fan-out).
They write to disjoint state keys so no reducer is needed.
If either node fails the entire superstep fails — neither result is applied.

Input state key:   stars_pdf_text   (str)
Output state keys: student_state, requirements_map
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from models.pipeline_state import StarsParserState
from nodes.stars_parser_nodes import requirements_map_node, student_state_node


def _build() -> StateGraph:
    builder = StateGraph(StarsParserState)

    builder.add_node("student_state_node", student_state_node)
    builder.add_node("requirements_map_node", requirements_map_node)

    # Fan-out: both nodes start in the same superstep
    builder.add_edge(START, "student_state_node")
    builder.add_edge(START, "requirements_map_node")

    # Fan-in: graph exits once both nodes complete
    builder.add_edge("student_state_node", END)
    builder.add_edge("requirements_map_node", END)

    return builder.compile()


stars_parser_graph = _build()
