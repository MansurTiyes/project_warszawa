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
from models.requirements_map import RequirementsMap
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

INCLUDE in courses_taken:
  - All completed courses (letter grades)
  - All in-progress courses (grade = RG with >IP suffix)
  - All courses from "Other courses in your academic account" section
    (e.g. MPGU120A, TAC116 — include even if not degree-applicable)

EXCLUDE from courses_taken:
  - The "TRNSFR WORK" aggregate line (captured in units_transfer only)
  - IB/AP placeholder lines where the code is not a real course code
    (e.g. "COMPSCI 6.0 TR IBTEST: COMPUTER SCIENCE", "DIPLOMA+2 2.0 TR DIPLOMA 2 IB UNITS")

INCLUDE transfer credits that appear under specific requirement sections with
real-looking codes (e.g. "PHYSICS 6.0 TR" under the science requirement) —
these satisfy real requirements and belong in the record.

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
    logger.info(
        "StudentState extracted: %s, %d courses taken.",
        student_state.name,
        len(student_state.courses_taken),
    )
    return {"student_state": student_state}


# ===========================================================================
# CALL 2 — RequirementsMap extraction
# ===========================================================================

# ---------------------------------------------------------------------------
# LLM — separate singleton bound to RequirementsMap schema
# ---------------------------------------------------------------------------

_structured_llm_rm = None


def _get_structured_llm_rm():
    global _structured_llm_rm
    if _structured_llm_rm is None:
        from config import settings
        llm = ChatAnthropic(model=_MODEL, api_key=settings.anthropic_api_key)
        _structured_llm_rm = llm.with_structured_output(RequirementsMap)
    return _structured_llm_rm


# ---------------------------------------------------------------------------
# Prompt — insert extraction prompt here
# ---------------------------------------------------------------------------

