"""
Course catalog search tools for the chat agent.

Two tools, both returning CourseSearchResult — a chat-scoped superset of
CourseNode that includes catalog metadata (terms_offered, grading_option,
instruction_mode) the pipeline doesn't need but chat answering does.

  search_courses(query, ...)
    Semantic search via ChromaDB, with optional filters.
    Over-retrieves top-50 from the vector store, applies Python post-filters
    for fields not cleanly indexable in metadata (numeric units, term codes,
    descriptive instruction_mode), and returns top-10 by relevance.

  get_course_details(course_code)
    Exact lookup by code. Use when the user names a specific course.

Both tools compute prereqs_satisfied per result by reading the student's
completed/registered/waived course set from runtime.context.student_state.

ChromaDB metadata shape (see services/chromadb_client.py:97-109 for ingestion):
  department         : str          — clean dept code, e.g. "CSCI"
  units, max_units   : str (numeric, e.g. "4.0") — stored stringified for
                       Chroma compatibility; coerce on read
  terms_offered      : str          — USC compact codes "Fa" / "Sp" / "Su",
                       concatenated e.g. "FaSp" (NOT a JSON list)
  grading_option     : str          — clean enum-like, e.g. "Letter", "CR/NC"
  instruction_mode   : str          — descriptive free text, e.g. "Lecture, Quiz"
                       — substring match only; do not use for exact filters
  prereqs            : str          — JSON-stringified list[list[str]]
                       (AND-of-OR groups)

Document text format: "{code}: {title}" or "{code}: {title}\\n\\n{description}".
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, Field

from models.chat_state import ChatContext
from models.course_nodes import PrereqItem
from models.student_state import StudentState
from services.chromadb_client import get_courses_collection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

class CourseSearchResult(BaseModel):
    """
    Chat-scoped course representation. Superset of CourseNode plus the
    catalog metadata the chat layer surfaces to the user.

    Returned as a list by both search_courses and get_course_details.
    Pydantic auto-serializes to JSON for the LLM tool message.
    """
    course_code: str
    name: str
    units: float
    prereqs: list[PrereqItem] = Field(default_factory=list)
    prereqs_satisfied: bool
    description: str | None = None

    # Catalog metadata — present in ChromaDB but not on CourseNode
    terms_offered: str = ""           # USC compact codes, e.g. "FaSp"
    grading_option: str | None = None # e.g. "Letter", "CR/NC"
    instruction_mode: str | None = None # e.g. "Lecture, Quiz"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# How many candidates to retrieve from ChromaDB before Python post-filtering.
# Over-retrieval ensures restrictive filters (e.g. min_units=2, term=Spring)
# still leave a useful pool to truncate.
_RETRIEVAL_N = 50

# How many to return after post-filtering — caps prompt cost on the model side.
_RETURN_N = 10

# Map full season names to USC's compact catalog codes.
_TERM_CODES: dict[str, str] = {"Fall": "Fa", "Spring": "Sp", "Summer": "Su"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _satisfied_codes(student_state: StudentState) -> set[str]:
    """Course codes the student counts as having satisfied for prereq purposes.

    Sources (mirrors hard_requirements_nodes._build_satisfied_set):
      courses_taken         — completed and in-progress (optimistic)
      current_registration  — in-progress not yet in courses_taken
      CW exception code     — course waived
      RE exception code     — requirement exchange / substitution

    RA is intentionally excluded: it adds to the elective pool but does
    not satisfy a specific requirement slot.
    """
    satisfied: set[str] = set()
    for record in student_state.courses_taken:
        satisfied.add(record.course_code.strip())
    for code in student_state.current_registration:
        satisfied.add(code.strip())
    for exc in student_state.exception_codes:
        if exc.code in ("CW", "RE"):
            satisfied.add(exc.course_code.strip())
    return satisfied


def _parse_prereqs(raw: str) -> list[PrereqItem]:
    """ChromaDB stores prereqs as a JSON-stringified list[list[str]] (AND-of-OR).
    Flatten single-item OR groups to bare strings for readability — same
    convention as CourseNode.prereqs.
    """
    try:
        groups: list[list[str]] = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        return []
    return [g[0] if len(g) == 1 else g for g in groups if g]


def _check_prereqs(prereqs: list[PrereqItem], satisfied: set[str]) -> bool:
    """All AND groups satisfied?  str = single course; list = OR group."""
    for item in prereqs:
        if isinstance(item, str):
            if item not in satisfied:
                return False
        else:
            if not any(opt in satisfied for opt in item):
                return False
    return True


def _to_search_result(
    course_id: str,
    metadata: dict[str, Any],
    document: str,
    satisfied: set[str],
) -> CourseSearchResult:
    """Reconstruct a CourseSearchResult from a single ChromaDB hit."""
    raw_units = metadata.get("units", "")
    try:
        units = float(raw_units) if raw_units else 0.0
    except (ValueError, TypeError):
        units = 0.0

    description: str | None = None
    parts = document.split("\n\n", 1)
    if len(parts) == 2 and parts[1].strip():
        description = parts[1].strip()

    prereqs = _parse_prereqs(metadata.get("prereqs", ""))

    return CourseSearchResult(
        course_code=course_id,
        name=metadata.get("title", ""),
        units=units,
        prereqs=prereqs,
        prereqs_satisfied=_check_prereqs(prereqs, satisfied),
        description=description,
        terms_offered=metadata.get("terms_offered", "") or "",
        grading_option=metadata.get("grading_option") or None,
        instruction_mode=metadata.get("instruction_mode") or None,
    )


def _build_where(
    department: str | None,
    grading_option: str | None,
) -> dict[str, Any] | None:
    """Build ChromaDB `where` clause for clean exact-match metadata fields.

    Only fields with discrete, well-formed values are pushed to ChromaDB.
    Everything else is post-filtered in Python (see _passes_post_filter).
    """
    clauses: list[dict[str, Any]] = []
    if department:
        clauses.append({"department": department.strip().upper()})
    if grading_option:
        clauses.append({"grading_option": grading_option})

    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _passes_post_filter(
    result: CourseSearchResult,
    min_units: float | None,
    max_units: float | None,
    term: str | None,
    instruction_mode: str | None,
) -> bool:
    """Apply filters that can't be expressed as ChromaDB exact-match `where`."""
    if min_units is not None and result.units < min_units:
        return False
    if max_units is not None and result.units > max_units:
        return False
    if term is not None:
        code = _TERM_CODES.get(term.strip().capitalize())
        if code is None or code not in result.terms_offered:
            return False
    if instruction_mode is not None:
        haystack = (result.instruction_mode or "").lower()
        if instruction_mode.strip().lower() not in haystack:
            return False
    return True


