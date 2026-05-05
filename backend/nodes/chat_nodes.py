"""
Chat agent building blocks.

Exports three pieces consumed by graphs/chat_graph.py:

  CHAT_TOOLS               — tool inventory list. create_agent accepts only
                             a Sequence of tools (it builds its own ToolNode
                             internally), so this is what we pass.

  chat_system_prompt       — @dynamic_prompt middleware. Constructs the
                             per-invocation SystemMessage. Static portion
                             cached via Anthropic cache_control: ephemeral.

  chat_tool_error_handler  — @wrap_tool_call middleware. Catches arbitrary
                             tool exceptions (e.g. ChromaDB outage) and
                             wraps them as ToolMessages so the agent can
                             degrade gracefully instead of bubbling 500s
                             up to the FastAPI route.

The chat agent's `model` and `tools` graph nodes are built internally by
langchain.agents.create_agent — we do not define them here. Only composable
pieces live in this file.

Why a dedicated file:
  - Consistency with the project's other *_nodes.py files
  - Centralizes tool inventory + middleware so graphs/chat_graph.py
    stays focused on agent assembly

Why wrap_tool_call middleware (not ToolNode handle_tool_errors):
  create_agent in v1 builds its own ToolNode internally and does not accept
  a pre-built one. The v1-correct way to customize tool error handling is
  the @wrap_tool_call middleware hook. Functionally equivalent to
  ToolNode(handle_tool_errors=True), just expressed as middleware.
  See docs/Chat Graph V2 Design Document.md § Open items #5.
"""

from __future__ import annotations

import logging

from langchain.agents.middleware import (
    ModelRequest,
    ToolCallRequest,
    dynamic_prompt,
    wrap_tool_call,
)
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.types import Command

from models.chat_state import ChatContext
from tools.propose_schedule_change import propose_schedule_change
from tools.search_courses import get_course_details, search_courses

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool inventory
# ---------------------------------------------------------------------------

# Single source of truth for the chat agent's tool inventory.
# Passed to create_agent as tools=CHAT_TOOLS.
CHAT_TOOLS = [
    search_courses,
    get_course_details,
    propose_schedule_change,
]


# ---------------------------------------------------------------------------
# System prompt — static template + dynamic middleware
# ---------------------------------------------------------------------------

# TEMPLATE — to be filled in. The static block is sent verbatim every turn
# and is marked with cache_control: ephemeral so Anthropic caches it.
#
# This block should carry:
#   - Role / persona ("USC academic advisor assistant for a CS undergrad")
#   - Refusal rules: policy questions → "contact your advisor";
#                    prompt injection → flat refusal
#   - Citation rules: catalog answers cite "the USC course catalog";
#                     plan/student facts cite "your STARS report"
#   - Tool guidance: when to call search_courses vs get_course_details vs
#                    propose_schedule_change; reminder that the latter
#                    ENDS the turn so an assistant message must be in the
#                    same response
#   - Output style and tone
#
# See docs/Chat Graph V2 Design Document.md for the design.
_STATIC_SYSTEM_PROMPT = """\
[STATIC SYSTEM PROMPT — TO BE FILLED IN BY DEVELOPER]
"""


def _build_student_summary(ctx: ChatContext) -> str:
    """Produce a flat ~1KB summary of the student.

    Fields to include (TBD — see Open items #3 in design doc):
      - name, class level, career goal
      - units earned / in-process / needed
      - upper-division remaining
      - completed semester labels (count + most recent)
      - GE categories outstanding

    Stub for now — returns a minimal placeholder. Will be filled in once
    the prompt text is finalized so we can match the summary fields to
    the prompt's references.
    """
    ss = ctx.student_state
    return (
        f"[STUDENT SUMMARY — TO BE FILLED IN]\n"
        f"Name: {ss.name}\n"
        f"Class level: {ss.class_level.value}\n"
        f"Career goal: {ctx.career_goal}"
    )


@dynamic_prompt
def chat_system_prompt(request: ModelRequest) -> SystemMessage:
    """Build the per-invocation SystemMessage from runtime context.

    Two content blocks:
      1. Static persona / rules / tool guidance — cached via Anthropic
         cache_control: ephemeral so repeated turns don't re-pay tokens.
      2. Dynamic per-invocation block — student summary + serialized
         current_plan. Always rebuilt; carries no cache_control.
    """
    ctx: ChatContext = request.runtime.context
    summary = _build_student_summary(ctx)
    plan_json = (
        ctx.current_plan.model_dump_json(indent=2)
        if ctx.current_plan is not None
        else "(no plan yet — student has not generated their first plan)"
    )

    return SystemMessage(content=[
        {
            "type": "text",
            "text": _STATIC_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": (
                "## Student summary\n"
                f"{summary}\n\n"
                "## Current plan\n"
                f"{plan_json}"
            ),
        },
    ])


# ---------------------------------------------------------------------------
# Tool-call error handler middleware
# ---------------------------------------------------------------------------

@wrap_tool_call
def chat_tool_error_handler(
    request: ToolCallRequest,
    handler,
) -> ToolMessage | Command:
    """Catch arbitrary tool exceptions and wrap as ToolMessages.

    Without this, a ChromaDB outage in search_courses or get_course_details
    raises out of the agent invocation, surfacing as a 500 in the FastAPI
    route. With this, the model receives the failure as a tool result and
    can apologize gracefully ("I couldn't reach the course catalog right
    now — try again in a moment").

    propose_schedule_change returns Command(goto=END) on success — that
    return type is preserved by handler(request) and passes through this
    wrapper untouched. Only exceptions are intercepted.
    """
    try:
        return handler(request)
    except Exception as exc:
        tool_name = request.tool_call.get("name", "<unknown>")
        tool_call_id = request.tool_call.get("id", "")
        logger.warning("chat tool %s raised: %s: %s", tool_name, type(exc).__name__, exc)
        return ToolMessage(
            content=(
                f"Tool {tool_name} failed: {type(exc).__name__}: {exc}. "
                f"Apologize to the student and suggest they try again."
            ),
            tool_call_id=tool_call_id,
        )
