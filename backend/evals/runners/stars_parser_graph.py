"""
Eval runner for stars_parser_graph — full graph integration test.

Extracts text from the STARS PDF fixture, invokes the compiled graph, and
writes the resulting state (student_state + requirements_map) to evals/results/
as JSON — simulating what the frontend would store in localStorage after
POST /api/stars.

Run from backend/:
    python -m evals.runners.stars_parser_graph
    python -m evals.runners.stars_parser_graph --output path/to/out.json
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from graphs.stars_parser import stars_parser_graph
from services.pdf_parser import extract_stars_text

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).parent.parent.parent   # backend/
_FIXTURE     = _BACKEND_DIR / "evals" / "fixtures" / "stars.pdf"
_RESULTS_DIR = Path(__file__).parent.parent / "results"


def run(output: Path | None = None) -> Path:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # --- extract PDF text ---
    if not _FIXTURE.exists():
        raise FileNotFoundError(f"STARS fixture not found: {_FIXTURE}")

    logger.info("Reading STARS PDF: %s", _FIXTURE)
    stars_text = extract_stars_text(_FIXTURE.read_bytes())
    logger.info("Extracted %d chars from PDF.", len(stars_text))

    # --- invoke graph ---
    logger.info("Invoking stars_parser_graph...")
    result = stars_parser_graph.invoke({
        "stars_pdf_text": stars_text,
        "student_state": None,
        "requirements_map": None,
    })

    student_state    = result["student_state"]
    requirements_map = result["requirements_map"]
    logger.info(
        "Graph complete — StudentState: %s (%d courses), RequirementsMap: %s (%d cs_core slots, %d electives).",
        student_state.name,
        len(student_state.courses_taken),
        requirements_map.major,
        len(requirements_map.major_cs_core),
        len(requirements_map.electives_options),
    )

    # --- serialize — mirrors what the frontend stores after POST /api/stars ---
    # stars_pdf_text is excluded: it's never stored client-side (FERPA) and adds noise.
    payload = {
        "student_state":    json.loads(student_state.model_dump_json()),
        "requirements_map": json.loads(requirements_map.model_dump_json()),
    }

    # --- write results ---
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = output or _RESULTS_DIR / f"stars_parser_graph_{timestamp}.json"

    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    logger.info("Results → %s", out_path)
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run stars_parser_graph integration eval")
    parser.add_argument("--output", type=Path, default=None, help="Override results output path")
    args = parser.parse_args()
    run(output=args.output)
