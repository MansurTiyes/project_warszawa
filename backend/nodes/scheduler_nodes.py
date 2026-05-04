"""
Nodes for the schedule_validator_loop subgraph.

Node inventory (implementation order):
  reconstruct_timeline_node   — Python: group courses_taken into CompletedSemester list
  schedule_construction_node  — LLM (Sonnet): produce DraftSchedule
  merge_schedule_node         — Python: combine timeline + draft → PlanJSON + remarks
  validator_node              — Python: 5 hard checks → ValidationResult
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from models.pipeline_state import CourseNode, ScoredCourse, SchedulerState
from models.plan import (
    CompletedSemester,
    DraftSchedule,
    PlanJSON,
    PlanMetadata,
    PlannedCourse,
    SemesterPlan,
    SemesterSlot,
    ValidationResult,
    Violation,
)
from models.student_state import CourseRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Semester sort helpers (shared by reconstruct_timeline_node and merge_schedule_node)
# ---------------------------------------------------------------------------

# MVP: only Fall and Spring. Spring precedes Fall within the same year.
_SEASON_ORDER: dict[str, int] = {"Spring": 0, "Fall": 1}


def _semester_sort_key(label: str) -> tuple[int, int]:
    """Parse "Fall 2024" → (2024, 1) for chronological sorting."""
    parts = label.split()
    season = parts[0] if len(parts) >= 2 else ""
    year = int(parts[-1]) if parts and parts[-1].isdigit() else 0
    return (year, _SEASON_ORDER.get(season, 2))


# ---------------------------------------------------------------------------
# reconstruct_timeline_node
# ---------------------------------------------------------------------------

def reconstruct_timeline_node(state: SchedulerState) -> dict[str, Any]:
    """Group student_state.courses_taken into a chronologically ordered
    list of CompletedSemester objects.

    All courses in courses_taken are included — the STARS parser already
    determined what belongs there, including in-progress courses (>IP flag).
    In-progress courses are treated optimistically as completed for planning.

    merge_schedule_node later converts CourseRecord entries into PlannedCourse
    objects and sets is_completed per course based on the >IP flag.
    """
    courses: list[CourseRecord] = state["student_state"].courses_taken

    by_semester: defaultdict[str, list[CourseRecord]] = defaultdict(list)
    for course in courses:
        by_semester[course.semester_label].append(course)

    completed_timeline = [
        CompletedSemester(
            label=label,
            courses=semester_courses,
            total_units=sum(c.units for c in semester_courses),
        )
        for label, semester_courses in sorted(
            by_semester.items(),
            key=lambda kv: _semester_sort_key(kv[0]),
        )
    ]

    return {"completed_timeline": completed_timeline}


# ---------------------------------------------------------------------------
# schedule_construction_node — private LLM output schemas
# ---------------------------------------------------------------------------

class _CoursePlacement(BaseModel):
    """Minimal course reference in the LLM draft output.

    The LLM only outputs course_code (and optionally name for novel courses
    added via additional_requirements). All other CourseNode fields are
    looked up from the input course lists by _to_draft_schedule.
    """
    course_code: str
    name: str = ""


class _SemesterDraft(BaseModel):
    label: str                           # relative integer string: "1", "2", "3", ...
    courses: list[_CoursePlacement]


class _ScheduleDraft(BaseModel):
    future_semesters: list[_SemesterDraft]
    remarks: list[str] = Field(default_factory=list)
    omitted_hard_courses: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# schedule_construction_node — module-level lazy LLM
# ---------------------------------------------------------------------------

_llm_structured: Any = None


def _get_llm() -> Any:
    global _llm_structured
    if _llm_structured is None:
        from config import settings
        from langchain_anthropic import ChatAnthropic
        _llm_structured = ChatAnthropic(
            model="claude-sonnet-4-6",
            api_key=settings.anthropic_api_key,
            temperature=0,
        ).with_structured_output(_ScheduleDraft)
    return _llm_structured


# ---------------------------------------------------------------------------
# schedule_construction_node — helpers
# ---------------------------------------------------------------------------

def _build_course_lookup(
    hard_courses: list[CourseNode],
    scored_electives: list[ScoredCourse],
    scored_enrichment: list[list[ScoredCourse]],
) -> dict[str, CourseNode]:
    """Build a course_code → CourseNode index from all known input courses."""
    lookup: dict[str, CourseNode] = {}
    for c in hard_courses:
        lookup[c.course_code] = c
    for sc in scored_electives:
        lookup[sc.course.course_code] = sc.course
    for chain in scored_enrichment:
        for sc in chain:
            lookup[sc.course.course_code] = sc.course
    return lookup


def _to_draft_schedule(
    llm_output: _ScheduleDraft,
    course_lookup: dict[str, CourseNode],
) -> DraftSchedule:
    """Convert slim LLM output into a DraftSchedule with full CourseNode objects.

    Resolution order per course_code:
      1. GE placeholder (starts with "GE-") → synthetic 4-unit CourseNode
      2. Known course in lookup → full enriched CourseNode from inputs
      3. Unknown (novel via additional_requirements) → minimal stub CourseNode
    """
    future_semesters: list[SemesterSlot] = []

    for sem in llm_output.future_semesters:
        courses: list[CourseNode] = []
        for placement in sem.courses:
            code = placement.course_code
            if code.startswith("GE-"):
                node = CourseNode(
                    course_code=code,
                    name=placement.name or code,
                    units=4.0,
                )
            elif code in course_lookup:
                node = course_lookup[code]
            else:
                # Novel course injected via additional_requirements
                logger.warning("schedule_construction_node: unknown course %s — creating stub", code)
                node = CourseNode(course_code=code, name=placement.name)
            courses.append(node)

        future_semesters.append(SemesterSlot(
            label=sem.label,
            courses=courses,
            total_units=sum(c.units for c in courses),
        ))

    return DraftSchedule(
        future_semesters=future_semesters,
        remarks=llm_output.remarks,
        omitted_hard_courses=llm_output.omitted_hard_courses,
    )


def _build_context_json(
    completed_timeline: list[CompletedSemester],
    hard_courses: list[CourseNode],
    ge_placeholders_remaining: list[str],
    scored_electives: list[ScoredCourse],
    scored_enrichment: list[list[ScoredCourse]],
    career_goal: str,
    other_must_reqs: Any,
    additional_requirements: str,
    current_plan: Any,
    violations: list[Violation],
) -> str:
    """Serialize all scheduling inputs as a JSON object embedded into the prompt.

    description is excluded from hard_courses (not needed for placement decisions).
    description is kept for scored_electives and scored_enrichment (career alignment rationale).
    CourseRecord (completed_timeline) has no description field.
    """
    data: dict[str, Any] = {
        "career_goal": career_goal,
        "other_must_reqs": other_must_reqs.model_dump() if other_must_reqs else {},
        "completed_timeline": [s.model_dump() for s in completed_timeline],
        "hard_courses": [c.model_dump(exclude={"description"}) for c in hard_courses],
        "ge_placeholders_remaining": ge_placeholders_remaining,
        "scored_electives": [sc.model_dump() for sc in scored_electives],
        "scored_enrichment": [[sc.model_dump() for sc in chain] for chain in scored_enrichment],
    }
    if additional_requirements:
        data["additional_requirements"] = additional_requirements
    if violations:
        data["violations"] = [v.model_dump() for v in violations]
    if current_plan:
        data["current_plan"] = current_plan.model_dump()
    return json.dumps(data, indent=2)


# Prompt templates — {context} is replaced at call time with the JSON object
# produced by _build_context_json(). Fill in instruction text around it.

_MODE1_PROMPT = """\
You are a USC academic advisor constructing a semester-by-semester course plan for an undergraduate. All inputs have been pre-filtered and pre-ranked by earlier pipeline stages — your job is placement, sequencing, and selection, not evaluation.

