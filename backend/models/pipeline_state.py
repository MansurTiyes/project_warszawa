"""
LangGraph state TypedDicts and intermediate Pydantic models for all graphs.

State hierarchy:
  StarsParserState        — standalone STARS parser graph (POST /api/stars)
  AdvisorState            — shared parent state for the full pipeline graph
  HardRequirementsState   — AdvisorState + private stubs (hard_requirements subgraph)
  SoftRequirementsState   — AdvisorState subtype; documents soft_requirements I/O contract
  EnrichmentState         — SoftRequirementsState + private intermediates (enrichment_subgraph)

Pydantic models defined here are intermediate pipeline data structures
("all intermediate structures" per CLAUDE.md). Domain models (StudentState,
RequirementsMap, PlanJSON) live in their own model files.

LangGraph subgraph state pattern:
  Subgraph states extend AdvisorState (or a subtype) with total=False and add
  private intermediate keys. When a compiled subgraph is added as a node to the
  pipeline graph, LangGraph maps shared key names automatically. Private keys
  exist only inside the subgraph's supersteps and are never written back to the
  parent pipeline state.
  Ref: https://docs.langchain.com/oss/python/langgraph/graph-api#multiple-schemas
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict, Union

from pydantic import BaseModel, Field

from models.requirements_map import RequirementsMap
from models.student_state import StudentState

if TYPE_CHECKING:
    from models.plan import (
        CompletedSemester,
        DraftSchedule,
        PlanJSON,
        ValidationResult,
    )


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
      - student_state, requirements_map, career_goal: present at pipeline start
      - hard_courses … ge_placeholders_remaining: written by hard_requirements subgraph
      - scored_electives, scored_enrichment: written by soft_requirements subgraph
      (scheduler, validator fields added as implemented)
    """
    # ---- Pipeline inputs (from STARS parser + Step 2 chip, sent by frontend) ----
    stars_pdf_text: str
    student_state: StudentState
    requirements_map: RequirementsMap
    career_goal: str                             # Step 2 chip — e.g. "machine learning engineer"

    # ---- Hard requirements subgraph outputs ----
    hard_courses: list[CourseNode]               # remaining required courses, RAG-enriched
    elective_courses: list[CourseNode]           # approved elective pool, RAG-enriched
    other_must_reqs: OtherRequirements           # unit/residency/writing/GE-seminar flags
    ge_placeholders_remaining: list[str]         # GE category codes still needed e.g. ["GE-C", "GE-G"]

    # ---- Soft requirements subgraph outputs ----
    scored_electives: list[ScoredCourse]         # elective pool ranked by career alignment, desc
    scored_enrichment: list[list[ScoredCourse]]  # prereq chains ranked by primary course score, desc

    # ---- Scheduler subgraph inputs (chat-driven) ----
    additional_requirements: str                 # free-form change instruction from chat; "" on initial run

    # ---- Scheduler subgraph outputs ----
    current_plan: PlanJSON | None                # None until scheduler runs; updated on every loop pass
    remarks: list[str]                           # inline remarks from schedule_construction_node


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


class SoftRequirementsState(AdvisorState, total=False):
    """
    State for the soft_requirements_subgraph.

    Reads from AdvisorState:  elective_courses, career_goal, student_state
    Writes to AdvisorState:   scored_electives, scored_enrichment

    No private intermediate keys at this level — all intermediates live inside
    EnrichmentState (the enrichment_subgraph's internal state). This class exists
    to document the subgraph's I/O contract and for LangGraph state type annotation.

    Two sub-subgraphs run as parallel nodes inside soft_requirements_subgraph:
      elective_ranking_subgraph  → uses SoftRequirementsState directly (single node, no privates)
      enrichment_subgraph        → uses EnrichmentState (4 nodes, 3 private intermediates)
    """


