"""
Student-context tools for the chat agent.

Four tools, each fetching a single field from runtime.context that
isn't already inlined in the system prompt:

  get_scored_electives             — scored_electives pool
  get_scored_enrichment            — scored_enrichment chains
  get_other_must_reqs              — aggregate degree-requirement status
  get_ge_placeholders_remaining    — GE categories the student still needs

What's NOT exposed as a tool (and why):
  - career_goal, current_plan         — already inlined in the system prompt
  - requirements_map                  — pipeline-internal, no chat use case
  - hard_courses                      — derivable from current_plan
                                        (every required course is in the plan)
  - student_state (full)              — too large; specific fields surface
                                        through the summary already

All return values are JSON-serializable (dicts / lists of dicts) so the
ToolMessage carries clean structured data the model can reason over
field-by-field. Pydantic models are dumped via .model_dump() at the
boundary; the model never sees Python repr.
"""

from __future__ import annotations

from langchain.tools import ToolRuntime, tool

from models.chat_state import ChatContext


# ---------------------------------------------------------------------------
# Tool 1: get_scored_electives
# ---------------------------------------------------------------------------

@tool
def get_scored_electives(runtime: ToolRuntime[ChatContext]) -> list[dict]:
    """Return the pre-ranked technical-elective pool for this student.

    Each entry is a candidate technical elective with:
      course_code, name, units, prereqs, prereqs_satisfied, description,
      score (0.0–1.0, higher = stronger fit with the student's career_goal)

    Use when the student asks about:
      - Which electives they should take
      - Swapping one elective for another
      - Which technical electives align with their career goal

    These electives are pre-curated and pre-scored for this student.
    You do NOT need to call search_courses for elective discovery —
    this pool IS the answer set. Ordered by score, descending.
    """
    return [
        {
            "course_code": sc.course.course_code,
            "name": sc.course.name,
            "units": sc.course.units,
            "prereqs": [p for p in sc.course.prereqs],  # pass-through
            "prereqs_satisfied": sc.course.prereqs_satisfied,
            "description": sc.course.description,
            "score": sc.score,
        }
        for sc in runtime.context.scored_electives
    ]


# ---------------------------------------------------------------------------
# Tool 2: get_scored_enrichment
# ---------------------------------------------------------------------------

@tool
def get_scored_enrichment(runtime: ToolRuntime[ChatContext]) -> list[list[dict]]:
    """Return pre-ranked enrichment course chains for this student's career goal.

    Each outer entry is a CHAIN — a list where the FINAL course is the
    career-aligned target and any earlier entries are prerequisite
    "enabler" courses needed to take it. All courses in a chain share
    the same score (inherited from the terminal course).

    Per-course fields (same as get_scored_electives):
      course_code, name, units, prereqs, prereqs_satisfied, description, score

    Use when the student asks about:
      - Extra courses to add for career fit
      - Optional courses beyond their required coursework
      - "What would help me as a [career goal]?"

    These are pre-ranked. You do NOT need search_courses for
    career-aligned discovery — this pool IS the answer set.
    Chains are ordered by score (terminal course's score), descending.
    """
    return [
        [
            {
                "course_code": sc.course.course_code,
                "name": sc.course.name,
                "units": sc.course.units,
                "prereqs": [p for p in sc.course.prereqs],
                "prereqs_satisfied": sc.course.prereqs_satisfied,
                "description": sc.course.description,
                "score": sc.score,
            }
            for sc in chain
        ]
        for chain in runtime.context.scored_enrichment
    ]


# ---------------------------------------------------------------------------
# Tool 3: get_other_must_reqs
# ---------------------------------------------------------------------------

@tool
def get_other_must_reqs(runtime: ToolRuntime[ChatContext]) -> dict:
    """Return aggregate degree-requirement status for this student.

    Returns:
      units_total_required: float           — total units the degree requires
      units_still_needed: float             — units left to graduate
      upper_division_required: float        — total upper-division units required
      upper_division_still_needed: float    — upper-division units left
      residency_satisfied: bool             — USC residency requirement met
      writing_requirement_satisfied: bool   — WRIT-340 (or equivalent) done
      ge_seminar_satisfied: bool            — GE seminar (GESM) done

    Use when the student asks about:
      - Whether they're on track to graduate
      - Total units still needed
      - Upper-division unit progress
      - Whether residency / writing / GE-seminar requirements are met
    """
    return runtime.context.other_must_reqs.model_dump()


# ---------------------------------------------------------------------------
# Tool 4: get_ge_placeholders_remaining
# ---------------------------------------------------------------------------

@tool
def get_ge_placeholders_remaining(runtime: ToolRuntime[ChatContext]) -> list[str]:
    """Return the list of GE category codes the student still needs.

    Each entry is a category code such as "GE-A", "GE-C", "GE-G".
    GE-A through GE-F are core-literacy categories. GE-G and GE-H are
    global-perspectives categories that can double-dip with any one of
    GE-A through GE-F (one course satisfies both at once).

    A category may appear more than once if multiple courses in that
    category are needed.

    Use when the student asks:
      - Which GE categories am I missing?
      - What GE requirements do I still have?

    NEVER recommend a specific GE course in response. The student picks
    from USC's catalog by interest within whichever category they choose;
    each category covers a deliberately wide range of options.
    """
    return list(runtime.context.ge_placeholders_remaining)
