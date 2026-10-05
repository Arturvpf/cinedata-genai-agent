"""SQL da sessão reduz chamadas sem reutilizar dados nem contornar o executor."""

from contextlib import closing
from datetime import date
import json
import logging
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pytest

from cinedata import agent as agent_module
from cinedata.agent import CineDataAgent, MAX_CACHED_SQL, TextCompletionClient
from cinedata.exceptions import (
    AnswerGenerationError,
    InvalidModelResponseError,
    QueryBlockedError,
    QueryExecutionError,
    QueryLimitError,
    QueryTimeoutError,
)


COUNT_SQL = "SELECT COUNT(*) FROM dim_movies"
QUESTION = "Quantos filmes existem no catalogo?"


@pytest.fixture
def cache_setup(tmp_path: Path):
    path = tmp_path / "cache.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            "CREATE TABLE dim_movies (sk_movie_id INTEGER PRIMARY KEY, titulo TEXT);"
            "INSERT INTO dim_movies VALUES (1, 'Alpha'), (2, 'Beta');"
        )
    client = Mock(spec=TextCompletionClient)
    client.complete.return_value = COUNT_SQL
    return path, client, CineDataAgent(path, client)


def test_repeated_count_skips_model_and_reads_updated_database(cache_setup, caplog):
    path, client, agent = cache_setup
    first = agent.ask(QUESTION)
    client.complete.side_effect = InvalidModelResponseError("Resposta vazia.")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("INSERT INTO dim_movies VALUES (3, 'Gamma')")
        connection.commit()
    original = path.read_bytes()
    with caplog.at_level(logging.INFO, logger="cinedata"):
        second = agent.ask(QUESTION)
    assert first.answer == "Resultado da contagem: 2."
    assert second.answer == "Resultado da contagem: 3."
    assert second.rows == ((3,),)
    assert not second.correction_attempted
    client.complete.assert_called_once()
    assert path.read_bytes() == original
    assert "Reutilizando SQL validado desta sessão." in caplog.text
    for private in (QUESTION, COUNT_SQL, "Gamma"):
        assert private not in caplog.text


def test_empty_result_is_executed_again_instead_of_remembered(cache_setup):
    path, client, agent = cache_setup
    client.complete.return_value = "SELECT titulo FROM dim_movies WHERE sk_movie_id = 3"
    assert agent.query("Terceiro filme").rows == ()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("INSERT INTO dim_movies VALUES (3, 'Gamma')")
        connection.commit()
    assert agent.query("Terceiro filme").rows == (("Gamma",),)
    client.complete.assert_called_once()


def test_other_question_generates_new_sql(cache_setup):
    _, client, agent = cache_setup
    agent.query(QUESTION)
    client.complete.return_value = "SELECT titulo FROM dim_movies ORDER BY sk_movie_id"
    assert agent.query("Quais filmes?").rows == (("Alpha",), ("Beta",))
    assert client.complete.call_count == 2


def test_cache_is_not_shared_with_other_session(cache_setup):
    path, client, agent = cache_setup
    agent.query(QUESTION)
    CineDataAgent(path, client).query(QUESTION)
    assert client.complete.call_count == 2


def test_date_change_generates_sql_with_new_reference(cache_setup, monkeypatch):
    _, client, agent = cache_setup
    calendar = Mock()
    calendar.today.side_effect = [date(2026, 10, 5), date(2026, 10, 6)]
    monkeypatch.setattr(agent_module, "date", calendar)
    agent.query(QUESTION)
    agent.query(QUESTION)
    assert calendar.today.call_count == 2
    assert client.complete.call_count == 2
    assert [json.loads(call.args[1])["reference_date"]
            for call in client.complete.call_args_list] == ["2026-10-05", "2026-10-06"]


def test_row_limit_change_generates_new_sql(cache_setup):
    _, client, agent = cache_setup
    client.complete.return_value = "SELECT titulo FROM dim_movies ORDER BY sk_movie_id"
    agent.max_rows = 1
    assert agent.query("Filmes").truncated
    agent.max_rows = 2
    assert not agent.query("Filmes").truncated
    assert client.complete.call_count == 2
    assert json.loads(client.complete.call_args.args[1])["result_row_limit"] == 2


