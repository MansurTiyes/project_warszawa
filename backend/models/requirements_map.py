"""
RequirementsMap — output of STARS Parser Agent (Call 2).

Pure structural document: what the major requires, student-agnostic.
Never mutated after initial extraction.

Course codes in required_courses / select_from arrive in whatever form
the LLM reads from STARS. The requirements_map_node normalizes all codes
to DEPT-NNN format (e.g. CSCI-103) after extraction so they resolve
directly against ChromaDB document IDs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SubRequirement(BaseModel):
    index: int                          # 1-based, matches STARS numbering within the parent group
    label: str | None = None            # section header; None when STARS omits it (e.g. Math sub-group 3)
    completion_type: Literal["fixed", "select_n", "select_units", "select_category"]
    required_courses: list[str] = Field(default_factory=list)   # "fixed" type only; empty otherwise
    select_from: list[str] = Field(default_factory=list)        # empty when select_from_is_category
    select_from_is_category: bool = False
    select_from_category: str | None = None                     # e.g. "GE-C"; None when select_from_is_category is False
    courses_required: int = 0           # 0 when unit-based
    units_required: float = 0.0         # 0.0 when course-count-based
    grade_minimum: str | None = None    # e.g. "C"; None when not stated
    notes: list[str] = Field(default_factory=list)


class RequirementGroup(BaseModel):
    label: str                          # section header as printed in STARS, stripped of status codes
    completion_measure: Literal["courses", "units", "subgroups"]
    required_count: float               # how many of completion_measure must be satisfied
    notes: list[str] = Field(default_factory=list)
    sub_requirements: list[SubRequirement] = Field(default_factory=list)


class RequirementsMap(BaseModel):
    major: str                          # e.g. "COMPUTER SCIENCE"
    degree: str                         # e.g. "BACHELOR OF SCIENCE"
    program_code: str                   # USC program code, e.g. "365"
    catalog_year: str                   # STARS semester code, e.g. "20243" (Fall 2024)
    minor: str | None = None            # minor name if requirements appear in STARS, else None
    requirement_groups: list[RequirementGroup] = Field(default_factory=list)
