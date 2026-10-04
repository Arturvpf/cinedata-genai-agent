"""Leitura de tokens para os guardrails, sem substituir o parser SQLite."""

from dataclasses import dataclass

from cinedata.exceptions import QueryBlockedError


@dataclass(frozen=True)
class SqlToken:
    kind: str
    text: str
    depth: int


def _quoted(sql: str, start: int) -> tuple[str, int]:
    delimiter = "]" if sql[start] == "[" else sql[start]
    characters = []
    position = start + 1
    while position < len(sql):
        character = sql[position]
        if character == delimiter:
            if (
                sql[start] != "["
                and position + 1 < len(sql)
                and sql[position + 1] == delimiter
            ):
                characters.append(delimiter)
                position += 2
                continue
            return "".join(characters), position + 1
        characters.append(character)
        position += 1
    raise QueryBlockedError("A consulta contém aspas ou colchetes incompletos.")


def tokenize_sql(sql: str) -> list[SqlToken]:
    """Ignore comentários e reconheça literais, identificadores e parênteses."""
    tokens = []
    position = 0
    depth = 0
    while position < len(sql):
        character = sql[position]
        if character.isspace():
            position += 1
            continue
        if sql.startswith("--", position):
            end = sql.find("\n", position + 2)
            position = len(sql) if end == -1 else end + 1
            continue
        if sql.startswith("/*", position):
            end = sql.find("*/", position + 2)
            if end == -1:
                raise QueryBlockedError("A consulta contém um comentário incompleto.")
            position = end + 2
            continue
        if character in "'\"`[":
            value, position = _quoted(sql, position)
            kind = "literal" if character == "'" else "identifier"
            tokens.append(SqlToken(kind, value, depth))
            continue
        if character.isalpha() or character == "_":
            start = position
            position += 1
            while position < len(sql) and (
                sql[position].isalnum() or sql[position] in "_$"
            ):
                position += 1
            tokens.append(SqlToken("word", sql[start:position], depth))
            continue
        if character == ")":
            depth -= 1
            if depth < 0:
                raise QueryBlockedError("A consulta contém parênteses inválidos.")
        tokens.append(SqlToken("symbol", character, depth))
        if character == "(":
            depth += 1
        position += 1
    if depth:
        raise QueryBlockedError("A consulta contém parênteses incompletos.")
    return tokens