You are building the future portion of the plan only. Completed semesters are provided for reference — do not reproduce them.

---

## Reading the inputs

Your inputs are provided as a JSON object. Every field described below is an actual field from the serialized Pydantic models — read them directly from the data.

---

### career_goal

A string. The student's target career track. Use as a tiebreaker when choosing between similarly-ranked options.

---

### other_must_reqs

An object with degree-level budget figures. The two fields you need:

- `upper_division_still_needed` — use this as your **elective unit target**: select technical electives from `scored_electives` until you have placed this many units
- `units_still_needed` — total units remaining for degree completion; sanity-check that your plan covers this

---

### completed_timeline

A list of completed semesters, chronologically ordered. Each entry has a `label`, `total_units`, and `courses` (each course has `course_code`, `name`, `units`, `semester_label`, `course_flags`).

These semesters are locked. Do not include any of these courses in your output.

Use `completed_timeline` to determine whether a prerequisite is already satisfied: collect all `course_code` values across all completed semesters into a set. Any prereq code that appears in that set is already satisfied — you do not need to place it again.

---

### hard_courses

A list of `CourseNode` objects — every course the student must still complete. Every entry must appear in your plan.

Fields:
- `course_code` — normalized DEPT-NNN identifier (e.g. `"CSCI-360"`)
- `name` — human-readable course name
- `units` — credit units as a float
- `prereqs` — prerequisite list in AND-of-OR format (explained below)
- `prereqs_satisfied` — `true` if all prereqs already appear in `completed_timeline`; `false` if at least one does not