def test_cached_query_rejects_invalid_options_before_execution(cache_setup, monkeypatch):
    _, client, agent = cache_setup
    agent.max_rows = 1
    agent.query(QUESTION)
    execute = Mock(wraps=agent_module.execute_readonly)
    monkeypatch.setattr(agent_module, "execute_readonly", execute)
    agent.max_rows = True  # True compares equal to 1 but must not bypass validation.
    with pytest.raises(ValueError):
        agent.query(QUESTION)
    execute.assert_not_called()
    client.complete.assert_called_once()


def test_cache_evicts_least_recently_used_sql(cache_setup):
    _, client, agent = cache_setup
    for index in range(MAX_CACHED_SQL):
        agent.query(f"Pergunta {index}")
    agent.query("Pergunta 0")  # Preserve this entry when adding a new one.
    agent.query("Pergunta extra")
    agent.query("Pergunta 0")
    assert client.complete.call_count == MAX_CACHED_SQL + 1
    agent.query("Pergunta 1")
    assert client.complete.call_count == MAX_CACHED_SQL + 2


def test_clear_cache_requests_generation_again(cache_setup):
    _, client, agent = cache_setup
    agent.query(QUESTION)
    agent.clear_sql_cache()
    agent.query(QUESTION)
    assert client.complete.call_count == 2


def test_standalone_generation_does_not_populate_query_cache(cache_setup):
    _, client, agent = cache_setup
    assert agent.generate_sql(QUESTION) == COUNT_SQL
    agent.query(QUESTION)
    assert agent.generate_sql(QUESTION) == COUNT_SQL
    assert client.complete.call_count == 3


def test_invalid_generation_is_not_cached(cache_setup):
    _, client, agent = cache_setup
    client.complete.return_value = '{"sql": ""}'
    with pytest.raises(InvalidModelResponseError):
        agent.query(QUESTION)
    client.complete.return_value = COUNT_SQL
    assert agent.query(QUESTION).rows == ((2,),)
    assert client.complete.call_count == 2


def test_failed_sql_and_failed_correction_are_not_cached(cache_setup):
    _, client, agent = cache_setup
    client.complete.side_effect = [
        "SELECT missing FROM dim_movies", "SELECT missing FROM dim_movies", COUNT_SQL,
    ]
    with pytest.raises(QueryExecutionError):
        agent.query(QUESTION)
    assert agent.query(QUESTION).rows == ((2,),)
    assert client.complete.call_count == 3


def test_successful_correction_is_remembered_as_final_sql(cache_setup):
    _, client, agent = cache_setup
    client.complete.side_effect = ["SELECT missing FROM dim_movies", COUNT_SQL]
    assert agent.query(QUESTION).correction_attempted
    repeated = agent.query(QUESTION)
    assert repeated.sql == COUNT_SQL
    assert not repeated.correction_attempted
    assert client.complete.call_count == 2


@pytest.mark.parametrize("error", [
    QueryBlockedError("Bloqueada."), QueryTimeoutError("Tempo excedido."),
    QueryLimitError("Limite excedido."),
])
def test_cached_sql_still_uses_executor_and_evicts_on_failure(
    cache_setup, monkeypatch, error,
):
    _, client, agent = cache_setup
    agent.query(QUESTION)
    real_execute = agent_module.execute_readonly
    execute = Mock(side_effect=error)
    monkeypatch.setattr(agent_module, "execute_readonly", execute)
    with pytest.raises(type(error)):
        agent.query(QUESTION)
    execute.assert_called_once()
    client.complete.assert_called_once()  # Security/resource errors cannot cause correction.
    monkeypatch.setattr(agent_module, "execute_readonly", real_execute)
    assert agent.query(QUESTION).rows == ((2,),)
    assert client.complete.call_count == 2


def test_cache_reuses_sql_but_retries_redaction_only_on_new_user_question(cache_setup):
    _, client, agent = cache_setup
    client.complete.side_effect = [
        "SELECT titulo FROM dim_movies ORDER BY sk_movie_id",
        InvalidModelResponseError("Resposta vazia."),
        "Os filmes são Alpha e Beta.",
    ]
    with pytest.raises(AnswerGenerationError):
        agent.ask("Quais filmes?")
    assert client.complete.call_count == 2
    assert agent.ask("Quais filmes?").answer == "Os filmes são Alpha e Beta."
    assert client.complete.call_count == 3
