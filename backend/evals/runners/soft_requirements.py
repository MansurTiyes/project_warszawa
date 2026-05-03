"""
Eval runner for soft_requirements_subgraph.

Loads StudentState + RequirementsMap from the latest stars_parser_graph result
and hard_courses + elective_courses from the latest hard_requirements result,
then invokes the compiled soft_requirements_subgraph and writes scored_electives
and scored_enrichment to evals/results/ as JSON.

Run from backend/:
    python -m evals.runners.soft_requirements
    python -m evals.runners.soft_requirements --career-goal "machine learning engineer"
    python -m evals.runners.soft_requirements --stars path/to/stars.json --hard path/to/hard.json
    python -m evals.runners.soft_requirements --output path/to/out.json
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from graphs.subgraphs.soft_requirements.subgraph import soft_requirements_subgraph
from models.pipeline_state import CourseNode, OtherRequirements
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
    career_goal: str = _DEFAULT_CAREER_GOAL,
    output: Path | None = None,
) -> Path:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # --- load inputs ---
    stars_source = stars_path or _latest("stars_parser_graph")
    hard_source  = hard_path  or _latest("hard_requirements")

    logger.info("Stars result:     %s", stars_source)
    logger.info("Hard reqs result: %s", hard_source)
    logger.info("Career goal:      %s", career_goal)

    stars_raw = json.loads(stars_source.read_text(encoding="utf-8"))
    hard_raw  = json.loads(hard_source.read_text(encoding="utf-8"))

    student_state    = StudentState(**stars_raw["student_state"])
    requirements_map = RequirementsMap(**stars_raw["requirements_map"])
    hard_courses     = [CourseNode(**c) for c in hard_raw["hard_courses"]]
    elective_courses = [CourseNode(**c) for c in hard_raw["elective_courses"]]
    other_must_reqs  = OtherRequirements(**hard_raw["other_must_reqs"])
    ge_placeholders  = hard_raw["ge_placeholders_remaining"]

    logger.info(
        "Loaded: %s | %d hard courses | %d electives | career_goal=%r",
        student_state.name,
        len(hard_courses),
        len(elective_courses),
        career_goal,
    )

    # --- invoke subgraph ---
    logger.info("Invoking soft_requirements_subgraph...")
    result = soft_requirements_subgraph.invoke({
        # pipeline inputs
        "stars_pdf_text":              "",
        "student_state":               student_state,
        "requirements_map":            requirements_map,
        "career_goal":                 career_goal,
        # hard requirements outputs (read by enrichment dedup)
        "hard_courses":                hard_courses,
        "elective_courses":            elective_courses,
        "other_must_reqs":             other_must_reqs,
        "ge_placeholders_remaining":   ge_placeholders,
        # soft requirements outputs — will be written by this subgraph
        "scored_electives":            [],
        "scored_enrichment":           [],
    })

    scored_electives  = result["scored_electives"]
    scored_enrichment = result["scored_enrichment"]

    logger.info("scored_electives:  %d courses", len(scored_electives))
    logger.info("scored_enrichment: %d chains", len(scored_enrichment))
    logger.info(
        "Top elective:    %s (%.3f)",
        scored_electives[0].course.course_code if scored_electives else "none",
        scored_electives[0].score if scored_electives else 0.0,
    )
    logger.info(
        "Top enrichment:  %s (%.3f) — chain length %d",
        scored_enrichment[0][-1].course.course_code if scored_enrichment else "none",
        scored_enrichment[0][-1].score if scored_enrichment else 0.0,
        len(scored_enrichment[0]) if scored_enrichment else 0,
    )

    # --- serialize ---
    def _serialize_chain(chain: list) -> list[dict]:
        return [json.loads(sc.model_dump_json()) for sc in chain]

    payload = {
        "source_stars":          str(stars_source),
        "source_hard":           str(hard_source),
        "student_name":          student_state.name,
        "career_goal":           career_goal,
        "scored_electives": [
            json.loads(sc.model_dump_json()) for sc in scored_electives
        ],
        "scored_enrichment": [
            _serialize_chain(chain) for chain in scored_enrichment
        ],
    }

    # --- write ---
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = output or _RESULTS_DIR / f"soft_requirements_{timestamp}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    logger.info("Results → %s", out_path)
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run soft_requirements_subgraph eval")
    parser.add_argument(
        "--stars", type=Path, default=None,
        help="Path to a stars_parser_graph result JSON (defaults to latest)",
    )
    parser.add_argument(
        "--hard", type=Path, default=None,
        help="Path to a hard_requirements result JSON (defaults to latest)",
    )
    parser.add_argument(
        "--career-goal", type=str, default=_DEFAULT_CAREER_GOAL,
        help=f'Career goal string (default: "{_DEFAULT_CAREER_GOAL}")',
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Override results output path",
    )
    args = parser.parse_args()
    run(
        stars_path=args.stars,
        hard_path=args.hard,
        career_goal=args.career_goal,
        output=args.output,
    )
