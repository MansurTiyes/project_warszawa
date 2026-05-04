"""
Eval runner for schedule_construction_node — Mode 2 (additional_requirements).

Scenario: student wants an extra semester filled with productive enrichment.

Loads an existing Mode 1 PlanJSON result as current_plan, then runs Mode 2
(schedule_construction_node → merge_schedule_node) with the change instruction.
reconstruct_timeline_node is re-run to rebuild completed_timeline from stars.

Run from backend/:
    python -m evals.runners.scheduler_mode2_extra_semester
    python -m evals.runners.scheduler_mode2_extra_semester --mode1 path/to/scheduler_mode1.json
    python -m evals.runners.scheduler_mode2_extra_semester --instruction "custom instruction"
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from models.pipeline_state import CourseNode, OtherRequirements, ScoredCourse
from models.plan import PlanJSON
from models.requirements_map import RequirementsMap
from models.student_state import StudentState
from nodes.scheduler_nodes import (
    merge_schedule_node,
    reconstruct_timeline_node,
    schedule_construction_node,
)

logger = logging.getLogger(__name__)

_RESULTS_DIR = Path(__file__).parent.parent / "results"
_DEFAULT_CAREER_GOAL = "backend engineering"
_DEFAULT_INSTRUCTION = (
    "I want to take an extra semester since I seem to be finishing early. "
    "Account for that and fill it with classes you believe would be productive for me."
)


def _latest(prefix: str) -> Path:
    candidates = sorted(_RESULTS_DIR.glob(f"{prefix}_*.json"))
    if not candidates:
        raise FileNotFoundError(
            f"No {prefix} result found in {_RESULTS_DIR}. "
            f"Run evals.runners.{prefix} first."
        )
    return candidates[-1]


def run(
    mode1_path: Path | None = None,
    stars_path: Path | None = None,
    hard_path: Path | None = None,
    soft_path: Path | None = None,
    additional_requirements: str = _DEFAULT_INSTRUCTION,
    output: Path | None = None,
) -> Path:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    mode1_source = mode1_path or _latest("scheduler_mode1")
    stars_source = stars_path or _latest("stars_parser_graph")
    hard_source  = hard_path  or _latest("hard_requirements")
    soft_source  = soft_path  or _latest("soft_requirements")

    logger.info("Mode1 result:  %s", mode1_source)
    logger.info("Stars result:  %s", stars_source)
    logger.info("Hard result:   %s", hard_source)
    logger.info("Soft result:   %s", soft_source)
    logger.info("Instruction:   %s", additional_requirements)

    mode1_raw = json.loads(mode1_source.read_text(encoding="utf-8"))
    stars_raw  = json.loads(stars_source.read_text(encoding="utf-8"))
    hard_raw   = json.loads(hard_source.read_text(encoding="utf-8"))
    soft_raw   = json.loads(soft_source.read_text(encoding="utf-8"))

    # --- Reconstruct current_plan from Mode 1 result ---
    current_plan = PlanJSON(**mode1_raw["plan"])
    career_goal  = mode1_raw["career_goal"]

    future_before = [s for s in current_plan.semesters if not s.is_completed]
    logger.info(
        "Loaded base plan: %d future semesters | %.1f future units | version_id=%d",
        len(future_before),
        current_plan.total_units_planned,
        current_plan.version_id,
    )
    for sem in future_before:
        codes = ", ".join(c.course_code for c in sem.courses)
        logger.info("  [base] %s [%.1f units]: %s", sem.label, sem.total_units, codes)

    # --- Load all other inputs ---
    student_state    = StudentState(**stars_raw["student_state"])
    requirements_map = RequirementsMap(**stars_raw["requirements_map"])
    hard_courses     = [CourseNode(**c) for c in hard_raw["hard_courses"]]
    elective_courses = [CourseNode(**c) for c in hard_raw["elective_courses"]]
    other_must_reqs  = OtherRequirements(**hard_raw["other_must_reqs"])
    ge_placeholders  = hard_raw["ge_placeholders_remaining"]
    scored_electives  = [ScoredCourse(**sc) for sc in soft_raw["scored_electives"]]
    scored_enrichment = [
        [ScoredCourse(**sc) for sc in chain]
        for chain in soft_raw["scored_enrichment"]
    ]

    # --- Build state ---
    state: dict = {
        "stars_pdf_text":            "",
        "student_state":             student_state,
        "requirements_map":          requirements_map,
        "career_goal":               career_goal,
        "hard_courses":              hard_courses,
        "elective_courses":          elective_courses,
        "other_must_reqs":           other_must_reqs,
        "ge_placeholders_remaining": ge_placeholders,
        "scored_electives":          scored_electives,
        "scored_enrichment":         scored_enrichment,
        "additional_requirements":   additional_requirements,
        "current_plan":              current_plan,
        "remarks":                   [],
    }

    # --- reconstruct_timeline_node (Mode 2 skips this in the loop, but we need
    #     completed_timeline in state for schedule_construction_node context) ---
    logger.info("Running reconstruct_timeline_node...")
    state.update(reconstruct_timeline_node(state))
    logger.info(
        "completed_timeline: %d semesters, last=%s",
        len(state["completed_timeline"]),
        state["completed_timeline"][-1].label if state["completed_timeline"] else "none",
    )

    # --- schedule_construction_node — Mode 2 (additional_requirements + current_plan set) ---
    logger.info("Running schedule_construction_node (LLM call — Mode 2)...")
    state.update(schedule_construction_node(state))
    draft = state["draft_schedule"]
    logger.info(
        "draft_schedule: %d future semesters | %d remarks | %d omitted_hard_courses",
        len(draft.future_semesters),
        len(draft.remarks),
        len(draft.omitted_hard_courses),
    )

    # --- merge_schedule_node ---
    logger.info("Running merge_schedule_node...")
    state.update(merge_schedule_node(state))
    revised_plan = state["current_plan"]

    future_after = [s for s in revised_plan.semesters if not s.is_completed]
    logger.info(
        "Revised plan: %d future semesters | %.1f future units | version_id=%d",
        len(future_after),
        revised_plan.total_units_planned,
        revised_plan.version_id,
    )
    for sem in future_after:
        codes = ", ".join(c.course_code for c in sem.courses)
        logger.info("  [revised] %s [%.1f units]: %s", sem.label, sem.total_units, codes)

    logger.info(
        "Semester delta: %d → %d future semesters",
        len(future_before),
        len(future_after),
    )

    logger.info("Remarks (%d):", len(state["remarks"]))
    for r in state["remarks"]:
        logger.info("  • %s", r)

    # --- Serialize ---
    payload = {
        "source_mode1":              str(mode1_source),
        "source_stars":              str(stars_source),
        "source_hard":               str(hard_source),
        "source_soft":               str(soft_source),
        "student_name":              student_state.name,
        "career_goal":               career_goal,
        "additional_requirements":   additional_requirements,
        "base_plan":                 mode1_raw["plan"],
        "base_remarks":              mode1_raw["remarks"],
        "revised_plan":              json.loads(revised_plan.model_dump_json()),
        "revised_remarks":           state["remarks"],
        "omitted_hard_courses":      state.get("omitted_hard_courses", []),
    }

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = output or _RESULTS_DIR / f"scheduler_mode2_extra_semester_{timestamp}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    logger.info("Results → %s", out_path)
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Eval: schedule_construction_node Mode 2 — extra semester instruction"
    )
    parser.add_argument(
        "--mode1", type=Path, default=None,
        help="Path to a scheduler_mode1 result JSON (defaults to latest)",
    )
    parser.add_argument("--stars",  type=Path, default=None)
    parser.add_argument("--hard",   type=Path, default=None)
    parser.add_argument("--soft",   type=Path, default=None)
    parser.add_argument(
        "--instruction", type=str, default=_DEFAULT_INSTRUCTION,
        help="Free-form change instruction (additional_requirements)",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    run(
        mode1_path=args.mode1,
        stars_path=args.stars,
        hard_path=args.hard,
        soft_path=args.soft,
        additional_requirements=args.instruction,
        output=args.output,
    )
