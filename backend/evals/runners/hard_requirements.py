"""
Eval runner for hard_requirements_subgraph.

Loads StudentState and RequirementsMap from the latest stars_parser_graph result,
invokes the compiled hard_requirements_subgraph, and writes the four output fields
(hard_courses, elective_courses, other_must_reqs, ge_placeholders_remaining) to
evals/results/ as JSON.

Run from backend/:
    python -m evals.runners.hard_requirements
    python -m evals.runners.hard_requirements --input path/to/stars_result.json
    python -m evals.runners.hard_requirements --output path/to/out.json
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from graphs.subgraphs.hard_requirements import hard_requirements_subgraph
from models.requirements_map import RequirementsMap
from models.student_state import StudentState

logger = logging.getLogger(__name__)

_RESULTS_DIR = Path(__file__).parent.parent / "results"


def _latest_stars_result() -> Path:
    """Return the most recently written stars_parser_graph result file."""
    candidates = sorted(_RESULTS_DIR.glob("stars_parser_graph_*.json"))
    if not candidates:
        raise FileNotFoundError(
            f"No stars_parser_graph result found in {_RESULTS_DIR}. "
            "Run evals.runners.stars_parser_graph first."
        )
    return candidates[-1]


def run(input_path: Path | None = None, output: Path | None = None) -> Path:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # --- load input ---
    source = input_path or _latest_stars_result()
    logger.info("Loading STARS parser result: %s", source)
    raw = json.loads(source.read_text(encoding="utf-8"))

    student_state    = StudentState(**raw["student_state"])
    requirements_map = RequirementsMap(**raw["requirements_map"])
    logger.info(
        "Loaded: %s (%d courses taken), %d elective options.",
        student_state.name,
        len(student_state.courses_taken),
        len(requirements_map.electives_options),
    )

    # --- invoke subgraph ---
    logger.info("Invoking hard_requirements_subgraph...")
    result = hard_requirements_subgraph.invoke({
        "student_state":            student_state,
        "requirements_map":         requirements_map,
        "stars_pdf_text":           "",
        "hard_courses":             [],
        "elective_courses":         [],
        "other_must_reqs":          None,
        "ge_placeholders_remaining": [],
    })

    hard_courses             = result["hard_courses"]
    elective_courses         = result["elective_courses"]
    other_must_reqs          = result["other_must_reqs"]
    ge_placeholders_remaining = result["ge_placeholders_remaining"]

    logger.info(
        "Subgraph complete — %d hard courses, %d electives, "
        "%d GE placeholders remaining.",
        len(hard_courses),
        len(elective_courses),
        len(ge_placeholders_remaining),
    )
    logger.info("Hard courses: %s", [c.course_code for c in hard_courses])
    logger.info("GE placeholders: %s", ge_placeholders_remaining)
    logger.info("Other reqs: %s", other_must_reqs)

    # --- serialize ---
    payload = {
        "source_file": str(source),
        "student_name": student_state.name,
        "hard_courses": [
            json.loads(c.model_dump_json()) for c in hard_courses
        ],
        "elective_courses": [
            json.loads(c.model_dump_json()) for c in elective_courses
        ],
        "other_must_reqs": json.loads(other_must_reqs.model_dump_json()),
        "ge_placeholders_remaining": ge_placeholders_remaining,
    }

    # --- write results ---
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = output or _RESULTS_DIR / f"hard_requirements_{timestamp}.json"

    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    logger.info("Results → %s", out_path)
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run hard_requirements_subgraph eval")
    parser.add_argument(
        "--input", type=Path, default=None,
        help="Path to a stars_parser_graph result JSON (defaults to latest in evals/results/)",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Override results output path",
    )
    args = parser.parse_args()
    run(input_path=args.input, output=args.output)
