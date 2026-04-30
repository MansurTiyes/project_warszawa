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
    "prerequisite:": "prereqs",
    "prerequisites:": "prereqs",
    "recommended preparation:": "recommended_preparation",
    "notes:": "notes",
    "instruction mode:": "instruction_mode",
    "grading option:": "grading_option",
}

# Lines that start with these prefixes are dropped — they're metadata noise, not description.
_DISCARD_PREFIXES: frozenset[str] = frozenset([
    "registration restriction:",
    "duplicates credit",
    "back to top",
])


def _normalize_code(dept: str, num: str) -> str:
    # Strip suffix letters after the digits, keeping only 'a'/'b' which
    # genuinely distinguish separate courses (e.g. 490a / 490b sequences).
    # Flags like 'L' (lab), 'x' (variable topics), 'g' (GE designation)
    # have no semantic meaning for planning and cause false mismatches in RAG.
    m = re.match(r"(\d+)([a-zA-Z]*)", num)
    if m:
        digits, suffix = m.group(1), m.group(2).lower()
        clean_suffix = "".join(c for c in suffix if c in ("a", "b"))
        num = digits + clean_suffix
    return f"{dept}-{num}"


# Matches any inline course code like "CSCI 103L", "ACCT 551T" within prose text
_INLINE_CODE_RE = re.compile(r"\b([A-Z]{2,})\s+(\d+\w*)\b")


def _extract_course_codes(text: str) -> list[str]:
    """Flat list of all course codes in text. Used for advisory fields
    (recommended_preparation) where AND/OR structure is not needed."""
    return [_normalize_code(m.group(1), m.group(2)) for m in _INLINE_CODE_RE.finditer(text)]


def _extract_prereq_groups(text: str) -> list[list[str]]:
    """Parse a prereq string into AND-of-OR groups.

    Outer list = AND  (student must satisfy every group).
    Inner list = OR   (student needs at least one course from the group).

    Parsing rule: split on 'and' first to get AND groups, then split each
    group on 'or' to get OR alternatives, then extract course codes from
    each alternative. Groups with no recognizable codes are dropped.

    Examples:
      "CSCI 102"                           → [["CSCI-102"]]
      "CSCI 102 or CSCI 103L"             → [["CSCI-102", "CSCI-103L"]]
      "CSCI 103L and CSCI 170"            → [["CSCI-103L"], ["CSCI-170"]]
      "CSCI 102 or CSCI 103L and CSCI 170"→ [["CSCI-102", "CSCI-103L"], ["CSCI-170"]]
    """
    and_groups = re.split(r"\band\b", text, flags=re.IGNORECASE)
    result: list[list[str]] = []
    for and_group in and_groups:
        or_parts = re.split(r"\bor\b", and_group, flags=re.IGNORECASE)
        codes: list[str] = []
        for part in or_parts:
            for m in _INLINE_CODE_RE.finditer(part):
                codes.append(_normalize_code(m.group(1), m.group(2)))
        if codes:
            result.append(codes)
    return result


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


# Pure punctuation to skip — does NOT include "and"/"or" so connectors are
# preserved in the collected text and _extract_prereq_groups can split on them.
_PUNCT_TOKENS = frozenset({".", ",", ";", ":", ""})


def _get_raw_detail(session: requests.Session, coid: str) -> str | None:
    url = f"{BASE}/preview_course_nopop.php?catoid={CATOID}&coid={coid}"
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    # Content lives under h1#course_preview_title.
    # "Prerequisite:" appears as <em> with the course codes in following <a> tags —
    # we must walk siblings manually to stitch that into one line.
    h1 = soup.find("h1", {"id": "course_preview_title"})
    if not h1:
        return None

    lines: list[str] = [h1.get_text(strip=True)]
    siblings = list(h1.next_siblings)
    i = 0

    while i < len(siblings):
        sib = siblings[i]

        if isinstance(sib, str):
            text = sib.strip()
            if text and text not in _PUNCT_TOKENS:
                lines.append(text)
            i += 1

        elif sib.name == "div":
            break  # "Back to Top / Print-Friendly" footer — nothing useful after this

        elif sib.name == "em":
            # Labeled field: <em>Prerequisite:</em> followed by <a> course links,
            # hidden <span> spacers, NavigableString connectors ("and"/"or"), and
            # finally a <br/> or the next labeled field.
            label = sib.get_text(strip=True)
            values: list[str] = []
            j = i + 1
            while j < len(siblings):
                nxt = siblings[j]
                if isinstance(nxt, str):
                    t = nxt.strip()
                    # Stop if this NavigableString starts a new labeled field
                    t_lower = t.lower()
                    if t and not (
                        t_lower in _PUNCT_TOKENS
                        or any(t_lower.startswith(p) for p in _LABELED_FIELDS)
                        or t_lower.startswith("max units:")
                        or any(t_lower.startswith(p) for p in _DISCARD_PREFIXES)
                    ):
                        # Prose value (e.g. "CSCI 103L and CSCI 170" as plain text)
                        values.append(t)
                    elif t and any(t_lower.startswith(p) for p in _LABELED_FIELDS):
                        break
                    j += 1
                elif hasattr(nxt, "name"):
                    if nxt.name == "br":
                        j += 1
                        break  # <br/> is the definitive end of this field
                    elif nxt.name in ("hr", "div", "em"):
                        break
                    elif nxt.name == "span":
                        j += 1  # hidden decorative spacer — skip
                    elif nxt.name == "a":
                        t = nxt.get_text(strip=True)
                        if t:
                            values.append(t)
                        j += 1
                    else:
                        t = nxt.get_text(strip=True)
                        if t:
                            values.append(t)
                        j += 1
                else:
                    j += 1
            lines.append(f"{label} {' '.join(values)}".strip())
            i = j

        else:
            text = sib.get_text(strip=True)
            if text:
                lines.append(text)
            i += 1

    return "\n".join(lines)


