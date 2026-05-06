from __future__ import annotations

from pydantic import BaseModel

from models.requirements_map import RequirementsMap
from models.student_state import (
    ClassLevel,
    StudentState,
    SubRequirementStatus,
)


class StudentStateSummary(BaseModel):
    # Identity
    name: str
    class_level: ClassLevel
    entrance_term: str
    expected_graduation: str
    major: str
    degree: str
    minor: str | None

    # Units
    units_earned: float
    units_in_process: float
    units_needed: float
    units_transfer: float

    # Residency
    residency_units_earned: float
    residency_units_in_process: float
    residency_satisfied: bool

    # Upper division
    upper_division_units_earned: float
    upper_division_units_in_process: float
    upper_division_units_needed: float

    # Requirements status
    major_requirements_status: dict[str, SubRequirementStatus]
    writing_requirement_satisfied: bool
    ge_seminar_satisfied: bool

    @classmethod
    def from_student_state(cls, s: StudentState) -> StudentStateSummary:
        return cls(
            name=s.name,
            class_level=s.class_level,
            entrance_term=s.entrance_term,
            expected_graduation=s.expected_graduation,
            major=s.major,
            degree=s.degree,
            minor=s.minor,
            units_earned=s.units_earned,
            units_in_process=s.units_in_process,
            units_needed=s.units_needed,
            units_transfer=s.units_transfer,
            residency_units_earned=s.residency_units_earned,
            residency_units_in_process=s.residency_units_in_process,
            residency_satisfied=s.residency_satisfied,
            upper_division_units_earned=s.upper_division_units_earned,
            upper_division_units_in_process=s.upper_division_units_in_process,
            upper_division_units_needed=s.upper_division_units_needed,
            major_requirements_status=s.major_requirements_status,
            writing_requirement_satisfied=s.writing_requirement_satisfied,
            ge_seminar_satisfied=s.ge_seminar_satisfied,
        )


class StarsResponse(BaseModel):
    student_state: StudentState
    student_state_summary: StudentStateSummary
    requirements_map: RequirementsMap
