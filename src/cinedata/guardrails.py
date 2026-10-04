"""Extração controlada do SQL retornado pelo modelo."""

import json
import re

from cinedata.exceptions import InvalidModelResponseError


MAX_SQL_RESPONSE_CHARS = 20_000
_MARKDOWN_BLOCK = re.compile(
    r"\A```([^\r\n`]*)\r?\n([\s\S]*?)\r?\n[ \t]*```\Z"
)
_FENCE_LINE = re.compile(r"(?m)^[ \t]*```")


def _unwrap_markdown(text: str) -> tuple[str, str]:
    """Remova um bloco externo completo, sem reescrever seu conteúdo."""
    text = text.strip().removeprefix("\ufeff").strip()
    if not text.startswith("```"):
        if _FENCE_LINE.search(text):
            raise InvalidModelResponseError(
                "O modelo retornou texto adicional junto ao bloco SQL."
            )
        return text, ""

    match = _MARKDOWN_BLOCK.fullmatch(text)
    if match is None:
        raise InvalidModelResponseError("O bloco Markdown do modelo está incompleto.")
    language = match[1].strip().lower()
    if language not in {"", "sql", "json"}:
        raise InvalidModelResponseError("O bloco do modelo deve conter SQL ou JSON.")
    if _FENCE_LINE.search(match[2]):
        raise InvalidModelResponseError("O modelo retornou múltiplos blocos Markdown.")
    return match[2].strip(), language


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Rejeite chaves JSON duplicadas em vez de escolher uma silenciosamente."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidModelResponseError("A resposta JSON contém chaves duplicadas.")
        result[key] = value
    return result


def _sql_from_json(text: str) -> str:
    try:
        payload = json.loads(text, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise InvalidModelResponseError("O modelo retornou JSON inválido.") from exc
    if not isinstance(payload, dict) or set(payload) != {"sql"}:
        raise InvalidModelResponseError(
            'A resposta JSON deve conter somente o campo "sql".'
        )
    if not isinstance(payload["sql"], str):
        raise InvalidModelResponseError('O campo "sql" deve conter texto.')
    return payload["sql"]


def sanitize_sql(response: str) -> str:
    """Extraia SQL puro, JSON {"sql": "..."} ou um bloco Markdown completo.

    Preserva comentários, literais e todas as statements. Esta função não
    determina se a consulta é permitida e nunca a executa. A validação de
    somente leitura deve ocorrer antes de qualquer execução.
    """
    if not isinstance(response, str):
        raise InvalidModelResponseError("A resposta SQL do modelo deve ser texto.")
    if len(response) > MAX_SQL_RESPONSE_CHARS:
        raise InvalidModelResponseError("A resposta SQL do modelo excedeu o limite.")

    sql, language = _unwrap_markdown(response)
    if language == "json" or sql.startswith(("{", "[", '"')):
        sql, nested_language = _unwrap_markdown(_sql_from_json(sql))
        if nested_language == "json":
            raise InvalidModelResponseError('O campo "sql" deve conter SQL, não JSON.')
    if not sql:
        raise InvalidModelResponseError("O modelo retornou uma consulta vazia.")
    if "\x00" in sql:
        raise InvalidModelResponseError("A consulta contém um caractere nulo inválido.")
    return sql
