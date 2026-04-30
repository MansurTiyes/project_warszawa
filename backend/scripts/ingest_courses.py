"""
Scrapes the USC course catalogue and returns a normalized dict keyed by
course ID (e.g. "CSCI-103").

Usage as a script:
    python scripts/ingest_courses.py           # writes usc_courses.json next to this file
    python scripts/ingest_courses.py --dry-run # scrape listing pages only, no detail fetches
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE = "https://catalogue.usc.edu"
CATOID = 21
NAVOID = 8861

# Matches "CSCI 103  Introduction to Programming" or "ACAD 174  ..."
_LISTING_RE = re.compile(r"^([A-Z]{2,})\s+(\d+\w*)\s+(.*)", re.DOTALL)

# Matches "Units: 3", "Units: 2.0", "Units: 1-4"
_UNITS_RE = re.compile(
    r"^[Uu]nits?:\s*(\d+(?:\.\d+)?)\s*(?:[-–]\s*(\d+(?:\.\d+)?))?",
    re.IGNORECASE,
)

_LABELED_FIELDS: dict[str, str] = {
    "terms offered:": "terms_offered",
    "notes:": "notes",
    "instruction mode:": "instruction_mode",
    "grading option:": "grading_option",
}


def _normalize_code(dept: str, num: str) -> str:
    return f"{dept}-{num}"


def _detect_total_pages(session: requests.Session) -> int:
    url = (
        f"{BASE}/content.php?catoid={CATOID}&navoid={NAVOID}"
        f"&filter[item_type]=3&filter[only_active]=1&filter[3]=1&filter[cpage]=1"
    )
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    pages: list[int] = []
    for a in soup.select("a[href*='cpage=']"):
        m = re.search(r"cpage=(\d+)", a["href"])
        if m:
            pages.append(int(m.group(1)))
    total = max(pages) if pages else 130
    logger.info("Detected %d listing pages.", total)
    return total


def _get_listing_page(session: requests.Session, page: int) -> list[dict[str, str]]:
    url = (
        f"{BASE}/content.php?catoid={CATOID}&navoid={NAVOID}"
        f"&filter[item_type]=3&filter[only_active]=1"
        f"&filter[3]=1&filter[cpage]={page}"
    )
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    courses: list[dict[str, str]] = []
    for a in soup.select("a[href*='preview_course_nopop']"):
        href = a["href"]
        # coid is the last query param; strip anything after it if chained
        coid = href.split("coid=")[-1].split("&")[0].strip()
        raw = a.get_text(" ", strip=True)
        m = _LISTING_RE.match(raw)
        if not m:
            logger.debug("Skipping unrecognized listing entry: %r", raw)
            continue
        dept, num, name = m.group(1), m.group(2), m.group(3).strip()
        courses.append({
            "coid": coid,
            "code": _normalize_code(dept, num),
            "name": name,
        })
    return courses


def _get_raw_detail(session: requests.Session, coid: str) -> str | None:
    url = f"{BASE}/preview_course_nopop.php?catoid={CATOID}&coid={coid}"
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    # Content lives in td.block_content; h1#course_preview_title is the start marker.
    # We collect from the h1 onwards to skip the toolbar ("HELP / Print-Friendly / ...").
    h1 = soup.find("h1", {"id": "course_preview_title"})
    if not h1:
        return None

    parts: list[str] = [h1.get_text(strip=True)]
    for sibling in h1.next_siblings:
        if hasattr(sibling, "get_text"):
            t = sibling.get_text(separator="\n", strip=True)
            if t:
                parts.append(t)
        elif isinstance(sibling, str) and sibling.strip():
            parts.append(sibling.strip())

    return "\n".join(parts)


def _parse_detail(name: str, text: str) -> dict[str, Any]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    out: dict[str, Any] = {
        "title": name,
        "units": None,
        "max_units": None,
        "terms_offered": None,
        "description": None,
        "notes": None,
        "instruction_mode": None,
        "grading_option": None,
    }

    desc_parts: list[str] = []
    units_found = False

    for line in lines:
        lower = line.lower()

        # Skip the repeated "DEPT NUM  Course Title" header line in the detail block
        if _LISTING_RE.match(line):
            continue

        # Units line — only match before we've seen it (first occurrence wins)
        if not units_found:
            m = _UNITS_RE.match(line)
            if m:
                out["units"] = float(m.group(1))
                out["max_units"] = float(m.group(2)) if m.group(2) else float(m.group(1))
                units_found = True
                continue

        # Labeled fields
        matched = False
        for prefix, field in _LABELED_FIELDS.items():
            if lower.startswith(prefix):
                value = line.split(":", 1)[1].strip()
                out[field] = value or None
                matched = True
                break
        if matched:
            continue

        # Everything else after units is description, filtering out short noise
        if units_found and len(line) > 3:
            desc_parts.append(line)

    out["description"] = " ".join(desc_parts).strip() or None
    return out


def ingest_usc_course_catalog(
    delay: float = 0.3,
    max_pages: int | None = None,
    dry_run: bool = False,
) -> dict[str, dict[str, Any]]:
    """
    Scrape the USC course catalogue.

    Args:
        delay:     Seconds to sleep between HTTP requests.
        max_pages: Override auto-detected page count (useful for testing).
        dry_run:   If True, collect stubs only — skip detail fetches.

    Returns:
        Dict keyed by normalized course ID (e.g. "CSCI-103"), each value
        containing: title, units, max_units, terms_offered, description,
        notes, instruction_mode, grading_option.
    """
    session = requests.Session()
    session.headers["User-Agent"] = "project-warszawa-research-bot/1.0 (academic advising tool)"

    total_pages = max_pages or _detect_total_pages(session)

    # Phase 1 — collect all course stubs from listing pages
    stubs: list[dict[str, str]] = []
    for page in range(1, total_pages + 1):
        try:
            page_courses = _get_listing_page(session, page)
            stubs.extend(page_courses)
            logger.debug("Page %d/%d — %d stubs so far", page, total_pages, len(stubs))
        except Exception as exc:
            logger.warning("Failed to fetch listing page %d: %s", page, exc)
        time.sleep(delay)

    logger.info("Phase 1 complete: %d course stubs collected.", len(stubs))

    if dry_run:
        logger.info("Dry run — skipping detail fetches.")
        return {s["code"]: {"title": s["name"]} for s in stubs}

    # Phase 2 — fetch and parse detail for each course
    catalog: dict[str, dict[str, Any]] = {}
    total = len(stubs)
    for i, stub in enumerate(stubs):
        code = stub["code"]
        try:
            text = _get_raw_detail(session, stub["coid"])
            if text is None:
                logger.warning("[%d/%d] No detail block for %s (coid=%s)", i + 1, total, code, stub["coid"])
            else:
                catalog[code] = _parse_detail(stub["name"], text)
        except Exception as exc:
            logger.warning("[%d/%d] Failed detail fetch for %s: %s", i + 1, total, code, exc)

        if (i + 1) % 200 == 0:
            logger.info("Detail progress: %d/%d (%.0f%%)", i + 1, total, 100 * (i + 1) / total)
        time.sleep(delay)

    logger.info("Phase 2 complete: %d courses ingested.", len(catalog))
    return catalog


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape USC course catalogue")
    parser.add_argument("--dry-run", action="store_true", help="Listing pages only, no detail fetches")
    parser.add_argument("--max-pages", type=int, default=None, help="Limit listing pages (for testing)")
    parser.add_argument("--delay", type=float, default=0.3, help="Seconds between requests")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "usc_courses.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    catalog = ingest_usc_course_catalog(
        delay=args.delay,
        max_pages=args.max_pages,
        dry_run=args.dry_run,
    )

    args.output.write_text(json.dumps(catalog, indent=2, ensure_ascii=False))
    print(f"Wrote {len(catalog)} courses to {args.output}")
