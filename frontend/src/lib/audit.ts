/**
 * Pure derivations over StudentState for the breakdown screen.
 *
 * No API calls, no fallbacks beyond what the schema permits — these are
 * thin shapers so ScreenStudentState stays declarative.
 */

import type { StudentState } from "@/types";

const TOTAL_REQUIRED_UNITS = 128;

const REQUIREMENT_LABELS: Record<string, string> = {
  core_courses: "Core",
  additional_required: "Additional req",
  capstone: "Capstone",
  technical_electives: "Tech electives",
  engr102: "ENGR-102",
  math: "Math",
  science: "Science",
};

export type RequirementRow = {
  key: string;
  label: string;
  status: "met" | "unmet" | "in_progress";
  courses: string[];
  needed: number;
};

export function requirementsSummary(s: StudentState): RequirementRow[] {
  const status = s.major_requirements_status ?? {};
  return Object.entries(status).map(([key, sub]) => ({
    key,
    label: REQUIREMENT_LABELS[key] ?? key,
    status: sub.status,
    courses: sub.courses_taken ?? [],
    needed: sub.courses_needed,
  }));
}

export type UnitsBreakdown = {
  earned: number;
  in_process: number;
  needed: number;
  transfer: number;
  total: number;
  cumulative: number;
  pctDone: number;
  pctInProgress: number;
  remainingSemesters: number;
};

export function unitsBreakdown(s: StudentState): UnitsBreakdown {
  const cumulative = s.units_earned + s.units_in_process;
  return {
    earned: s.units_earned,
    in_process: s.units_in_process,
    needed: s.units_needed,
    transfer: s.units_transfer,
    total: TOTAL_REQUIRED_UNITS,
    cumulative,
    pctDone: (s.units_earned / TOTAL_REQUIRED_UNITS) * 100,
    pctInProgress: (s.units_in_process / TOTAL_REQUIRED_UNITS) * 100,
    remainingSemesters: s.remaining_semesters_estimate,
  };
}
