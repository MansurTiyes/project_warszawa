"""
Shared course-level Pydantic models used by both pipeline_state and plan.

Extracted from pipeline_state.py to break the circular import:
  pipeline_state.py → plan.py (for PlanJSON etc.)
  plan.py           → pipeline_state.py (for CourseNode)

Both modules now import from here instead.

All other code continues to import these from models.pipeline_state,
which re-exports them for backward compatibility.
"""

from __future__ import annotations

from typing import Union

from pydantic import BaseModel, Field


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


class ScoredCourse(BaseModel):
    """
    A course paired with a career-goal alignment score.

    Score is a property of (course × career_goal), not of the course alone —
    the same CourseNode can have different scores for different career goals.

    For electives: score derived from ChromaDB L2 distance via 1/(1+distance).
    For enrichment: score assigned by enrichment_rerank_node (Gemini Flash, 0.0–1.0).
    For enrichment prereq chains: score inherited from the primary dependent course.
    """
    course: CourseNode
    score: float  # 0.0–1.0


class OtherRequirements(BaseModel):
    """Non-course degree requirements derived arithmetically from StudentState."""
    units_total_required: float          # units_earned + units_in_process + units_needed
    units_still_needed: float            # direct from StudentState.units_needed

    upper_division_required: float       # sum of earned + in_process + needed (32.0 for USC CS)
    upper_division_still_needed: float   # direct from StudentState.upper_division_units_needed

    residency_satisfied: bool            # if true, scheduler needs no residency action
    writing_requirement_satisfied: bool  # if false, WRIT-340 appears in hard_courses
    ge_seminar_satisfied: bool           # if false, a GESM slot appears in hard_courses