# Replace the placeholder below with the full extraction prompt.
# Keep {stars_text} — it is substituted at call time.
_PROMPT_TEMPLATE_RM = """\
You are a precise data extraction engine. Your only job is to extract the
structural degree requirements from a USC STARS degree progress report and
output them as a RequirementsMap JSON object.

CRITICAL RULE — STUDENT-AGNOSTIC EXTRACTION ONLY:
You must extract ONLY what the degree requires in general. You must NEVER
extract what this specific student has or has not done. Ignore all completion
status (OK, NO, IP, +, -), all "EARNED" and "NEEDS" counts, all courses
listed under a student's name, all GPA values, all grades, and all exception
codes (CW, RA, RE, RW, UW). Those fields describe a student. Your output
describes a degree program.

No explanation, no preamble, no markdown fences — raw JSON only.

══════════════════════════════════════════════════════════════
SECTION 1 — HOW TO READ A STARS REPORT
══════════════════════════════════════════════════════════════

--- 1A. DOCUMENT STRUCTURE ---

A STARS report contains the following sections in order:

  1. Header — program code, degree, major, catalog year
  2. Pertinent Data — student identity (IGNORE entirely)
  3. Time to Degree / Units Required — degree-level unit minimums
  4. Residency / Upper Division / GPA blocks (IGNORE — not requirement groups)
  5. Composition/Writing Requirement
  6. General Education Core Literacies Requirements
  7. General Education Global Perspectives Requirement
  8. Computer Science Math Requirement
  9. Basic Science Major Requirements
  10. Major Requirements for Computer Science
  11. Current Registration (IGNORE entirely)
  12. Other courses / Legend (IGNORE entirely)

Extract requirement groups from sections 5–10 only.

--- 1B. REQUIREMENT STATUS CODES — IGNORE ALL OF THEM ---

These codes appear throughout the document prefixed on section headers and
sub-requirements. DO NOT use them. They describe a student's progress, not
the degree structure.

  OK, NO, IP, IP+, IP-, +, -   → IGNORE ALL

Read past these codes to extract only the structural text and course lists.

--- 1C. EXCEPTION CODES — IGNORE ALL OF THEM ---

Exception codes modify requirements for a specific student. They are
student-specific overrides and have no place in RequirementsMap.

  NOTE: CW - ...   (Course Waived — a student was excused from a course)
  NOTE: RA - ...   (Requirement Alternative — a student got a substitute)
  NOTE: RE - ...   (Requirement Exchange)
  NOTE: RW - ...   (Requirement Waiver)
  NOTE: UW - ...   (Unit Waiver)

IGNORE all NOTE lines that begin with an exception code.
Do NOT let them influence which courses you put in required_courses or
select_from. Extract the full structural requirement as the degree program
intends it, regardless of any student-specific modifications visible in this
particular report.

--- 1D. COURSE LINE FORMAT ---

Course lines follow this pattern:
  [semester_code] [DEPT NNN] [flags] [units] [grade] [name]

Example:
  20243 CSCI103 L 4.0 A- Introduction to Programming

For RequirementsMap you only care about:
  - The DEPT NNN course code (e.g. "CSCI103", "EE 109", "ITP 342")
  - The course name in requirement headers and fixed lists

Store course codes exactly as printed — preserve spaces between department
and number (e.g. "EE 109", "ITP 342"). Do not normalize.

Ignore semester codes, units, grades, and course suffix flags entirely.

--- 1E. SELECT FROM LISTS ---

SELECT FROM lines enumerate the courses a student may choose from:

  SELECT FROM: CSCI401 CSCI404 CSCI420 CSCI423 CSCI426
               CSCI430 CSCI445 CSCI461 CSCI467 ...

These lists may wrap across multiple lines — collect ALL codes until the
next section begins (next numbered sub-requirement or section header).

When SELECT FROM is followed by "CATEGORY [name]" instead of course codes,
that is a named category, not an enumerable list:

  SELECT FROM: CATEGORY GE-C
  SELECT FROM: CATEGORY GE-G

In this case, set select_from_is_category: true, set select_from_category
to the category name (e.g. "GE-C"), and leave select_from as an empty list.

--- 1F. HOW TO IDENTIFY completion_type ---

For each sub-requirement, determine its type using this decision tree:

  1. Does the sub-requirement list specific named courses with no SELECT FROM?
       → "fixed"   (all listed courses are required)
       → required_courses = the full list
       → select_from = []

  2. Does SELECT FROM list specific course codes AND the requirement asks
     for a count of courses (N COURSE(S))?
       → "select_n"
       → courses_required = N
       → select_from = the code list

  3. Does SELECT FROM list specific course codes AND the requirement asks
     for a unit total (N UNITS)?
       → "select_units"
       → units_required = N
       → select_from = the code list

  4. Does SELECT FROM reference a named category (CATEGORY GE-C, etc.)?
       → "select_category"
       → select_from_is_category = true
       → select_from_category = the category name

  SPECIAL CASE: If SELECT FROM lists exactly ONE course code, treat it as
  "fixed" with required_courses = [that code]. Do not classify single-course
  SELECT FROM as "select_n". Example: "SELECT FROM: WRIT340" → fixed,
  required_courses: ["WRIT340"].

--- 1G. HOW TO DETERMINE required_count FOR A GROUP ---

required_count is structural — it reflects how many sub-requirements, courses,
or units the degree program demands.

  - Count of sub-requirements that must be satisfied:
      required_count = number of numbered items (1, 2, 3...) in the section
      that the degree requires to be completed. Do NOT derive this from
      "EARNED: N SUB-GROUPS" or "NEEDS: N SUB-GROUPS" — those are
      student-specific. Count the sub-requirements directly.

  - Count of courses from across sub-requirements:
      Stated explicitly in the section header text.
      Example: "COMPLETE EIGHT COURSES FROM SIX CATEGORIES"
      → required_count: 8

  - Unit total:
      Stated explicitly in the sub-requirement.
      Example: "COMPLETE 12 UNITS FROM THE FOLLOWING"
      → units_required: 12.0

══════════════════════════════════════════════════════════════
SECTION 2 — REQUIREMENT GROUPS TO EXTRACT
══════════════════════════════════════════════════════════════

Extract exactly these six requirement groups, in this order. Use the exact
label strings shown here. Do not invent, merge, or omit groups.

--- GROUP 1: COMPOSITION/WRITING REQUIREMENT ---

label: "COMPOSITION/WRITING REQUIREMENT"
completion_measure: "subgroups"
required_count: 2   (both sub-requirements must be satisfied)
notes: capture any notes about letter-grade requirements

Sub-requirements:
  1) FIRST YEAR WRITING REQUIREMENT
       completion_type: "fixed"
       required_courses: derive from the course listed in this sub-req
       courses_required: 1

  2) UPPER DIVISION WRITING REQUIREMENT
       completion_type: "fixed"     ← single SELECT FROM course, treat as fixed
       required_courses: ["WRIT340"]
       courses_required: 1
       notes: capture "must be taken at USC" if printed

--- GROUP 2: GENERAL EDUCATION CORE LITERACIES REQUIREMENTS ---

label: "GENERAL EDUCATION CORE LITERACIES REQUIREMENTS"
completion_measure: "courses"
required_count: 8   (eight total courses across six categories)
notes: capture the numbered NOTE lines about freshman/transfer rules

Sub-requirements (categories A through F, plus the GE Seminar item):
  Each GE category (A, B, C, D, E, F) is one sub-requirement.
  Category labels as printed (e.g. "CATEGORY A: THE ARTS").
  All GE categories use completion_type: "select_category".
  Set select_from_is_category: true for all.
  Set select_from_category to the category code (e.g. "GE-A", "GE-B").
  courses_required = how many courses that category demands (usually 1, GE-C is 2).

  The GE SEMINAR item is also a sub-requirement:
    label: "FIRST YEAR GE SEMINAR REQUIREMENT"
    completion_type: "fixed"
    required_courses: [] (any GESM course qualifies — no fixed code)
    courses_required: 1
    notes: ["FRESHMAN REQUIREMENT"]

--- GROUP 3: GENERAL EDUCATION GLOBAL PERSPECTIVES REQUIREMENT ---

label: "GENERAL EDUCATION GLOBAL PERSPECTIVES REQUIREMENT"
completion_measure: "subgroups"
required_count: 2   (one course from each of two categories)
notes: capture any note about a course applying to both Core Literacy and
       Global Perspectives simultaneously

Sub-requirements:
  1) CATEGORY G — GLOBAL PERSPECTIVES I
       completion_type: "select_category"
       select_from_is_category: true
       select_from_category: "GE-G"
       courses_required: 1

  2) CATEGORY H — GLOBAL PERSPECTIVES II
       completion_type: "select_category"
       select_from_is_category: true
       select_from_category: "GE-H"
       courses_required: 1

--- GROUP 4: COMPUTER SCIENCE MATH REQUIREMENT ---

label: "COMPUTER SCIENCE MATH REQUIREMENT"
completion_measure: "subgroups"
required_count: count the numbered sub-requirements in this section

Sub-requirements: extract each numbered item.
  Some sub-requirements in this section have no label — set label: null.
  Do not invent a label for unlabeled items.
  Extract any SELECT FROM lists exactly as they appear.

--- GROUP 5: BASIC SCIENCE MAJOR REQUIREMENTS ---

label: "BASIC SCIENCE MAJOR REQUIREMENTS FOR COMPUTER SCIENCE"
completion_measure: "subgroups"
required_count: count the numbered options in this section

IMPORTANT: This section contains exception code NOTE lines for this
specific student (CW, RE). Ignore those entirely. Extract the structural
requirement as stated — the science option and any courses it lists are
the degree requirement regardless of what this student's exceptions say.

--- GROUP 6: MAJOR REQUIREMENTS FOR COMPUTER SCIENCE ---

label: "MAJOR REQUIREMENTS FOR COMPUTER SCIENCE"
completion_measure: "subgroups"
required_count: count the numbered sub-requirements (do not use EARNED/NEEDS)

Sub-requirements: extract all numbered items (1 through N).

IMPORTANT RULES FOR THIS GROUP:
  a) required_courses for "fixed" sub-requirements must include ALL courses
     the degree program lists — including any that appear only as a CW waiver
     NOTE in this student's report. A waiver means the student was excused
     from the course, not that the course isn't required by the degree.
     The full structural required_courses list must be reconstructed from the
     degree program structure, not from which courses this student happened
     to take. If STARS clearly prints a course in the requirement structure
     (even in a NOTE), include it in required_courses.

  b) select_from for "select_n" and "select_units" sub-requirements:
     Collect the ENTIRE SELECT FROM list, including all wrapped lines.
     Do not stop at the first line break.

  c) courses_required is the degree-level count — what the major requires,
     not what this student still needs. Extract it from the structural
     requirement text.

══════════════════════════════════════════════════════════════
SECTION 3 — OUTPUT SCHEMA
══════════════════════════════════════════════════════════════

{
  "major": string,
  "degree": string,
  "program_code": string,
  "catalog_year": string,       // raw STARS code e.g. "20243"
  "minor": string | null,

  "requirement_groups": [
    {
      "label": string,
      "completion_measure": "courses" | "units" | "subgroups",
      "required_count": float,
      "notes": [string],
      "sub_requirements": [
        {
          "index": integer,             // 1-based, matching STARS numbering
          "label": string | null,       // null if no label in STARS
          "completion_type": "fixed" | "select_n" | "select_units" | "select_category",
          "required_courses": [string], // for "fixed" only; empty list otherwise
          "select_from": [string],      // explicit codes; empty if select_from_is_category
          "select_from_is_category": boolean,
          "select_from_category": string | null,
          "courses_required": integer,  // 0 if unit-based
          "units_required": float,      // 0.0 if course-count-based
          "grade_minimum": string | null,
          "notes": [string]
        }
      ]
    }
  ]
}

Output raw JSON only. No text before or after the JSON object.

══════════════════════════════════════════════════════════════
SECTION 4 — STARS REPORT TEXT
══════════════════════════════════════════════════════════════

{stars_text}
"""


