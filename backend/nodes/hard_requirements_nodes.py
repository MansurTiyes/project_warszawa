"""
Hard requirements subgraph nodes.

Nodes exported by this module:
    other_requirements_node    — pure Python, reads StudentState → writes OtherRequirements
    ge_placeholders_node       — pure Python, reads StudentState → writes ge_placeholders_remaining
    hard_courses_node          — pure Python, reads StudentState + RequirementsMap → writes hard_courses_stub
    elective_courses_node      — pure Python, reads RequirementsMap → writes elective_courses_stub
    course_details_retrieval_node — RAG, reads stubs + StudentState → writes hard_courses + elective_courses
"""

from __future__ import annotations

import json
import logging

from models.pipeline_state import (
    AdvisorState,
    CourseNode,
    HardRequirementsState,
    OtherRequirements,
    PrereqItem,
)
from services.chromadb_client import get_courses_collection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node 1: other_requirements_node
# ---------------------------------------------------------------------------

def other_requirements_node(state: AdvisorState) -> dict:
    """
    Derives non-course degree requirements arithmetically from StudentState.
    Pure Python — no LLM, no I/O.

    Writes: other_must_reqs (AdvisorState key)
    """
    ss = state["student_state"]

    other = OtherRequirements(
        units_total_required=(
            ss.units_earned + ss.units_in_process + ss.units_needed
        ),
        units_still_needed=ss.units_needed,

        upper_division_required=(
            ss.upper_division_units_earned
            + ss.upper_division_units_in_process
            + ss.upper_division_units_needed
        ),
        upper_division_still_needed=ss.upper_division_units_needed,

        residency_satisfied=ss.residency_satisfied,
        writing_requirement_satisfied=ss.writing_requirement_satisfied,
        ge_seminar_satisfied=ss.ge_seminar_satisfied,
    )

    logger.info(
        "OtherRequirements: %.0f units still needed, upper div still needed: %.0f, "
        "residency=%s, writing=%s, GE seminar=%s.",
        other.units_still_needed,
        other.upper_division_still_needed,
        other.residency_satisfied,
        other.writing_requirement_satisfied,
        other.ge_seminar_satisfied,
    )
    return {"other_must_reqs": other}


# ---------------------------------------------------------------------------
# Node 2: ge_placeholders_node
# ---------------------------------------------------------------------------

def ge_placeholders_node(state: AdvisorState) -> dict:
    """
    Builds the list of GE category placeholders the student still needs.

    Walks StudentState.ge_completion. For each category where courses_needed > 0,
    appends that category code once per needed course.

    GE-G / GE-H double-dip is handled upstream: if a course already satisfied
    GE-G by double-dipping with a Core Literacy category, the LLM extraction
    sets courses_needed=0 for GE-G. We just read courses_needed directly.

    Writes: ge_placeholders_remaining (AdvisorState key)
    """
    ge_completion = state["student_state"].ge_completion

    placeholders: list[str] = []
    for category_code, status in ge_completion.items():
        for _ in range(status.courses_needed):
            placeholders.append(category_code)

    logger.info(
        "GE placeholders remaining: %s.",
        placeholders if placeholders else "none",
    )
    return {"ge_placeholders_remaining": placeholders}


# ---------------------------------------------------------------------------
# Node 4: hard_courses_node
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Node 4: hard_courses_node — helpers
# ---------------------------------------------------------------------------

def _build_satisfied_set(ss) -> set[str]:
    """
    Courses that count as already handled for requirements checking.

    Sources:
      - courses_taken        (all completed + in-progress, optimistic expectation)
      - current_registration (in-progress registrations not yet in courses_taken)
      - CW exception codes   (course waived — counts as satisfied)
      - RE exception codes   (requirement exchange — requirement satisfied by substitution)

    RA (Requirement Alternative) is intentionally excluded: it adds to the
    elective pool but does not satisfy a specific requirement slot.
    """
    satisfied: set[str] = set()

    for course in ss.courses_taken:
        satisfied.add(course.course_code.strip())
    for code in ss.current_registration:
        satisfied.add(code.strip())
    for exc in ss.exception_codes:
        if exc.code in ("CW", "RE"):
            satisfied.add(exc.course_code.strip())

    return satisfied


