"""
Chat agent state and context schemas.

Two distinct shapes:

  ChatState   — mutable, conversation-scoped. Extends langchain.agents.AgentState
                (which provides `messages`). The model and tools can read and
                write fields here. `modify_intent` is the only chat-specific
                addition; it is set by the propose_schedule_change tool to
                signal that the agent's final state contains a pending
                schedule-modification proposal.

  ChatContext — immutable, invocation-scoped. Carries the full per-request
                pipeline state. Passed once at agent.ainvoke(context=...) and
                read inside tools via `runtime.context` (ToolRuntime parameter)
                and inside the @dynamic_prompt middleware via
                `request.runtime.context`. Never mutated, never visible to the
                LLM's tool schema.

Why split state from context:
  - Pipeline state is read-only reference material. Putting it in ChatState
    would let tool calls accidentally mutate it and would force every state-
    reading tool to redeclare each field in its schema.
  - Context is exactly the right abstraction: invocation-scoped, immutable,
    type-checked, and invisible to the model.

See docs/Chat Graph V2 Design Document.md for the full contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain.agents import AgentState

from models.course_nodes import CourseNode, OtherRequirements, ScoredCourse
from models.plan import PlanJSON
from models.requirements_map import RequirementsMap
from models.student_state import StudentState


class ChatState(AgentState):
    """
    Mutable, conversation-scoped state for the chat agent.

    Inherits from langchain.agents.AgentState:
      - messages: list[AnyMessage]              — conversation history (annotated with add_messages reducer)
      - jump_to: JumpTo | None                  — middleware control flow (private)
      - structured_response: ResponseT          — unused (no response_format)

    Adds:
      - modify_intent: free-form natural-language instruction set by the
        propose_schedule_change tool. None on every turn except those
        terminated by that tool. Surfaced in the /api/chat response body
        and stored by the frontend as a pending proposal.
    """
    modify_intent: str | None


@dataclass
class ChatContext:
    """
    Immutable, invocation-scoped context for the chat agent.

    Built per-request by the FastAPI route handler from the chat request
    body, then passed to chat_agent.ainvoke(context=...). Read inside tools
    via `runtime.context` and inside @dynamic_prompt middleware via
    `request.runtime.context`.

    Fields mirror the AdvisorState contract output by the pipeline graph,
    plus current_plan (the version the user is currently viewing in the UI).
    """
    student_state: StudentState
    requirements_map: RequirementsMap
    career_goal: str
    hard_courses: list[CourseNode]
    scored_electives: list[ScoredCourse]
    scored_enrichment: list[list[ScoredCourse]]
    other_must_reqs: OtherRequirements
    ge_placeholders_remaining: list[str]
    current_plan: PlanJSON | None
