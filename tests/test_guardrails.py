"""Extração de SQL sem executar as respostas do modelo."""

import json

import pytest

from cinedata.exceptions import InvalidModelResponseError, QueryBlockedError
from cinedata.guardrails import MAX_SQL_RESPONSE_CHARS, sanitize_sql, validate_sql


@pytest.mark.parametrize(
    "response, expected",
    [
        ("  SELECT titulo FROM dim_movies;\n", "SELECT titulo FROM dim_movies;"),
        ("```sql\nSELECT 1;\n```", "SELECT 1;"),
        ("```SQL\r\nSELECT 1;\r\n```", "SELECT 1;"),
        ("```\nSELECT 1;\n```", "SELECT 1;"),
        ('{"sql": "SELECT 1;"}', "SELECT 1;"),
        ('```json\n{"sql": "SELECT 1;"}\n```', "SELECT 1;"),
        ('{"sql": "```sql\\nSELECT 1;\\n```"}', "SELECT 1;"),
        ("\ufeff SELECT 1", "SELECT 1"),
    ],
)
def test_extracts_supported_formats(response: str, expected: str) -> None:
    assert sanitize_sql(response) == expected


def test_preserves_literals_comments_and_line_endings() -> None:
    sql = "-- comentário\r\nSELECT 'linha A\r\nlinha B; ```' AS texto;"
    assert sanitize_sql("```sql\r\n" + sql + "\r\n```") == sql
    assert sanitize_sql(json.dumps({"sql": sql})) == sql


@pytest.mark.parametrize(
    "response",
    [
        "",
        " \n\t",
        "```sql\n\n```",
        '{"sql": ""}',
        '{"sql": null}',
        '{"sql": 42}',
        '{"query": "SELECT 1"}',
        '{"sql": "SELECT 1", "explanation": "texto"}',
        '{"sql": "SELECT 1", "sql": "DROP TABLE dim_movies"}',
        '{"sql": "SELECT 1"',
        '[{"sql": "SELECT 1"}]',
        pytest.param("[" * 1200 + "0" + "]" * 1200, id="deep-json"),
        '"SELECT 1"',
        "```sql\nSELECT 1",
        "```python\nprint('SELECT 1')\n```",
        "Segue a consulta:\n```sql\nSELECT 1\n```",
        "```sql\nSELECT 1\n```\nExplicação adicional",
        "```sql\nSELECT 1\n```\n```sql\nSELECT 2\n```",
        "```json\nSELECT 1\n```",
        "SELECT '\x00'",
        '{"sql": "SELECT \\u0000"}',
    ],
)
def test_rejects_malformed_or_ambiguous_responses(response: str) -> None:
    with pytest.raises(InvalidModelResponseError):
        sanitize_sql(response)


@pytest.mark.parametrize("response", [None, {"sql": "SELECT 1"}, b"SELECT 1"])
def test_rejects_non_text_response(response: object) -> None:
    with pytest.raises(InvalidModelResponseError):
        sanitize_sql(response)


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE dim_movies",
        "DELETE FROM dim_movies",
        "SELECT titulo FROM dim_movies; DROP TABLE dim_movies;",
        "WITH x AS (SELECT 1) SELECT * FROM x;",
    ],
)
def test_extraction_keeps_all_sql_for_subsequent_validation(sql: str) -> None:
    assert sanitize_sql(sql) == sql
    assert sanitize_sql(json.dumps({"sql": sql})) == sql
    assert sanitize_sql("```sql\n" + sql + "\n```") == sql


