from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from api.schemas.pipeline import PipelineRequest, PipelineResponse
from graphs.pipeline_graph import pipeline_graph

logger = logging.getLogger(__name__)

router = APIRouter()

# Maps pipeline graph node names → (SSE event name, user-facing message)
_NODE_PROGRESS: dict[str, tuple[str, str]] = {
    "hard_requirements": ("hard_requirements_complete", "Identified remaining requirements"),
    "soft_requirements": ("soft_requirements_complete", "Ranked electives for your career goal"),
    "scheduler":         ("plan_generated",             "Building your schedule..."),
}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/api/pipeline")
async def generate_pipeline(req: PipelineRequest) -> StreamingResponse:
    diff_label = "Initial plan — " + datetime.now(timezone.utc).strftime("%b %-d")

    initial_state = {
        "stars_pdf_text":   "",
        "student_state":    req.student_state,
        "requirements_map": req.requirements_map,
        "career_goal":      req.career_goal,
    }

    async def stream():
        final_state: dict = {}
        try:
            yield _sse({"event": "started", "message": "Analyzing your requirements..."})

            async for chunk in pipeline_graph.astream(initial_state, stream_mode="updates"):
                node_name = next(iter(chunk))
                final_state.update(chunk[node_name])

                if node_name in _NODE_PROGRESS:
                    event_name, message = _NODE_PROGRESS[node_name]
                    yield _sse({"event": event_name, "message": message})

            response = PipelineResponse(
                current_plan=final_state["current_plan"],
                schedule_remarks=final_state.get("remarks", []),
                diff_label=diff_label,
                hard_courses=final_state["hard_courses"],
                other_must_reqs=final_state["other_must_reqs"],
                ge_placeholders_remaining=final_state["ge_placeholders_remaining"],
                scored_electives=final_state["scored_electives"],
                scored_enrichment=final_state["scored_enrichment"],
            )
            yield _sse({"event": "complete", "payload": json.loads(response.model_dump_json())})

        except Exception as exc:
            logger.exception("Pipeline failed")
            yield _sse({"event": "error", "message": str(exc)})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
