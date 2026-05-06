"""
STARS parser nodes — Call 1: StudentState, Call 2: RequirementsMap extraction.

Nodes exported by this module:
    student_state_node      — reads stars_pdf_text, writes student_state
    requirements_map_node   — reads stars_pdf_text, writes requirements_map
"""

from __future__ import annotations

import logging
import re

from langchain_anthropic import ChatAnthropic
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage
from pydantic import ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential

from models.pipeline_state import StarsParserState
from models.requirements_map import CSCI_BS_REQUIREMENTS
from models.student_state import StudentState

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# LLM — lazy singleton so settings are not read at import time
# ---------------------------------------------------------------------------

_structured_llm = None


def _get_structured_llm():
    global _structured_llm
    if _structured_llm is None:
        from config import settings
        llm = ChatAnthropic(model=_MODEL, api_key=settings.anthropic_api_key)
        _structured_llm = llm.with_structured_output(StudentState)
    return _structured_llm


# ---------------------------------------------------------------------------
# Prompt — insert extraction prompt here
# ---------------------------------------------------------------------------

# Replace the placeholder below with the full extraction prompt.
# Keep {stars_text} — it is substituted at call time.
_PROMPT_TEMPLATE = """\
You are a precise data extraction engine. Your only job is to extract structured
student data from a USC STARS degree progress report. You must output valid JSON
matching the schema exactly. No explanation, no preamble, no markdown fences —
raw JSON only.

══════════════════════════════════════════════════════════════
SECTION 1 — HOW TO READ A STARS REPORT
══════════════════════════════════════════════════════════════

--- 1A. SEMESTER CODE DECODING ---

All semester codes follow the pattern XXYYZ where XX+YY = academic year, Z = term.

  Z = 1 → Spring
  Z = 2 → Summer
  Z = 3 → Fall

Decode examples:
  20243 → Fall 2024
  20251 → Spring 2025
  20252 → Summer 2025
  20253 → Fall 2025
  20261 → Spring 2026
  20263 → Fall 2026

Always decode semester codes to human-readable labels before storing them.

--- 1B. COURSE LINE FORMAT ---

Each course appears as:
  [semester_code] [DEPT NNN] [flags] [units] [grade] [name]

Example:
  20243 CSCI103 L  4.0 A-  Introduction to Programming
  20261 ENGR395A X 2.0 RG >IP Cooperative Education Work Ex

Components:
  - semester_code: 5-digit code (decode per 1A)
  - DEPT NNN: department + number. Store exactly as printed including any space.
    ("ITP 342", "EE 109", "TAC 116" — do not remove the space)
  - flags (between course code and units): single letters L, G, X, M, W, P
      G = General Education course
      L = course with a lab
      X = credit restriction
      M = meets diversity requirement
      P = meets Traditions/Historical Foundations
      W = meets Citizenship in a Global Era
      Multi-character flag runs like "GP" mean both G and P — split into ["G", "P"]
  - units: float (4.0, 2.0, etc.)
  - grade: A, A-, B+, RG, TR, IN, etc. — IGNORE entirely, do not store it
  - course suffix flags (after grade): >IP, >D, >Z, >R, >EX, >OS, >P, >FF
  - name: remainder of the line (may be truncated — store as-is, never complete it)

--- 1C. REQUIREMENT STATUS CODES ---

OK or +    → "met"
NO or -    → "unmet"
IP, IP+, IP- → "in_progress"

CRITICAL: When a section says "IP [requirement] HAS BEEN SATISFIED" — the
requirement IS satisfied. IP means satisfaction currently depends on in-progress
courses completing. Still set residency_satisfied = true in this case.

--- 1D. EXCEPTION CODE LINES ---

Exception codes appear inline as NOTE lines inside requirement sections:

  NOTE: CW - COURSE WAIVED CSCI102
  NOTE: RA - ADDED COURSE ITP 342
  NOTE: RE - SUB PHYSICS FOR PHYS152

Each NOTE line must be captured in exception_codes. The format is:
  NOTE: [CODE] - [raw descriptive text] [course reference]

Exception code meanings:
  CW = Course Waived
  RA = Requirement Alternative
  RE = Requirement Exchange
  RW = Requirement Waiver
  UW = Unit Waiver

══════════════════════════════════════════════════════════════
SECTION 2 — EXTRACTION RULES
══════════════════════════════════════════════════════════════

--- 2A. IDENTITY FIELDS ---

name:                From "Name as it will appear on your USC Diploma"
class_level:         Extract from "Current Class Level [X]".
                       Map to: "Freshman" | "Sophomore" | "Junior" | "Senior"
entrance_term:       Decode semester code from "Term of USC Entrance [code]"
                       e.g. 20243 → "Fall 2024"
expected_graduation: Convert printed date to semester format.
                       "12 MAY 2028" → "Spring 2028"
                       Month mapping: Jan–May = Spring, Jun–Aug = Summer, Sep–Dec = Fall
major:               From program header or CURRENT POST line (e.g. "COMPUTER SCIENCE")
degree:              From program header (e.g. "BACHELOR OF SCIENCE")
minor:               From "MINOR: [X]". If "No minor declared" → null

--- 2B. PDP FIELDS ---

pdp:                  true ONLY if the CURRENT POST section shows a dual-degree or
                        BS+MS/PDP program code. Otherwise false.
pdp_degree:           If pdp is true, the graduate degree program code. Otherwise null.
pdp_reflected_in_stars: true only if pdp is true AND STARS already reflects that
                        dual enrollment. Otherwise false.

--- 2C. UNIT COUNTS ---

Use ONLY values from the main degree block ("A MINIMUM OF 128 UNITS IS REQUIRED").
Do NOT use numbers from the "FA INTERNAL AUDITING" or "INTERNAL USE ONLY" sections.

units_earned:       EARNED from the 128-unit block
units_in_process:   IN-PROCESS from the 128-unit block
units_needed:       NEEDS value from the 128-unit block (marked "-->")
units_transfer:     From the "TRNSFR WORK [X].0 TR" line — extract the number only.
                      Do NOT create a CourseRecord for this line.

--- 2D. RESIDENCY ---

residency_units_earned:      EARNED from the "64-UNIT RESIDENCY REQUIREMENT" block
residency_units_in_process:  IN-PROCESS from the same block
residency_satisfied:         true if block text includes "HAS BEEN SATISFIED"
                               (even if prefixed with IP)

--- 2E. UPPER DIVISION ---

upper_division_units_earned:     EARNED from "A MINIMUM OF 32 UPPER DIVISION UNITS" block
upper_division_units_in_process: IN-PROCESS from the same block
upper_division_units_needed:     NEEDS value (marked "-->"). If no NEEDS line: 0.0

--- 2F. PLANNING METADATA ---

remaining_semesters_estimate:  USC prints this as an English word — convert to integer.
                                 "FOUR REMAINING SEMESTERS" → 4
free_elective_units:           From the "INTERNAL USE ONLY" section:
                                 "Total number of FREE ELECTIVES in this PoSt: [X]"
                                 Extract this despite the "INTERNAL USE ONLY" label.

--- 2G. GE COMPLETION ---

Extract one entry per GE category present in the report.
Keys must be: "GE-A", "GE-B", "GE-C", "GE-D", "GE-E", "GE-F", "GE-G", "GE-H"

For each category:

  status:          Derive from the STARS prefix using 1C rules.
  courses_taken:   All course codes listed under that category (completed + in-progress).
                     Include transfer credit codes as they appear (e.g. "PHYSICS").
                     Store codes exactly as printed — do not normalize spaces.
  courses_needed:  Number from "NEEDS: N COURSE(S)" line. 0 if no NEEDS line present.

Note: Categories A–F live in "GENERAL EDUCATION CORE LITERACIES".
      Categories G–H live in "GENERAL EDUCATION GLOBAL PERSPECTIVES".
      Only emit keys for categories that appear in the report.

--- 2H. MAJOR REQUIREMENTS STATUS ---

Extract one entry per sub-requirement listed under "MAJOR REQUIREMENTS FOR COMPUTER SCIENCE"
and the adjacent math/science blocks. Use these canonical key names:

  "core_courses"         → sub-req 1: Core Courses
  "additional_required"  → sub-req 2: Additional Required Course Work
  "capstone"             → sub-req 3: Capstone Requirement
  "technical_electives"  → sub-req 4: Technical Elective Requirement
  "engr102"              → sub-req 5: ENGR102
  "math"                 → COMPUTER SCIENCE MATH REQUIREMENT block
  "science"              → BASIC SCIENCE MAJOR REQUIREMENTS block

For each sub-requirement:

  status:          Derive from the STARS prefix using 1C rules.
  courses_taken:   All course codes listed under that sub-req (completed + in-progress).
                     Store codes exactly as printed — do not normalize spaces.
  units_earned:    EARNED units value if stated. 0.0 if not stated.
  units_needed:    NEEDS units value if stated. 0.0 if met.
  courses_needed:  NEEDS courses count if stated. 0 if met.

--- 2I. WRITING AND SEMINAR BOOLEANS ---

writing_requirement_satisfied:
  true if the COMPOSITION/WRITING REQUIREMENT section is prefixed OK.
  false if prefixed NO or IP.
  Note: This is the ENTIRE writing requirement — both sub-requirements must be
  met (First Year + Upper Division Writing). If either is unmet, this is false.

ge_seminar_satisfied:
  true if the "FIRST YEAR GE SEMINAR REQUIREMENT HAS BEEN MET" line is present
  and prefixed with + or OK.
  false otherwise.

--- 2J. EXCEPTION CODES ---

Scan the entire document for all NOTE lines with exception codes.
For each one found, emit one StudentException object:

  code:        The exception type: "CW" | "RA" | "RE" | "RW" | "UW"
  course_code: The course referenced in the NOTE line, exactly as printed.
               e.g. "CSCI102", "ITP 342", "PHYS151", "PHYS152"
  note:        The full raw text after the exception code and dash.
               e.g. "COURSE WAIVED CSCI102", "ADDED COURSE ITP 342"

Do NOT infer or fabricate exception entries. Only extract what is explicitly
printed as a NOTE line in the document.

--- 2K. COURSES TAKEN ---

The top-level `courses_taken` field represents real USC semester enrollments —
each entry will be grouped by `semester_label` to reconstruct the student's
calendar timeline. Anything that does not correspond to a real semester of
USC coursework MUST be excluded, even if it appears under a requirement
section. Transfer/test credit satisfaction is already tracked in
`units_transfer`, in per-requirement `courses_taken` lists (under
`major_requirements_status` and `ge_completion`), and in `exception_codes` —
no information is lost by excluding these rows from the top-level list.

INCLUDE in courses_taken:
  - All completed courses with a real semester code and a real DEPT-NNN code
    (e.g. `20243 CSCI103 L 4.0 A- Introduction to Programming`)
  - All in-progress courses (grade = RG with >IP suffix) with a real
    DEPT-NNN code
  - All courses from "Other courses in your academic account" section that
    have a real DEPT-NNN code (e.g. MPGU120A, TAC116 — include even if not
    degree-applicable)

EXCLUDE from courses_taken (these are NEVER real semester enrollments):
  - The "TRNSFR WORK" aggregate line (captured in units_transfer only).
  - Any IB / AP / test-credit row. Recognize these by ANY of:
      * `name` field starts with "IBTEST:", "APTEST:", "AP " (AP credit), or
        contains "IB UNITS" / "DIPLOMA"
      * `course_code` is a bare department word with NO numeric portion
        (e.g. "PHYSICS", "COMPSCI", "MATH", "DIPLOMA+2")
      * grade is `TR` with no associated semester code, OR the row appears
        without a leading 5-digit semester code
    Examples to EXCLUDE:
      - `COMPSCI 6.0 TR IBTEST: COMPUTER SCIENCE`
      - `PHYSICS 6.0 TR IBTEST: PHYSICS`         ← exclude even if it
        appears under the science requirement section
      - `DIPLOMA+2 2.0 TR DIPLOMA 2 IB UNITS`
      - `MATH 4.0 TR APTEST: CALCULUS BC`

If you find yourself about to assign a `semester_label` to a row that has no
5-digit STARS semester code, stop — that row belongs in `units_transfer` /
per-requirement `courses_taken` only, not in the top-level `courses_taken`.

DE-DUPLICATION: If a course appears in both the main course list and the
"Other courses in your academic account" section, include it ONCE.
The current_registration section is the authoritative source for in-progress courses.

Store all course codes exactly as printed — do not remove spaces.

--- 2L. CURRENT REGISTRATION ---

Extract all course codes listed under "CURRENT REGISTRATION".
This section may include courses in multiple future semesters — include all of them.
Store as a flat list of codes exactly as printed.

--- 2M. COURSE FLAGS ---

For each CourseRecord collect:
  1. Letter flags before the unit count (L, G, X, M, W, P).
     Multi-character runs like "GP" → split: ["G", "P"]
  2. Suffix flags after the grade (>IP, >D, >Z, >R, >EX, >OS, >P, >FF).
     Store verbatim: ">IP", ">EX", etc.

Do not include grades, semester codes, or unit counts in course_flags.

══════════════════════════════════════════════════════════════
SECTION 3 — OUTPUT SCHEMA
══════════════════════════════════════════════════════════════

{
  "name": string,
  "class_level": "Freshman" | "Sophomore" | "Junior" | "Senior",
  "entrance_term": string,
  "expected_graduation": string,
  "major": string,
  "degree": string,
  "minor": string | null,

  "pdp": boolean,
  "pdp_degree": string | null,
  "pdp_reflected_in_stars": boolean,

  "units_earned": float,
  "units_in_process": float,
  "units_needed": float,
  "units_transfer": float,

  "residency_units_earned": float,
  "residency_units_in_process": float,
  "residency_satisfied": boolean,

  "upper_division_units_earned": float,
  "upper_division_units_in_process": float,
  "upper_division_units_needed": float,

  "remaining_semesters_estimate": integer,
  "free_elective_units": float,

  "ge_completion": {
    "GE-A": {
      "status": "met" | "unmet" | "in_progress",
      "courses_taken": [string],
      "courses_needed": integer
    },
    // ... one entry per GE category present in the report
  },

  "major_requirements_status": {
    "core_courses": {
      "status": "met" | "unmet" | "in_progress",
      "courses_taken": [string],
      "units_earned": float,
      "units_needed": float,
      "courses_needed": integer
    },
    // ... one entry per canonical key listed in 2H
  },

  "writing_requirement_satisfied": boolean,
  "ge_seminar_satisfied": boolean,

  "courses_taken": [
    {
      "semester_label": string,
      "course_code": string,       // exactly as printed, spaces preserved
      "units": float,
      "name": string,              // store as-is, never complete truncated names
      "course_flags": [string]
    }
  ],

  "current_registration": [string],

  "exception_codes": [
    {
      "code": "CW" | "RA" | "RE" | "RW" | "UW",
      "course_code": string,       // exactly as printed
      "note": string
    }
  ]
}

Output raw JSON only. No text before or after the JSON object.

══════════════════════════════════════════════════════════════
SECTION 4 — STARS REPORT TEXT
══════════════════════════════════════════════════════════════

{stars_text}
"""


