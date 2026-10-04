"""Testes da introspecção usando bancos SQLite temporários."""

from pathlib import Path
import sqlite3

import pytest

from cinedata.database import readonly_connection
from cinedata.exceptions import SchemaInspectionError
from cinedata.schema import list_tables


def test_lists_real_tables_in_order_and_excludes_internal_objects(
    tmp_path: Path,
) -> None:
    path = tmp_path / "schema.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            '''
            CREATE TABLE zeta (id INTEGER PRIMARY KEY AUTOINCREMENT);
            CREATE TABLE alpha (id INTEGER);
            CREATE TABLE alembic_version (version_num TEXT);
            CREATE TABLE sqliteX_custom (id INTEGER);
            CREATE TABLE "filmes especiais" (titulo TEXT);
            CREATE INDEX alpha_index ON alpha (id);
            CREATE VIEW alpha_view AS SELECT id FROM alpha;
            CREATE TRIGGER alpha_trigger AFTER INSERT ON alpha
                BEGIN INSERT INTO zeta (id) VALUES (NEW.id); END;
            '''
        )
        connection.commit()
    finally:
        connection.close()

    with readonly_connection(path) as connection:
        assert list_tables(connection) == [
            "alembic_version",
            "alpha",
            "filmes especiais",
            "sqliteX_custom",
            "zeta",
        ]
        # A introspecção não fecha a conexão recebida.
        assert connection.execute("SELECT 1").fetchone()[0] == 1


def test_only_lists_tables_from_main_database() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE movies (id INTEGER)")
        connection.execute("CREATE TEMP TABLE session_data (id INTEGER)")
        connection.execute("ATTACH DATABASE ':memory:' AS other")
        connection.execute("CREATE TABLE other.external_data (id INTEGER)")
        assert list_tables(connection) == ["movies"]
    finally:
        connection.close()


def test_empty_database_returns_empty_list(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    with readonly_connection(path) as connection:
        assert list_tables(connection) == []


def test_closed_connection_has_friendly_error() -> None:
    connection = sqlite3.connect(":memory:")
    connection.close()
    with pytest.raises(SchemaInspectionError, match="listar as tabelas") as error:
        list_tables(connection)
    assert isinstance(error.value.__cause__, sqlite3.ProgrammingError)