# ---------------------------------------------------------------------------
# Tool 1: search_courses
# ---------------------------------------------------------------------------

@tool
def search_courses(
    query: str,
    runtime: ToolRuntime[ChatContext],
    department: str | None = None,
    min_units: float | None = None,
    max_units: float | None = None,
    term: str | None = None,
    grading_option: str | None = None,
    instruction_mode: str | None = None,
) -> list[dict]:
    """Search the USC course catalog by free-text query, with optional filters.

    Use this when the student asks about courses, topics, or options that are
    not already in their plan. Each result includes prereqs_satisfied computed
    against the student's completed and in-progress coursework — surface that
    when discussing whether a course is takeable now.

    Always cite catalog answers as coming from "the USC course catalog".

    Args:
        query: Free-text search terms. Examples: "machine learning intro",
            "graphics rendering", "3-unit elective related to networking".
        department: Optional department code filter, e.g. "CSCI", "EE", "MATH".
            Exact match.
        min_units: Optional lower bound on course units. For "exactly 2 units",
            set both min_units=2 and max_units=2.
        max_units: Optional upper bound on course units.
        term: Optional term filter — one of "Fall", "Spring", or "Summer".
            Returns courses offered in that term.
        grading_option: Optional grading filter, e.g. "Letter", "CR/NC".
            Exact match.
        instruction_mode: Optional substring match against the course's
            instruction-mode description (e.g. "Lecture", "Lab", "Discussion").

    Returns:
        Up to 10 dicts (one per matching course), each with the same fields
        as a CourseSearchResult: course_code, name, units, prereqs,
        prereqs_satisfied, description, terms_offered, grading_option,
        instruction_mode. Ordered by semantic relevance to the query.
        Returns an empty list if nothing matches.
    """
    where = _build_where(department=department, grading_option=grading_option)

    collection = get_courses_collection()
    try:
        results = collection.query(
            query_texts=[query],
            n_results=_RETRIEVAL_N,
            where=where,
            include=["metadatas", "documents"],
        )
    except Exception as exc:
        logger.error("search_courses: ChromaDB query failed: %s", exc)
        raise

    ids: list[str] = results["ids"][0] if results["ids"] else []
    metadatas: list[dict] = results["metadatas"][0] if results["metadatas"] else []
    documents: list[str] = results["documents"][0] if results["documents"] else []

    satisfied = _satisfied_codes(runtime.context.student_state)

    parsed = [
        _to_search_result(code, meta, doc, satisfied)
        for code, meta, doc in zip(ids, metadatas, documents)
    ]

    filtered = [
        r for r in parsed
        if _passes_post_filter(
            r,
            min_units=min_units,
            max_units=max_units,
            term=term,
            instruction_mode=instruction_mode,
        )
    ]

    logger.info(
        "search_courses: query=%r filters=(dept=%s, units=%s..%s, term=%s, "
        "grade=%s, mode=%s) → retrieved=%d filtered=%d returning=%d",
        query, department, min_units, max_units, term, grading_option,
        instruction_mode, len(parsed), len(filtered), min(len(filtered), _RETURN_N),
    )

    return [r.model_dump() for r in filtered[:_RETURN_N]]


# ---------------------------------------------------------------------------
# Tool 2: get_course_details
# ---------------------------------------------------------------------------

@tool
def get_course_details(
    course_code: str,
    runtime: ToolRuntime[ChatContext],
) -> dict | None:
    """Fetch full details for a single course by exact code.

    Use this when the student names a specific course (e.g. "tell me about
    CSCI-467"). Returns prereqs_satisfied computed against the student's
    completed and in-progress coursework. Always cite catalog answers as
    coming from "the USC course catalog".

    Args:
        course_code: Hyphenated course code, e.g. "CSCI-467", "MATH-225".
            Case-insensitive — normalized internally.

    Returns:
        A dict with the same fields as a CourseSearchResult (course_code,
        name, units, prereqs, prereqs_satisfied, description, terms_offered,
        grading_option, instruction_mode), or None if the course code is
        not in the catalog (treat as "I couldn't find that course —
        double-check the code").
    """
    code = course_code.strip().upper()

    collection = get_courses_collection()
    try:
        result = collection.get(ids=[code], include=["metadatas", "documents"])
    except Exception as exc:
        logger.error("get_course_details: ChromaDB get failed for %s: %s", code, exc)
        raise

    if not result["ids"]:
        logger.info("get_course_details: %s not in catalog", code)
        return None

    satisfied = _satisfied_codes(runtime.context.student_state)
    course = _to_search_result(
        course_id=result["ids"][0],
        metadata=result["metadatas"][0],
        document=result["documents"][0],
        satisfied=satisfied,
    )
    return course.model_dump()
