"""
Eval runner for the full schedule_validator_loop subgraph — Mode 2.

Scenario: student wants an extra semester filled with productive enrichment.

Loads a PlanJSON from the most recent scheduler_subgraph run as current_plan,
then invokes the compiled schedule_validator_loop with additional_requirements set.
The subgraph detects Mode 2 (current_plan + additional_requirements present) and
runs revision + validator loop end-to-end.

Run from backend/:
    python -m evals.runners.scheduler_subgraph_mode2
    python -m evals.runners.scheduler_subgraph_mode2 --base path/to/scheduler_subgraph.json
    python -m evals.runners.scheduler_subgraph_mode2 --instruction "custom instruction"
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from graphs.subgraphs.scheduler import schedule_validator_loop
from models.course_nodes import CourseNode, OtherRequirements, ScoredCourse
from models.plan import PlanJSON
from models.requirements_map import RequirementsMap
from models.student_state import StudentState

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
    base_path: Path | None = None,
    stars_path: Path | None = None,
    hard_path: Path | None = None,
    soft_path: Path | None = None,
    additional_requirements: str = _DEFAULT_INSTRUCTION,
    output: Path | None = None,
) -> Path:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if base_path is not None:
        base_source = base_path
    else:
        # Exclude mode2 results — they don't have the "plan" key expected here
        candidates = sorted(
            p for p in _RESULTS_DIR.glob("scheduler_subgraph_*.json")
            if "mode2" not in p.name
        )
        if not candidates:
            raise FileNotFoundError(
                "No scheduler_subgraph result found. Run evals.runners.scheduler_subgraph first."
            )
        base_source = candidates[-1]
    stars_source = stars_path or _latest("stars_parser_graph")
    hard_source  = hard_path  or _latest("hard_requirements")
    soft_source  = soft_path  or _latest("soft_requirements")

    logger.info("Base plan:    %s", base_source)
    logger.info("Stars result: %s", stars_source)
    logger.info("Hard result:  %s", hard_source)
    logger.info("Soft result:  %s", soft_source)
    logger.info("Instruction:  %s", additional_requirements)

    base_raw  = json.loads(base_source.read_text(encoding="utf-8"))
    stars_raw = json.loads(stars_source.read_text(encoding="utf-8"))
    hard_raw  = json.loads(hard_source.read_text(encoding="utf-8"))
    soft_raw  = json.loads(soft_source.read_text(encoding="utf-8"))

    current_plan = PlanJSON(**base_raw["plan"])
    career_goal  = base_raw["career_goal"]

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
        "Loaded inputs: %s | %d hard | %d electives | %d enrichment chains",
        student_state.name,
        len(hard_courses),
        len(scored_electives),
        len(scored_enrichment),
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
        "additional_requirements":   additional_requirements,
        "current_plan":              current_plan,
        "remarks":                   [],
    }

    logger.info("Invoking schedule_validator_loop subgraph (Mode 2)...")
    result = schedule_validator_loop.invoke(initial_state)

    revised_plan      = result["current_plan"]
    remarks           = result.get("remarks", [])
    validation_result = result.get("validation_result")
    iteration_count   = result.get("iteration_count", 0)
    omitted           = result.get("omitted_hard_courses", [])

    is_valid   = validation_result.is_valid if validation_result else True
    violations = validation_result.violations if validation_result else []

    future_after = [s for s in revised_plan.semesters if not s.is_completed]
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
    if omitted:
        logger.info("omitted_hard_courses: %s", omitted)

    logger.info("Remarks (%d):", len(remarks))
    for r in remarks:
        logger.info("  • %s", r)

    payload = {
        "source_base":               str(base_source),
        "source_stars":              str(stars_source),
        "source_hard":               str(hard_source),
        "source_soft":               str(soft_source),
        "student_name":              student_state.name,
        "career_goal":               career_goal,
        "additional_requirements":   additional_requirements,
        "iterations":                iteration_count,
        "is_valid":                  is_valid,
        "violations":                [v.model_dump() for v in violations],
        "base_plan":                 base_raw["plan"],
        "base_remarks":              base_raw.get("remarks", []),
        "revised_plan":              json.loads(revised_plan.model_dump_json()),
        "revised_remarks":           remarks,
        "omitted_hard_courses":      omitted,
    }

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = output or _RESULTS_DIR / f"scheduler_subgraph_mode2_{timestamp}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    logger.info("Results → %s", out_path)
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Eval: full schedule_validator_loop subgraph — Mode 2 (extra semester)"
    )
    parser.add_argument(
        "--base", type=Path, default=None,
        help="Path to a scheduler_subgraph result JSON to use as current_plan (defaults to latest)",
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
        base_path=args.base,
        stars_path=args.stars,
        hard_path=args.hard,
        soft_path=args.soft,
        additional_requirements=args.instruction,
        output=args.output,
    )
