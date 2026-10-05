"""SDK real com transporte HTTP simulado, sem rede ou chave válida."""

from collections.abc import Callable, Iterator
import json
from pathlib import Path
from typing import Any

import httpx2
from openai import OpenAI as SDKOpenAI
import pytest

from cinedata import llm
from cinedata.config import Settings
from cinedata.exceptions import (
    InvalidModelResponseError,
    LLMAuthenticationError,
    LLMRateLimitError,
    LLMServiceError,
    LLMTimeoutError,
)
from cinedata.llm import OpenRouterClient
from cinedata.prompts import SQL_RESPONSE_SCHEMA


FAKE_KEY = "test-key-for-llm-only"
SYSTEM_PROMPT = "Instruções de teste."
USER_PROMPT = "Pergunta de teste."
_UNSET = object()


def completion(content: Any = "SELECT 1", reason: str = "stop") -> dict:
    return {
        "id": "test-completion",
        "object": "chat.completion",
        "created": 0,
        "model": "test/model",
        "choices": [{"index": 0, "finish_reason": reason,
                     "message": {"role": "assistant", "content": content}}],
    }


@pytest.fixture
def make_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> Iterator[Callable]:
    clients = []

    def build(
        *, body: Any = _UNSET, status: int = 200,
        transport_error: type[httpx2.RequestError] | None = None,
        timeout_seconds: float = 30.0,
    ):
        requests = []
        construction = {}
        payload = completion() if body is _UNSET else body

        def respond(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            if transport_error is not None:
                raise transport_error("private-error-marker", request=request)
            if isinstance(payload, bytes):
                return httpx2.Response(
                    status, content=payload,
                    headers={"Content-Type": "application/json"},
                )
            return httpx2.Response(status, json=payload)

        def create_sdk(**kwargs):
            construction.update(kwargs)
            sdk = SDKOpenAI(
                **kwargs,
                http_client=httpx2.Client(transport=httpx2.MockTransport(respond)),
            )
            clients.append(sdk)
            return sdk

        monkeypatch.setattr(llm, "OpenAI", create_sdk)
        settings = Settings(FAKE_KEY, "openrouter/free", tmp_path / "unused.db")
        client = OpenRouterClient(settings, timeout_seconds=timeout_seconds)
        return client, requests, construction, clients[-1]

    yield build
    for client in clients:
        client.close()


def test_constructor_sets_endpoint_timeout_and_disables_retries(make_client) -> None:
    client, requests, options, sdk = make_client(timeout_seconds=12.5)
    assert options["base_url"] == "https://openrouter.ai/api/v1"
    assert options["api_key"] == FAKE_KEY
    assert options["max_retries"] == 0
    assert options["timeout"] == 12.5
    assert client.model == "openrouter/free"
    assert not sdk.is_closed()
    assert requests == []


def test_sends_one_authenticated_request_and_returns_text(make_client) -> None:
    client, requests, _, _ = make_client(body=completion("  SELECT 1;\n"))
    assert client.complete(SYSTEM_PROMPT, USER_PROMPT, max_tokens=500) == "SELECT 1;"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    body = json.loads(request.content)
    assert body["model"] == "openrouter/free"
    assert body["messages"] == [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT},
    ]
    assert body["max_tokens"] == 500
    assert body["stream"] is False
    assert "tools" not in body
    assert "response_format" not in body
    assert "provider" not in body


def test_structured_request_requires_compatible_provider_without_affecting_next_text_call(
    make_client,
) -> None:
    client, requests, _, _ = make_client(body=completion('{"sql": "SELECT 1"}'))
    client.complete(SYSTEM_PROMPT, USER_PROMPT, response_schema=SQL_RESPONSE_SCHEMA)
    client.complete(SYSTEM_PROMPT, USER_PROMPT)
    structured, plain = [json.loads(request.content) for request in requests]
    assert structured["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "response", "strict": True, "schema": {
            "type": "object", "properties": SQL_RESPONSE_SCHEMA["properties"],
            "required": ["sql"], "additionalProperties": False,
        }},
    }
    assert structured["response_format"]["json_schema"]["schema"]["properties"]["sql"]["type"] == "string"
    assert structured["provider"] == {"require_parameters": True}
    assert "response_format" not in plain and "provider" not in plain


