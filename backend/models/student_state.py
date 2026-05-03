"""
StudentState — output of STARS Parser Agent (Call 1).

The LLM extracts these fields as-is from the STARS text. Course codes on
CourseRecord arrive in whatever form the LLM reads; the node is responsible
for normalizing them to DEPT-NNN format (e.g. CSCI-103) after extraction.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ClassLevel(str, Enum):
    FRESHMAN = "Freshman"
    SOPHOMORE = "Sophomore"
    JUNIOR = "Junior"
    SENIOR = "Senior"


class CourseRecord(BaseModel):
    semester_label: str          # "Fall 2024", "Spring 2025" — decoded from STARS semester code
    course_code: str             # normalized to DEPT-NNN by node post-processing
    units: float
    name: str                    # may be truncated; store as-is, do not hallucinate full name
    course_flags: list[str] = Field(default_factory=list)  # raw STARS suffixes e.g. ["L"], [">IP"]


class GECategoryStatus(BaseModel):
    status: Literal["met", "unmet", "in_progress"]
    courses_taken: list[str] = Field(default_factory=list)  # codes satisfying this category
    courses_needed: int                                       # 0 if met, >0 otherwise


class StudentException(BaseModel):
    code: Literal["CW", "RA", "RE", "RW", "UW"]
    course_code: str   # normalized to DEPT-NNN by node post-processing
    note: str          # raw text from STARS, e.g. "COURSE WAIVED BY ADVISOR"


class SubRequirementStatus(BaseModel):
    status: Literal["met", "unmet", "in_progress"]
    courses_taken: list[str] = Field(default_factory=list)
    units_earned: float
    units_needed: float   # 0.0 if met
    courses_needed: int   # 0 if met


class StudentState(BaseModel):
    # --- Identity ---
    name: str
    class_level: ClassLevel
    entrance_term: str           # "Fall 2024"
    expected_graduation: str     # "Spring 2028"
    major: str
    degree: str
    minor: str | None = None

    # --- PDP ---
    pdp: bool = False
    pdp_degree: str | None = None
    pdp_reflected_in_stars: bool = False

    # --- Units ---
    units_earned: float
    units_in_process: float
    units_needed: float
    units_transfer: float

    # --- Residency ---
    residency_units_earned: float
    residency_units_in_process: float
    residency_satisfied: bool

    # --- Upper Division ---
    upper_division_units_earned: float
    upper_division_units_in_process: float
    upper_division_units_needed: float

    # --- Planning Metadata ---
    remaining_semesters_estimate: int  # advisory only — planner recomputes from prereq depth
    free_elective_units: float         # budget from "INTERNAL USE ONLY" section of STARS

    # --- Requirements Status ---
    # GE: keys "GE-A" through "GE-H". Category membership lives here, not on CourseRecord,
    # because one course can satisfy multiple GE categories (double-dip).
    ge_completion: dict[str, GECategoryStatus] = Field(default_factory=dict)
    # Major sub-requirements: keys match RequirementsMap sub-requirement labels
    # e.g. "core_courses", "additional_required", "capstone", "technical_electives"
    major_requirements_status: dict[str, SubRequirementStatus] = Field(default_factory=dict)
    writing_requirement_satisfied: bool
    ge_seminar_satisfied: bool

    # --- Course History ---
    courses_taken: list[CourseRecord] = Field(default_factory=list)
    current_registration: list[str] = Field(default_factory=list)  # normalized codes only
    # CW/RA/RE/RW/UW entries — must be present for Hard Requirements to correctly resolve
    # waived and alternative-satisfied courses; absence causes false missing-course violations.
    exception_codes: list[StudentException] = Field(default_factory=list)
