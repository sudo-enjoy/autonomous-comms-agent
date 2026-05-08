"""Thin wrapper around the Anthropic SDK for tool-use calls."""
from __future__ import annotations


def call_with_tool(
    model: str,
    system: str,
    user_message: str,
    tool_schema: dict,
) -> dict:
    """Single forced tool-use call. Returns the parsed `input` from the tool_use block.

    Raises if the model does not call the tool.
    """
    raise NotImplementedError


def call_with_tools(
    model: str,
    system: str,
    user_message: str,
    tools: list[dict],
) -> list[dict]:
    """Multi-step tool-use loop, max 5 iterations. Returns tool inputs in call order."""
    raise NotImplementedError
