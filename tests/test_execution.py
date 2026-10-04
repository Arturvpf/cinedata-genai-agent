"""Execução protegida, limites e erros sem depender de API ou do banco real."""

from contextlib import contextmanager
from itertools import count
from pathlib import Path
import sqlite3

import pytest

from cinedata import database
from cinedata.database import execute_readonly
from cinedata.exceptions import (
    DatabaseNotFoundError,
    QueryBlockedError,
    QueryExecutionError,
    QueryLimitError,
    QueryTimeoutError,
)


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "filmes.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            "CREATE TABLE movies (id INTEGER PRIMARY KEY, title TEXT, score REAL);"
            "INSERT INTO movies VALUES (1, 'Primeiro', 8.5);"
            "INSERT INTO movies VALUES (2, 'Segundo', NULL);"
            "INSERT INTO movies VALUES (3, 'Terceiro', 7.0);"
            "CREATE TABLE private_table (secret TEXT);"
            "INSERT INTO private_table VALUES ('private-marker');"
        )
    finally:
        connection.close()
    return path


def execute(database_path: Path, sql: str, **options):
    return execute_readonly(database_path, sql, allowed_tables={"movies"}, **options)


def test_returns_typed_rows_columns_sql_and_elapsed(database_path: Path) -> None:
    sql = "SELECT id, title, score FROM movies ORDER BY id"
    result = execute(database_path, sql)
    assert result.sql == sql
    assert result.columns == ("id", "title", "score")
    assert result.rows == (
        (1, "Primeiro", 8.5), (2, "Segundo", None), (3, "Terceiro", 7.0),
    )
    assert not result.truncated
    assert 0 <= result.elapsed_seconds < 5


@pytest.mark.parametrize(
    "max_rows, expected_ids, truncated",
    [(1, (3,), True), (2, (3, 2), True), (3, (3, 2, 1), False),
     (4, (3, 2, 1), False)],
)
def test_row_cap_preserves_order_and_reports_partial_results(
    database_path: Path, max_rows: int, expected_ids: tuple, truncated: bool,
) -> None:
    result = execute(
        database_path, "SELECT id FROM movies ORDER BY id DESC; -- sem reescrita",
        max_rows=max_rows,
    )
    assert result.rows == tuple((item,) for item in expected_ids)
    assert result.truncated == truncated
    assert result.sql.endswith("; -- sem reescrita")


def test_preserves_sql_limit_and_offset(database_path: Path) -> None:
    result = execute(
        database_path, "SELECT title FROM movies ORDER BY id LIMIT 1 OFFSET 1",
        max_rows=2,
    )
    assert result.rows == (("Segundo",),)
    assert not result.truncated


def test_empty_result_retains_columns(database_path: Path) -> None:
    result = execute(database_path, "SELECT id AS filme FROM movies WHERE id < 0")
    assert result.columns == ("filme",)
    assert result.rows == ()
    assert not result.truncated


def test_cte_aggregation_and_duplicate_column_names(database_path: Path) -> None:
    result = execute(
        database_path,
        "WITH x AS (SELECT score FROM movies) "
        "SELECT COUNT(*) AS valor, AVG(score) AS valor FROM x",
    )
    assert result.columns == ("valor", "valor")
    assert result.rows == ((3, 7.75),)


def test_preserves_blob_values(database_path: Path) -> None:
    result = execute(database_path, "SELECT X'00FF' AS binario")
    assert result.rows == ((b"\x00\xff",),)


@pytest.mark.parametrize("max_rows", [0, -1, 1001, True, 2.5, "2", None])
def test_rejects_invalid_row_limit(database_path: Path, max_rows: object) -> None:
    with pytest.raises(ValueError, match="max_rows"):
        execute(database_path, "SELECT 1", max_rows=max_rows)


@pytest.mark.parametrize(
    "timeout", [0, -1, 61, True, "5", None, float("nan"), float("inf")],
)
def test_rejects_invalid_timeout(database_path: Path, timeout: object) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        execute(database_path, "SELECT 1", timeout_seconds=timeout)


@pytest.mark.parametrize(
    "sql", ["DROP TABLE movies", "DELETE FROM movies", "SELECT 1; DELETE FROM movies"],
)
def test_rejects_writes_before_opening_database(tmp_path: Path, sql: str) -> None:
    path = tmp_path / "missing.db"
    with pytest.raises(QueryBlockedError):
        execute(path, sql)
    assert not path.exists()