def _build_prompt_rm(stars_text: str) -> str:
    return _PROMPT_TEMPLATE_RM.replace("{stars_text}", stars_text)


# ---------------------------------------------------------------------------
# Course code normalization for RequirementsMap
# ---------------------------------------------------------------------------

def _normalize_all_codes_rm(rm: RequirementsMap) -> RequirementsMap:
    """Normalize DEPT-NNN codes in all required_courses / select_from lists."""
    n = _normalize_code
    return rm.model_copy(update={
        "requirement_groups": [
            g.model_copy(update={
                "sub_requirements": [
                    sr.model_copy(update={
                        "required_courses": [n(c) for c in sr.required_courses],
                        "select_from":      [n(c) for c in sr.select_from],
                    })
                    for sr in g.sub_requirements
                ]
            })
            for g in rm.requirement_groups
        ]
    })


# ---------------------------------------------------------------------------
# LLM invocation — tenacity for API failures, one schema correction retry
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _raw_invoke_rm(messages: list) -> RequirementsMap:
    return _get_structured_llm_rm().invoke(messages)


def _extract_requirements_map(stars_text: str) -> RequirementsMap:
    """Call LLM with one schema-correction retry on top of tenacity API retries."""
    prompt = _build_prompt_rm(stars_text)
    messages = [HumanMessage(content=prompt)]
    try:
        return _raw_invoke_rm(messages)
    except (ValidationError, OutputParserException) as exc:
        logger.warning("RequirementsMap schema validation failed, retrying with correction: %s", exc)
        return _raw_invoke_rm([
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

def requirements_map_node(state: StarsParserState) -> dict:
    stars_text = state["stars_pdf_text"]
    requirements_map = _extract_requirements_map(stars_text)
    requirements_map = _normalize_all_codes_rm(requirements_map)
    logger.info(
        "RequirementsMap extracted: %s %s, %d requirement group(s).",
        requirements_map.major,
        requirements_map.degree,
        len(requirements_map.requirement_groups),
    )
    return {"requirements_map": requirements_map}
