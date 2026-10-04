"""Proteção do compilador SQLite, inclusive se a validação textual for omitida."""

from collections.abc import Iterator
from pathlib import Path
import sqlite3

import pytest

from cinedata.exceptions import QueryBlockedError
from cinedata.guardrails import (
    ReadOnlyAuthorizer,
    install_readonly_authorizer,
    validate_sql,
)


@pytest.fixture
def protected_connection() -> Iterator[sqlite3.Connection]:
    # Usa SQLite gravável em memória para isolar a proteção do autorizador.
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.executescript(
        "CREATE TABLE movies (id INTEGER PRIMARY KEY, title TEXT);"
        "INSERT INTO movies VALUES (1, 'Exemplo');"
        "CREATE TABLE private_table (secret TEXT);"
        "INSERT INTO private_table VALUES ('private-marker');"
        "CREATE TABLE alembic_version (version_num TEXT);"
    )
    install_readonly_authorizer(connection, {"movies"})
    try:
        yield connection
    finally:
        connection.close()


@pytest.mark.parametrize(
    "sql, expected",
    [
        ("SELECT title FROM movies", [("Exemplo",)]),
        ("SELECT COUNT(*) FROM movies", [(1,)]),
        ("SELECT COUNT(*) FROM main.movies", [(1,)]),
        ("SELECT COUNT(*) FROM MOVIES", [(1,)]),
        ("WITH x AS (SELECT title FROM movies) SELECT title FROM x", [("Exemplo",)]),
        ("SELECT REPLACE(title, 'Ex', 'ex') FROM movies", [("exemplo",)]),
        ("SELECT 'DROP TABLE movies; UPDATE;'", [("DROP TABLE movies; UPDATE;",)]),
        ("WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x "
         "WHERE n<3) SELECT MAX(n) FROM x", [(3,)]),
        ("SELECT ROW_NUMBER() OVER (ORDER BY id) FROM movies", [(1,)]),
    ],
)
def test_allows_reads_aggregations_and_ctes(
    protected_connection: sqlite3.Connection, sql: str, expected: list[tuple],
) -> None:
    assert protected_connection.execute(validate_sql(sql)).fetchall() == expected


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM movies",
        "UPDATE movies SET title = 'Alterado'",
        "INSERT INTO movies VALUES (2, 'Outro')",
        "REPLACE INTO movies VALUES (1, 'Outro')",
        "DROP TABLE movies",
        "ALTER TABLE movies ADD COLUMN year INTEGER",
        "CREATE TABLE other (id INTEGER)",
        "CREATE TEMP TABLE other (id INTEGER)",
        "WITH x AS (SELECT 1) DELETE FROM movies",
        "PRAGMA query_only = OFF",
        "PRAGMA writable_schema = ON",
        "SELECT secret FROM private_table",
        "SELECT COUNT(*) FROM private_table",
        "SELECT name FROM sqlite_master",
        "SELECT version_num FROM alembic_version",
    ],
)
def test_engine_denies_operations_without_text_validation(
    protected_connection: sqlite3.Connection, sql: str,
) -> None:
    with pytest.raises(sqlite3.DatabaseError):
        protected_connection.execute(sql)
    count = protected_connection.execute("SELECT COUNT(*) FROM movies").fetchone()
    title = protected_connection.execute("SELECT title FROM movies").fetchone()
    assert count[0] == 1
    assert title[0] == "Exemplo"


def test_attach_is_denied_before_creating_a_file(
    protected_connection: sqlite3.Connection, tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.db"
    with pytest.raises(sqlite3.DatabaseError):
        protected_connection.execute("ATTACH DATABASE ? AS outside", (str(outside),))
    assert not outside.exists()


@pytest.mark.parametrize(
    "name", ["writefile", "readfile", "load_extension", "unknown_fn"]
)
def test_unknown_or_dangerous_function_is_never_called(name: str) -> None:
    calls = []
    connection = sqlite3.connect(":memory:")
    try:
        connection.create_function(name, 0, lambda: calls.append(name) or 1)
        authorizer = install_readonly_authorizer(connection, ())
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(f'SELECT "{name}"()')
        assert not calls
        assert authorizer.denied_reason is not None
    finally:
        connection.close()


@pytest.mark.parametrize("setup", ["CREATE TEMP TABLE movies (id INTEGER)",
                                  "ATTACH DATABASE ':memory:' AS other"])
def test_requires_a_connection_without_temp_or_attached_databases(setup: str) -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(setup)
        with pytest.raises(QueryBlockedError, match="banco principal"):
            install_readonly_authorizer(connection, {"movies"})
    finally:
        connection.close()


def test_does_not_merge_distinct_unicode_table_names() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute('CREATE TABLE "Å" (id INTEGER)')
        connection.execute('CREATE TABLE "å" (id INTEGER)')
        install_readonly_authorizer(connection, {"Å"})
        assert connection.execute('SELECT COUNT(*) FROM "Å"').fetchone()[0] == 0
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute('SELECT COUNT(*) FROM "å"')
    finally:
        connection.close()


def test_unknown_authorizer_action_is_denied() -> None:
    authorizer = ReadOnlyAuthorizer({"movies"})
    assert authorizer(99999, None, None, None, None) == sqlite3.SQLITE_DENY
    assert authorizer.denied_reason is not None
