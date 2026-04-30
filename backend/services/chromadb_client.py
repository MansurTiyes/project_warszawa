"""
ChromaDB persistent client and course ingestion pipeline.

Runtime — import helpers from nodes and services:
    from services.chromadb_client import get_courses_collection, get_policy_collection

Ingestion — run once from backend/ to (re)populate the courses collection:
    python -m services.chromadb_client [--delay 0.3]
"""

from __future__ import annotations

import json
import logging
from typing import Any

import chromadb

from scripts.ingest_courses import CATOID, ingest_usc_course_catalog

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500

_client: chromadb.PersistentClient | None = None


def get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        from config import settings
        _client = chromadb.PersistentClient(path=settings.chroma_db_path)
    return _client


def get_courses_collection() -> chromadb.Collection:
    from config import settings
    return get_client().get_or_create_collection(
        name=settings.chromadb_collection_courses,
        # No embedding_function → Chroma default (all-MiniLM-L6-v2)
    )


def get_policy_collection() -> chromadb.Collection:
    from config import settings
    return get_client().get_or_create_collection(
        name=settings.chromadb_collection_policy,
    )


def _to_str(value: Any) -> str:
    """Coerce any value to a ChromaDB-safe string; None → empty string."""
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def ingest_courses(delay: float = 0.3) -> int:
    """
    Scrape the full USC course catalogue and upsert every course into ChromaDB.
    Returns the number of courses upserted.
    """
    logger.info("Fetching USC course catalogue (this takes several minutes)...")
    catalog = ingest_usc_course_catalog(delay=delay)
    logger.info("Catalogue fetched: %d courses.", len(catalog))

    collection = get_courses_collection()
    source = f"usc_catalog_{CATOID}"

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []

    for code, course in catalog.items():
        title = course.get("title") or ""
        description = course.get("description") or ""

        # Document text — what Chroma embeds for semantic search
        doc_text = f"{code}: {title}"
        if description:
            doc_text = f"{doc_text}\n\n{description}"

        # Extract dept prefix — everything before the first hyphen.
        # NOTE: USC dept codes are 2–6 letters (e.g. AME, CSCI, GESM).
        # "First four characters" would silently mangle short codes, so we
        # split on the hyphen instead.
        dept = code.split("-")[0]

        ids.append(code)
        documents.append(doc_text)
        metadatas.append({
            "doc_type": "course",
            "class_code": code,
            "title": title,
            "department": dept,
            "prereqs": _to_str(course.get("prereqs")),
            "units": _to_str(course.get("units")),
            "max_units": _to_str(course.get("max_units")),
            "terms_offered": _to_str(course.get("terms_offered")),
            "instruction_mode": _to_str(course.get("instruction_mode")),
            "grading_option": _to_str(course.get("grading_option")),
            "source": source,
        })

    total = len(ids)
    for start in range(0, total, _BATCH_SIZE):
        end = min(start + _BATCH_SIZE, total)
        collection.upsert(
            ids=ids[start:end],
            documents=documents[start:end],
            metadatas=metadatas[start:end],
        )
        logger.info("Upserted %d / %d.", end, total)

    logger.info(
        "Ingestion complete: %d courses in collection '%s'.",
        total,
        collection.name,
    )
    return total


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Ingest USC course catalogue into ChromaDB")
    parser.add_argument("--delay", type=float, default=0.3, help="Seconds between HTTP requests")
    args = parser.parse_args()

    count = ingest_courses(delay=args.delay)
    print(f"Done. {count} courses upserted.")