def _resolve_science(science: list[list[str]], satisfied: set[str]) -> list[str]:
    """
    ScienceSequenceGroup: outer=OR (pick one sequence), inner=AND (take all).

    Case 1 — any sequence fully satisfied → science done, return [].
    Case 2 — any sequence partially satisfied → add remaining courses from it.
    Case 3 — no sequence started → return [] (Planner assigns the sequence).
    """
    for sequence in science:
        normalized = [c.strip() for c in sequence]
        satisfied_in_seq = [c for c in normalized if c in satisfied]

        if len(satisfied_in_seq) == len(normalized):
            return []
        elif satisfied_in_seq:
            return [c for c in normalized if c not in satisfied]

    return []


def _extract_remaining_hard_codes(
    rm,
    satisfied: set[str],
) -> tuple[list[str], list[list[str]]]:
    """
    Walk every CourseList section and the science sequences.

    Returns:
      resolved         — flat course codes clearly required (unsatisfied fixed slots)
      unresolved_groups — OR slots where no option is yet satisfied
    """
    resolved: list[str] = []
    unresolved_groups: list[list[str]] = []

    # All CourseList (list[str | list[str]]) sections.
    # pre_major_science is ScienceSequenceGroup — handled separately by _resolve_science.
    # GE and electives are handled by their own nodes.
    # NOTE: pre_major_engineering (ENGR-102) is included here — it was omitted from
    # the original design doc but is a required course and must be checked.
    courselist_sections = [
        rm.pre_major_engineering,
        rm.major_cs_core,
        rm.pre_major_math,
        rm.major_ee,
        rm.pre_major_stats,
        rm.writing,
    ]

    for section in courselist_sections:
        for item in section:
            if isinstance(item, str):
                if item.strip() not in satisfied:
                    resolved.append(item.strip())
            elif isinstance(item, list):
                options = [c.strip() for c in item]
                if not any(opt in satisfied for opt in options):
                    unresolved_groups.append(options)

    resolved.extend(_resolve_science(rm.pre_major_science, satisfied))

    return resolved, unresolved_groups


def hard_courses_node(state: AdvisorState) -> dict:
    """
    Determines which required courses the student still needs.

    Algorithm:
      1. Build satisfied set (courses_taken + current_registration + CW/RE exceptions)
      2. Walk all CourseList sections — collect unsatisfied fixed slots and
         unresolved OR groups (no option in satisfied set yet)
      3. Handle pre_major_science separately with AND-within-sequence logic
      4. Resolve OR groups by picking the first option (deterministic default)
      5. Deduplicate while preserving order
      6. Build one stub CourseNode per remaining code

    Writes: hard_courses_stub (HardRequirementsState private key)
    The final hard_courses (AdvisorState key) is written after RAG enrichment.
    """
    ss = state["student_state"]
    rm = state["requirements_map"]

    satisfied = _build_satisfied_set(ss)
    resolved, unresolved_groups = _extract_remaining_hard_codes(rm, satisfied)

    # Resolve OR groups: pick first option as deterministic default.
    # Planner has full freedom to swap based on career goals.
    resolved.extend(group[0] for group in unresolved_groups)

    # Deduplicate while preserving order (in case a code appears in multiple sections)
    seen: set[str] = set()
    final_codes: list[str] = []
    for code in resolved:
        if code not in seen:
            seen.add(code)
            final_codes.append(code)

    stubs = [CourseNode(course_code=code) for code in final_codes]

    logger.info(
        "hard_courses_stub: %d course(s) — %s.",
        len(stubs),
        [s.course_code for s in stubs],
    )
    return {"hard_courses_stub": stubs}


# ---------------------------------------------------------------------------
# Node 3: elective_courses_node
# ---------------------------------------------------------------------------

def elective_courses_node(state: AdvisorState) -> dict:
    """
    Builds stub CourseNodes for every course in the approved elective pool.

    Reads requirements_map.electives_options (flat list of course codes) and
    creates one stub CourseNode per code — course_code only, all other fields
    at defaults. Metadata is filled in by course_details_retrieval_node.

    Writes: elective_courses_stub (HardRequirementsState private key)
    The final elective_courses (AdvisorState key) is written after RAG enrichment.
    """
    elective_codes: list[str] = state["requirements_map"].electives_options

    stubs = [CourseNode(course_code=code) for code in elective_codes]

    logger.info(
        "Elective pool: %d stub CourseNodes created.", len(stubs)
    )
    return {"elective_courses_stub": stubs}


# ---------------------------------------------------------------------------
# Node 5: course_details_retrieval_node — helpers
# ---------------------------------------------------------------------------

