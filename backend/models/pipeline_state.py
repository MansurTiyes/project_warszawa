"""
LangGraph state TypedDicts for all graphs in the pipeline.

Nodes import their state type from here. Graphs import from both nodes and
models — keeping state definitions here avoids circular imports.
"""

from __future__ import annotations

from typing import TypedDict

from models.requirements_map import RequirementsMap
from models.student_state import StudentState


class StarsParserState(TypedDict):
    stars_pdf_text: str
    student_state: StudentState | None
    requirements_map: RequirementsMap | None


# AdvisorState (full pipeline graph state) will be defined here once the
# remaining pipeline models (plan.py, etc.) exist.