**Reading the `prereqs` field — AND-of-OR structure:**

`prereqs` is a list where each element is an AND requirement. ALL elements must be satisfied. Each element is one of:
- A **string**: a single course code that must be taken before this course
- A **list of strings**: an OR group — taking any one course in the list satisfies this AND slot

Example: `["CSCI-270", ["CSCI-170", "CSCI-180"]]` means: CSCI-270 must be taken AND (CSCI-170 OR CSCI-180 must be taken).

**Using `prereqs_satisfied`:**
- `true` — all prereqs appear in `completed_timeline`; no prereq placement needed
- `false` — at least one prereq has not been taken yet; inspect `prereqs` to find which ones are missing (not in `completed_timeline`), then place those in EARLIER semesters in your plan

For OR groups with unsatisfied prereqs: pick the most useful option. Prefer a course that also appears in `hard_courses` (so it gets placed anyway), one whose own `prereqs_satisfied` is `true`, or one that unlocks the most downstream courses.

---

### ge_placeholders_remaining

A list of strings — GE category codes the student still needs.

Every category in this list must appear in your plan as a 4-unit placeholder slot. Each placeholder counts as 4.0 units for load-balancing purposes.

**CRITICAL: These are placeholders, not real courses.** Output each GE slot using only its category code (e.g. `"GE-A"`). Never substitute a real course code or name. The student selects the specific course themselves.

---

### scored_electives

A list of `ScoredCourse` objects — technical elective candidates, pre-ranked by career alignment (descending). Do not re-rank.

Each entry has `score` (1.0 = perfect match, 0.0 = none) and `course` (full CourseNode including `prereqs` and `prereqs_satisfied`).

Select from the top of this list until you have placed `other_must_reqs.upper_division_still_needed` units of electives. When two adjacent courses have scores within 0.05 of each other, prefer the one with `prereqs_satisfied: true`.

If a selected elective has `prereqs_satisfied: false`, place its unsatisfied prereqs in earlier semesters exactly as you would for a hard course.

---

### scored_enrichment

A list of course groups — career-relevant catalog courses not required but valuable where capacity allows. Pre-ranked by score (descending). Do not re-rank.

Each group is a list of `ScoredCourse`. All courses in a group belong together and share the same `score`. Each course has its own `prereqs` and `prereqs_satisfied` — apply the same prereq ordering rules as for any course.

Use the relative ranking of groups to guide selection. A high-ranked group with a meaningful unit cost is worth more than a low-ranked single-course group that fits easily. Weigh total group unit cost against its score and career fit.

---

## GE double-dip rule

GE-G (Global Perspectives I) and GE-H (Global Perspectives II) can be satisfied by a single course that also satisfies a Core Literacy category (GE-A through GE-F). This means one 4-unit slot can count for two GE requirements at once.

Before you begin scheduling, inspect `ge_placeholders_remaining`. If GE-G or GE-H appears alongside any of GE-A through GE-F, merge them into one combined slot.

Output format for a merged slot: `"GE-B / GE-H"` — Core Literacy category first, slash with spaces, then the Global Perspectives category.

Rules:
- Do NOT add a separate slot for GE-G or GE-H if it is being merged
- A merged slot still counts as 4.0 units for load purposes
- GE-G and GE-H may each be double-dipped independently (e.g. GE-A/GE-G and GE-C/GE-H are two separate merged slots)

This is the most common over-scheduling mistake. Check for it before you begin placing anything.

---

## Scheduling process

Work through the plan in this order. Do not skip or reorder steps.

Step 1 — Place all required courses
Assign every required course to a future semester, respecting prereq ordering. If a course has unsatisfied prereqs that are also in required courses, place those prereqs in strictly earlier semesters. Aim to unlock downstream courses as early as possible — placing prereqs early creates more scheduling flexibility later.

Step 2 — Distribute GE placeholders
Spread all GE slots across the plan alongside required courses. Apply the GE-G/H double-dip check before placing any GE slot. Treat each placeholder as a 4-unit course for load-balancing purposes — do not cluster them.

Step 3 — Fill elective unit budget
Select electives from the top of the ranked elective pool until the target unit count is reached. Place selected electives across the plan where load allows, respecting any prerequisites.

