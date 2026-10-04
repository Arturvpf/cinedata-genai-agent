"""Extração de SQL sem executar as respostas do modelo."""

import json

import pytest

from cinedata.exceptions import InvalidModelResponseError
from cinedata.guardrails import MAX_SQL_RESPONSE_CHARS, sanitize_sql


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
