"""
LangGraph state TypedDicts and intermediate Pydantic models for all graphs.

State hierarchy:
  StarsParserState   — standalone STARS parser graph (POST /api/stars)
  AdvisorState       — shared parent state for the full pipeline graph
  HardRequirementsState — AdvisorState + private intermediate keys internal
                          to the hard_requirements subgraph only

Pydantic models defined here are intermediate pipeline data structures
("all intermediate structures" per CLAUDE.md). Domain models (StudentState,
RequirementsMap, PlanJSON) live in their own model files.

LangGraph subgraph state pattern used here:
  HardRequirementsState extends AdvisorState with two private keys
  (hard_courses_stub, elective_courses_stub). When the compiled subgraph is
  added as a node to the pipeline graph, LangGraph maps shared key names
  automatically. The private keys exist only inside the subgraph's supersteps
  and are never written back to the parent pipeline state.
  Ref: https://docs.langchain.com/oss/python/langgraph/graph-api#multiple-schemas
"""

from __future__ import annotations

from typing import TypedDict, Union

from pydantic import BaseModel, Field

from models.requirements_map import RequirementsMap
from models.student_state import StudentState


# ---------------------------------------------------------------------------
# Intermediate Pydantic models
# ---------------------------------------------------------------------------

class OtherRequirements(BaseModel):
    """Non-course degree requirements derived arithmetically from StudentState."""
    units_total_required: float          # units_earned + units_in_process + units_needed
    units_still_needed: float            # direct from StudentState.units_needed

    upper_division_required: float       # sum of earned + in_process + needed (32.0 for USC CS)
    upper_division_still_needed: float   # direct from StudentState.upper_division_units_needed

    residency_satisfied: bool            # if true, scheduler needs no residency action
    writing_requirement_satisfied: bool  # if false, WRIT-340 appears in hard_courses
    ge_seminar_satisfied: bool           # if false, a GESM slot appears in hard_courses


# Prereq item in a CourseNode:
#   str       = a single required prerequisite (AND — must be satisfied)
#   list[str] = an OR group (any one of these satisfies this prereq slot)
#
# Example: ["CSCI-270", ["CSCI-350", "CSCI-356"]]
# means: CSCI-270 AND (CSCI-350 OR CSCI-356)
#
# This is the flattened form of ChromaDB's list[list[str]] (AND-of-OR groups):
# single-item OR groups are unwrapped to bare str for readability.
PrereqItem = Union[str, list[str]]


class CourseNode(BaseModel):
    """
    A course required or eligible for inclusion in the student's plan.

    Stub form (after hard_courses_node / elective_courses_node):
        Only course_code is set. All other fields hold their defaults.

    Enriched form (after course_details_retrieval_node):
        All fields populated from ChromaDB.
    """
    course_code: str                                        # "CSCI-467" — normalized, hyphenated
    name: str = ""                                          # "Foundations of Machine Learning"
    units: float = 0.0                                      # 4.0
    prereqs: list[PrereqItem] = Field(default_factory=list) # AND-of-OR prereq structure
    prereqs_satisfied: bool = False                         # computed against courses_taken
    description: str | None = None                          # from ChromaDB; None if not found


# ---------------------------------------------------------------------------
# LangGraph state TypedDicts
# ---------------------------------------------------------------------------

class StarsParserState(TypedDict):
    """State for the standalone STARS parser graph (POST /api/stars)."""
    stars_pdf_text: str
    student_state: StudentState | None
    requirements_map: RequirementsMap | None


class AdvisorState(TypedDict):
    """
    Shared state for the full pipeline graph (POST /api/pipeline).

    Fields are populated incrementally as subgraphs complete:
      - student_state, requirements_map: present at pipeline start (from STARS parser)
      - hard_courses … ge_placeholders_remaining: written by hard_requirements subgraph
      (future soft requirements, scheduler, validator fields added as implemented)
    """
    # ---- Pipeline inputs (from STARS parser, sent by frontend) ----
    stars_pdf_text: str
    student_state: StudentState
    requirements_map: RequirementsMap

    # ---- Hard requirements subgraph outputs ----
    hard_courses: list[CourseNode]               # remaining required courses, RAG-enriched
    elective_courses: list[CourseNode]           # approved elective pool, RAG-enriched
    other_must_reqs: OtherRequirements           # unit/residency/writing/GE-seminar flags
    ge_placeholders_remaining: list[str]         # GE category codes still needed e.g. ["GE-C", "GE-G"]


class HardRequirementsState(AdvisorState, total=False):
    """
    Internal state for the hard_requirements subgraph.

    Extends AdvisorState with two private intermediate keys that carry stub
    CourseNode lists from the parallel fan-out nodes to course_details_retrieval_node.
    These keys are never written back to the parent AdvisorState.

    Flow:
      hard_courses_node     → writes hard_courses_stub   (stubs, code-only)
      elective_courses_node → writes elective_courses_stub (stubs, code-only)
          ↓ (fan-in)
      course_details_retrieval_node
          reads  hard_courses_stub, elective_courses_stub
          writes hard_courses, elective_courses  (AdvisorState keys — enriched)

    Note: total=False makes the private keys optional at the TypedDict level.
    LangGraph does not enforce TypedDict completeness at runtime; state is a
    plain dict and these keys are absent until the parallel nodes write them.
    The AdvisorState keys they inherit remain required by their own definition.
    """
    hard_courses_stub: list[CourseNode]     # written by hard_courses_node
    elective_courses_stub: list[CourseNode] # written by elective_courses_node
