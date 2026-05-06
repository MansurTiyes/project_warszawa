/**
 * AdvisorState persistence — single source of truth for the localStorage
 * envelope described in CLAUDE.md (§ "localStorage schema").
 *
 * Three write moments:
 *   Write 1: saveAfterStars()    — after /api/stars success
 *   Write 2: saveAfterPipeline() — after /api/pipeline complete event
 *   Write 3: saveAfterModify()   — after /api/schedule/modify response
 *
 * Reads (loadAdvisorState) tolerate corruption / schema drift by returning
 * EMPTY_ADVISOR_STATE rather than throwing — page should never break on
 * stale localStorage.
 */

import {
  ADVISOR_STATE_KEY,
  ADVISOR_STATE_SCHEMA_VERSION,
  EMPTY_ADVISOR_STATE,
  type AdvisorState,
  type PartialState,
  type PipelineCompletePayload,
  type PlanJSON,
  type PlanVersion,
  type RequirementsMap,
  type StudentState,
  type StudentStateSummary,
} from "@/types";

// ============================================================
// Read
// ============================================================

export function loadAdvisorState(): AdvisorState {
  if (typeof window === "undefined") return EMPTY_ADVISOR_STATE;

  let raw: string | null;
  try {
    raw = window.localStorage.getItem(ADVISOR_STATE_KEY);
  } catch {
    return EMPTY_ADVISOR_STATE;
  }
  if (!raw) return EMPTY_ADVISOR_STATE;

  try {
    const parsed = JSON.parse(raw) as Partial<AdvisorState>;
    if (parsed.schema_version !== ADVISOR_STATE_SCHEMA_VERSION) {
      // Future: run migrations here. For MVP, drop on version mismatch.
      console.warn(
        `[advisor_state] schema_version mismatch (got ${parsed.schema_version}, expected ${ADVISOR_STATE_SCHEMA_VERSION}). Resetting.`,
      );
      return EMPTY_ADVISOR_STATE;
    }
    // Trust shape — backend is source of truth and we control all writers.
    return parsed as AdvisorState;
  } catch (err) {
    console.warn("[advisor_state] parse failed, resetting", err);
    return EMPTY_ADVISOR_STATE;
  }
}

// ============================================================
// Write helpers — all go through this so we get one place to
// catch QuotaExceededError.
// ============================================================

function persist(state: AdvisorState): AdvisorState {
  try {
    window.localStorage.setItem(ADVISOR_STATE_KEY, JSON.stringify(state));
  } catch (err) {
    // QuotaExceededError or serialization failure. Surface to caller via
    // throwing — App can show a toast. We don't auto-clear.
    console.error("[advisor_state] write failed", err);
    throw err;
  }
  return state;
}

// ============================================================
// Write 1 — after /api/stars
// ============================================================

export function saveAfterStars(args: {
  student_state: StudentState;
  requirements_map: RequirementsMap;
  student_state_summary: StudentStateSummary;
}): AdvisorState {
  const partial: PartialState = {
    student_state: args.student_state,
    requirements_map: args.requirements_map,
    student_state_summary: args.student_state_summary,
    career_goal: null,
  };
  return persist({
    ...EMPTY_ADVISOR_STATE,
    partial_state: partial,
  });
}

/**
 * Persist career_goal selection on Screen 4 before kicking off /api/pipeline.
 * Called on "Generate schedule" click. Survives a refresh on Screen 5.
 */
export function setCareerGoal(career_goal: string): AdvisorState {
  const cur = loadAdvisorState();
  if (!cur.partial_state) {
    throw new Error("setCareerGoal called without partial_state");
  }
  return persist({
    ...cur,
    partial_state: { ...cur.partial_state, career_goal },
  });
}

// ============================================================
// Write 2 — after /api/pipeline complete event
// ============================================================

export function saveAfterPipeline(
  payload: PipelineCompletePayload,
  partial: PartialState,
): AdvisorState {
  if (!partial.career_goal) {
    throw new Error("saveAfterPipeline: career_goal missing from partial_state");
  }

  const version: PlanVersion = {
    id: 1,
    label: payload.diff_label,
    plan: payload.current_plan,
    schedule_remarks: payload.schedule_remarks,
  };

  return persist({
    schema_version: ADVISOR_STATE_SCHEMA_VERSION,
    pipeline_state: {
      student_state: partial.student_state,
      requirements_map: partial.requirements_map,
      student_state_summary: partial.student_state_summary,
      career_goal: partial.career_goal,
      hard_courses: payload.hard_courses,
      other_must_reqs: payload.other_must_reqs,
      ge_placeholders_remaining: payload.ge_placeholders_remaining,
      scored_electives: payload.scored_electives,
      scored_enrichment: payload.scored_enrichment,
    },
    partial_state: null,
    versions: [version],
    currentIndex: 0,
    modify_intent: null,
  });
}

// ============================================================
// Write 3 — after /api/schedule/modify
// ============================================================

export function saveAfterModify(args: {
  new_plan: PlanJSON;
  diff_label: string;
  schedule_remarks: string[];
}): AdvisorState {
  const cur = loadAdvisorState();
  if (!cur.pipeline_state) {
    throw new Error("saveAfterModify called without pipeline_state");
  }
  const nextId =
    cur.versions.reduce((m, v) => Math.max(m, v.id), 0) + 1;
  const newVersion: PlanVersion = {
    id: nextId,
    label: args.diff_label,
    plan: args.new_plan,
    schedule_remarks: args.schedule_remarks,
  };
  const versions = [...cur.versions, newVersion];
  return persist({
    ...cur,
    versions,
    currentIndex: versions.length - 1,
    modify_intent: null,
  });
}

// ============================================================
// modify_intent confirmation gate
// ============================================================

export function setModifyIntent(intent: string): AdvisorState {
  const cur = loadAdvisorState();
  return persist({ ...cur, modify_intent: intent });
}

export function clearModifyIntent(): AdvisorState {
  const cur = loadAdvisorState();
  if (cur.modify_intent === null) return cur;
  return persist({ ...cur, modify_intent: null });
}

// ============================================================
// Version navigation
// ============================================================

export function setCurrentIndex(idx: number): AdvisorState {
  const cur = loadAdvisorState();
  if (idx < 0 || idx >= cur.versions.length) return cur;
  return persist({ ...cur, currentIndex: idx });
}

// ============================================================
// Dev escape hatch
// ============================================================

export function clearAll(): AdvisorState {
  try {
    window.localStorage.removeItem(ADVISOR_STATE_KEY);
  } catch {
    // ignore
  }
  return EMPTY_ADVISOR_STATE;
}
