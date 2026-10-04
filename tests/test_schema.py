"""Testes da introspecção usando bancos SQLite temporários."""

from pathlib import Path
import sqlite3

import pytest

from cinedata.database import readonly_connection
from cinedata.exceptions import SchemaInspectionError
from cinedata.schema import inspect_schema, inspect_table, list_tables


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


@pytest.fixture
def metadata_database(tmp_path: Path) -> Path:
    path = tmp_path / "metadata.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            '''
            CREATE TABLE parent (
                first INTEGER,
                second TEXT,
                PRIMARY KEY (second, first)
            );
            CREATE TABLE single_parent (id INTEGER PRIMARY KEY);
            CREATE TABLE child (
                id INTEGER PRIMARY KEY,
                first INTEGER,
                second TEXT NOT NULL DEFAULT 'unknown',
                note,
                label TEXT GENERATED ALWAYS AS (second || first) VIRTUAL,
                single_id INTEGER REFERENCES single_parent(id),
                FOREIGN KEY (second, first) REFERENCES parent (second, first)
                    ON UPDATE CASCADE ON DELETE RESTRICT
            );
            CREATE TABLE implicit_child (
                a TEXT,
                b INTEGER,
                FOREIGN KEY (a, b) REFERENCES parent
            );
            CREATE TABLE "odd "" table; --" (titulo TEXT);
            CREATE VIEW child_view AS SELECT id FROM child;
            '''
        )
        connection.commit()
    finally:
        connection.close()
    return path


def test_extracts_column_types_defaults_and_generated_columns(
    metadata_database: Path,
) -> None:
    with readonly_connection(metadata_database) as connection:
        table = inspect_table(connection, "child")
    columns = {column.name: column for column in table.columns}
    assert tuple(columns) == ("id", "first", "second", "note", "label", "single_id")
    assert columns["id"].declared_type == "INTEGER"
    assert columns["id"].primary_key_position == 1
    # table_xinfo não marca INTEGER PRIMARY KEY como NOT NULL declarado.
    assert columns["id"].not_null is False
    assert columns["second"].declared_type == "TEXT"
    assert columns["second"].not_null is True
    assert columns["second"].default_value == "'unknown'"
    assert columns["note"].declared_type == ""
    assert columns["note"].default_value is None
    assert columns["label"].hidden == 2


def test_preserves_composite_primary_key_order(metadata_database: Path) -> None:
    with readonly_connection(metadata_database) as connection:
        table = inspect_table(connection, "parent")
    assert table.primary_key == ("second", "first")
    assert table.foreign_keys == ()


def test_groups_composite_foreign_keys_in_column_order(
    metadata_database: Path,
) -> None:
    with readonly_connection(metadata_database) as connection:
        table = inspect_table(connection, "child")
    assert len(table.foreign_keys) == 2
    keys = {key.target_table: key for key in table.foreign_keys}
    composite = keys["parent"]
    assert composite.source_columns == ("second", "first")
    assert composite.target_columns == ("second", "first")
    assert composite.on_update == "CASCADE"
    assert composite.on_delete == "RESTRICT"
    assert composite.match == "NONE"
    single = keys["single_parent"]
    assert single.source_columns == ("single_id",)
    assert single.target_columns == ("id",)
    assert single.on_update == "NO ACTION"
    assert single.on_delete == "NO ACTION"


def test_preserves_implicit_references_without_inventing_columns(
    metadata_database: Path,
) -> None:
    with readonly_connection(metadata_database) as connection:
        table = inspect_table(connection, "implicit_child")
    key = table.foreign_keys[0]
    assert key.target_table == "parent"
    assert key.source_columns == ("a", "b")
    assert key.target_columns == (None, None)


def test_inspects_table_name_with_quotes_and_sql_punctuation(
    metadata_database: Path,
) -> None:
    name = 'odd " table; --'
    with readonly_connection(metadata_database) as connection:
        table = inspect_table(connection, name)
    assert table.name == name
    assert table.columns[0].name == "titulo"
    assert table.primary_key == ()


@pytest.mark.parametrize(
    "name", ["missing", "child_view", "child'); DROP TABLE child; --"]
)
def test_rejects_unknown_tables_views_and_sql_injection(
    metadata_database: Path, name: str,
) -> None:
    with readonly_connection(metadata_database) as connection:
        with pytest.raises(SchemaInspectionError, match="Tabela não encontrada"):
            inspect_table(connection, name)
        assert inspect_table(connection, "child").primary_key == ("id",)


def test_inspects_complete_schema_in_order(metadata_database: Path) -> None:
    with readonly_connection(metadata_database) as connection:
        schema = inspect_schema(connection)
    assert [table.name for table in schema] == [
        "child", "implicit_child", 'odd " table; --', "parent", "single_parent"
    ]
    assert all(table.columns for table in schema)


def test_inspection_error_on_closed_connection() -> None:
    connection = sqlite3.connect(":memory:")
    connection.close()
    with pytest.raises(SchemaInspectionError, match="inspecionar a tabela") as error:
        inspect_table(connection, "child")
    assert isinstance(error.value.__cause__, sqlite3.ProgrammingError)
