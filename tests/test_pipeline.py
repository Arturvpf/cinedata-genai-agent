"""Pergunta até resultado SQLite com modelo simulado e execução real protegida."""

from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pytest

from cinedata import agent as agent_module
from cinedata.agent import CineDataAgent, TextCompletionClient
from cinedata.exceptions import (
    InvalidModelResponseError,
    LLMRateLimitError,
    QueryBlockedError,
    QueryExecutionError,
    QueryLimitError,
    QueryTimeoutError,
)
from cinedata.models import AgentResult


@pytest.fixture
def pipeline_database(tmp_path: Path) -> Path:
    path = tmp_path / "pipeline.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            "CREATE TABLE dim_movies (sk_movie_id INTEGER PRIMARY KEY, titulo TEXT);"
            "INSERT INTO dim_movies VALUES "
            "(1, 'Filme A'), (2, 'Filme B'), (3, 'Filme C');"
            "CREATE TABLE dim_genres (sk_genre_id INTEGER PRIMARY KEY, "
            "nome_genero TEXT);"
            "INSERT INTO dim_genres VALUES (1, 'Drama'), (2, 'Ação');"
            "CREATE TABLE bridge_movie_genre (sk_movie_id INTEGER REFERENCES "
            "dim_movies, sk_genre_id INTEGER REFERENCES dim_genres, "
            "PRIMARY KEY(sk_movie_id, sk_genre_id));"
            "INSERT INTO bridge_movie_genre VALUES (1,1), (2,1), (3,1), (1,2);"
            "CREATE TABLE alembic_version (version_num TEXT);"
        )
    finally:
        connection.close()
    return path


@pytest.fixture
def simulated_client() -> Mock:
    client = Mock(spec=TextCompletionClient)
    client.complete.return_value = '{"sql":"SELECT COUNT(*) AS total FROM dim_movies"}'
    return client


def test_returns_question_sql_and_real_database_results(
    pipeline_database: Path, simulated_client: Mock,
) -> None:
    original = pipeline_database.read_bytes()
    agent = CineDataAgent(pipeline_database, simulated_client)
    result = agent.query("Quantos filmes existem?")
    assert isinstance(result, AgentResult)
    assert result.question == "Quantos filmes existem?"
    assert result.sql == "SELECT COUNT(*) AS total FROM dim_movies"
    assert result.columns == ("total",)
    assert result.rows == ((3,),)
    assert not result.truncated
    assert 0 <= result.elapsed_seconds < 5
    simulated_client.complete.assert_called_once()
    assert pipeline_database.read_bytes() == original


def test_executes_join_and_aggregation_from_generated_sql(
    pipeline_database: Path, simulated_client: Mock,
) -> None:
    simulated_client.complete.return_value = (
        "SELECT g.nome_genero, COUNT(DISTINCT m.sk_movie_id) AS total "
        "FROM dim_movies AS m JOIN bridge_movie_genre AS b "
        "ON m.sk_movie_id = b.sk_movie_id JOIN dim_genres AS g "
        "ON g.sk_genre_id = b.sk_genre_id GROUP BY g.sk_genre_id, g.nome_genero "
        "ORDER BY total DESC, g.nome_genero"
    )
    agent = CineDataAgent(pipeline_database, simulated_client)
    result = agent.query("Filmes por gênero")
    assert result.columns == ("nome_genero", "total")
    assert result.rows == (("Drama", 3), ("Ação", 1))


def test_marks_partial_results_using_agent_limit(
    pipeline_database: Path, simulated_client: Mock,
) -> None:
    simulated_client.complete.return_value = (
        "SELECT titulo FROM dim_movies ORDER BY sk_movie_id DESC"
    )
    agent = CineDataAgent(pipeline_database, simulated_client, max_rows=2)
    result = agent.query("Filmes")
    assert result.rows == (("Filme C",), ("Filme B",))
    assert result.truncated


def test_empty_results_preserve_columns_and_question(
    pipeline_database: Path, simulated_client: Mock,
) -> None:
    simulated_client.complete.return_value = "SELECT titulo FROM dim_movies WHERE 0"
    result = CineDataAgent(pipeline_database, simulated_client).query("Nenhum filme")
    assert result.columns == ("titulo",)
    assert result.rows == ()
    assert result.question == "Nenhum filme"
    assert not result.truncated


@pytest.mark.parametrize(
    "sql", ["SELECT version_num FROM alembic_version",
            "SELECT name FROM sqlite_master",
            "SELECT COUNT(*) FROM alembic_version", "SELECT sqlite_source_id()"],
)
def test_native_authorizer_blocks_reads_outside_policy(
    pipeline_database: Path, simulated_client: Mock, sql: str,
) -> None:
    original = pipeline_database.read_bytes()
    simulated_client.complete.return_value = sql
    with pytest.raises(QueryBlockedError):
        CineDataAgent(pipeline_database, simulated_client).query("Leia o controle")
    simulated_client.complete.assert_called_once()
    assert pipeline_database.read_bytes() == original


