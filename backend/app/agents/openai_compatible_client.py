"""A thin adapter so app/agents/runtime.py can call any OpenAI-compatible
chat completions API (Groq, a local LM Studio server, etc.) through the exact
same `client.messages.create(...)` shape the Anthropic SDK exposes — the
tool-calling loop in runtime.py stays provider-agnostic. Uses httpx (already
a dependency) instead of adding the openai SDK for one endpoint.
"""

import json
import logging
import re
from types import SimpleNamespace

import httpx

logger = logging.getLogger(__name__)

_THINK_BLOCK = re.compile(r"<think>.*?(?:</think>|\Z)", re.DOTALL)


def _strip_reasoning(text: str) -> str:
    """Some models (Qwen3 and other "thinking" models) put their chain-of-
    thought directly inside `content` wrapped in <think> tags — unlike models
    that use a separate `reasoning_content` field — so it has to be stripped
    out here or it leaks straight into what the customer sees.

    The closing </think> is optional in the pattern: if the model hits
    max_tokens mid-thought, the tag never closes and everything after
    <think> is still unfinished reasoning, not an answer — matching to the
    end of the string strips it too, instead of dumping thousands of
    characters of raw chain-of-thought into the reply (which previously blew
    past the messages.content column limit and crashed the save)."""
    return _THINK_BLOCK.sub("", text).strip()


def _to_openai_tool(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {"name": tool["name"], "description": tool["description"], "parameters": tool["input_schema"]},
    }


def _disable_qwen3_thinking(openai_messages: list[dict]) -> None:
    """`chat_template_kwargs.enable_thinking` only works if the server
    unpacks it into the Jinja render context (vLLM/SGLang do; LM Studio's
    engine has been observed not to) — the pre-seeded `<think></think>`
    branch in Qwen3's own chat template never fires, so the model free-runs
    a reasoning block that can eat the whole max_tokens budget. Qwen3 is
    separately trained to honor a literal "/no_think" directive in the user
    turn regardless of template support, so append it there as a fallback
    that works even when the template-level switch is silently ignored."""
    for m in reversed(openai_messages):
        if m["role"] == "user":
            m["content"] = f"{m['content']} /no_think"
            return


def _to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    out = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
        elif m["role"] == "assistant":
            text = "".join(b.text for b in content if b.type == "text")
            tool_calls = [
                {"id": b.id, "type": "function", "function": {"name": b.name, "arguments": json.dumps(b.input)}}
                for b in content
                if b.type == "tool_use"
            ]
            # Some OpenAI-compatible chat templates (Qwen3's included) do
            # `'</think>' in message.content` unconditionally and choke on
            # `null` — send "" instead of None for a tool-call-only turn.
            entry: dict = {"role": "assistant", "content": text}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
        else:
            # Anthropic groups tool results from one turn into a single "user"
            # message's content list; OpenAI-style wants one "tool" message each.
            for block in content:
                out.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": block["content"]})
    return out


class _OpenAICompatibleMessages:
    def __init__(self, base_url: str, api_key: str, timeout: float):
        self._base_url = base_url
        self._api_key = api_key
        self._timeout = timeout

    async def create(self, *, model: str, max_tokens: int, system: str, messages: list[dict], tools: list[dict]):
        openai_messages = _to_openai_messages(system, messages)
        if "qwen3" in model.lower():
            _disable_qwen3_thinking(openai_messages)
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            # Lower than most chat-UI defaults (often ~0.7-0.8): a tool-calling
            # agent loop needs consistent, decisive output far more than
            # creative variety — high temperature on a small local model makes
            # the occasional empty (no text, no tool call) turn more likely.
            "temperature": 0.2,
            "messages": openai_messages,
            "tools": [_to_openai_tool(t) for t in tools],
            "tool_choice": "auto",
            # Qwen3's chat template branches into a "thinking" mode that some
            # servers (vLLM) render differently for tool-call turns, tripping
            # the template's own guard clauses. Disabling it keeps the
            # template on its plain tool-use path.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        logger.debug("openai_compatible request payload=%s", json.dumps(payload))
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as http:
            resp = await http.post(
                "/chat/completions", json=payload, headers={"Authorization": f"Bearer {self._api_key}"}
            )
            if resp.status_code >= 400:
                logger.error("openai_compatible error status=%d body=%s", resp.status_code, resp.text)
            resp.raise_for_status()
            data = resp.json()

        message = data["choices"][0]["message"]
        logger.debug(
            "openai_compatible response finish_reason=%s raw_content=%r tool_calls=%s",
            data["choices"][0].get("finish_reason"), message.get("content"), message.get("tool_calls"),
        )
        content = []
        text = _strip_reasoning(message["content"]) if message.get("content") else ""
        if text:
            content.append(SimpleNamespace(type="text", text=text))
        for call in message.get("tool_calls") or []:
            content.append(
                SimpleNamespace(
                    type="tool_use",
                    id=call["id"],
                    name=call["function"]["name"],
                    input=json.loads(call["function"]["arguments"]),
                )
            )

        usage = data.get("usage", {})
        return SimpleNamespace(
            content=content,
            stop_reason="tool_use" if message.get("tool_calls") else "end_turn",
            usage=SimpleNamespace(
                input_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("completion_tokens", 0)
            ),
        )


class OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key: str = "not-needed", timeout: float = 60):
        self.messages = _OpenAICompatibleMessages(base_url, api_key, timeout)
