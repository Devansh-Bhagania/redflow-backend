from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.agents.openai_compatible_client import OpenAICompatibleClient, _strip_reasoning, _to_openai_messages, _to_openai_tool


def test_strip_reasoning_removes_think_block():
    raw = "<think>\nLet me consider the options.\n</think>\n\nHello! How can I help you today?"
    assert _strip_reasoning(raw) == "Hello! How can I help you today?"


def test_strip_reasoning_leaves_plain_text_untouched():
    assert _strip_reasoning("Hello! How can I help you today?") == "Hello! How can I help you today?"


def test_strip_reasoning_of_think_only_content_is_empty():
    assert _strip_reasoning("<think>\nNo function call needed here.\n</think>") == ""


def test_strip_reasoning_handles_an_unclosed_think_tag():
    """If the model hits max_tokens mid-thought, </think> never appears —
    everything after <think> is still unfinished reasoning, not an answer,
    and must be stripped the same way a closed block is (previously this
    leaked thousands of chars of raw chain-of-thought into the reply)."""
    raw = "<think>\nOkay, let's see. The customer initially asked about socks, and I tried searching but got no results. Then I broadened the search"
    assert _strip_reasoning(raw) == ""


@pytest.mark.asyncio
async def test_client_strips_think_tags_before_returning_content():
    fake_response = httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": "<think>reasoning here</think>\n\nActual answer."}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
        request=httpx.Request("POST", "http://localhost:1234/v1/chat/completions"),
    )
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
        client = OpenAICompatibleClient(base_url="http://localhost:1234/v1")
        response = await client.messages.create(model="qwen", max_tokens=50, system="sys", messages=[], tools=[])

    assert len(response.content) == 1
    assert response.content[0].text == "Actual answer."


def test_to_openai_tool_converts_input_schema_to_parameters():
    tool = {"name": "check_inventory", "description": "Check stock.", "input_schema": {"type": "object", "properties": {}}}
    converted = _to_openai_tool(tool)
    assert converted == {
        "type": "function",
        "function": {"name": "check_inventory", "description": "Check stock.", "parameters": tool["input_schema"]},
    }


def test_to_openai_messages_expands_tool_result_into_separate_tool_messages():
    history = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": [
                SimpleNamespace(type="text", text="checking"),
                SimpleNamespace(type="tool_use", id="call_1", name="check_inventory", input={"product_variant_id": "abc"}),
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": '{"available_quantity": 5}', "is_error": False}],
        },
    ]
    out = _to_openai_messages("system prompt", history)

    assert out[0] == {"role": "system", "content": "system prompt"}
    assert out[1] == {"role": "user", "content": "hi"}
    assert out[2]["role"] == "assistant"
    assert out[2]["content"] == "checking"
    assert out[2]["tool_calls"][0]["function"]["name"] == "check_inventory"
    assert out[3] == {"role": "tool", "tool_call_id": "call_1", "content": '{"available_quantity": 5}'}


@pytest.mark.asyncio
async def test_client_create_parses_tool_call_response():
    fake_response = httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {"id": "call_1", "function": {"name": "check_inventory", "arguments": '{"product_variant_id": "abc"}'}}
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 42, "completion_tokens": 7},
        },
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
    )

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
        client = OpenAICompatibleClient(base_url="https://api.groq.com/openai/v1", api_key="test-key")
        response = await client.messages.create(
            model="llama-3.3-70b-versatile", max_tokens=100, system="sys", messages=[], tools=[]
        )

    assert response.stop_reason == "tool_use"
    assert response.usage.input_tokens == 42
    assert response.usage.output_tokens == 7
    assert response.content[0].type == "tool_use"
    assert response.content[0].name == "check_inventory"
    assert response.content[0].input == {"product_variant_id": "abc"}


@pytest.mark.asyncio
async def test_client_sends_a_low_temperature():
    """A high default temperature makes a small local model more likely to
    emit an empty completion (no text, no tool call) mid tool-calling loop."""
    fake_response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": "hi there"}}], "usage": {}},
        request=httpx.Request("POST", "http://localhost:1234/v1/chat/completions"),
    )
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)) as mock_post:
        client = OpenAICompatibleClient(base_url="http://localhost:1234/v1")
        await client.messages.create(model="local-model", max_tokens=50, system="sys", messages=[], tools=[])

    sent_payload = mock_post.call_args.kwargs["json"]
    assert sent_payload["temperature"] <= 0.3


@pytest.mark.asyncio
async def test_client_defaults_to_placeholder_api_key_for_local_servers():
    """LM Studio and similar local servers don't check the key at all —
    the client shouldn't require a real one."""
    fake_response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": "hi there"}}], "usage": {}},
        request=httpx.Request("POST", "http://localhost:1234/v1/chat/completions"),
    )
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
        client = OpenAICompatibleClient(base_url="http://localhost:1234/v1")
        response = await client.messages.create(model="local-model", max_tokens=50, system="sys", messages=[], tools=[])

    assert response.stop_reason == "end_turn"
    assert response.content[0].text == "hi there"
