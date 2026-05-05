"""LLM-bound tools for the chat agent.

Modules in this package define callables decorated with @tool that the chat
agent invokes via tool calling. Tools may import from services/ and models/
but not from nodes/ or graphs/ — keep this package self-contained.
"""