Step 4 — Assess total remaining capacity
After steps 1–3, review every future semester. Compute the total unit capacity remaining across ALL semesters — the sum of available space between what is placed and the 18-unit ceiling in each semester. This is your enrichment budget.

Step 5 — Select and place enrichment
From the top of the ranked enrichment list, determine which options fit within the remaining capacity from step 4. Use judgment:
- Prefer higher-ranked options — the ranking already reflects career relevance
- Weigh whether a chain's total unit cost is justified by the terminal course's ranking and fit with the career goal; a top-ranked chain that costs 8 units is often better than two mid-ranked standalone courses
- Place enrichment where it best balances load across semesters
- For chains: all enabler courses must appear in strictly earlier semesters than the target

---

## Scheduling rules

1. Prereq ordering — a course with an unsatisfied prereq must always appear in a strictly later semester than that prereq. Never violate this.

2. Load — every semester must have between 12 and 18 units. Always. No exceptions for any semester, including the final one.

3. Minimum semesters — use the fewest semesters needed to satisfy all requirements within the 12–18 unit constraint. Do not pad with empty semesters.

4. Semester labels — use relative integer ordering only: "1", "2", "3", and so on. Do not use Fall/Spring/year labels. Calendar labels are assigned by a downstream step from the student's completed timeline.

5. Capstone timing — if CSCI-401, CSCI-402, or CSCI-404 appear in required courses, place them in the final two semesters of your plan.

6. Electives before enrichment — always fill the elective unit budget first. Enrichment fills remaining capacity only.

7. No duplicates — never place a course that already appears in the completed semesters. Cross-reference before placing.

8. GE placeholders — GE entries must use the exact placeholder code (e.g. "GE-A") or the double-dipped form (e.g. "GE-B / GE-H"). Never output a real course name or number for a GE slot. These are placeholders — the student selects the actual course themselves.

---

## GE placeholder rule (expanded)

All GE categories listed in `ge_placeholders_remaining` must appear in your plan.

GE-G / GE-H double-dip: GE-G (Global Perspectives I) and GE-H (Global Perspectives II) can share a single 4-unit slot with a Core Literacy category (GE-A through GE-F). If GE-G or GE-H appears in the list alongside any GE-A through GE-F category, merge them into one slot.

Output format for a merged slot: "GE-B / GE-H" — parent Core Literacy category first, slash with spaces, then GE-G or GE-H.

Do not add a separate slot for GE-G or GE-H if it is being double-dipped. Check for this explicitly before finalizing — it is the most common over-scheduling mistake.

---

## Remarks

Generate remarks as you make each decision — not as a post-hoc pass at the end. Write as complete sentences that reference course codes and semester numbers.

Generate a remark when:
- A prereq OR-group required choosing one option over another — state which you chose and why
- A prereq constraint forced a course to a later semester and created a meaningful load consequence
- A high-scoring elective was skipped because of a genuine constraint — state what blocked it
- An enrichment chain was included — briefly name the career alignment reason and acknowledge the unit cost
- An enrichment option was excluded despite ranking highly — explain the constraint
- A GE was double-dipped — name both categories merged into one slot
- Career alignment drove a non-obvious placement decision
- A required course was omitted — name the course, the completed dependent, and your reasoning

Do not remark on:
- Routine placements with no meaningful tradeoff or alternative
- Prereqs that had only one valid placement
- Electives placed simply because they were highest-ranked with no complication

Target 3–6 remarks total. If you find yourself writing one remark per course, stop — consolidate or drop. Remarks surface directly to the student; only include ones that give them genuine insight into why their plan looks the way it does.

---

## Omitting required courses

Every course in `hard_courses` must appear in your plan — with one narrow exception.

You may omit a required course if it is already implicitly covered by the student's completed history. This means every course that depends on it (directly or transitively) is already in `completed_timeline`. In this case, the course has no remaining scheduling purpose.

**Example:** MATH-125 appears in `hard_courses`, but MATH-226 (which requires MATH-125) is already in `completed_timeline`. MATH-125 is redundant — the student clearly already satisfied it.

Rules:
- Do NOT omit a course because it seems easy, low-value, or inconvenient to place
- Do NOT omit a course just because its prereqs are already satisfied — that means it can be placed, not that it should be skipped
- Only omit if you can confirm the specific redundancy argument above
- List every omitted code in `omitted_hard_courses`
- Generate a remark for each omission naming the course, the dependent already completed, and your reasoning
- When in doubt, place it — the downstream validator will not penalize a placed course

