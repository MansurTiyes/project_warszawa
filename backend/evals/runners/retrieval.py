"""
Retrieval eval runner — queries ChromaDB with every item in the retrieval
dataset and writes raw results to evals/results/retrieval_{timestamp}.json.

Run from backend/:
    python -m evals.runners.retrieval
    python -m evals.runners.retrieval --top-k 10
    python -m evals.runners.retrieval --output path/to/out.json
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from evals.datasets import load_dataset
from services.chromadb_client import get_courses_collection

logger = logging.getLogger(__name__)

_RESULTS_DIR = Path(__file__).parent.parent / "results"


def _unpack(raw: dict) -> list[dict]:
    """Flatten a single-query ChromaDB response into a ranked list."""
    ids = raw["ids"][0]
    docs = raw["documents"][0]
    dists = raw["distances"][0]
    metas = raw["metadatas"][0]
    return [
        {
            "rank": i + 1,
            "id": ids[i],
            "distance": round(dists[i], 6),
            "document": docs[i],
            "metadata": metas[i],
        }
        for i in range(len(ids))
    ]


def _check(retrieved: list[dict], expected_output: dict | None) -> dict:
    """
    Lightweight hits/misses summary for items that have expected_output.
    Not an evaluator — purely for faster manual review.
    """
    if not expected_output:
        return {}

    out: dict = {}
    retrieved_ids = {r["id"] for r in retrieved}

    if "top_k_contains" in expected_output:
        expected = expected_output["top_k_contains"]
        hits = [c for c in expected if c in retrieved_ids]
        misses = [c for c in expected if c not in retrieved_ids]
        out["hits"] = hits
        out["misses"] = misses
        out["passed"] = not misses

    if "top_k_department" in expected_output:
        dept = expected_output["top_k_department"]
        dept_hits = [r["id"] for r in retrieved if r["metadata"].get("department") == dept]
        out["department_hits"] = dept_hits
        out["passed"] = bool(dept_hits)

    return out


def run(top_k: int = 5, output: Path | None = None) -> Path:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    cases = load_dataset("retrieval")
    collection = get_courses_collection()
    logger.info("Loaded %d cases. Collection: '%s'. top_k=%d.", len(cases), collection.name, top_k)

    results = []

    for case in cases:
        inp = case["input"]
        query: str = inp["query"]
        filters: dict | None = inp.get("filters") or None

        try:
            raw = collection.query(
                query_texts=[query],
                n_results=top_k,
                where=filters,
                include=["documents", "distances", "metadatas"],
            )
            retrieved = _unpack(raw)
        except Exception as exc:
            logger.warning("[%s] Query failed: %s", case["id"], exc)
            retrieved = []

        check = _check(retrieved, case.get("expected_output"))

        # Console status line
        if "passed" in check:
            label = "PASS" if check["passed"] else f"FAIL  misses={check.get('misses', [])}"
        else:
            label = "—"
        logger.info("[%s] set=%s  %-50s  %s", case["id"], case["set"], query[:50], label)

        results.append({
            "id": case["id"],
            "set": case["set"],
            "set_label": case.get("set_label"),
            "query": query,
            "filters": filters,
            "note": case.get("note"),
            "expected_output": case.get("expected_output"),
            "retrieved": retrieved,
            **check,
        })

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = output or _RESULTS_DIR / f"retrieval_{timestamp}.json"

    out_path.write_text(
        json.dumps(
            {
                "run_id": f"retrieval_{timestamp}",
                "dataset": "retrieval",
                "top_k": top_k,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "total_items": len(results),
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
        )
    )

    passed = sum(1 for r in results if r.get("passed") is True)
    checkable = sum(1 for r in results if "passed" in r)
    logger.info(
        "Done. %d / %d checkable items passed. Results → %s",
        passed, checkable, out_path,
    )
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run retrieval evals against ChromaDB")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    run(top_k=args.top_k, output=args.output)
