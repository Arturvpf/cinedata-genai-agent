"""Inspeção de dados limitada, usando arquivos SQLite temporários."""

from pathlib import Path
import sqlite3

import pytest

from cinedata.database import readonly_connection
from cinedata.exceptions import SchemaInspectionError
from cinedata.inspection import inspect_table_data


@pytest.fixture
def preview_database(tmp_path: Path) -> Path:
    path = tmp_path / "preview.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE movies (id INTEGER PRIMARY KEY, title TEXT, "
            "popularity REAL, note TEXT, payload BLOB)"
        )
        connection.executemany(
            "INSERT INTO movies VALUES (?, ?, ?, ?, ?)",
            [
                (number, "Título longo " + str(number), number / 2, None, b"x" * 2048)
                for number in range(15, 0, -1)
            ],
        )
        connection.commit()
    finally:
        connection.close()
    return path


def test_count_is_complete_but_sample_is_limited(preview_database: Path) -> None:
    with readonly_connection(preview_database) as connection:
        preview = inspect_table_data(connection, "movies")
    assert preview.row_count == 15
    assert preview.columns == ("id", "title", "popularity", "note", "payload")
    assert len(preview.rows) == 3
    assert [row[0] for row in preview.rows] == [1, 2, 3]
    assert preview.rows[0][2] == 0.5
    assert preview.rows[0][3] is None


def test_text_is_truncated_and_blob_is_not_returned(preview_database: Path) -> None:
    with readonly_connection(preview_database) as connection:
        preview = inspect_table_data(connection, "movies", max_cell_chars=6)
    assert preview.rows[0][1] == "Título..."
    assert preview.rows[0][4] == "[BLOB: 2048 bytes]"
    assert not any(isinstance(value, bytes) for row in preview.rows for value in row)


def test_zero_sample_rows_returns_only_count(preview_database: Path) -> None:
    with readonly_connection(preview_database) as connection:
        preview = inspect_table_data(connection, "movies", sample_rows=0)
    assert preview.row_count == 15
    assert preview.rows == ()


def test_empty_table_has_zero_count_and_no_sample(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE movies (id INTEGER)")
        connection.commit()
    finally:
        connection.close()
    with readonly_connection(path) as connection:
        preview = inspect_table_data(connection, "movies")
    assert preview.row_count == 0
    assert preview.rows == ()


@pytest.mark.parametrize("sample_rows", [-1, 11, 1.5, True])
def test_rejects_invalid_sample_limits(
    preview_database: Path, sample_rows: int,
) -> None:
    with readonly_connection(preview_database) as connection:
        with pytest.raises(ValueError, match="amostra"):
            inspect_table_data(connection, "movies", sample_rows=sample_rows)


@pytest.mark.parametrize("max_cell_chars", [0, 1001, 1.5, True])
def test_rejects_invalid_text_limits(
    preview_database: Path, max_cell_chars: int,
) -> None:
    with readonly_connection(preview_database) as connection:
        with pytest.raises(ValueError, match="limite de texto"):
            inspect_table_data(connection, "movies", max_cell_chars=max_cell_chars)


def test_quotes_special_table_and_column_names(tmp_path: Path) -> None:
    path = tmp_path / "names.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            'CREATE TABLE "odd "" table; --" ("select" INTEGER PRIMARY KEY, '
            '"texto "" especial" TEXT)'
        )
        connection.execute(
            'INSERT INTO "odd "" table; --" VALUES (?, ?)', (1, "Exemplo")
        )
        connection.commit()
    finally:
        connection.close()
    with readonly_connection(path) as connection:
        preview = inspect_table_data(connection, 'odd " table; --')
    assert preview.columns == ("select", 'texto " especial')
    assert preview.rows == ((1, "Exemplo"),)


def test_invalid_table_name_cannot_change_database(preview_database: Path) -> None:
    original = preview_database.read_bytes()
    with readonly_connection(preview_database) as connection:
        with pytest.raises(SchemaInspectionError, match="Tabela não encontrada"):
            inspect_table_data(connection, 'movies"; DROP TABLE movies; --')
        assert inspect_table_data(connection, "movies", sample_rows=0).row_count == 15
    assert preview_database.read_bytes() == original