def _build_prompt(stars_text: str) -> str:
    return _PROMPT_TEMPLATE.replace("{stars_text}", stars_text)


# ---------------------------------------------------------------------------
# Course code normalization
# ---------------------------------------------------------------------------

# Matches CSCI103, CSCI 103, CSCI-103 — any spacing/separator variant.
_CODE_RE = re.compile(r"^([A-Z]{2,6})[\s\-]?(\d{3}[A-Z]{0,2})$")


def _normalize_code(code: str) -> str:
    """Normalize any course code variant to DEPT-NNN (e.g. CSCI-103)."""
    m = _CODE_RE.match(code.strip().upper())
    return f"{m.group(1)}-{m.group(2)}" if m else code


def _normalize_all_codes(ss: StudentState) -> StudentState:
    """Walk every code-bearing field in StudentState and normalize in-place."""
    n = _normalize_code
    return ss.model_copy(update={
        "courses_taken": [
            r.model_copy(update={"course_code": n(r.course_code)})
            for r in ss.courses_taken
        ],
        "current_registration": [n(c) for c in ss.current_registration],
        "exception_codes": [
            e.model_copy(update={"course_code": n(e.course_code)})
            for e in ss.exception_codes
        ],
        "ge_completion": {
            cat: s.model_copy(update={"courses_taken": [n(c) for c in s.courses_taken]})
            for cat, s in ss.ge_completion.items()
        },
        "major_requirements_status": {
            req: s.model_copy(update={"courses_taken": [n(c) for c in s.courses_taken]})
            for req, s in ss.major_requirements_status.items()
        },
    })


