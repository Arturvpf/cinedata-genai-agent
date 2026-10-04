"""A proteção de escrita é testada apenas em bancos temporários."""

from pathlib import Path
import sqlite3

import pytest

from cinedata.database import readonly_connection
from cinedata.exceptions import DatabaseConnectionError, DatabaseNotFoundError


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    # Espaços, Unicode, # e % precisam ser preservados na URI SQLite.
    path = tmp_path / "catálogo #100%.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE movies (id INTEGER PRIMARY KEY, title TEXT)")
        connection.execute("INSERT INTO movies VALUES (?, ?)", (1, "Filme de teste"))
        connection.commit()
    finally:
        connection.close()
    return path


def test_reads_existing_database_with_special_path(database_path: Path) -> None:
    with readonly_connection(database_path) as connection:
        row = connection.execute("SELECT id, title FROM movies").fetchone()
        assert row["id"] == 1
        assert row["title"] == "Filme de teste"
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        assert connection.execute("PRAGMA trusted_schema").fetchone()[0] == 0


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO movies VALUES (2, 'Outro filme')",
        "UPDATE movies SET title = 'Alterado' WHERE id = 1",
        "DELETE FROM movies",
        "DROP TABLE movies",
        "ALTER TABLE movies ADD COLUMN year INTEGER",
        "CREATE TABLE other (id INTEGER)",
        "REPLACE INTO movies VALUES (1, 'Substituído')",
    ],
)
def test_blocks_writes_without_changing_file(database_path: Path, sql: str) -> None:
    original = database_path.read_bytes()
    with readonly_connection(database_path) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute(sql)
    assert database_path.read_bytes() == original


def test_file_remains_readonly_if_query_only_is_disabled(database_path: Path) -> None:
    with readonly_connection(database_path) as connection:
        connection.execute("PRAGMA query_only = OFF")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM movies")
        assert connection.execute("SELECT COUNT(*) FROM movies").fetchone()[0] == 1


def test_missing_database_is_not_created(tmp_path: Path) -> None:
    path = tmp_path / "missing.db"
    with pytest.raises(DatabaseNotFoundError, match="não encontrado"):
        with readonly_connection(path):
            pytest.fail("Um arquivo inexistente não deve ser aberto.")
    assert not path.exists()


def test_directory_is_not_accepted(tmp_path: Path) -> None:
    with pytest.raises(DatabaseNotFoundError):
        with readonly_connection(tmp_path):
            pytest.fail("Um diretório não é um banco SQLite.")


def test_invalid_file_has_friendly_error(tmp_path: Path) -> None:
    path = tmp_path / "invalid.db"
    path.write_text("Este arquivo não é SQLite.", encoding="utf-8")
    with pytest.raises(DatabaseConnectionError, match="arquivo é válido") as error:
        with readonly_connection(path):
            pytest.fail("Um arquivo inválido não deve ser aberto.")
    assert isinstance(error.value.__cause__, sqlite3.DatabaseError)


def test_connection_is_closed_after_use(database_path: Path) -> None:
    with readonly_connection(database_path) as connection:
        connection.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_connection_is_closed_when_query_fails(database_path: Path) -> None:
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        with readonly_connection(database_path) as connection:
            connection.execute("SELECT title FROM nonexistent")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
