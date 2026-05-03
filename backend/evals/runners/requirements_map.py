"""
Eval runner for requirements_map_node.

For MVP the node returns the hardcoded CSCI_BS_REQUIREMENTS constant,
so no PDF fixture is needed. This runner serializes the constant to JSON
for inspection and writes it to evals/results/.

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

logger = logging.getLogger(__name__)

_RESULTS_DIR = Path(__file__).parent.parent / "results"


def run(output: Path | None = None) -> Path:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    logger.info("Running requirements_map_node (hardcoded constant)...")
    node_output = requirements_map_node({"stars_pdf_text": "", "requirements_map": None, "student_state": None})
    requirements_map = node_output["requirements_map"]
    actual = json.loads(requirements_map.model_dump_json())
    logger.info(
        "RequirementsMap: %s %s, %d writing slots, %d GE slots, %d cs_core slots, %d elective options.",
        requirements_map.major,
        requirements_map.degree,
        len(requirements_map.writing),
        len(requirements_map.general_education),
        len(requirements_map.major_cs_core),
        len(requirements_map.electives_options),
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = output or _RESULTS_DIR / f"requirements_map_{timestamp}.json"

    out_path.write_text(json.dumps(actual, indent=2, ensure_ascii=False))
    logger.info("Results → %s", out_path)
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dump hardcoded RequirementsMap constant to JSON")
    parser.add_argument("--output", type=Path, default=None, help="Override results output path")
    args = parser.parse_args()
    run(output=args.output)
