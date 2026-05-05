"""
Chat agent graph — entry point for POST /api/chat.

Assembles the ReAct-pattern agent via langchain.agents.create_agent.
The factory returns a compiled LangGraph (4 nodes: __start__, model,
tools, __end__) — there is no separate StateGraph to build.

  Topology (verified):
    __start__ → model → [conditional: has tool_calls?]
                          ├── yes → tools → [conditional] → model
                          └── no  → __end__
    propose_schedule_change tool returns Command(goto=END), which
    short-circuits the loop (the tools→model edge is conditional, so
    the dynamic goto cleanly overrides routing — no static edge to
    fight with).

State / context split:
  state_schema=ChatState     — mutable, conversation-scoped (messages,
                               modify_intent). modify_intent is set only
                               by propose_schedule_change.
  context_schema=ChatContext — immutable, per-invocation pipeline state.
                               Tools read it via runtime.context. The
                               dynamic_prompt middleware reads it via
                               request.runtime.context to build the
                               per-turn SystemMessage.

Invocation pattern (from /api/chat route handler):
    ctx = ChatContext(...)  # built from request body
    result = await chat_agent.ainvoke(
        {"messages": [HumanMessage(...), *prior_messages]},
        config={"recursion_limit": 8},   # cap ReAct iterations
        context=ctx,
    )
    # Read result["messages"][-1].content for the assistant text
    # Read result.get("modify_intent") for the proposal (or None)

See docs/Chat Graph V2 Design Document.md for the full design.
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain_anthropic import ChatAnthropic

from config import settings
from models.chat_state import ChatContext, ChatState
from nodes.chat_nodes import (
    CHAT_TOOLS,
    chat_system_prompt,
    chat_tool_error_handler,
)


# Sonnet 4.6 with low temperature — advisor responses should be consistent
# across turns. create_agent binds the tools (CHAT_TOOLS) to this model
# automatically and builds its own internal ToolNode for execution.
_model = ChatAnthropic(
    model="claude-sonnet-4-6",
    api_key=settings.anthropic_api_key,
    temperature=0,
)


# Compiled chat agent. Imported by api/routes/chat.py.
#
# Middleware order matters: chat_system_prompt runs in the model-call hook
# (wrap_model_call) and chat_tool_error_handler runs in the tool-call hook
# (wrap_tool_call). They operate on different phases and don't compete.
chat_agent = create_agent(
    model=_model,
    tools=CHAT_TOOLS,
    middleware=[chat_system_prompt, chat_tool_error_handler],
    state_schema=ChatState,
    context_schema=ChatContext,
)
