"""
Eval runner for the full schedule_validator_loop subgraph.

Invokes the compiled LangGraph subgraph end-to-end:
  reconstruct_timeline_node → schedule_construction_node → merge_schedule_node
  → validator_node → (loop if violations) → END

Uses the same input extraction as scheduler_mode1.py but runs through the
compiled graph rather than calling nodes individually.

Run from backend/:
    python -m evals.runners.scheduler_subgraph
    python -m evals.runners.scheduler_subgraph --career-goal "machine learning engineer"
    python -m evals.runners.scheduler_subgraph --stars p --hard p --soft p --output p
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from graphs.subgraphs.scheduler import schedule_validator_loop
from models.course_nodes import CourseNode, OtherRequirements, ScoredCourse
from models.requirements_map import RequirementsMap
from models.student_state import StudentState

logger = logging.getLogger(__name__)

_RESULTS_DIR = Path(__file__).parent.parent / "results"
_DEFAULT_CAREER_GOAL = "backend engineering"


def _latest(prefix: str) -> Path:
    candidates = sorted(_RESULTS_DIR.glob(f"{prefix}_*.json"))
    if not candidates:
        raise FileNotFoundError(
            f"No {prefix} result found in {_RESULTS_DIR}. "
            f"Run evals.runners.{prefix} first."
        )
    return candidates[-1]


def run(
    stars_path: Path | None = None,
    hard_path: Path | None = None,
    soft_path: Path | None = None,
    career_goal: str = _DEFAULT_CAREER_GOAL,
    output: Path | None = None,
) -> Path:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    stars_source = stars_path or _latest("stars_parser_graph")
    hard_source  = hard_path  or _latest("hard_requirements")
    soft_source  = soft_path  or _latest("soft_requirements")

    logger.info("Stars result: %s", stars_source)
    logger.info("Hard result:  %s", hard_source)
    logger.info("Soft result:  %s", soft_source)
    logger.info("Career goal:  %s", career_goal)

    stars_raw = json.loads(stars_source.read_text(encoding="utf-8"))
    hard_raw  = json.loads(hard_source.read_text(encoding="utf-8"))
    soft_raw  = json.loads(soft_source.read_text(encoding="utf-8"))

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

    logger.info(
        "Loaded: %s | %d hard | %d electives | %d enrichment chains | %d GE placeholders",
        student_state.name,
        len(hard_courses),
        len(scored_electives),
        len(scored_enrichment),
        len(ge_placeholders),
    )

    initial_state = {
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
        "additional_requirements":   "",
        "current_plan":              None,
        "remarks":                   [],
    }

    # --- Invoke compiled subgraph ---
    logger.info("Invoking schedule_validator_loop subgraph...")
    result = schedule_validator_loop.invoke(initial_state)

    # --- Extract outputs ---
    plan             = result["current_plan"]
    remarks          = result.get("remarks", [])
    validation_result = result.get("validation_result")
    iteration_count  = result.get("iteration_count", 0)
    omitted          = result.get("omitted_hard_courses", [])

    is_valid = validation_result.is_valid if validation_result else True
    violations = validation_result.violations if validation_result else []

    # --- Log summary ---
    logger.info(
        "Loop finished: %d iteration(s) | valid=%s | %d violation(s) remaining",
        iteration_count,
        is_valid,
        len(violations),
    )
    if violations:
        logger.warning("Unresolved violations (MAX_ITERATIONS hit):")
        for v in violations:
            logger.warning("  • [%s] %s", v.course_code or v.semester or "plan", v.detail)

    future = [s for s in plan.semesters if not s.is_completed]
    logger.info(
        "PlanJSON: %d future semesters | %.1f future units | version_id=%d",
        len(future),
        plan.total_units_planned,
        plan.version_id,
    )
    for sem in future:
        codes = ", ".join(c.course_code for c in sem.courses)
        logger.info("  %s [%.1f units]: %s", sem.label, sem.total_units, codes)

    if omitted:
        logger.info("omitted_hard_courses: %s", omitted)

    logger.info("Remarks (%d):", len(remarks))
    for r in remarks:
        logger.info("  • %s", r)

    # --- Serialize ---
    payload = {
        "source_stars":          str(stars_source),
        "source_hard":           str(hard_source),
        "source_soft":           str(soft_source),
        "student_name":          student_state.name,
        "career_goal":           career_goal,
        "iterations":            iteration_count,
        "is_valid":              is_valid,
        "violations":            [v.model_dump() for v in violations],
        "plan":                  json.loads(plan.model_dump_json()),
        "remarks":               remarks,
        "omitted_hard_courses":  omitted,
    }

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = output or _RESULTS_DIR / f"scheduler_subgraph_{timestamp}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    logger.info("Results → %s", out_path)
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Eval: full schedule_validator_loop subgraph")
    parser.add_argument("--stars",       type=Path, default=None)
    parser.add_argument("--hard",        type=Path, default=None)
    parser.add_argument("--soft",        type=Path, default=None)
    parser.add_argument("--career-goal", type=str,  default=_DEFAULT_CAREER_GOAL)
    parser.add_argument("--output",      type=Path, default=None)
    args = parser.parse_args()
    run(
        stars_path=args.stars,
        hard_path=args.hard,
        soft_path=args.soft,
        career_goal=args.career_goal,
        output=args.output,
    )
