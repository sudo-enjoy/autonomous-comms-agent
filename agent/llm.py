"""LLM wrapper — OpenAI-compatible chat-completions against ppq.ai.

ppq.ai is an aggregator that routes to Anthropic (and others) but exposes an
OpenAI-compatible REST surface. We talk to it directly with `requests` rather
than pulling in the openai SDK — keeps the dep list to what's already there.

Two patterns:
- `call_with_tool` — single forced tool call, used for structured output
  (Router → `dispatch_email`, handlers → `draft_*_reply`).
- `call_with_tools` — multi-step tool-use loop where the model invokes real
  side-effecting tools (e.g. `check_capacity`) before producing a final answer.
  Caller supplies executors. Loop stops on `finish_reason != "tool_calls"` or
  on `max_iterations`.

Tool schemas are passed as plain dicts (`{"name", "description", "parameters"}`)
and wrapped into OpenAI's `{"type": "function", "function": {...}}` envelope
inside this module — keeps caller code uncluttered.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable

import requests

from agent.logging_setup import get_logger

log = get_logger(__name__)

DEFAULT_BASE_URL = "https://api.ppq.ai/v1"
HTTP_TIMEOUT_SECONDS = 60


def _api_key() -> str:
    key = os.environ.get("PPQ_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "PPQ_API_KEY is not set. Add it to .env (see .env.example)."
        )
    return key


def _base_url() -> str:
    return os.environ.get("PPQ_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _post_chat(payload: dict) -> dict:
    resp = requests.post(
        f"{_base_url()}/chat/completions",
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    if resp.status_code >= 400:
        # Surface the body, not just the status — helps debugging tool-schema issues.
        raise RuntimeError(
            f"ppq.ai {resp.status_code}: {resp.text[:500]}"
        )
    return resp.json()


def _wrap_tool(tool: dict) -> dict:
    """OpenAI tools are `{"type": "function", "function": {...}}`."""
    return {"type": "function", "function": tool}


def _parse_tool_args(raw: Any, tool_name: str) -> dict:
    """Parse a tool_call's arguments. OpenAI-compatible APIs return them as a
    JSON-encoded string; some providers occasionally emit malformed JSON
    (unescaped quotes, trailing commas). On failure, log the raw payload so
    the demo / debug session sees what the model actually produced.
    """
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise RuntimeError(
            f"tool {tool_name!r} arguments have unexpected type {type(raw).__name__}"
        )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        log.error(
            f"[LLM] malformed tool arguments for {tool_name!r}: {exc} | "
            f"raw[:200]={raw[:200]!r}"
        )
        raise RuntimeError(
            f"tool {tool_name!r} returned malformed JSON arguments"
        ) from exc


def call_with_tool(
    model: str,
    system: str,
    user_message: str,
    tool_schema: dict,
    max_tokens: int = 1024,
) -> dict:
    """Force the model to call exactly one tool. Returns parsed arguments dict.

    `tool_schema` shape: `{"name", "description", "parameters"}` where
    `parameters` is a JSONSchema for the tool input. Raises if the response
    doesn't include a tool_call for the named tool.
    """
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
        "tools": [_wrap_tool(tool_schema)],
        "tool_choice": {
            "type": "function",
            "function": {"name": tool_schema["name"]},
        },
    }
    data = _post_chat(payload)
    msg = data["choices"][0]["message"]
    for call in msg.get("tool_calls") or []:
        if call.get("function", {}).get("name") == tool_schema["name"]:
            return _parse_tool_args(
                call["function"].get("arguments", "{}"),
                tool_schema["name"],
            )
    finish = data["choices"][0].get("finish_reason")
    raise RuntimeError(
        f"Model {model!r} did not call tool {tool_schema['name']!r} "
        f"(finish_reason={finish!r})"
    )


def call_with_tools(
    model: str,
    system: str,
    user_message: str,
    tools: list[dict],
    tool_executors: dict[str, Callable[[dict], Any]],
    max_tokens: int = 2048,
    max_iterations: int = 5,
) -> list[dict]:
    """Multi-step tool-use loop. Returns tool argument dicts in call order.

    `tool_executors` maps tool name → callable that takes the parsed argument
    dict and returns a JSON-serializable result. The result is stringified and
    fed back as a `tool` role message. Loop ends when finish_reason is no
    longer `tool_calls` (model wrote a final answer), or after max_iterations.
    """
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]
    wrapped_tools = [_wrap_tool(t) for t in tools]
    inputs_in_call_order: list[dict] = []

    for iteration in range(max_iterations):
        data = _post_chat(
            {
                "model": model,
                "max_tokens": max_tokens,
                "messages": messages,
                "tools": wrapped_tools,
            }
        )
        choice = data["choices"][0]
        msg = choice["message"]
        finish = choice.get("finish_reason")

        # Append the assistant turn so the next iteration has context.
        # Strip nulls and unknown keys to keep the payload compact and valid.
        assistant_turn: dict = {"role": "assistant"}
        if msg.get("content"):
            assistant_turn["content"] = msg["content"]
        if msg.get("tool_calls"):
            assistant_turn["tool_calls"] = msg["tool_calls"]
        messages.append(assistant_turn)

        if finish != "tool_calls" or not msg.get("tool_calls"):
            return inputs_in_call_order

        for call in msg["tool_calls"]:
            name = call["function"]["name"]
            args = _parse_tool_args(call["function"].get("arguments", "{}"), name)
            inputs_in_call_order.append(args)

            executor = tool_executors.get(name)
            if executor is None:
                tool_content = f"error: no executor for tool {name!r}"
            else:
                try:
                    result = executor(args)
                    tool_content = json.dumps(result, default=str)
                except Exception as exc:
                    log.warning(f"tool {name!r} raised: {exc}")
                    tool_content = f"error: {exc}"

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": tool_content,
                }
            )

    log.warning(
        f"call_with_tools hit max_iterations={max_iterations} on model {model!r}"
    )
    return inputs_in_call_order
