"""Agent Framework chat client wired up for a local llama-server.

llama-server (llama.cpp's OpenAI-compatible endpoint) streams a model's thinking in a
non-standard ``delta.reasoning_content`` field. The stock ``OpenAIChatCompletionClient``
only knows about OpenRouter's ``reasoning_details``, so the thinking is silently dropped.

``ReasoningChatClient`` teaches the chunk parser to surface ``reasoning_content`` as a
``TextReasoningContent``, which is what lets the TUI show thinking distinctly from the
answer. It also leaves llama-server's native ``timings`` block intact on the raw chunk so
the metrics layer can read real prefill/generation throughput.
"""

from __future__ import annotations

import urllib.request
import json

from agent_framework import ChatOptions, ChatResponseUpdate, Content, Message
from agent_framework.openai import OpenAIChatCompletionClient
from openai.types.chat import ChatCompletionChunk


class ReasoningChatClient(OpenAIChatCompletionClient):
    """An OpenAI chat-completions client that surfaces llama.cpp reasoning content."""

    def _prepare_message_for_openai(self, message: Message):
        # Never send reasoning back to the server. llama-server rejects a content part whose
        # type is "text_reasoning" ("unsupported content[].type"), and thinking should not be
        # replayed into context anyway — only the answer and any tool calls/results matter.
        if any(getattr(c, "type", None) == "text_reasoning" for c in message.contents):
            kept = [c for c in message.contents if getattr(c, "type", None) != "text_reasoning"]
            try:
                message = message.model_copy(update={"contents": kept})
            except Exception:
                message = Message(
                    role=message.role,
                    contents=kept,
                    author_name=message.author_name,
                    message_id=message.message_id,
                    additional_properties=message.additional_properties,
                )
        return super()._prepare_message_for_openai(message)

    def _parse_response_update_from_openai(self, chunk: ChatCompletionChunk) -> ChatResponseUpdate:
        update = super()._parse_response_update_from_openai(chunk)
        for choice in chunk.choices:
            delta = getattr(choice, "delta", None)
            reasoning = getattr(delta, "reasoning_content", None) if delta is not None else None
            if reasoning:
                update.contents.append(
                    Content.from_text_reasoning(text=reasoning, raw_representation=chunk)
                )
        return update


def make_message(role: str, text: str) -> Message:
    """Build a chat Message of ``role`` carrying a single text part."""
    return Message(role=role, contents=[Content.from_text(text=text)])


def build_agent(
    *,
    base_url: str,
    model: str,
    api_key: str = "not-needed",
    instructions: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 32000,
    top_p: float | None = None,
    tools=None,
):
    """Create a ChatAgent backed by the reasoning-aware llama-server client."""
    client = ReasoningChatClient(base_url=base_url, api_key=api_key, model=model)
    opts: dict = {"temperature": temperature, "max_tokens": max_tokens}
    if top_p is not None:
        opts["top_p"] = top_p
    kwargs: dict = {"instructions": instructions, "default_options": ChatOptions(**opts)}
    if tools:
        kwargs["tools"] = tools
    return client.as_agent(**kwargs)


def detect_model_id(base_url: str, timeout: float = 4.0) -> str | None:
    """Ask the server which model is loaded (the first id from ``/v1/models``)."""
    try:
        url = base_url.rstrip("/") + "/models"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = data.get("data") or []
        if models:
            return models[0].get("id")
    except Exception:
        return None
    return None


def detect_context_window(base_url: str, timeout: float = 4.0) -> int | None:
    """Read the server's context window (n_ctx) from ``/props`` when available."""
    try:
        # /props lives at the server root, not under /v1.
        root = base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[: -len("/v1")]
        with urllib.request.urlopen(root + "/props", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        n_ctx = data.get("default_generation_settings", {}).get("n_ctx")
        if isinstance(n_ctx, int) and n_ctx > 0:
            return n_ctx
    except Exception:
        return None
    return None
