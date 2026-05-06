/**
 * Cross-cutting types — wire schemas + UI-only types.
 *
 * Wire schemas come from src/types/api.d.ts (auto-generated from /openapi.json
 * via `npm run gen:types`). We re-export them under friendly names here so
 * components don't have to know about the `components["schemas"]` indirection.
 *
 * UI-only types (AdvisorState envelope, Step enum, PipelineEvent SSE shapes)
 * live below. PipelineEvent is hand-written because the /api/pipeline endpoint
 * returns StreamingResponse, which FastAPI can't describe in OpenAPI.
 */

import type { components } from "./api";

type S = components["schemas"];

// ============================================================
// Wire schemas (re-exported from generated openapi types)
// ============================================================

export type StarsResponse           = S["StarsResponse"];
export type StudentState            = S["StudentState"];
export type StudentStateSummary     = S["StudentStateSummary"];
export type RequirementsMap         = S["RequirementsMap"];
export type CourseRecord            = S["CourseRecord"];
export type ClassLevel              = S["ClassLevel"];

export type PipelineRequest         = S["PipelineRequest"];

export type ChatRequest             = S["ChatRequest"];
export type ChatResponse            = S["ChatResponse"];

export type ScheduleModifyRequest   = S["ScheduleModifyRequest"];
export type ScheduleModifyResponse  = S["ScheduleModifyResponse"];

export type PlanJSON                = S["PlanJSON"];
export type SemesterPlan            = S["SemesterPlan"];
export type PlannedCourse           = S["PlannedCourse"];
export type PlanMetadata            = S["PlanMetadata"];

export type CourseNode              = S["CourseNode"];
export type ScoredCourse            = S["ScoredCourse"];
export type OtherRequirements       = S["OtherRequirements"];

// ============================================================
// SSE event shapes for /api/pipeline
//
// The complete-event payload mirrors the backend Pydantic
// PipelineResponse (api/schemas/pipeline.py). Update both sides
// in lockstep if a field changes.
// ============================================================

export type PipelineCompletePayload = {
  current_plan: PlanJSON;
  schedule_remarks: string[];
  diff_label: string;
  hard_courses: CourseNode[];
  other_must_reqs: OtherRequirements;
  ge_placeholders_remaining: string[];
  scored_electives: ScoredCourse[];
  scored_enrichment: ScoredCourse[][];
};

export type PipelineEvent =
  | { event: "started";                       message: string }
  | { event: "hard_requirements_complete";    message: string }
  | { event: "soft_requirements_complete";    message: string }
  | { event: "plan_generated";                message: string }
  | { event: "complete";                      payload: PipelineCompletePayload }
  | { event: "error";                         message: string };

// Order matters — used by ScreenGenerating to drive the ticker UI.
export const PIPELINE_PROGRESS_EVENTS = [
  "started",
  "hard_requirements_complete",
  "soft_requirements_complete",
  "plan_generated",
  "complete",
] as const satisfies readonly PipelineEvent["event"][];

// ============================================================
// localStorage envelope (UI-only — never crosses the wire)
// ============================================================

export const ADVISOR_STATE_KEY = "advisor_state";
export const ADVISOR_STATE_SCHEMA_VERSION = 1;

/**
 * Holding pen for state between /api/stars success and /api/pipeline success.
 * Lets the user refresh on Screen 3 (breakdown) or 4 (goal) without losing work.
 */
export type PartialState = {
  student_state: StudentState;
  requirements_map: RequirementsMap;
  student_state_summary: StudentStateSummary;
  career_goal: string | null;     // set on Screen 4 before kicking off /api/pipeline
};

/**
 * Full pipeline-output state — frozen after Write 2, never mutated.
 * Sent (in pieces) to /api/chat and /api/schedule/modify.
 */
export type PipelineState = {
  // Inputs that fed the pipeline (kept so chat / modify don't have to refetch)
  student_state: StudentState;
  requirements_map: RequirementsMap;
  career_goal: string;
  student_state_summary: StudentStateSummary;

  // Pipeline outputs
  hard_courses: CourseNode[];
  other_must_reqs: OtherRequirements;
  ge_placeholders_remaining: string[];
  scored_electives: ScoredCourse[];
  scored_enrichment: ScoredCourse[][];
};

export type PlanVersion = {
  id: number;          // monotonically increasing, starting at 1
  label: string;       // "Initial plan — May 6", "Updated plan — May 6"
  plan: PlanJSON;
  schedule_remarks: string[];
};

export type AdvisorState = {
  schema_version: typeof ADVISOR_STATE_SCHEMA_VERSION;
  pipeline_state: PipelineState | null;     // null until pipeline completes
  partial_state: PartialState | null;       // null after pipeline completes
  versions: PlanVersion[];                  // [] until first plan
  currentIndex: number;                     // index into versions
  modify_intent: string | null;             // pending confirmation
};

export const EMPTY_ADVISOR_STATE: AdvisorState = {
  schema_version: ADVISOR_STATE_SCHEMA_VERSION,
  pipeline_state: null,
  partial_state: null,
  versions: [],
  currentIndex: 0,
  modify_intent: null,
};

// ============================================================
// UI-only types
// ============================================================

export type Step =
  | "upload"
  | "parsing"
  | "breakdown"
  | "goal"
  | "generating"
  | "plan";

/**
 * Chat-panel local state. Wire format matches LangChain's `convert_to_messages`
 * shape — `type` (NOT `role`), `content` (string).
 */
export type ChatMessage = {
  type: "human" | "ai";
  content: string;
};