If you omit a course without listing it in `omitted_hard_courses`, the validator will flag it as missing and the plan will loop.

---

{context}

---

## Output

Your output is a DraftSchedule. A downstream Python step (merge_schedule_node) will combine it with the completed timeline, look up full course details by code, annotate each course with its category, and produce the final PlanJSON that reaches the student.

Your DraftSchedule structure:

{
  "future_semesters": [
    {
      "label": "1",
      "courses": [
        { "course_code": "CSCI-270" },
        { "course_code": "GE-A" },
        { "course_code": "MATH-225" }
      ]
    },
    {
      "label": "2",
      "courses": [
        { "course_code": "CSCI-360" },
        { "course_code": "GE-B / GE-H" },
        { "course_code": "CSCI-467" }
      ]
    }
  ],
  "remarks": [
    "CSCI-360 placed in Semester 2 — requires CSCI-270 which was placed in Semester 1; this was the earliest valid placement.",
    "GE-B and GE-H merged into a single slot in Semester 2 — double-dip saves one 4-unit slot across the plan.",
    "CSCI-467 selected as top-ranked elective for machine learning engineer goal; all prereqs already in completed history."
  ],
  "omitted_hard_courses": []
}

Field rules:
- "label": string integer ("1", "2", "3", ...) representing relative semester order. No calendar labels.
- "courses": one entry per placement. Include only course_code.
- GE course_code: exact placeholder code ("GE-A") or double-dipped form ("GE-B / GE-H"). Never a real course number.
- "remarks": list of complete-sentence strings referencing course codes and semester numbers.
- "omitted_hard_courses": list of course codes from hard_courses intentionally not placed. Empty list when all required courses are placed.

You produce DraftSchedule only. merge_schedule_node produces PlanJSON.
"""

_MODE2_PROMPT = """\
You are a USC academic advisor revising an existing semester-by-semester course plan. The plan was previously generated and has been flagged with violations that must be fixed, or has received a change instruction from the student.

Your job: make the minimum changes necessary to resolve all violations and apply any change instruction. Do not restructure semesters that are not directly affected.

---

## What you have

The JSON data block below contains all original scheduling inputs plus revision-specific fields:

- `current_plan` — the existing plan (all semesters, completed and future)
- `violations` — a list of specific issues to fix (present when the validator flagged problems)
- `additional_requirements` — a free-form change instruction from the student (present when triggered by chat)
- All original inputs: `hard_courses`, `scored_electives`, `scored_enrichment`, `ge_placeholders_remaining`, `career_goal`, `other_must_reqs`, `completed_timeline`

---

## Reading current_plan

`current_plan.semesters` contains all semesters — completed and future. Filter by `is_completed`:
- `is_completed: true` — locked; these are the same as completed_timeline and must not be reproduced in your output
- `is_completed: false` — future semesters; your working set

Each future semester has `label` (calendar string, e.g. `"Fall 2026"`), `total_units`, and `courses`. Each course has `course_code`, `name`, `units`, `is_placeholder`, and annotation fields you do not need to output.

Your output must cover ALL future semesters — changed and unchanged alike. Use the existing calendar label for any semester you preserve unchanged. If you must add a new semester beyond the current plan, derive the next calendar label chronologically (next Fall or Spring after the last existing label).

---

## Reading violations

`violations` is a list where each entry has:
- `course_code` — the course involved (`null` for plan-level violations)
- `semester` — the semester involved (`null` for course-level violations)
- `detail` — a precise, actionable description of the problem — read this directly and act on it

Fix all violations in one pass. Common types and their fixes:

| Violation type | Indicated by `detail` | Fix |
|---|---|---|
| Prereq ordering | "X requires Y, which appears in the same or a later semester" | Move Y earlier or X later |
| Missing prereq | "X requires Y, which is not in the plan" | Add Y in an earlier semester, or drop X if it is optional enrichment |
| Missing required course | "X is required for graduation but is not in the plan" | Place X in an appropriate semester |
| Unit cap exceeded | "Semester S has N units — exceeds 18 unit cap" | Move one or more courses to an adjacent semester |
| Missing GE placeholder | "GE category X has no placeholder slot" | Add the placeholder to an appropriate semester |
| Total units insufficient | "Plan covers N future units — M still needed" | Add electives or enrichment to cover the gap |

---

## Reading additional_requirements

If present, `additional_requirements` is a free-form instruction from the student. Apply it using judgment:
- Treat it as a strong preference, not an override of hard constraints (prereq ordering, 12–18 unit load, all required courses present)
- If it conflicts with fixing a violation, fix the violation first and note the tension in remarks
- If it conflicts with a hard rule, satisfy the hard rule first and note the conflict in remarks

