"""
propose_schedule_change — terminator tool for the chat agent.

Signals "the user wants to modify their plan; here is the change in
natural language." Calling this tool ends the agent's turn:

  - ChatState.modify_intent is set to the description string.
  - The chat graph routes to END (no further model turn this invocation).

Why a tool, not response_format:
  Using create_agent's response_format would force an extra LLM call
  every turn to produce a structured shape, even on pure Q&A turns
  where there is no proposed change. Modeling proposal as a tool
  cleanly limits the cost to turns where the model decides to propose.

Why goto=END works:
  create_agent's compiled graph routes `tools → model` via a CONDITIONAL
  edge, not a static one. A Command(goto=END) returned from inside a
  tool overrides that conditional routing dynamically — there is no
  static edge to fight with. (Verified by inspecting the compiled graph.)

Required ToolMessage:
  When a tool returns Command, ToolNode validates that update["messages"]
  contains a ToolMessage with a matching tool_call_id. Without it, the
  message history is malformed and ToolNode raises. The ToolMessage is
  also useful for traces/logging even though the LLM never reads it
  (we exit before the model gets another turn).

The user-facing assistant text ("I'd suggest swapping CSCI-467 for
CSCI-499 — sound good?") is emitted by the model in the SAME turn as
this tool call, in a separate text content block alongside the tool_use
block. The /api/chat route handler reads it from the last AIMessage.
"""

from __future__ import annotations

import logging

from langchain_core.messages import ToolMessage
from langchain.tools import ToolRuntime, tool
from langgraph.graph import END
from langgraph.types import Command

from models.chat_state import ChatContext

logger = logging.getLogger(__name__)


@tool
def propose_schedule_change(
    description: str,
    runtime: ToolRuntime[ChatContext],
) -> Command:
    """Propose a change to the student's current plan.

    Call this when the student wants to modify their schedule (swap a
    course, add an elective, push a course to a later semester, drop a
    course, take an extra semester, etc.).

    Calling this tool ends the conversation turn. The student will see
    your assistant message and a button to confirm the change. On
    confirmation, the description you provide is passed verbatim to the
    scheduler as `additional_requirements` — so make it precise and
    self-contained.

    Before calling this tool, you should already have:
      - confirmed feasibility (e.g. checked prereqs via get_course_details)
      - emitted a clear natural-language assistant message in the SAME
        response, telling the student what you're about to propose

    Do NOT call this tool to ask clarifying questions. Use a regular
    assistant message for that. Only call when you have a concrete,
    actionable change to propose.

    Args:
        description: A precise natural-language instruction describing the
            change. The downstream scheduler treats this as a free-form
            student request. Reference courses by code (e.g. "CSCI-467")
            and semesters by their calendar label (e.g. "Spring 2027").
            Examples:
              - "Swap CSCI-467 for CSCI-499 in Spring 2027."
              - "Move CSCI-360 from Fall 2026 to Spring 2026."
              - "Add CSCI-485 as an extra elective in Fall 2027."
              - "Push graduation by one semester to fit CSCI-585 and CSCI-560."

    Returns:
        Internal control-flow signal — terminates the agent loop.
    """
    logger.info("propose_schedule_change: %s", description)

    return Command(
        update={
            "modify_intent": description,
            "messages": [
                ToolMessage(
                    content=f"Proposed change recorded: {description}",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        },
        goto=END,
    )
