"""
Eval runner for requirements_map_node (STARS Parser Call 2).

Reads the STARS PDF fixture, runs the extraction node, and writes the raw
result JSON to evals/results/ for manual inspection.

Run from backend/:
    python -m evals.runners.requirements_map
    python -m evals.runners.requirements_map --output path/to/out.json
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from nodes.stars_parser_nodes import requirements_map_node
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

    # --- run node ---
    logger.info("Running requirements_map_node...")
    node_output = requirements_map_node({"stars_pdf_text": stars_text, "requirements_map": None})
    requirements_map = node_output["requirements_map"]
    actual = json.loads(requirements_map.model_dump_json())
    logger.info(
        "RequirementsMap extracted: %s, %d requirement group(s).",
        requirements_map.major,
        len(requirements_map.requirement_groups),
    )

    # --- write results ---
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = output or _RESULTS_DIR / f"requirements_map_{timestamp}.json"

    out_path.write_text(
        json.dumps(actual, indent=2, ensure_ascii=False)
    )
    logger.info("Results → %s", out_path)
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run requirements_map extraction eval")
    parser.add_argument("--output", type=Path, default=None, help="Override results output path")
    args = parser.parse_args()
    run(output=args.output)