@pytest.mark.parametrize(
    "sql",
    ["SELECT secret FROM private_table", "SELECT COUNT(*) FROM private_table",
     "SELECT name FROM sqlite_master", "SELECT sqlite_source_id()"],
)
def test_engine_denial_becomes_blocked_error(database_path: Path, sql: str) -> None:
    original = database_path.read_bytes()
    with pytest.raises(QueryBlockedError):
        execute(database_path, sql)
    assert database_path.read_bytes() == original


@pytest.mark.parametrize(
    "sql, detail",
    [("SELECT nonexistent FROM movies", "no such column"),
     ("SELECT id FROM nonexistent", "no such table"),
     ("SELECT FROM movies", "syntax error")],
)
def test_sql_errors_keep_detail_for_one_future_correction(
    database_path: Path, sql: str, detail: str,
) -> None:
    with pytest.raises(QueryExecutionError) as error:
        execute(database_path, sql)
    assert error.value.recoverable
    assert detail in error.value.sqlite_error
    assert isinstance(error.value.__cause__, sqlite3.Error)
    assert "SQL gerado" in str(error.value)


def test_missing_database_is_not_created(tmp_path: Path) -> None:
    path = tmp_path / "missing.db"
    with pytest.raises(DatabaseNotFoundError):
        execute(path, "SELECT 1")
    assert not path.exists()


def test_interrupts_unbounded_recursive_aggregation(database_path: Path) -> None:
    with pytest.raises(QueryTimeoutError) as error:
        execute(
            database_path,
            "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) "
            "SELECT SUM(n) FROM x",
            timeout_seconds=0.02,
        )
    assert not error.value.recoverable
    # A próxima consulta usa outra conexão e não herda a interrupção.
    assert execute(database_path, "SELECT COUNT(*) FROM movies").rows == ((3,),)


def test_native_limit_rejects_giant_function_output(database_path: Path) -> None:
    with pytest.raises(QueryLimitError) as error:
        execute(
            database_path,
            "SELECT printf('%600000s', 'x') || printf('%600000s', 'x')",
        )
    assert not error.value.recoverable


def test_rejects_total_payload_over_limit(database_path: Path) -> None:
    with pytest.raises(QueryLimitError) as error:
        execute(database_path, "SELECT printf('%400000s', title) FROM movies")
    assert not error.value.recoverable


def test_payload_limit_counts_utf8_bytes(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("UPDATE movies SET title = ?", ("漢" * 200_000,))
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(QueryLimitError):
        execute(database_path, "SELECT title FROM movies")


@pytest.mark.parametrize(
    "sql, expected_error",
    [("SELECT id FROM movies", None),
     ("SELECT secret FROM private_table", QueryBlockedError),
     ("SELECT unknown_column FROM movies", QueryExecutionError),
     ("SELECT printf('%600000s', 'x') || printf('%600000s', 'x')", QueryLimitError),
     ("WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) "
      "SELECT SUM(n) FROM x", QueryTimeoutError)],
)
def test_closes_connection_on_success_and_failure(
    database_path: Path, monkeypatch: pytest.MonkeyPatch, sql: str,
    expected_error: type[Exception] | None,
) -> None:
    captured = []
    original_connection = database.readonly_connection

    @contextmanager
    def capture_connection(path):
        with original_connection(path) as connection:
            captured.append(connection)
            yield connection

    monkeypatch.setattr(database, "readonly_connection", capture_connection)
    timeout = 0.02 if expected_error is QueryTimeoutError else 5.0
    if expected_error:
        with pytest.raises(expected_error):
            execute(database_path, sql, timeout_seconds=timeout)
    else:
        execute(database_path, sql, timeout_seconds=timeout)
    assert len(captured) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        captured[0].execute("SELECT 1")


def test_logs_metadata_without_row_values(
    database_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO", logger="cinedata.database"):
        execute(database_path, "SELECT title FROM movies")
    assert "3 linhas" in caplog.text
    assert "Primeiro" not in caplog.text


def test_deadline_also_applies_when_fetching_short_queries(
    database_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SQL curto pode não atingir o intervalo do progress handler.
    clock = count(start=0, step=0.01)
    monkeypatch.setattr(database, "monotonic", lambda: next(clock))
    with pytest.raises(QueryTimeoutError):
        execute(database_path, "SELECT title FROM movies", timeout_seconds=0.015)
