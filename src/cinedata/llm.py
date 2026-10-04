"""Cliente OpenRouter reutilizável para SQL e respostas em linguagem natural."""

import logging
import math
from types import TracebackType

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
)

from cinedata.config import Settings
from cinedata.exceptions import (
    InvalidModelResponseError,
    LLMAuthenticationError,
    LLMRateLimitError,
    LLMServiceError,
    LLMTimeoutError,
)


logger = logging.getLogger(__name__)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_TOKENS = 2_048
MAX_INPUT_CHARS = 100_000
MAX_OUTPUT_CHARS = 50_000


def _http_error(status_code: int | None) -> LLMServiceError:
    if status_code in {401, 403}:
        return LLMAuthenticationError(
            "O OpenRouter recusou a chave ou o acesso ao modelo. "
            "Confira OPENROUTER_API_KEY e as permissões da conta.",
            status_code=status_code,
        )
    if status_code == 429:
        return LLMRateLimitError(
            "O OpenRouter informou limite de requisições ou capacidade. "
            "Aguarde antes de tentar novamente ou escolha outro modelo.",
            status_code=status_code,
        )
    if status_code == 402:
        message = "O OpenRouter informou créditos insuficientes para esse modelo."
    elif status_code == 404:
        message = "O modelo não foi encontrado. Confira OPENROUTER_MODEL."
    elif status_code in {400, 422}:
        message = "O OpenRouter recusou os parâmetros ou o conteúdo da requisição."
    elif status_code is not None and status_code >= 500:
        message = "O OpenRouter ou o provedor do modelo está indisponível."
    else:
        message = "O OpenRouter informou uma falha ao processar a requisição."
    return LLMServiceError(message, status_code=status_code)


def _response_text(response: object) -> str:
    error = getattr(response, "error", None)
    if error is not None:
        code = error.get("code") if isinstance(error, dict) else None
        raise _http_error(code if type(code) is int else None)
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or len(choices) != 1:
        raise InvalidModelResponseError("O modelo não retornou uma resposta única.")
    choice = choices[0]
    reason = getattr(choice, "finish_reason", None)
    if reason == "length":
        raise InvalidModelResponseError(
            "A resposta do modelo foi interrompida pelo limite de tokens. "
            "Não é possível utilizar uma resposta incompleta."
        )
    if reason != "stop":
        raise InvalidModelResponseError("O modelo não concluiu uma resposta textual.")
    message = getattr(choice, "message", None)
    if (
        getattr(message, "refusal", None)
        or getattr(message, "tool_calls", None)
        or getattr(message, "function_call", None)
    ):
        raise InvalidModelResponseError("O modelo não retornou o texto solicitado.")
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise InvalidModelResponseError(
            "O modelo retornou uma resposta vazia ou inválida."
        )
    if len(content) > MAX_OUTPUT_CHARS or "\x00" in content:
        raise InvalidModelResponseError(
            "A resposta do modelo excede os limites de texto."
        )
    return content.strip()


class OpenRouterClient:
    """Uma chamada por complete, sem retries automáticos ou chamadas ao iniciar."""

    def __init__(
        self, settings: Settings, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 120
        ):
            raise ValueError("timeout_seconds deve estar entre 0 e 120 segundos.")
        self.model = settings.model
        self._closed = False
        self._client = OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=settings.api_key,
            timeout=timeout_seconds,
            max_retries=0,
        )
        logger.info("Cliente OpenRouter iniciado; modelo=%s.", self.model)

    def complete(
        self, system_prompt: str, user_prompt: str, *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> str:
        """Envie duas mensagens de texto e exija uma resposta completa."""
        if self._closed:
            raise LLMServiceError("O cliente OpenRouter já foi fechado.")
        if any(
            not isinstance(prompt, str) or not prompt.strip()
            for prompt in (system_prompt, user_prompt)
        ):
            raise ValueError("Os prompts devem conter texto não vazio.")
        if len(system_prompt) + len(user_prompt) > MAX_INPUT_CHARS:
            raise ValueError("Os prompts excedem o limite de tamanho permitido.")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 8_192:
            raise ValueError("max_tokens deve ser inteiro entre 1 e 8192.")
        logger.info("Enviando requisição ao OpenRouter.")
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                stream=False,
            )
        except APITimeoutError as exc:
            logger.warning("Timeout na chamada ao OpenRouter.")
            raise LLMTimeoutError("O OpenRouter excedeu o timeout de rede.") from exc
        except APIConnectionError as exc:
            logger.warning("Falha de conexão com o OpenRouter.")
            raise LLMServiceError(
                "Não foi possível conectar ao OpenRouter. Verifique sua conexão."
            ) from exc
        except APIStatusError as exc:
            logger.warning("OpenRouter retornou erro HTTP %d.", exc.status_code)
            raise _http_error(exc.status_code) from exc
        except APIError as exc:
            logger.warning("Falha na resposta da API OpenRouter.")
            raise LLMServiceError(
                "Não foi possível ler a resposta do OpenRouter."
            ) from exc
        except (ValueError, TypeError) as exc:
            raise InvalidModelResponseError(
                "O OpenRouter retornou uma resposta que não pôde ser interpretada."
            ) from exc
        text = _response_text(response)
        logger.info("Resposta textual recebida do OpenRouter.")
        return text

    def close(self) -> None:
        if not self._closed:
            self._client.close()
            self._closed = True

    def __enter__(self) -> "OpenRouterClient":
        if self._closed:
            raise LLMServiceError("O cliente OpenRouter já foi fechado.")
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