def _parse_prereqs(raw: str) -> list[PrereqItem]:
    """
    Convert ChromaDB prereqs JSON to CourseNode.prereqs format.

    ChromaDB stores: list[list[str]]  (AND-of-OR groups from scraper)
      outer list = AND — every group must be satisfied
      inner list = OR  — any one course in the group suffices

    CourseNode.prereqs: list[str | list[str]]
      Single-item OR groups are flattened to bare str for readability.

    Example:
      '[["CSCI-170"], ["CSCI-104", "CSCI-114"]]'
      → ["CSCI-170", ["CSCI-104", "CSCI-114"]]
    """
    try:
        groups: list[list[str]] = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        return []
    return [group[0] if len(group) == 1 else group for group in groups]


def _check_prereqs(prereqs: list[PrereqItem], satisfied: set[str]) -> bool:
    """
    True if all prereq AND groups are satisfied given the student's satisfied set.

    str item  = AND — that specific course must be in satisfied set.
    list item = OR group — at least one option must be in satisfied set.
    """
    for item in prereqs:
        if isinstance(item, str):
            if item not in satisfied:
                return False
        else:
            if not any(opt in satisfied for opt in item):
                return False
    return True


# ---------------------------------------------------------------------------
# Node 5: course_details_retrieval_node
# ---------------------------------------------------------------------------

def course_details_retrieval_node(state: HardRequirementsState) -> dict:
    """
    Enriches hard and elective stub CourseNodes with metadata from ChromaDB.

    Reads:  hard_courses_stub, elective_courses_stub (private HardRequirementsState keys)
            student_state (to compute prereqs_satisfied)
    Writes: hard_courses, elective_courses (AdvisorState keys — fully enriched)

    ChromaDB query:
      Uses collection.get(ids=[...]) — document IDs are course codes directly.
      Both lists are batched into a single get() call for efficiency.
      description is parsed from the document text (not a metadata field):
        format: "{code}: {title}\n\n{description}" — split on first \n\n.

    prereqs_satisfied:
      Computed deterministically from StudentState using _build_satisfied_set
      and _check_prereqs. No LLM needed.

    Missing courses:
      If a course code is absent from ChromaDB, it is dropped and logged.
      A missing course is a data ingestion gap, not a subgraph error.
    """
    hard_stubs: list[CourseNode] = state["hard_courses_stub"]
    elective_stubs: list[CourseNode] = state["elective_courses_stub"]
    satisfied = _build_satisfied_set(state["student_state"])

    collection = get_courses_collection()

    # Batch both lists into a single ChromaDB get() call
    all_stubs = hard_stubs + elective_stubs
    if not all_stubs:
        return {"hard_courses": [], "elective_courses": []}

    all_codes = [s.course_code for s in all_stubs]
    result = collection.get(ids=all_codes, include=["metadatas", "documents"])

    found: dict[str, tuple[dict, str]] = {
        id_: (meta, doc)
        for id_, meta, doc in zip(
            result["ids"], result["metadatas"], result["documents"]
        )
    }

    def _enrich(stubs: list[CourseNode], *, keep_if_missing: bool) -> list[CourseNode]:
        enriched = []
        for stub in stubs:
            entry = found.get(stub.course_code)
            if entry is None:
                if keep_if_missing:
                    # Required course — keep stub so Scheduler still plans it.
                    # Metadata fields stay at defaults (name="", units=0.0, etc.).
                    logger.warning(
                        "Required course not in ChromaDB — keeping stub: %s",
                        stub.course_code,
                    )
                    enriched.append(stub)
                else:
                    logger.warning(
                        "Elective not in ChromaDB — dropping: %s", stub.course_code
                    )
                continue
            meta, doc = entry
            prereqs = _parse_prereqs(meta.get("prereqs", "[]"))
            description = doc.split("\n\n", 1)[1] if "\n\n" in doc else None
            enriched.append(CourseNode(
                course_code=stub.course_code,
                name=meta.get("title", ""),
                units=float(meta.get("units") or 0.0),
                prereqs=prereqs,
                prereqs_satisfied=_check_prereqs(prereqs, satisfied),
                description=description,
            ))
        return enriched

    hard_courses = _enrich(hard_stubs, keep_if_missing=True)
    elective_courses = _enrich(elective_stubs, keep_if_missing=False)

    logger.info(
        "course_details_retrieval_node: %d/%d hard courses enriched, "
        "%d/%d electives enriched.",
        len(hard_courses), len(hard_stubs),
        len(elective_courses), len(elective_stubs),
    )
    return {
        "hard_courses": hard_courses,
        "elective_courses": elective_courses,
    }