@pytest.mark.parametrize(
    "response, error",
    [("DELETE FROM dim_movies", QueryBlockedError),
     ("SELECT 1; DROP TABLE dim_movies", QueryBlockedError),
     ('{"sql": ""}', InvalidModelResponseError)],
)
def test_rejected_generation_never_reaches_executor(
    pipeline_database: Path, simulated_client: Mock, monkeypatch: pytest.MonkeyPatch,
    response: str, error: type[Exception],
) -> None:
    execute = Mock(wraps=agent_module.execute_readonly)
    monkeypatch.setattr(agent_module, "execute_readonly", execute)
    simulated_client.complete.return_value = response
    with pytest.raises(error):
        CineDataAgent(pipeline_database, simulated_client).query("Filmes")
    execute.assert_not_called()
    simulated_client.complete.assert_called_once()


@pytest.mark.parametrize(
    "sql, detail", [("SELECT missing_column FROM dim_movies", "no such column"),
                    ("SELECT titulo FROM missing_table", "no such table"),
                    ("SELECT FROM dim_movies", "syntax error")],
)
def test_execution_errors_propagate_without_model_retry(
    pipeline_database: Path, simulated_client: Mock, sql: str, detail: str,
) -> None:
    simulated_client.complete.return_value = sql
    with pytest.raises(QueryExecutionError) as error:
        CineDataAgent(pipeline_database, simulated_client).query("Filmes")
    assert error.value.recoverable
    assert detail in error.value.sqlite_error
    simulated_client.complete.assert_called_once()


def test_query_timeout_does_not_trigger_model_retry(
    pipeline_database: Path, simulated_client: Mock,
) -> None:
    simulated_client.complete.return_value = (
        "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) "
        "SELECT SUM(n) FROM x"
    )
    agent = CineDataAgent(
        pipeline_database, simulated_client, query_timeout_seconds=0.02,
    )
    with pytest.raises(QueryTimeoutError) as error:
        agent.query("Consulta demorada")
    assert not error.value.recoverable
    simulated_client.complete.assert_called_once()


def test_result_size_limit_does_not_trigger_model_retry(
    pipeline_database: Path, simulated_client: Mock,
) -> None:
    simulated_client.complete.return_value = (
        "SELECT printf('%600000s', 'x') || printf('%600000s', 'x')"
    )
    with pytest.raises(QueryLimitError) as error:
        CineDataAgent(pipeline_database, simulated_client).query("Texto gigante")
    assert not error.value.recoverable
    simulated_client.complete.assert_called_once()


@pytest.mark.parametrize(
    "timeout", [0, -1, 61, True, "5", float("nan"), float("inf"),
                pytest.param(10**1_000, id="large-integer")],
)
def test_invalid_timeout_is_rejected_before_database_or_model_access(
    tmp_path: Path, simulated_client: Mock, timeout,
) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        CineDataAgent(
            tmp_path / "missing.db", simulated_client, query_timeout_seconds=timeout,
        )
    simulated_client.complete.assert_not_called()


def test_changed_invalid_timeout_is_rejected_before_model_call(
    pipeline_database: Path, simulated_client: Mock,
) -> None:
    agent = CineDataAgent(pipeline_database, simulated_client)
    agent.query_timeout_seconds = 0
    with pytest.raises(ValueError, match="timeout_seconds"):
        agent.query("Filmes")
    simulated_client.complete.assert_not_called()


def test_passes_matching_schema_policy_and_limits_to_executor(
    pipeline_database: Path, simulated_client: Mock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = Mock(wraps=agent_module.execute_readonly)
    monkeypatch.setattr(agent_module, "execute_readonly", execute)
    agent = CineDataAgent(
        pipeline_database, simulated_client, max_rows=20, query_timeout_seconds=2.5,
    )
    agent.query("Filmes")
    execute.assert_called_once()
    assert execute.call_args.kwargs["allowed_tables"] == agent.allowed_tables
    assert execute.call_args.kwargs["max_rows"] == 20
    assert execute.call_args.kwargs["timeout_seconds"] == 2.5


def test_model_failure_never_reaches_executor(
    pipeline_database: Path, simulated_client: Mock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = Mock(wraps=agent_module.execute_readonly)
    monkeypatch.setattr(agent_module, "execute_readonly", execute)
    simulated_client.complete.side_effect = LLMRateLimitError("Limite", status_code=429)
    with pytest.raises(LLMRateLimitError):
        CineDataAgent(pipeline_database, simulated_client).query("Filmes")
    execute.assert_not_called()
    simulated_client.complete.assert_called_once()


def test_pipeline_logs_metadata_without_question_sql_or_rows(
    pipeline_database: Path, simulated_client: Mock, caplog: pytest.LogCaptureFixture,
) -> None:
    simulated_client.complete.return_value = "SELECT titulo FROM dim_movies"
    with caplog.at_level("INFO", logger="cinedata"):
        agent = CineDataAgent(pipeline_database, simulated_client)
        agent.query("private-question-marker")
    assert "3 linhas" in caplog.text
    assert "private-question-marker" not in caplog.text
    assert "SELECT titulo" not in caplog.text
    assert "Filme A" not in caplog.text
