from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter

from api.schemas.schedule import ScheduleModifyRequest, ScheduleModifyResponse
from graphs.subgraphs.scheduler import schedule_validator_loop

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/schedule/modify", response_model=ScheduleModifyResponse)
async def modify_schedule(req: ScheduleModifyRequest) -> ScheduleModifyResponse:
    diff_label = "Updated plan — " + datetime.now(timezone.utc).strftime("%b %-d")

    final_state = await schedule_validator_loop.ainvoke({
        "stars_pdf_text":            "",
        "student_state":             req.student_state,
        "requirements_map":          req.requirements_map,
        "career_goal":               req.career_goal,
        "hard_courses":              req.hard_courses,
        "other_must_reqs":           req.other_must_reqs,
        "ge_placeholders_remaining": req.ge_placeholders_remaining,
        "scored_electives":          req.scored_electives,
        "scored_enrichment":         req.scored_enrichment,
        "current_plan":              req.current_plan,
        "additional_requirements":   req.modify_intent,
        "remarks":                   [],
    })

    logger.info(
        "schedule/modify complete: version_id=%s, remarks=%d",
        final_state["current_plan"].version_id,
        len(final_state.get("remarks", [])),
    )

    return ScheduleModifyResponse(
        new_plan=final_state["current_plan"],
        diff_label=diff_label,
        schedule_remarks=final_state.get("remarks", []),
    )
