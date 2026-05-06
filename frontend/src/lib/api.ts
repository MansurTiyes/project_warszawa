/**
 * Typed fetch wrappers for the four backend endpoints.
 *
 * - JSON encoding centralized here. Multipart for /api/stars only.
 * - Errors thrown as ApiError; callers decide UX. We do NOT swallow.
 * - /api/pipeline is StreamingResponse (SSE). Native EventSource only does
 *   GET, so we use fetch + ReadableStream and parse SSE chunks manually.
 *
 * Base URL comes from VITE_API_URL (.env.local). No proxy in vite.config.ts —
 * frontend on :3000, backend on :8000, backend CORS allows localhost:3000.
 */

import type {
  ChatRequest,
  ChatResponse,
  PipelineEvent,
  PipelineRequest,
  ScheduleModifyRequest,
  ScheduleModifyResponse,
  StarsResponse,
} from "@/types";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// ============================================================
// ApiError
// ============================================================

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown, message?: string) {
    super(message ?? `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function throwIfNotOk(res: Response): Promise<void> {
  if (res.ok) return;
  let detail: unknown = null;
  try {
    detail = await res.json();
  } catch {
    try {
      detail = await res.text();
    } catch {
      detail = null;
    }
  }
  const message =
    typeof detail === "object" && detail && "detail" in detail
      ? String((detail as { detail: unknown }).detail)
      : `HTTP ${res.status}`;
  throw new ApiError(res.status, detail, message);
}

// ============================================================
// /api/stars  (multipart)
// ============================================================

export async function postStars(file: File): Promise<StarsResponse> {
  const form = new FormData();
  form.append("stars_pdf", file);

  const res = await fetch(`${API_BASE}/api/stars`, {
    method: "POST",
    body: form,
  });
  await throwIfNotOk(res);
  return (await res.json()) as StarsResponse;
}

// ============================================================
// /api/pipeline  (SSE stream — async generator)
//
// Yields each event in order. Caller drives UI from event types.
// Pass `signal` (AbortController.signal) to cancel mid-stream
// e.g. if the user navigates away.
// ============================================================

export async function* streamPipeline(
  req: PipelineRequest,
  signal?: AbortSignal,
): AsyncGenerator<PipelineEvent, void, void> {
  const res = await fetch(`${API_BASE}/api/pipeline`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(req),
    signal,
  });
  await throwIfNotOk(res);
  if (!res.body) throw new Error("Pipeline response has no body");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  // SSE format:  "data: {...}\n\n"  (we ignore "event:" / "id:" lines —
  // FastAPI route only emits "data:" lines).
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      let sep: number;
      while ((sep = buf.indexOf("\n\n")) !== -1) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep + 2);

        // Each frame may have multiple "data:" lines per spec; concatenate.
        const dataLines = frame
          .split("\n")
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).trimStart());
        if (dataLines.length === 0) continue;

        const json = dataLines.join("\n");
        try {
          yield JSON.parse(json) as PipelineEvent;
        } catch {
          // Malformed frame — skip; backend should never send these.
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

// ============================================================
// /api/chat  (JSON)
// ============================================================

export async function postChat(req: ChatRequest): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  await throwIfNotOk(res);
  return (await res.json()) as ChatResponse;
}

// ============================================================
// /api/schedule/modify  (JSON)
// ============================================================

export async function postScheduleModify(
  req: ScheduleModifyRequest,
): Promise<ScheduleModifyResponse> {
  const res = await fetch(`${API_BASE}/api/schedule/modify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  await throwIfNotOk(res);
  return (await res.json()) as ScheduleModifyResponse;
}