---

## Revision rules

1. **Minimum changes** — do not modify semesters not directly affected by a violation or change instruction. Reproduce them exactly in your output.

2. **Prereq ordering** — any course you move must still appear in a strictly later semester than all its unsatisfied prereqs. Check this for every moved course.

3. **Load** — every semester, including ones you preserve unchanged, must have between 12 and 18 units.

4. **No duplicates** — no course may appear more than once across all future semesters.

5. **All required courses present** — every code in `hard_courses` must appear in the plan (either in completed history or in your output). Same omission rules apply as in initial generation.

6. **GE placeholders** — all categories in `ge_placeholders_remaining` must have a slot in the plan. If fixing a violation removes a GE slot, re-add it elsewhere.

7. **Calendar labels** — use the existing calendar labels from `current_plan` for preserved semesters. Derive new labels chronologically if adding semesters (e.g. if the last semester is "Fall 2027", the next is "Spring 2028").

---

## Omitting required courses

Same rules as initial generation. You may omit a required course only if every downstream dependent is already in completed history. List every omission in `omitted_hard_courses` and generate a remark for each. Silent omissions loop.

---

## Remarks

Generate a remark for each meaningful change:
- State what moved, from where to where, and why (the specific violation it fixed)
- Note any tradeoff — e.g. fixing one violation constrained load in an adjacent semester
- Note any conflict between a violation fix and the change instruction
- Note any required course omission

Target 3–6 remarks. Do not remark on semesters you left unchanged.

---

{context}

---

## Output

Same DraftSchedule structure as initial generation. Cover ALL future semesters — changed and unchanged:

{
  "future_semesters": [
    {
      "label": "Fall 2026",
      "courses": [
        { "course_code": "CSCI-270" },
        { "course_code": "CSCI-360" },
        { "course_code": "GE-A" }
      ]
    },
    {
      "label": "Spring 2027",
      "courses": [
        { "course_code": "CSCI-467" },
        { "course_code": "GE-B" },
        { "course_code": "CSCI-350" }
      ]
    }
  ],
  "remarks": [
    "CSCI-360 moved from Spring 2027 to Fall 2026 — it is a prereq for CSCI-467, which was already in Spring 2027.",
    "Spring 2027 load held at 16 units after the move; no further adjustment needed."
  ],
  "omitted_hard_courses": []
}

Field rules:
- "label": calendar label string (e.g. "Fall 2026"). Use labels from current_plan for preserved semesters. Derive new labels chronologically if adding semesters.
- "courses": one entry per placement. Include only course_code.
- GE course_code: exact placeholder code ("GE-A") or double-dipped form ("GE-B / GE-H"). Never a real course number.
- "remarks": complete-sentence strings referencing course codes and calendar labels.
- "omitted_hard_courses": course codes from hard_courses intentionally not placed. Empty list when all required courses are placed.