@pytest.mark.parametrize("status", [400, 404])
def test_unsupported_structured_output_fails_without_retry_as_plain_text(make_client, status):
    client, requests, _, _ = make_client(status=status, body={
        "error": {"message": "private-provider-diagnostic", "code": status},
    })
    with pytest.raises(LLMServiceError) as error:
        client.complete(SYSTEM_PROMPT, USER_PROMPT, response_schema=SQL_RESPONSE_SCHEMA)
    assert error.value.status_code == status
    assert len(requests) == 1
    assert "private-provider-diagnostic" not in str(error.value)
    if status == 404:
        assert "JSON Schema" in str(error.value)


def test_invalid_response_schema_fails_before_api_call(make_client):
    client, requests, _, _ = make_client()
    with pytest.raises(ValueError, match="response_schema"):
        client.complete(SYSTEM_PROMPT, USER_PROMPT, response_schema="not-a-schema")
    assert requests == []


@pytest.mark.parametrize(
    "status, expected_error, message",
    [(401, LLMAuthenticationError, "chave"),
     (403, LLMAuthenticationError, "acesso"),
     (429, LLMRateLimitError, "limite"),
     (402, LLMServiceError, "créditos"),
     (404, LLMServiceError, "OPENROUTER_MODEL"),
     (400, LLMServiceError, "parâmetros"),
     (422, LLMServiceError, "parâmetros"),
     (500, LLMServiceError, "indisponível"),
     (503, LLMServiceError, "indisponível")],
)
def test_http_errors_are_friendly_and_never_retried(
    make_client, caplog: pytest.LogCaptureFixture, status: int,
    expected_error: type[LLMServiceError], message: str,
) -> None:
    body = {"error": {"message": f"private-error-marker {FAKE_KEY}", "code": status}}
    client, requests, _, _ = make_client(body=body, status=status)
    with caplog.at_level("INFO", logger="cinedata.llm"):
        with pytest.raises(expected_error) as error:
            client.complete(SYSTEM_PROMPT, USER_PROMPT)
    assert len(requests) == 1
    assert error.value.status_code == status
    assert message in str(error.value)
    assert "private-error-marker" not in str(error.value)
    assert FAKE_KEY not in str(error.value)
    assert FAKE_KEY not in caplog.text
    assert "private-error-marker" not in caplog.text


@pytest.mark.parametrize(
    "transport_error, expected_error",
    [(httpx2.ReadTimeout, LLMTimeoutError),
     (httpx2.ConnectTimeout, LLMTimeoutError),
     (httpx2.ConnectError, LLMServiceError)],
)
def test_network_errors_are_translated_without_retry(
    make_client, transport_error, expected_error,
) -> None:
    client, requests, _, _ = make_client(transport_error=transport_error)
    with pytest.raises(expected_error) as error:
        client.complete(SYSTEM_PROMPT, USER_PROMPT)
    assert len(requests) == 1
    assert "private-error-marker" not in str(error.value)


@pytest.mark.parametrize("content", [None, "", " \n", 123, ["texto"]])
def test_rejects_empty_or_non_text_content(make_client, content: Any) -> None:
    client, requests, _, _ = make_client(body=completion(content))
    with pytest.raises(InvalidModelResponseError):
        client.complete(SYSTEM_PROMPT, USER_PROMPT)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "reason", ["length", "content_filter", "tool_calls", "function_call", None],
)
def test_rejects_unfinished_or_non_text_completions(make_client, reason) -> None:
    client, _, _, _ = make_client(body=completion("Texto parcial", reason))
    with pytest.raises(InvalidModelResponseError):
        client.complete(SYSTEM_PROMPT, USER_PROMPT)


@pytest.mark.parametrize(
    "body", [{}, {"choices": []}, {"choices": None}, {"choices": [{}]},
             {"choices": [None]}, {"choices": [completion()["choices"][0]] * 2}],
)
def test_rejects_incomplete_or_ambiguous_payloads(make_client, body: dict) -> None:
    client, _, _, _ = make_client(body=body)
    with pytest.raises(InvalidModelResponseError):
        client.complete(SYSTEM_PROMPT, USER_PROMPT)


def test_rejects_malformed_json(make_client) -> None:
    client, requests, _, _ = make_client(body=b"{not valid JSON")
    with pytest.raises(InvalidModelResponseError):
        client.complete(SYSTEM_PROMPT, USER_PROMPT)
    assert len(requests) == 1


@pytest.mark.parametrize("code", [429, 503, "429", None])
def test_handles_error_object_even_with_http_200(make_client, code) -> None:
    client, requests, _, _ = make_client(
        body={"error": {"code": code, "message": "private-error-marker"}},
    )
    expected = LLMRateLimitError if code == 429 else LLMServiceError
    with pytest.raises(expected) as error:
        client.complete(SYSTEM_PROMPT, USER_PROMPT)
    assert len(requests) == 1
    assert "private-error-marker" not in str(error.value)