def test_rejects_oversized_response_without_truncating_sql() -> None:
    with pytest.raises(InvalidModelResponseError, match="limite"):
        sanitize_sql("SELECT '" + "x" * MAX_SQL_RESPONSE_CHARS + "'")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT titulo FROM dim_movies",
        "select titulo from dim_movies;",
        "WITH x AS (SELECT * FROM dim_movies) SELECT * FROM x",
        "WITH x AS (SELECT 1), y AS (SELECT 2) SELECT * FROM x, y;",
        "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x "
        "WHERE n<3) SELECT MAX(n) FROM x",
        "WITH x AS (SELECT 1) VALUES (2)",
        "SELECT 'DELETE; DROP TABLE movies; -- comentário' AS texto",
        "SELECT 'O''Brien; /* UPDATE */' AS nome;",
        'SELECT "update", [delete], `drop` FROM "dim_movies";',
        "SELECT replace(titulo, ';', '') FROM dim_movies",
        'SELECT "REPLACE"(titulo, \'a\', \'b\') FROM dim_movies',
        "SELECT CASE WHEN receita_brl IS NULL THEN 0 ELSE receita_brl END "
        "FROM fact_movies_performance",
        "/* DELETE FROM movies; */ SELECT titulo FROM dim_movies; -- DROP",
        "SELECT titulo -- UPDATE;\nFROM dim_movies /* ; DROP */",
        "SELECT 1 UNION ALL SELECT 2;",
        "SELECT '(texto)' AS texto",
    ],
)
def test_allows_single_readonly_statement(sql: str) -> None:
    assert validate_sql(sql) == sql
    assert validate_sql(sanitize_sql(json.dumps({"sql": sql}))) == sql


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM dim_movies",
        "DROP TABLE dim_movies",
        "UPDATE dim_movies SET titulo = 'Alterado'",
        "INSERT INTO dim_movies (titulo) VALUES ('Outro filme')",
        "REPLACE INTO dim_movies (titulo) VALUES ('Outro filme')",
        "ALTER TABLE dim_movies ADD COLUMN other INTEGER",
        "CREATE TABLE other (id INTEGER)",
        "ATTACH DATABASE 'outside.db' AS outside",
        "DETACH DATABASE outside",
        "VACUUM",
        "REINDEX",
        "TRUNCATE TABLE dim_movies",
        "PRAGMA writable_schema = ON",
        "ANALYZE",
        "BEGIN TRANSACTION",
        "EXPLAIN SELECT titulo FROM dim_movies",
        "WITH x AS (SELECT 1) DELETE FROM dim_movies",
        "WITH x AS (SELECT 1) UPDATE dim_movies SET titulo = 'Alterado'",
        "WITH x AS (SELECT 1) INSERT INTO dim_movies (titulo) SELECT 'Novo'",
        "SELECT titulo FROM dim_movies; DROP TABLE dim_movies;",
        "SELECT 1; SELECT 2",
        "SELECT 1;;",
        "SELECT 1; /* comentário */ DELETE FROM dim_movies",
        "SELECT load_extension('outside')",
        'SELECT "load_extension"(\'outside\')',
        "SELECT `writefile`('outside', 'data')",
        "SELECT [readfile]('outside')",
        "SELECT eval('DELETE FROM dim_movies')",
        "SELECT randomblob(1000000000)",
        "SELECT zeroblob(1000000000)",
        "SELECT * FROM pragma_table_info('dim_movies')",
        'SELECT * FROM "pragma_table_info"(\'dim_movies\')',
        "SELECT 'aspas incompletas",
        "SELECT [coluna incompleta",
        "SELECT `coluna incompleta",
        'SELECT "coluna incompleta',
        "SELECT 1 /* comentário incompleto",
        "SELECT (1",
        "SELECT 1)",
        "WITH x AS (SELECT 1)",
        "-- Apenas comentário",
        "SELECT '\x00'",
        "",
    ],
)
def test_blocks_writes_multiple_statements_and_unsafe_sql(sql: str) -> None:
    with pytest.raises(QueryBlockedError):
        validate_sql(sql)


def test_validation_rejects_oversized_sql() -> None:
    with pytest.raises(QueryBlockedError):
        validate_sql("SELECT '" + "x" * MAX_SQL_RESPONSE_CHARS + "'")
