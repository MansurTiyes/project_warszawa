"""
Soft requirements subgraph nodes.

Nodes exported by this module:
    score_electives_node              — RAG (ChromaDB bi-encoder query), reads elective_courses
                                        + career_goal → writes scored_electives
    enrichment_courses_retrieval_node — RAG (ChromaDB semantic search, top 50), reads career_goal
                                        → writes temp_retrieved_enrichment
    remove_taken_courses_node         — pure Python, deduplicates against student history
                                        → writes retrieved_enrichment
    enrichment_rerank_node            — LLM (Gemini Flash, structured output), scores candidates
                                        → writes scored_enrichment_flat (top 10)
    find_missing_prereqs_node         — Python + RAG metadata fetch, groups prereq chains
                                        → writes scored_enrichment
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from models.pipeline_state import (
    CourseNode,
    EnrichmentState,
    PrereqItem,
    ScoredCourse,
    SoftRequirementsState,
)
from services.chromadb_client import get_courses_collection


# ---------------------------------------------------------------------------
# Shared LLM output schemas
# ---------------------------------------------------------------------------

class _ScoreItem(BaseModel):
    course_code: str
    score: float  # 0.0–1.0

class _ScoreList(BaseModel):
    scores: list[_ScoreItem]

logger = logging.getLogger(__name__)

# Departments included in enrichment retrieval. Keeps the top-50 pool focused
# on CS-adjacent courses rather than the full university catalog.
_ENRICHMENT_DEPARTMENTS = ["CSCI", "EE", "MATH", "TAC", "DSCI"]

# Number of enrichment candidates to retrieve before LLM rerank.
_ENRICHMENT_RETRIEVAL_N = 50

# Number of enrichment courses to keep after LLM rerank.
_ENRICHMENT_TOP_N = 10


# ---------------------------------------------------------------------------
# Helpers shared across nodes
# ---------------------------------------------------------------------------

def _normalize(code: str) -> str:
    """Uppercase and strip whitespace — codes already in DEPT-NNN form."""
    return code.strip().upper()


def _build_satisfied_set(state: SoftRequirementsState | EnrichmentState) -> set[str]:
    """
    Build a set of course codes the student has completed or is registered for.
    Mirrors the same helper in hard_requirements_nodes to keep prereq logic consistent.

    Sources:
      - courses_taken        (all completed + in-progress, optimistic expectation)
      - current_registration (in-progress registrations not yet in courses_taken)
      - CW exception codes   (course waived — counts as satisfied)
      - RE exception codes   (requirement exchange — satisfied by substitution)

    RA is intentionally excluded: it adds to the elective pool but does not
    satisfy a specific requirement slot.
    """
    ss = state["student_state"]
    satisfied: set[str] = set()
    for record in ss.courses_taken:
        satisfied.add(_normalize(record.course_code))
    for code in ss.current_registration:
        satisfied.add(_normalize(code))
    for exc in ss.exception_codes:
        if exc.code in ("CW", "RE"):
            satisfied.add(_normalize(exc.course_code))
    return satisfied


def _parse_chromadb_course(course_id: str, metadata: dict[str, Any], document: str) -> CourseNode:
    """
    Reconstruct a CourseNode from a raw ChromaDB get() result entry.
    Mirrors the enrichment logic in course_details_retrieval_node.
    """
    import json as _json

    raw_prereqs = metadata.get("prereqs", "")
    try:
        nested: list[list[str]] = _json.loads(raw_prereqs) if raw_prereqs else []
    except (ValueError, TypeError):
        nested = []

    prereqs: list[PrereqItem] = []
    for group in nested:
        if len(group) == 1:
            prereqs.append(group[0])
        elif len(group) > 1:
            prereqs.append(group)

    raw_units = metadata.get("units", "")
    try:
        units = float(raw_units) if raw_units else 0.0
    except (ValueError, TypeError):
        units = 0.0

    # Document text is "<code>\n\n<description>" — split on first double newline
    description: str | None = None
    parts = document.split("\n\n", 1)
    if len(parts) == 2 and parts[1].strip():
        description = parts[1].strip()

    return CourseNode(
        course_code=course_id,
        name=metadata.get("title", ""),
        units=units,
        prereqs=prereqs,
        prereqs_satisfied=False,  # not needed for enrichment display
        description=description,
    )


def _fetch_course(collection, code: str) -> CourseNode | None:
    """Fetch a single CourseNode from ChromaDB by course code. Returns None on miss."""
    try:
        fetch = collection.get(ids=[code], include=["metadatas", "documents"])
    except Exception as exc:
        logger.warning("_fetch_course: ChromaDB fetch failed for %s: %s", code, exc)
        return None
    if not fetch["ids"]:
        return None
    return _parse_chromadb_course(fetch["ids"][0], fetch["metadatas"][0], fetch["documents"][0])


def _expand_prereqs(
    course: CourseNode,
    satisfied: set[str],
    collection,
    stack: set[str],
    seen: set[str],
) -> list[CourseNode]:
    """
    Recursively expand all unsatisfied prereqs for `course`, returning them in
    topological order (deepest prereqs first, course itself excluded).

    Args:
        stack: course codes on the current DFS path — detects cycles.
               Uses set union (not mutation) so sibling branches don't share state.
        seen:  course codes already included in this chain — deduplicates diamonds
               (e.g. two prereqs that both require the same course). Mutated in place
               and shared across the full expansion for one top-level course.
    """
    if course.course_code in stack:
        logger.warning("_expand_prereqs: cycle at %s — stopping", course.course_code)
        return []

    new_stack = stack | {course.course_code}
    chain: list[CourseNode] = []

    for prereq in course.prereqs:
        if isinstance(prereq, str):
            code = _normalize(prereq)
        elif isinstance(prereq, list):
            if any(_normalize(p) in satisfied for p in prereq):
                continue  # OR group — at least one option already satisfied
            code = _normalize(prereq[0])  # pick first option, same as hard_courses_node
        else:
            continue

        if code in satisfied or code in seen:
            continue

        prereq_node = _fetch_course(collection, code)
        seen.add(code)  # mark before recursing — prevents re-fetching in diamond case

        if prereq_node is None:
            logger.warning("_expand_prereqs: %s not in ChromaDB, skipping", code)
            continue

        # Recurse: get prereqs of this prereq first (topological ordering)
        sub_chain = _expand_prereqs(prereq_node, satisfied, collection, new_stack, seen)
        chain.extend(sub_chain)
        chain.append(prereq_node)

    return chain


# ---------------------------------------------------------------------------
# Node 1: score_electives_node  (elective_ranking_subgraph)
# ---------------------------------------------------------------------------

def score_electives_node(state: SoftRequirementsState) -> dict:
    """
    Scores each pre-approved technical elective against career_goal using
    Gemini Flash with structured output. Returns the full pool sorted
    descending — the Scheduler consumes all of them to fill unit slots.

    No grad-course penalty: the elective pool is already curated and all
    courses are at an appropriate level. Score reflects only career relevance.

    On LLM failure: returns all electives at score 0.0 so the Scheduler
    still receives the full pool, just unranked.

    Writes: scored_electives (AdvisorState key)
    """
    from langchain_google_genai import ChatGoogleGenerativeAI
    from config import settings

    elective_courses: list[CourseNode] = state["elective_courses"]
    career_goal: str = state["career_goal"]

    if not elective_courses:
        logger.warning("score_electives_node: elective_courses is empty")
        return {"scored_electives": []}

    course_list = "\n".join(
        f"- {c.course_code}: {c.name}. {c.description or ''}".strip(".")
        for c in elective_courses
    )

    prompt = (
        f"You are selecting technical electives for a CS student pursuing: {career_goal}.\n\n"
        "These are pre-approved electives from the USC CS curriculum — all are at an "
        "appropriate level and have been vetted for the degree. Score each 0.0–1.0 "
        "based solely on how directly useful the course content is for the career goal.\n\n"
        "Scoring guidance:\n"
        "- 0.8–1.0: course content is core to day-to-day work in this career\n"
        "- 0.5–0.8: course is relevant and builds useful skills, not always central\n"
        "- 0.2–0.5: tangentially related or provides general engineering value\n"
        "- 0.0–0.2: rarely applicable to this specific career path\n\n"
        f"Electives to score:\n{course_list}"
    )

    llm = ChatGoogleGenerativeAI(
        model="gemini-3.1-flash-lite-preview",
        google_api_key=settings.gemini_api_key,
        temperature=0,
    ).with_structured_output(_ScoreList)

    try:
        response: _ScoreList = llm.invoke(prompt)
    except Exception as exc:
        logger.error("score_electives_node: LLM call failed: %s — returning unranked pool", exc)
        return {
            "scored_electives": [ScoredCourse(course=c, score=0.0) for c in elective_courses]
        }

    code_to_node = {c.course_code: c for c in elective_courses}
    scored: list[ScoredCourse] = []
    for item in response.scores:
        node = code_to_node.get(item.course_code)
        if node is None:
            logger.warning("score_electives_node: LLM returned unknown code %r", item.course_code)
            continue
        scored.append(ScoredCourse(course=node, score=max(0.0, min(1.0, item.score))))

    # Any elective the LLM didn't score gets 0.0 and stays in the pool
    scored_codes = {sc.course.course_code for sc in scored}
    for c in elective_courses:
        if c.course_code not in scored_codes:
            logger.warning("score_electives_node: LLM did not score %s — assigning 0.0", c.course_code)
            scored.append(ScoredCourse(course=c, score=0.0))

    scored.sort(key=lambda x: x.score, reverse=True)

    logger.info(
        "score_electives_node: scored %d electives | career_goal=%r | "
        "top=%s (%.3f)  bottom=%s (%.3f)",
        len(scored),
        career_goal,
        scored[0].course.course_code,
        scored[0].score,
        scored[-1].course.course_code,
        scored[-1].score,
    )

    return {"scored_electives": scored}


# ---------------------------------------------------------------------------
# Node 2: enrichment_courses_retrieval_node  (enrichment_subgraph)
# ---------------------------------------------------------------------------

def enrichment_courses_retrieval_node(state: EnrichmentState) -> dict:
    """
    Retrieves the top-50 career-aligned courses from ChromaDB, filtered to
    CS-adjacent departments. The "undergraduate" prefix in the query biases
    retrieval before the LLM reranker applies an explicit grad-course penalty.

    Writes: temp_retrieved_enrichment (EnrichmentState private key)
    """
    career_goal: str = state["career_goal"]
    query = f"undergraduate computer science courses related to {career_goal}"

    collection = get_courses_collection()

    try:
        results = collection.query(
            query_texts=[query],
            n_results=_ENRICHMENT_RETRIEVAL_N,
            where={"department": {"$in": _ENRICHMENT_DEPARTMENTS}},
            include=["metadatas", "documents"],
        )
    except Exception as exc:
        logger.error("enrichment_courses_retrieval_node: ChromaDB query failed: %s", exc)
        return {"temp_retrieved_enrichment": []}

    ids: list[str] = results["ids"][0]
    metadatas: list[dict] = results["metadatas"][0]
    documents: list[str] = results["documents"][0]

    courses = [
        _parse_chromadb_course(course_id, meta, doc)
        for course_id, meta, doc in zip(ids, metadatas, documents)
    ]

    logger.info(
        "enrichment_courses_retrieval_node: retrieved %d candidates | career_goal=%r",
        len(courses),
        career_goal,
    )

    return {"temp_retrieved_enrichment": courses}


# ---------------------------------------------------------------------------
# Node 3: remove_taken_courses_node  (enrichment_subgraph)
# ---------------------------------------------------------------------------

def remove_taken_courses_node(state: EnrichmentState) -> dict:
    """
    Removes courses already taken, currently registered for, present in the
    approved elective pool, or already in hard_courses (required courses) from
    the retrieved enrichment candidates.

    Writes: retrieved_enrichment (EnrichmentState private key)
    """
    retrieved: list[CourseNode] = state.get("temp_retrieved_enrichment", [])
    elective_courses: list[CourseNode] = state["elective_courses"]
    hard_courses: list[CourseNode] = state.get("hard_courses", [])

    satisfied = _build_satisfied_set(state)
    elective_codes = {_normalize(c.course_code) for c in elective_courses}
    hard_codes = {_normalize(c.course_code) for c in hard_courses}
    excluded = satisfied | elective_codes | hard_codes

    filtered = [
        c for c in retrieved
        if _normalize(c.course_code) not in excluded
    ]

    logger.info(
        "remove_taken_courses_node: %d → %d "
        "(removed taken/registered=%d  elective=%d  hard=%d)",
        len(retrieved),
        len(filtered),
        len([c for c in retrieved if _normalize(c.course_code) in satisfied]),
        len([c for c in retrieved if _normalize(c.course_code) in elective_codes]),
        len([c for c in retrieved if _normalize(c.course_code) in hard_codes]),
    )

    return {"retrieved_enrichment": filtered}


# ---------------------------------------------------------------------------
# Node 4: enrichment_rerank_node  (enrichment_subgraph)
# ---------------------------------------------------------------------------

def enrichment_rerank_node(state: EnrichmentState) -> dict:
    """
    Scores each candidate against career_goal using Gemini Flash with structured
    output. Applies an explicit undergrad preference penalty (500+ courses score
    ≤ 0.6 unless exceptionally relevant). Returns top-10 sorted descending.

    Writes: scored_enrichment_flat (EnrichmentState private key)
    """
    from langchain_google_genai import ChatGoogleGenerativeAI

    retrieved: list[CourseNode] = state.get("retrieved_enrichment", [])
    career_goal: str = state["career_goal"]

    if not retrieved:
        logger.warning("enrichment_rerank_node: no candidates to rerank")
        return {"scored_enrichment_flat": []}

    course_list = "\n".join(
        f"- {c.course_code}: {c.name}. {c.description or ''}".strip(".")
        for c in retrieved
    )

    prompt = (
        f"You are scoring university courses for a student pursuing: {career_goal}.\n\n"
        "Score each course 0.0–1.0:\n"
        "- Primary factor: direct relevance to the career goal\n"
        "- Strongly prefer undergraduate courses (course number < 500): "
        "graduate courses (500+) should score no higher than 0.6 unless exceptionally relevant\n"
        "- These are enrichment options beyond required coursework\n\n"
        f"Courses to score:\n{course_list}"
    )

    from config import settings
    llm = ChatGoogleGenerativeAI(
        model="gemini-3.1-flash-lite-preview",
        google_api_key=settings.gemini_api_key,
        temperature=0,
    ).with_structured_output(_ScoreList)

    try:
        response: _ScoreList = llm.invoke(prompt)
    except Exception as exc:
        logger.error("enrichment_rerank_node: LLM call failed: %s", exc)
        return {"scored_enrichment_flat": []}

    code_to_node = {c.course_code: c for c in retrieved}
    scored: list[ScoredCourse] = []
    for item in response.scores:
        node = code_to_node.get(item.course_code)
        if node is None:
            logger.warning("enrichment_rerank_node: LLM returned unknown code %r", item.course_code)
            continue
        scored.append(ScoredCourse(course=node, score=max(0.0, min(1.0, item.score))))

    scored.sort(key=lambda x: x.score, reverse=True)
    top = scored[:_ENRICHMENT_TOP_N]

    logger.info(
        "enrichment_rerank_node: reranked %d → top %d | top=%s (%.3f)",
        len(retrieved),
        len(top),
        top[0].course.course_code if top else "none",
        top[0].score if top else 0.0,
    )

    return {"scored_enrichment_flat": top}


# ---------------------------------------------------------------------------
# Node 5: find_missing_prereqs_node  (enrichment_subgraph)
# ---------------------------------------------------------------------------

def find_missing_prereqs_node(state: EnrichmentState) -> dict:
    """
    For each top-10 enrichment course, recursively expands all unsatisfied prereqs
    into a topologically ordered chain. Prereq nodes inherit the primary course's
    score so the Scheduler can reason about total slot cost vs. benefit.

    Each chain is independent — `seen` is reset per top-level course so that
    two different enrichment courses that share a prereq each show it in their
    own chain. The Scheduler plans enrichment options independently.

    Cycle detection via `stack` (current DFS path, not mutated across siblings).
    Diamond dedup via `seen` (shared within one chain expansion).

    Writes: scored_enrichment (AdvisorState key — list of prereq chains)
    """
    scored_flat: list[ScoredCourse] = state.get("scored_enrichment_flat", [])
    satisfied = _build_satisfied_set(state)

    if not scored_flat:
        return {"scored_enrichment": []}

    collection = get_courses_collection()
    result: list[list[ScoredCourse]] = []

    for scored_course in scored_flat:
        seen: set[str] = set()  # reset per top-level course — chains are independent
        prereq_chain = _expand_prereqs(
            scored_course.course,
            satisfied,
            collection,
            stack=set(),
            seen=seen,
        )

        chain: list[ScoredCourse] = [
            ScoredCourse(course=node, score=scored_course.score)
            for node in prereq_chain
        ] + [scored_course]
        result.append(chain)

    # Sort by primary course score (last element in each chain)
    result.sort(key=lambda chain: chain[-1].score, reverse=True)

    lengths = sorted({len(c) for c in result})
    dist = {ln: sum(1 for c in result if len(c) == ln) for ln in lengths}
    logger.info("find_missing_prereqs_node: %d chains | length dist: %s", len(result), dist)

    return {"scored_enrichment": result}
