"""
Eval runner for student_state_node (STARS Parser Call 1).

Reads the STARS PDF fixture, runs the extraction node, diffs the result
against the expected output in datasets/student_state.json, and writes
a timestamped results file.

Run from backend/:
    python -m evals.runners.student_state
    python -m evals.runners.student_state --output path/to/out.json
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from evals.datasets import load_dataset
from nodes.stars_parser_nodes import student_state_node
from services.pdf_parser import extract_stars_text

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).parent.parent.parent   # backend/
_RESULTS_DIR = Path(__file__).parent.parent / "results"


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def _sorted_repr(obj: object) -> str:
    """Stable canonical string for any JSON-serialisable value."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _diff(expected: object, actual: object, path: str = "") -> list[str]:
    """
    Recursive field-level diff. Returns human-readable mismatch strings.

    - dicts: recurse key by key
    - lists: compare as order-independent sorted sets
    - scalars: exact equality
    """
    mismatches: list[str] = []

    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            child = f"{path}.{key}" if path else key
            if key not in actual:
                mismatches.append(f"MISSING   {child}")
            elif key not in expected:
                mismatches.append(f"EXTRA     {child}: {actual[key]!r}")
            else:
                mismatches.extend(_diff(expected[key], actual[key], path=child))

    elif isinstance(expected, list) and isinstance(actual, list):
        # Sort both sides for order-independent comparison.
        exp_s = sorted(_sorted_repr(x) for x in expected)
        act_s = sorted(_sorted_repr(x) for x in actual)
        if exp_s != act_s:
            missing = [x for x in exp_s if x not in act_s]
            extra   = [x for x in act_s if x not in exp_s]
            if missing:
                mismatches.append(f"MISMATCH  {path} — missing items: {missing}")
            if extra:
                mismatches.append(f"MISMATCH  {path} — extra items:   {extra}")

    else:
        if expected != actual:
            mismatches.append(
                f"MISMATCH  {path}\n"
                f"          expected: {expected!r}\n"
                f"          actual:   {actual!r}"
            )

    return mismatches


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(output: Path | None = None) -> Path:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # --- load dataset ---
    items = load_dataset("student_state")
    item = items[0]
    logger.info("Loaded dataset item: %s", item["id"])

    # --- extract PDF text ---
    fixture_path = _BACKEND_DIR / item["fixture"]
    if not fixture_path.exists():
        raise FileNotFoundError(f"STARS fixture not found: {fixture_path}")

    logger.info("Reading STARS PDF: %s", fixture_path)
    stars_text = extract_stars_text(fixture_path.read_bytes())
    logger.info("Extracted %d chars from PDF.", len(stars_text))

    # --- run node ---
    logger.info("Running student_state_node...")
    node_output = student_state_node({"stars_pdf_text": stars_text, "student_state": None})
    student_state = node_output["student_state"]
    actual = json.loads(student_state.model_dump_json())
    logger.info("Node complete: %s, %d courses.", student_state.name, len(student_state.courses_taken))

    # --- diff ---
    expected = item["expected"]
    mismatches = _diff(expected, actual)

    if mismatches:
        logger.warning("%d mismatch(es):", len(mismatches))
        for m in mismatches:
            logger.warning("  %s", m)
    else:
        logger.info("All fields match expected output. ✓")

    # --- write results ---
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = output or _RESULTS_DIR / f"student_state_{timestamp}.json"

    out_path.write_text(
        json.dumps(
            {
                "run_id": f"student_state_{timestamp}",
                "item_id": item["id"],
                "fixture": item["fixture"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "passed": len(mismatches) == 0,
                "mismatch_count": len(mismatches),
                "mismatches": mismatches,
                "actual": actual,
                "expected": expected,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    logger.info("Results → %s", out_path)
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run student_state extraction eval")
    parser.add_argument("--output", type=Path, default=None, help="Override results output path")
    args = parser.parse_args()
    run(output=args.output)
