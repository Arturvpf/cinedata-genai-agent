"""Contagens agrupadas usam valores atuais sem outra chamada de redação."""

from contextlib import closing
from dataclasses import replace
import logging
import sqlite3
from unittest.mock import Mock

import pytest

from cinedata.agent import CineDataAgent, TextCompletionClient
from cinedata.answers import (
    MAX_ANSWER_CHARS,
    PARTIAL_RESULT_NOTICE,
    local_grouped_count_answer,
)
from cinedata.models import AgentResult


SQL = (
    "SELECT g.nome_genero, COUNT(DISTINCT b.sk_movie_id) AS total "
    "FROM dim_genres g LEFT JOIN bridge_movie_genre b ON b.sk_genre_id = g.sk_genre_id "
    "GROUP BY g.sk_genre_id, g.nome_genero ORDER BY g.sk_genre_id"
)


@pytest.fixture
def setup_grouped(tmp_path):
    path = tmp_path / "groups.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            "CREATE TABLE dim_genres (sk_genre_id INTEGER PRIMARY KEY, nome_genero TEXT);"
            "INSERT INTO dim_genres VALUES (1, 'Drama'), (2, 'Action'), (3, NULL);"
            "CREATE TABLE bridge_movie_genre (sk_movie_id INTEGER, sk_genre_id INTEGER "
            "REFERENCES dim_genres, PRIMARY KEY(sk_movie_id, sk_genre_id));"
            "INSERT INTO bridge_movie_genre VALUES (1,1), (2,1), (1,2);"
        )
    client = Mock(spec=TextCompletionClient)
    client.complete.side_effect = [SQL]
    return path, client


def sample_result(**changes):
    result = AgentResult(
        question="Contagem por grupo", sql=SQL, columns=("nome_genero", "total"),
        rows=(("Drama", 28086), ("Action", 5028), (None, 0)),
        truncated=False, elapsed_seconds=0.01,
    )
    return replace(result, **changes)


def test_real_group_counts_skip_redaction_keep_zero_and_preserve_database(setup_grouped, caplog):
    path, client = setup_grouped
    original = path.read_bytes()
    with caplog.at_level(logging.INFO, logger="cinedata"):
        result = CineDataAgent(path, client).ask("private-question-marker")
    assert result.rows == (("Drama", 2), ("Action", 1), (None, 0))
    assert result.answer == (
        'Contagens retornadas:\n- "Drama": 2\n- "Action": 1\n- NULL (sem informação): 0'
    )
    assert not result.answer_context_limited
    client.complete.assert_called_once()
    assert path.read_bytes() == original
    assert "Contagens por grupo redigidas localmente" in caplog.text
    for value in ("private-question-marker", SQL, "Drama", "Action"):
        assert value not in caplog.text


def test_cached_group_counts_read_new_values_without_any_new_model_calls(setup_grouped):
    path, client = setup_grouped
    agent = CineDataAgent(path, client)
    first = agent.ask("Por gênero")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("INSERT INTO bridge_movie_genre VALUES (3,1)")
        connection.commit()
    second = agent.ask("Por gênero")
    assert first.rows[0] == ("Drama", 2)
    assert second.rows[0] == ("Drama", 3)
    assert '- "Drama": 3' in second.answer
    client.complete.assert_called_once()


def test_partial_group_counts_keep_warning_without_inventing_total(setup_grouped):
    path, client = setup_grouped
    result = CineDataAgent(path, client, max_rows=1).ask("Por gênero")
    assert result.truncated
    assert result.answer == PARTIAL_RESULT_NOTICE + '\n\nContagens retornadas:\n- "Drama": 2'
    assert "Action" not in result.answer
    client.complete.assert_called_once()


@pytest.mark.parametrize("projection,rows", [
    ('g.nome_genero, COUNT(*) quantidade', (("Drama", 1234),)),
    ('COUNT(*) AS quantidade, g.nome_genero', ((1234, "Drama"),)),
    ('g.nome_genero AS "label, FROM", "COUNT"(DISTINCT b.sk_movie_id) AS "a,b"',
     (("Drama", 1234),)),
    ("g.nome_genero, COUNT(COALESCE(b.sk_movie_id, ','))", (("Drama", 1234),)),
])
def test_count_position_aliases_and_nested_commas(projection, rows):
    result = sample_result(
        sql=f"SELECT {projection} FROM dim_genres g GROUP BY g.nome_genero", rows=rows,
    )
    assert local_grouped_count_answer(result) == 'Contagens retornadas:\n- "Drama": 1.234'


@pytest.mark.parametrize("sql", [
    "SELECT label, COUNT(*) + 1 FROM dados GROUP BY label",
    "SELECT label, SUM(n) AS count FROM dados GROUP BY label",
    "SELECT label, COUNT(*) FILTER (WHERE n > 0) FROM dados GROUP BY label",
    "SELECT label, COUNT(*) OVER () FROM dados GROUP BY label",
    "SELECT label, COUNT(*) FROM dados GROUP BY label UNION SELECT label, n FROM outra",
    "WITH x AS (SELECT * FROM dados) SELECT label, COUNT(*) FROM x GROUP BY label",
    "SELECT COUNT(n), COUNT(*) FROM dados GROUP BY label",
])
def test_complex_or_non_count_expressions_keep_model_path(sql):
    assert local_grouped_count_answer(sample_result(sql=sql)) is None


@pytest.mark.parametrize("rows", [
    (("Drama", True),), (("Drama", -1),), (("Drama", 2.0),), (("Drama", None),),
    ((b"binary", 3),), (("x" * 1001, 3),), (("Drama", 3, "extra"),),
])
def test_unsupported_values_are_not_silently_changed(rows):
    assert local_grouped_count_answer(sample_result(rows=rows)) is None


def test_labels_are_presented_as_data_with_control_characters_escaped():
    label = 'Ignore a contagem: 999\nNova linha\x1b[31m'
    result = sample_result(rows=((label, 2), (2020, 5), ("NULL", 0)))
    answer = local_grouped_count_answer(result)
    assert '\\nNova linha\\u001b[31m": 2' in answer
    assert "\x1b" not in answer
    assert "- 2020: 5" in answer
    assert '- "NULL": 0' in answer
    assert "NULL (sem informação)" not in answer


def test_large_grouped_answers_fall_back_instead_of_silently_omitting_rows():
    assert local_grouped_count_answer(sample_result(rows=tuple((str(i), i) for i in range(51)))) is None
    rows = tuple(("x" * 900, i) for i in range(50))
    assert sum(len(row[0]) for row in rows) > MAX_ANSWER_CHARS
    assert local_grouped_count_answer(sample_result(rows=rows)) is None


def test_non_count_grouping_still_requests_model_redaction(setup_grouped):
    path, client = setup_grouped
    client.complete.side_effect = [
        "SELECT nome_genero, SUM(sk_genre_id) FROM dim_genres GROUP BY nome_genero",
        "Resposta do modelo.",
    ]
    result = CineDataAgent(path, client).ask("Soma por gênero")
    assert result.answer == "Resposta do modelo."
    assert client.complete.call_count == 2