# A real USC course code is always DEPT-NNN[suffix] after normalization.
# IB/AP placeholder rows the LLM occasionally emits ("PHYSICS", "COMPSCI",
# "DIPLOMA+2") have no digits and never match this pattern.
_REAL_COURSE_CODE_RE = re.compile(r"^[A-Z]{2,6}-\d{3}[A-Z]{0,2}$")

# Markers in the `name` field that identify test-credit / non-semester rows
# regardless of how the LLM coded them.
_TEST_CREDIT_NAME_RE = re.compile(
    r"\b(IBTEST|APTEST|IB UNITS|IB DIPLOMA|DIPLOMA \d|AP TEST|AP CREDIT)\b",
    re.IGNORECASE,
)


def _drop_test_credit_courses(ss: StudentState) -> StudentState:
    """Deterministic guardrail: drop test-credit / non-semester rows from
    top-level `courses_taken`.

    The STARS extraction prompt already instructs the LLM to exclude these,
    but the model occasionally still includes IB/AP rows under requirement
    sections (e.g. PHYSICS 6.0 TR IBTEST: PHYSICS) and even fabricates a
    `semester_label` for them. That inflates the reconstructed timeline
    with phantom semesters.

    Drop a row when EITHER:
      - `course_code` does not match DEPT-NNN (no digits / not a real code), OR
      - `name` carries an IBTEST/APTEST/DIPLOMA marker.

    Lossless for downstream planning:
      - units_transfer already accounts for the units
      - per-requirement courses_taken (major_requirements_status, ge_completion)
        retains the placeholder code for satisfaction tracking
      - exception_codes (CW/RE) drives science/req exchange via _build_satisfied_set
    """
    kept: list = []
    dropped: list[str] = []
    for r in ss.courses_taken:
        bad_code = not _REAL_COURSE_CODE_RE.match(r.course_code)
        is_test = bool(_TEST_CREDIT_NAME_RE.search(r.name or ""))
        if bad_code or is_test:
            dropped.append(f"{r.course_code!r} ({r.name!r}, {r.semester_label!r})")
            continue
        kept.append(r)
    if dropped:
        logger.info(
            "Dropped %d non-semester row(s) from courses_taken: %s",
            len(dropped), dropped,
        )
    return ss.model_copy(update={"courses_taken": kept})