@pytest.mark.parametrize(
    "extra", [{"refusal": "Recusa"},
              {"tool_calls": [{"id": "x", "type": "function",
                               "function": {"name": "x", "arguments": "{}"}}]},
              {"function_call": {"name": "x", "arguments": "{}"}}],
)
def test_rejects_tool_calls_or_refusals_despite_stop_reason(make_client, extra) -> None:
    body = completion()
    body["choices"][0]["message"].update(extra)
    client, _, _, _ = make_client(body=body)
    with pytest.raises(InvalidModelResponseError):
        client.complete(SYSTEM_PROMPT, USER_PROMPT)


@pytest.mark.parametrize(
    "content", ["x" * 50_001, "text\x00text"], ids=["oversized", "null-character"],
)
def test_rejects_oversized_or_null_character_output(make_client, content: str) -> None:
    client, _, _, _ = make_client(body=completion(content))
    with pytest.raises(InvalidModelResponseError):
        client.complete(SYSTEM_PROMPT, USER_PROMPT)


@pytest.mark.parametrize("prompt", ["", " \n", None])
def test_invalid_prompts_do_not_send_requests(make_client, prompt) -> None:
    client, requests, _, _ = make_client()
    with pytest.raises(ValueError):
        client.complete(prompt, USER_PROMPT)
    with pytest.raises(ValueError):
        client.complete(SYSTEM_PROMPT, prompt)
    assert requests == []


def test_oversized_input_is_rejected_before_request(make_client) -> None:
    client, requests, _, _ = make_client()
    with pytest.raises(ValueError, match="tamanho"):
        client.complete("x" * 100_000, "pergunta")
    assert requests == []


@pytest.mark.parametrize("tokens", [0, -1, 8193, True, 1.5, "10"])
def test_invalid_token_limits_do_not_send_requests(make_client, tokens) -> None:
    client, requests, _, _ = make_client()
    with pytest.raises(ValueError, match="max_tokens"):
        client.complete(SYSTEM_PROMPT, USER_PROMPT, max_tokens=tokens)
    assert requests == []


@pytest.mark.parametrize("timeout", [0, -1, 121, True, float("nan"), float("inf")])
def test_invalid_timeouts_are_rejected(make_client, timeout) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        make_client(timeout_seconds=timeout)


def test_context_manager_closes_client_after_failure(make_client) -> None:
    client, _, _, sdk = make_client(status=429, body={"error": {"message": "limit"}})
    with pytest.raises(LLMRateLimitError):
        with client:
            client.complete(SYSTEM_PROMPT, USER_PROMPT)
    assert sdk.is_closed()


def test_context_manager_closes_client_after_success(make_client) -> None:
    client, _, _, sdk = make_client()
    with client as entered:
        assert entered is client
        assert client.complete(SYSTEM_PROMPT, USER_PROMPT) == "SELECT 1"
    assert sdk.is_closed()


def test_close_is_idempotent_and_prevents_reuse(make_client) -> None:
    client, requests, _, sdk = make_client()
    client.close()
    client.close()
    assert sdk.is_closed()
    with pytest.raises(LLMServiceError, match="fechado"):
        client.complete(SYSTEM_PROMPT, USER_PROMPT)
    with pytest.raises(LLMServiceError, match="fechado"):
        with client:
            pytest.fail("O cliente fechado não pode ser reutilizado.")
    assert requests == []


def test_client_is_reused_without_retaining_conversation(make_client) -> None:
    client, requests, _, _ = make_client()
    client.complete(SYSTEM_PROMPT, "Primeira pergunta")
    client.complete(SYSTEM_PROMPT, "Segunda pergunta")
    assert len(requests) == 2
    second = json.loads(requests[1].content)
    assert len(second["messages"]) == 2
    assert second["messages"][1]["content"] == "Segunda pergunta"


def test_application_logs_do_not_include_key_prompts_or_output(
    make_client, caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO", logger="cinedata.llm"):
        client, _, _, _ = make_client(body=completion("private-output-marker"))
        client.complete("private-system-marker", "private-user-marker")
    assert "modelo=openrouter/free" in caplog.text
    for marker in [FAKE_KEY, "private-output-marker", "private-system-marker",
                   "private-user-marker"]:
        assert marker not in caplog.text
