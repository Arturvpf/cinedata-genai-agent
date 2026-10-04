"""Extração, validação de leitura e autorização de operações SQLite."""

from collections.abc import Collection
from contextlib import closing
import json
import re
import sqlite3

from cinedata._sql_lexer import tokenize_sql
from cinedata.exceptions import InvalidModelResponseError, QueryBlockedError


MAX_SQL_RESPONSE_CHARS = 20_000
_MARKDOWN_BLOCK = re.compile(
    r"\A```([^\r\n`]*)\r?\n([\s\S]*?)\r?\n[ \t]*```\Z"
)
_FENCE_LINE = re.compile(r"(?m)^[ \t]*```")
_WRITE_COMMANDS = frozenset({
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE",
    "ATTACH", "DETACH", "VACUUM", "REINDEX", "TRUNCATE", "PRAGMA", "ANALYZE",
})
_FORBIDDEN_FUNCTIONS = frozenset({
    "load_extension", "readfile", "writefile", "eval", "fts3_tokenizer",
    "randomblob", "zeroblob",
})
_READ_FUNCTIONS = frozenset({
    "abs", "avg", "char", "coalesce", "concat", "concat_ws", "count",
    "cume_dist", "date", "datetime", "dense_rank", "first_value", "format",
    "glob", "group_concat", "hex", "ifnull", "iif", "instr", "julianday",
    "lag", "last_value", "lead", "length", "like", "lower", "ltrim", "max",
    "min", "nth_value", "ntile", "nullif", "percent_rank", "printf", "quote",
    "rank", "replace", "round", "row_number", "rtrim", "sqlite_version",
    "strftime", "substr", "substring", "sum", "time", "timediff", "total",
    "trim", "typeof", "unicode", "unixepoch", "upper",
})
_ASCII_LOWER = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"
)


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


def validate_sql(sql: str) -> str:
    """Exija uma única instrução SELECT ou WITH de leitura.

    A análise distingue comentários, strings e identificadores entre aspas.
    A sintaxe completa e as permissões são verificadas pelo SQLite, com o
    autorizador instalado, antes de executar a consulta.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise QueryBlockedError("A consulta SQL está vazia ou não contém texto.")
    if len(sql) > MAX_SQL_RESPONSE_CHARS or "\x00" in sql:
        raise QueryBlockedError(
            "A consulta excede o limite ou contém caractere inválido."
        )
    sql = sql.strip()
    tokens = tokenize_sql(sql)
    if not tokens or tokens[0].kind != "word" or tokens[0].text.upper() not in {
        "SELECT", "WITH"
    }:
        raise QueryBlockedError(
            "Apenas consultas SELECT ou WITH de leitura são permitidas."
        )

    for index, token in enumerate(tokens):
        if token.kind == "symbol" and token.text == ";":
            if index != len(tokens) - 1 or token.depth != 0:
                raise QueryBlockedError("Múltiplas instruções SQL não são permitidas.")
        is_function = index + 1 < len(tokens) and tokens[index + 1].text == "("
        if token.kind == "word" and token.text.upper() in _WRITE_COMMANDS:
            if not (token.text.upper() == "REPLACE" and is_function):
                raise QueryBlockedError(
                    "A consulta contém um comando de escrita ou configuração."
                )
        if token.kind != "symbol" and is_function:
            name = token.text.translate(_ASCII_LOWER)
            if name in _FORBIDDEN_FUNCTIONS or name.startswith("pragma_"):
                raise QueryBlockedError("A consulta usa uma função SQL não permitida.")

    if tokens[0].text.upper() == "WITH" and not any(
        token.kind == "word" and token.depth == 0
        and token.text.upper() in {"SELECT", "VALUES"}
        for token in tokens[1:]
    ):
        raise QueryBlockedError("O WITH deve terminar em uma consulta de leitura.")
    return sql


class ReadOnlyAuthorizer:
    """Política de permissão para uma conexão nova, com apenas o banco main."""

    def __init__(self, allowed_tables: Collection[str]) -> None:
        self.allowed_tables = frozenset(
            name.translate(_ASCII_LOWER) for name in allowed_tables
        )
        self.denied_reason: str | None = None

    def __call__(
        self, action: int, first: str | None, second: str | None,
        database: str | None, source: str | None,
    ) -> int:
        if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            # COUNT(*) pode informar database=None e nome de coluna vazio.
            main_read = database == "main" or (database is None and second == "")
            if (
                main_read
                and (first or "").translate(_ASCII_LOWER) in self.allowed_tables
            ):
                return sqlite3.SQLITE_OK
            self.denied_reason = (
                "A consulta acessa uma tabela fora do esquema permitido."
            )
        elif action == sqlite3.SQLITE_FUNCTION:
            if (second or "").translate(_ASCII_LOWER) in _READ_FUNCTIONS:
                return sqlite3.SQLITE_OK
            self.denied_reason = "A consulta usa uma função SQL não permitida."
        else:
            self.denied_reason = (
                "A consulta tenta escrever ou alterar a configuração SQLite."
            )
        return sqlite3.SQLITE_DENY


def install_readonly_authorizer(
    connection: sqlite3.Connection, allowed_tables: Collection[str],
) -> ReadOnlyAuthorizer:
    """Instale a política antes de preparar SQL gerado, em uma conexão nova.

    Rejeita conexões com bancos adicionais ou temporários para evitar leituras
    ambíguas de COUNT(*). Mantém o autorizador ativo até fechar a conexão.
    """
    try:
        with closing(connection.execute("PRAGMA database_list")) as cursor:
            databases = {row[1] for row in cursor.fetchall()}
        if databases != {"main"}:
            raise QueryBlockedError(
                "A execução protegida exige uma conexão somente com o banco principal."
            )
        authorizer = ReadOnlyAuthorizer(allowed_tables)
        connection.set_authorizer(authorizer)
    except sqlite3.Error as exc:
        raise QueryBlockedError(
            "Não foi possível configurar a proteção SQLite."
        ) from exc
    return authorizer