class EnrichmentState(TypedDict, total=False):
    """
    Internal state for the enrichment_subgraph.

    Standalone TypedDict — does NOT extend AdvisorState or SoftRequirementsState.
    Intentionally excludes scored_electives: the enrichment subgraph never reads
    or writes that key. If EnrichmentState inherited AdvisorState, the compiled
    subgraph would write scored_electives: [] back to the parent in the same
    superstep as score_electives_node, causing a LangGraph parallel-write conflict.
    Keeping it standalone eliminates the conflict without needing a wrapper node.

    LangGraph maps shared keys by name automatically when the compiled subgraph is
    added via add_node — inheritance is not required for key mapping to work.

    Inputs (populated at invocation, shared with SoftRequirementsState):
      stars_pdf_text, student_state, requirements_map, career_goal,
      hard_courses, elective_courses, other_must_reqs, ge_placeholders_remaining

    Output (written back to SoftRequirementsState / AdvisorState):
      scored_enrichment

    Private intermediates (never written back to parent):
      temp_retrieved_enrichment, retrieved_enrichment, scored_enrichment_flat

    Flow:
      enrichment_courses_retrieval_node
          reads  career_goal
          writes temp_retrieved_enrichment   (top 50 from ChromaDB, unfiltered)
          ↓
      remove_taken_courses_node
          reads  temp_retrieved_enrichment, student_state, elective_courses
          writes retrieved_enrichment        (taken courses, elective dupes removed)
          ↓
      enrichment_rerank_node
          reads  retrieved_enrichment, career_goal
          writes scored_enrichment_flat      (top 10, sorted desc, no chains yet)
          ↓
      find_missing_prereqs_node
          reads  scored_enrichment_flat, student_state
          writes scored_enrichment           (grouped prereq chains)
    """
    # ---- inputs (shared keys — populated at invocation) ----
    stars_pdf_text: str
    student_state: StudentState
    requirements_map: RequirementsMap
    career_goal: str
    hard_courses: list[CourseNode]
    elective_courses: list[CourseNode]
    other_must_reqs: OtherRequirements
    ge_placeholders_remaining: list[str]

    # ---- output (written back to parent) ----
    scored_enrichment: list[list[ScoredCourse]]

    # ---- private intermediates (never written back to parent) ----
    temp_retrieved_enrichment: list[CourseNode]   # top-50 ChromaDB results, pre-dedup
    retrieved_enrichment: list[CourseNode]         # post-dedup, ready for LLM rerank
    scored_enrichment_flat: list[ScoredCourse]     # top-10 scored, pre-prereq-chain grouping


class SchedulerState(AdvisorState, total=False):
    """
    Internal state for the schedule_validator_loop subgraph.

    Extends AdvisorState with private intermediate keys that are never
    written back to the parent pipeline state.

    Public keys read from AdvisorState:
      hard_courses, ge_placeholders_remaining, scored_electives,
      scored_enrichment, career_goal, additional_requirements,
      student_state, current_plan

    Public keys written back to AdvisorState:
      current_plan   (updated on every merge_schedule_node pass)
      remarks        (overwritten on every merge_schedule_node pass;
                      only the final iteration's remarks reach the user)

    Private intermediates (never written back):
      completed_timeline  — from reconstruct_timeline_node
      draft_schedule      — from schedule_construction_node
      validation_result   — from validator_node
      iteration_count     — incremented by validator_node; drives MAX_ITERATIONS check

    Loop flow:
      reconstruct_timeline_node
          reads  student_state
          writes completed_timeline
          ↓
      schedule_construction_node
          reads  completed_timeline, hard_courses, ge_placeholders_remaining,
                 scored_electives, scored_enrichment, career_goal,
                 additional_requirements, current_plan, validation_result
          writes draft_schedule
          ↓
      merge_schedule_node
          reads  completed_timeline, draft_schedule, hard_courses,
                 scored_electives, scored_enrichment, ge_placeholders_remaining
          writes current_plan (PlanJSON), remarks
          ↓
      validator_node
          reads  current_plan, other_must_reqs, ge_placeholders_remaining,
                 hard_courses, iteration_count
          writes validation_result, iteration_count
          ↓ (conditional edge)
      if valid or MAX_ITERATIONS → END
      else → schedule_construction_node  (skips reconstruct_timeline_node)
    """
    completed_timeline: list[CompletedSemester]  # from reconstruct_timeline_node
    draft_schedule: DraftSchedule                # from schedule_construction_node
    validation_result: ValidationResult          # from validator_node
    iteration_count: int                         # incremented by validator_node
