from __future__ import annotations

import logging

from fastapi import APIRouter
from langchain_core.messages import AIMessage, HumanMessage, convert_to_messages
from langgraph.errors import GraphRecursionError

from api.schemas.chat import ChatRequest, ChatResponse
from graphs.chat_graph import chat_agent
from models.chat_state import ChatContext

logger = logging.getLogger(__name__)

router = APIRouter()

_RECURSION_FALLBACK = (
    "I couldn't fully resolve that — could you rephrase or break it into a smaller question?"
)


def _extract_message_text(messages: list) -> str:
    """Extract assistant text from the last AIMessage.

    AIMessage.content is a plain str for text-only responses, or a list of
    content blocks when the model emits text alongside a tool_use block (the
    propose_schedule_change case on Anthropic). Pull text blocks only.
    """
    for m in reversed(messages):
        if not isinstance(m, AIMessage):
            continue
        if isinstance(m.content, str):
            return m.content
        parts = [
            block.get("text", "")
            for block in m.content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(parts).strip()
    return ""


@router.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    ctx = ChatContext(
        student_state=req.student_state,
        requirements_map=req.requirements_map,
        career_goal=req.career_goal,
        hard_courses=req.hard_courses,
        scored_electives=req.scored_electives,
        scored_enrichment=req.scored_enrichment,
        other_must_reqs=req.other_must_reqs,
        ge_placeholders_remaining=req.ge_placeholders_remaining,
        current_plan=req.current_plan,
    )

    prior_messages = convert_to_messages(req.messages) if req.messages else []

    try:
        result = await chat_agent.ainvoke(
            {"messages": [*prior_messages, HumanMessage(content=req.message)]},
            config={"recursion_limit": 8},
            context=ctx,
        )
    except GraphRecursionError:
        logger.warning("chat_agent hit recursion limit for message: %.120s", req.message)
        return ChatResponse(message=_RECURSION_FALLBACK, modify_intent=None)

    message = _extract_message_text(result.get("messages", []))
    modify_intent: str | None = result.get("modify_intent")

    logger.info(
        "chat response: modify_intent=%s, message_len=%d",
        "set" if modify_intent else "none",
        len(message),
    )

    return ChatResponse(message=message, modify_intent=modify_intent)