def _parse_detail(name: str, text: str) -> dict[str, Any]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    out: dict[str, Any] = {
        "title": name,
        "units": None,
        "max_units": None,
        "terms_offered": None,
        "prereqs": None,           # None = field not seen; converted to list at end
        "recommended_preparation": None,
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

        # Silently discard metadata noise lines
        if any(lower.startswith(p) for p in _DISCARD_PREFIXES):
            continue

        # Max Units: — updates max_units (separate numeric field, not a string field)
        if lower.startswith("max units:"):
            val = line.split(":", 1)[1].strip()
            try:
                out["max_units"] = float(val)
            except ValueError:
                pass
            continue

        # Labeled fields
        matched = False
        for prefix, field in _LABELED_FIELDS.items():
            if lower.startswith(prefix):
                value = line.split(":", 1)[1].strip()
                out[field] = value          # empty string is fine; converted below
                matched = True
                break
        if matched:
            continue

        # Everything else after units is description, filtering out short noise
        if units_found and len(line) > 3:
            desc_parts.append(line)

    out["description"] = " ".join(desc_parts).strip() or None
    # prereqs → nested list[list[str]] preserving AND/OR structure
    out["prereqs"] = _extract_prereq_groups(out["prereqs"]) if out["prereqs"] else []
    # recommended_preparation → flat list[str]; it's advisory/display-only, structure not needed
    out["recommended_preparation"] = _extract_course_codes(out["recommended_preparation"]) if out["recommended_preparation"] else []
    return out


def ingest_usc_course_catalog(
    delay: float = 0.3,
    max_pages: int | None = None,
    page: int | None = None,
    dry_run: bool = False,
) -> dict[str, dict[str, Any]]:
    """
    Scrape the USC course catalogue.

    Args:
        delay:     Seconds to sleep between HTTP requests.
        max_pages: Override auto-detected page count (useful for testing).
        page:      Scrape exactly this one listing page (overrides max_pages).
        dry_run:   If True, collect stubs only — skip detail fetches.

    Returns:
        Dict keyed by normalized course ID (e.g. "CSCI-103"), each value
        containing: title, units, max_units, terms_offered, description,
        notes, instruction_mode, grading_option.
    """
    session = requests.Session()
    session.headers["User-Agent"] = "project-warszawa-research-bot/1.0 (academic advising tool)"

    if page is not None:
        page_range = range(page, page + 1)
    else:
        total_pages = max_pages or _detect_total_pages(session)
        page_range = range(1, total_pages + 1)

    # Phase 1 — collect all course stubs from listing pages
    stubs: list[dict[str, str]] = []
    for p in page_range:
        try:
            page_courses = _get_listing_page(session, p)
            stubs.extend(page_courses)
            logger.debug("Page %d — %d stubs so far", p, len(stubs))
        except Exception as exc:
            logger.warning("Failed to fetch listing page %d: %s", p, exc)
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
    parser.add_argument("--page", type=int, default=None, help="Scrape exactly this one listing page")
    parser.add_argument("--max-pages", type=int, default=None, help="Scrape pages 1..N (ignored if --page is set)")
    parser.add_argument("--delay", type=float, default=0.3, help="Seconds between requests")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "usc_courses.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    catalog = ingest_usc_course_catalog(
        delay=args.delay,
        max_pages=args.max_pages,
        page=args.page,
        dry_run=args.dry_run,
    )

    args.output.write_text(json.dumps(catalog, indent=2, ensure_ascii=False))
    print(f"Wrote {len(catalog)} courses to {args.output}")