You produce DraftSchedule only. merge_schedule_node produces PlanJSON.
"""


# ---------------------------------------------------------------------------
# schedule_construction_node
# ---------------------------------------------------------------------------

def schedule_construction_node(state: SchedulerState) -> dict[str, Any]:
    """Core generative node. Calls Claude Sonnet to produce a DraftSchedule.

    Mode 1 (first generation): violations empty, no additional_requirements,
    no current_plan. Builds a full plan from scratch.

    Mode 2 (revision): violations present, OR additional_requirements set, OR
    current_plan present. Makes minimum changes to fix violations / apply changes.
    """
    hard_courses: list[CourseNode]              = state.get("hard_courses", [])
    ge_placeholders_remaining: list[str]        = state.get("ge_placeholders_remaining", [])
    scored_electives: list[ScoredCourse]        = state.get("scored_electives", [])
    scored_enrichment: list[list[ScoredCourse]] = state.get("scored_enrichment", [])
    career_goal: str                            = state.get("career_goal", "")
    other_must_reqs                             = state.get("other_must_reqs")
    additional_requirements: str               = state.get("additional_requirements", "")
    current_plan                                = state.get("current_plan")
    completed_timeline: list[CompletedSemester] = state.get("completed_timeline", [])

    validation_result: ValidationResult | None = state.get("validation_result")
    violations = validation_result.violations if validation_result else []

    is_revision = bool(violations) or bool(additional_requirements) or current_plan is not None

    context = _build_context_json(
        completed_timeline=completed_timeline,
        hard_courses=hard_courses,
        ge_placeholders_remaining=ge_placeholders_remaining,
        scored_electives=scored_electives,
        scored_enrichment=scored_enrichment,
        career_goal=career_goal,
        other_must_reqs=other_must_reqs,
        additional_requirements=additional_requirements,
        current_plan=current_plan,
        violations=violations,
    )

    prompt = (_MODE2_PROMPT if is_revision else _MODE1_PROMPT).replace("{context}", context)

    course_lookup = _build_course_lookup(hard_courses, scored_electives, scored_enrichment)

    try:
        llm_output: _ScheduleDraft = _get_llm().invoke(prompt)
        draft_schedule = _to_draft_schedule(llm_output, course_lookup)
    except Exception:
        logger.exception("schedule_construction_node LLM call failed — returning empty draft")
        draft_schedule = DraftSchedule(
            future_semesters=[],
            remarks=["Schedule generation failed — please try again."],
        )

    return {"draft_schedule": draft_schedule}


# ---------------------------------------------------------------------------
# merge_schedule_node — helpers
# ---------------------------------------------------------------------------

_CAPSTONE_CODES: frozenset[str] = frozenset({"CSCI-401", "CSCI-402", "CSCI-404"})
_WRITING_CODES: frozenset[str] = frozenset({"WRIT-340"})
_MATH_PREFIX = "MATH-"
_SCIENCE_DEPTS: frozenset[str] = frozenset({"PHYS", "CHEM", "BISC"})

# Maps Fall/Spring to the next season and the year offset to apply.
_NEXT_SEASON: dict[str, tuple[str, int]] = {
    "Spring": ("Fall", 0),   # Spring YYYY → Fall YYYY
    "Fall":   ("Spring", 1), # Fall YYYY  → Spring YYYY+1
}


def _infer_hard_category(course_code: str) -> str:
    if course_code in _CAPSTONE_CODES:
        return "capstone"
    if course_code in _WRITING_CODES:
        return "writing"
    dept = course_code.split("-")[0] if "-" in course_code else ""
    if dept == "MATH":
        return "math_foundation"
    if dept in _SCIENCE_DEPTS:
        return "science_foundation"
    return "core"


def _is_enabler(course_code: str, scored_enrichment: list[list[ScoredCourse]]) -> bool:
    """True if the course is an enrichment prereq-only enabler (not the terminal course)."""
    terminal_codes = {chain[-1].course.course_code for chain in scored_enrichment if chain}
    return course_code not in terminal_codes


def _next_calendar_label(label: str) -> str:
    parts = label.split()
    if len(parts) != 2 or not parts[-1].isdigit():
        return label
    season, year = parts[0], int(parts[1])
    next_season, year_offset = _NEXT_SEASON.get(season, ("Fall", 0))
    return f"{next_season} {year + year_offset}"


def _future_calendar_labels(completed_timeline: list[CompletedSemester], count: int) -> list[str]:
    """Generate `count` sequential calendar labels starting after the last completed semester."""
    anchor = completed_timeline[-1].label if completed_timeline else "Spring 2025"
    labels: list[str] = []
    current = anchor
    for _ in range(count):
        current = _next_calendar_label(current)
        labels.append(current)
    return labels


def _annotate_course(
    node: CourseNode,
    hard_codes: set[str],
    elective_codes: set[str],
    enrichment_codes: set[str],
    scored_enrichment: list[list[ScoredCourse]],
) -> tuple[str, bool, bool]:
    """Return (category, is_placeholder, is_enabler) for a future course."""
    code = node.course_code
    if code.startswith("GE-"):
        return ("ge_placeholder", True, False)
    if code in hard_codes:
        return (_infer_hard_category(code), False, False)
    if code in elective_codes:
        return ("technical_elective", False, False)
    if code in enrichment_codes:
        enabler = _is_enabler(code, scored_enrichment)
        return ("enrichment", False, enabler)
    # Novel course injected via additional_requirements
    return ("enrichment", False, False)


# ---------------------------------------------------------------------------
# merge_schedule_node
# ---------------------------------------------------------------------------

def merge_schedule_node(state: SchedulerState) -> dict[str, Any]:
    """Combine completed_timeline + draft_schedule → PlanJSON.

    Two responsibilities:
    1. Calendar label resolution — Mode 1 draft uses relative integers ("1", "2", ...);
       Mode 2 draft uses calendar strings ("Fall 2026"). Detected per label.isdigit().
    2. PlannedCourse annotation — category, is_placeholder, is_enabler derived from
       context sets (hard_codes, elective_codes, enrichment_codes). Never from the LLM.

    Writes back to SchedulerState:
      current_plan        — final PlanJSON
      remarks             — from draft_schedule.remarks (overwritten each loop pass)
      omitted_hard_courses — forwarded from draft_schedule for validator check #3
    """
    completed_timeline: list[CompletedSemester]    = state.get("completed_timeline", [])
    draft_schedule: DraftSchedule                  = state["draft_schedule"]
    hard_courses: list[CourseNode]                 = state.get("hard_courses", [])
    scored_electives: list[ScoredCourse]           = state.get("scored_electives", [])
    scored_enrichment: list[list[ScoredCourse]]    = state.get("scored_enrichment", [])
    career_goal: str                               = state.get("career_goal", "")
    existing_plan: PlanJSON | None                 = state.get("current_plan")

    hard_codes     = {c.course_code for c in hard_courses}
    elective_codes = {sc.course.course_code for sc in scored_electives}
    enrichment_codes = {
        sc.course.course_code
        for chain in scored_enrichment
        for sc in chain
    }

    # --- Completed semesters → SemesterPlan ---
    completed_semesters: list[SemesterPlan] = []
    for idx, sem in enumerate(completed_timeline):
        planned_courses = [
            PlannedCourse(
                course_code=c.course_code,
                name=c.name,
                units=c.units,
                category=(
                    _infer_hard_category(c.course_code) if c.course_code in hard_codes
                    else "technical_elective" if c.course_code in elective_codes
                    else "enrichment" if c.course_code in enrichment_codes
                    else "core"
                ),
                is_placeholder=False,
                is_completed=True,
                is_enabler=False,
            )
            for c in sem.courses
        ]
        completed_semesters.append(SemesterPlan(
            label=sem.label,
            semester_index=idx,
            is_completed=True,
            total_units=sem.total_units,
            courses=planned_courses,
        ))

    # --- Calendar label resolution ---
    draft_slots = draft_schedule.future_semesters
    if draft_slots and draft_slots[0].label.isdigit():
        # Mode 1: relative integers → derive from completed timeline
        cal_labels = _future_calendar_labels(completed_timeline, len(draft_slots))
    else:
        # Mode 2: LLM output calendar strings — use directly
        cal_labels = [s.label for s in draft_slots]

    # --- Future semesters → SemesterPlan ---
    base_idx = len(completed_semesters)
    future_semesters: list[SemesterPlan] = []

    elective_units_placed = 0.0
    enrichment_count = 0
    ge_placeholders_placed = 0

    for rel_idx, (slot, cal_label) in enumerate(zip(draft_slots, cal_labels)):
        planned_courses = []
        for node in slot.courses:
            category, is_placeholder, is_enabler = _annotate_course(
                node, hard_codes, elective_codes, enrichment_codes, scored_enrichment
            )
            planned_courses.append(PlannedCourse(
                course_code=node.course_code,
                name=node.name,
                units=node.units,
                prereqs=node.prereqs,
                prereqs_satisfied=node.prereqs_satisfied,
                description=node.description,
                category=category,
                is_placeholder=is_placeholder,
                is_completed=False,
                is_enabler=is_enabler,
            ))
            if is_placeholder:
                ge_placeholders_placed += 1
            elif category == "technical_elective":
                elective_units_placed += node.units
            elif category == "enrichment" and not is_enabler:
                enrichment_count += 1

        future_units = sum(c.units for c in planned_courses)
        future_semesters.append(SemesterPlan(
            label=cal_label,
            semester_index=base_idx + rel_idx,
            is_completed=False,
            total_units=future_units,
            courses=planned_courses,
        ))

    # --- Assemble PlanJSON ---
    all_semesters = completed_semesters + future_semesters
    total_future_units = sum(s.total_units for s in future_semesters)
    version_id = (existing_plan.version_id + 1) if existing_plan else 1

    plan = PlanJSON(
        generated_at=datetime.now(timezone.utc).isoformat(),
        version_id=version_id,
        track=career_goal,
        total_units_planned=total_future_units,
        total_semesters=len(all_semesters),
        semesters=all_semesters,
        metadata=PlanMetadata(
            elective_units_placed=elective_units_placed,
            enrichment_count=enrichment_count,
            ge_placeholders_placed=ge_placeholders_placed,
            unresolved_remarks=[],
        ),
    )

    return {
        "current_plan": plan,
        "remarks": draft_schedule.remarks,
        "omitted_hard_courses": draft_schedule.omitted_hard_courses,
    }
