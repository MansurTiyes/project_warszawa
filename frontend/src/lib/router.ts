/**
 * Resume-on-reload step decision.
 *
 * Called once on App mount. Returns the step the user should land on
 * given the current AdvisorState. After mount, the App component drives
 * step transitions via local React state — this function is only used
 * to bootstrap.
 *
 * Linear flow (no router lib):
 *
 *   upload     ← no state at all
 *      ↓                     /api/stars
 *   parsing    ← (transient — not resumed; if user reloaded mid-parse,
 *                 we treat it as never-started and send them back to upload)
 *      ↓
 *   breakdown  ← partial_state present, no pipeline_state
 *      ↓
 *   goal       ← partial_state.career_goal set (still no pipeline_state)
 *      ↓                     /api/pipeline
 *   generating ← (transient — not resumed; mid-stream reloads return to goal)
 *      ↓
 *   plan       ← pipeline_state present, versions non-empty
 *
 * "parsing" and "generating" are deliberately non-resumable: a mid-flight
 * fetch that the page didn't see complete cannot be recovered, and the
 * deterministic recovery is to send the user back one step.
 */

import type { AdvisorState, Step } from "@/types";

export function stepFor(state: AdvisorState): Step {
  if (state.pipeline_state && state.versions.length > 0) return "plan";
  if (state.partial_state) {
    return state.partial_state.career_goal ? "goal" : "breakdown";
  }
  return "upload";
}
