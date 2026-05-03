"""
RequirementsMap — structural degree requirements for the pipeline.

For MVP the requirements map is hardcoded per program. In V2 this will be
retrieved via RAG from parsed USC catalog sources.

Schema design:
  CourseSlot  = str | list[str]
    - str        → one fixed required course
    - list[str]  → pick exactly one from these alternatives (OR)

  CourseList  = list[CourseSlot]
    Ordered sequence of slots; every slot must be satisfied.

  ScienceSequenceGroup = list[list[str]]
    Outer list: pick exactly ONE inner sequence (OR between sequences).
    Inner list: take ALL courses in the chosen sequence (AND within sequence).
"""

from __future__ import annotations

from typing import Union

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

CourseSlot = Union[str, list[str]]
CourseList = list[CourseSlot]
ScienceSequenceGroup = list[list[str]]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TechnicalElectives(BaseModel):
    min_units: int          # total units required
    min_courses: int        # minimum number of courses
    level_minimum: int      # e.g. 300 means 300- or 400-level only
    dept_primary: str       # primary department (e.g. "CSCI")
    note: str               # adviser approval path for non-primary substitutions


class RequirementsMap(BaseModel):
    major: str
    degree: str
    program_code: str
    catalog_year: str       # STARS format e.g. "20243"

    writing: CourseList
    general_education: CourseList

    pre_major_engineering: CourseList
    pre_major_math: CourseList          # mathematics slots
    pre_major_stats: CourseList         # statistics / probability slot
    pre_major_science: ScienceSequenceGroup  # pick exactly ONE full sequence

    major_cs_core: CourseList           # fixed CS courses + capstone slot
    major_ee: CourseList
    major_technical_electives: TechnicalElectives
    electives_options: list[str] = Field(default_factory=list)  # approved elective pool


# ---------------------------------------------------------------------------
# Hardcoded constant — CS BS catalog year 20243 (Fall 2024)
# ---------------------------------------------------------------------------

CSCI_BS_REQUIREMENTS = RequirementsMap(
    major="COMPUTER SCIENCE",
    degree="BACHELOR OF SCIENCE",
    program_code="365",
    catalog_year="20243",

    writing=[
        "WRIT-150",
        "WRIT-340",
    ],

    # Each string is one required GE slot (category placeholder).
    # GE-B and GE-C each require 2 courses, hence the duplicates.
    # GE-G and GE-H may double-dip with a Core Literacy course (see CLAUDE.md).
    general_education=[
        "GE-A",
        "GE-B",
        "GE-B",
        "GE-C",
        "GE-C",
        "GE-D",
        "GE-E",
        "GE-F",
        "GE-G",
        "GE-H",
    ],

    pre_major_engineering=[
        "ENGR-102",
    ],

    pre_major_math=[
        "MATH-125",
        ["MATH-126", "MATH-129"],   # Calc II — pick one
        ["MATH-225", "MATH-235"],   # Linear Algebra — pick one
        ["MATH-226", "MATH-229"],   # Calc III — pick one
    ],

    pre_major_stats=[
        ["EE-364", "MATH-407"],     # Stats / Probability — pick one
    ],

    # Pick exactly ONE full sequence; take ALL courses in it.
    pre_major_science=[
        ["BISC-120", "BISC-220"],   # Biology standard
        ["BISC-121", "BISC-221"],   # Biology advanced
        ["CHEM-105A", "CHEM-105B"], # Chemistry standard  (NOTE: uppercase suffix — verify against ChromaDB IDs)
        ["CHEM-115A", "CHEM-115B"], # Chemistry advanced  (NOTE: uppercase suffix — verify against ChromaDB IDs)
        ["PHYS-151", "PHYS-152"],   # Physics standard
        ["PHYS-161", "PHYS-162"],   # Physics advanced
    ],

    major_cs_core=[
        "CSCI-102",
        "CSCI-103",
        "CSCI-104",
        "CSCI-170",
        "CSCI-201",
        "CSCI-270",
        "CSCI-310",
        "CSCI-350",
        "CSCI-353",
        "CSCI-356",
        "CSCI-360",
        ["CSCI-401", "CSCI-404"],   # Capstone — pick one
    ],

    major_ee=[
        "EE-109",
    ],

    major_technical_electives=TechnicalElectives(
        min_units=12,
        min_courses=3,
        level_minimum=300,
        dept_primary="CSCI",
        note=(
            "At least three 4-unit 300- or 400-level CSCI courses. "
            "One non-CSCI substitution may be permitted with adviser approval."
        ),
    ),

    electives_options=[
        "CSCI-420",
        "CSCI-423",
        "CSCI-426",
        "CSCI-430",
        "CSCI-445",
        "CSCI-461",
        "CSCI-467",
        "CSCI-475",
        "CSCI-476",
        "CSCI-485",
        "CSCI-490",
        "CSCI-491A",
        "CSCI-491B",
        "CSCI-499",
        "EE-354",
        "EE-451",
        "EE-454",
        "EE-457",
        "EE-459",
        "EE-477",
        "EE-490",
        "EE-499",
        "ENGR-395B",
        "ENGR-395C",
        "MATH-458",
        "TAC-368",
        "TAC-380",
        "TAC-435",
        "TAC-439",
        "TAC-485",
    ],
)
