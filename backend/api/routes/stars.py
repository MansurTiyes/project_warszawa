from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile

from api.schemas.stars import StarsResponse, StudentStateSummary
from graphs.stars_parser import stars_parser_graph
from services.pdf_parser import extract_stars_text

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/stars", response_model=StarsResponse)
async def parse_stars(stars_pdf: UploadFile = File(...)) -> StarsResponse:
    pdf_bytes = await stars_pdf.read()

    try:
        stars_text = extract_stars_text(pdf_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = await stars_parser_graph.ainvoke({"stars_pdf_text": stars_text})

    student_state = result["student_state"]
    requirements_map = result["requirements_map"]

    logger.info(
        "STARS parsed: %s, %d courses taken.",
        student_state.name,
        len(student_state.courses_taken),
    )

    return StarsResponse(
        student_state=student_state,
        student_state_summary=StudentStateSummary.from_student_state(student_state),
        requirements_map=requirements_map,
    )