# ---------------------------------------------------------------------------
# LLM invocation — tenacity for API failures, one schema correction retry
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _raw_invoke(messages: list) -> StudentState:
    return _get_structured_llm().invoke(messages)


def _extract_student_state(stars_text: str) -> StudentState:
    """Call LLM with one schema-correction retry on top of tenacity API retries."""
    prompt = _build_prompt(stars_text)
    messages = [HumanMessage(content=prompt)]
    try:
        return _raw_invoke(messages)
    except (ValidationError, OutputParserException) as exc:
        logger.warning("Schema validation failed, retrying with correction: %s", exc)
        return _raw_invoke([
            HumanMessage(content=prompt),
            HumanMessage(
                content=(
                    "Your previous response did not match the required schema. "
                    f"Error: {exc}. Please try again with all required fields present "
                    "and correctly typed."
                )
            ),
        ])


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

def student_state_node(state: StarsParserState) -> dict:
    stars_text = state["stars_pdf_text"]
    student_state = _extract_student_state(stars_text)
    student_state = _normalize_all_codes(student_state)
    student_state = _drop_test_credit_courses(student_state)
    logger.info(
        "StudentState extracted: %s, %d courses taken.",
        student_state.name,
        len(student_state.courses_taken),
    )
    return {"student_state": student_state}


# ===========================================================================
# CALL 2 — RequirementsMap (hardcoded constant for MVP)
# ===========================================================================

# For MVP the requirements map is a pre-calculated constant for CS BS 20243.
# The node ignores stars_pdf_text — the graph still passes it via state
# (parallel fan-out), which is harmless.
# In V2 this will be retrieved via RAG from parsed USC catalog sources.

def requirements_map_node(_state: StarsParserState) -> dict:
    logger.info(
        "RequirementsMap: returning hardcoded constant (%s %s).",
        CSCI_BS_REQUIREMENTS.major,
        CSCI_BS_REQUIREMENTS.degree,
    )
    return {"requirements_map": CSCI_BS_REQUIREMENTS}
