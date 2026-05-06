/**
 * Pure helpers for rendering PlanJSON. No side effects, no React.
 *
 * Wire-side `PlannedCourse.category` is plain `string` (Pydantic uses str,
 * not Literal). The 9 known values map to category-tinted CSS vars below;
 * unknowns fall back to --cat-elective so the UI never breaks on a backend
 * vocabulary change.
 */

import type { PlanJSON, PlannedCourse, StudentState } from "@/types";

const CATEGORY_COLOR: Record<string, string> = {
  core:                "var(--cat-core)",
  additional_required: "var(--cat-additional)",
  capstone:            "var(--cat-capstone)",
  math_foundation:     "var(--cat-math)",
  science_foundation:  "var(--cat-math)",     // no dedicated science token; reuse math
  writing:             "var(--cat-writing)",
  technical_elective:  "var(--cat-elective)",
  enrichment:          "var(--cat-enrichment)",
  ge_placeholder:      "var(--cat-ge)",
};

export function categoryColor(category: PlannedCourse["category"]): string {
  return CATEGORY_COLOR[category] ?? "var(--cat-elective)";
}

/**
 * Find the semester the student is currently in.
 *
 * Heuristic: a semester is "in progress" if it is not yet completed AND any
 * of its courses match a code in student_state.current_registration. Returns
 * the first such semester's `semester_index`, or null if none.
 */
export function inProgressSemester(
  plan: PlanJSON,
  student_state: StudentState,
): number | null {
  const registered = new Set(student_state.current_registration ?? []);
  if (registered.size === 0) return null;

  for (const sem of plan.semesters ?? []) {
    if (sem.is_completed) continue;
    if (sem.courses.some((c) => registered.has(c.course_code))) {
      return sem.semester_index;
    }
  }
  return null;
}
