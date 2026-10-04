"""Contagens e amostras limitadas para inspeção local dos dados."""

from contextlib import closing
import sqlite3

from cinedata.exceptions import SchemaInspectionError
from cinedata.models import TablePreview
from cinedata.schema import inspect_table


MAX_SAMPLE_ROWS = 10
MAX_CELL_CHARS = 1000
DEFAULT_SAMPLE_ROWS = 3
DEFAULT_CELL_CHARS = 160


def _quote_identifier(name: str) -> str:
    """Preserve um identificador SQLite, incluindo aspas em seu nome."""
    return '"' + name.replace('"', '""') + '"'


def _sample_expression(name: str) -> str:
    """Limite textos no SQLite e substitua BLOBs por seu tamanho."""
    identifier = _quote_identifier(name)
    return (
        f"CASE WHEN typeof({identifier}) = 'blob' "
        f"THEN printf('[BLOB: %d bytes]', length({identifier})) "
        f"WHEN typeof({identifier}) = 'text' AND length({identifier}) > ? "
        f"THEN substr({identifier}, 1, ?) || '...' "
        f"ELSE {identifier} END AS {identifier}"
    )


def inspect_table_data(
    connection: sqlite3.Connection,
    table_name: str,
    *,
    sample_rows: int = DEFAULT_SAMPLE_ROWS,
    max_cell_chars: int = DEFAULT_CELL_CHARS,
) -> TablePreview:
    """Conte registros e leia até dez linhas, sem alterar o banco.

    Textos longos são truncados antes de chegarem ao Python; BLOBs não são
    retornados. Use sample_rows=0 para obter apenas a contagem. A amostra
    segue a ordem da PK, quando disponível, e não representa um ranking.
    """
    if type(sample_rows) is not int or not 0 <= sample_rows <= MAX_SAMPLE_ROWS:
        raise ValueError(f"A amostra deve ter de 0 a {MAX_SAMPLE_ROWS} linhas.")
    if type(max_cell_chars) is not int or not 1 <= max_cell_chars <= MAX_CELL_CHARS:
        raise ValueError(
            f"O limite de texto deve ser de 1 a {MAX_CELL_CHARS} caracteres."
        )

    table = inspect_table(connection, table_name)
    columns = tuple(column.name for column in table.columns)
    quoted_table = "main." + _quote_identifier(table.name)
    try:
        with closing(connection.execute(
            f"SELECT COUNT(*) FROM {quoted_table}"
        )) as cursor:
            row_count = cursor.fetchone()[0]
        rows = ()
        if sample_rows:
            expressions = ", ".join(_sample_expression(name) for name in columns)
            order_by = ""
            if table.primary_key:
                order_by = " ORDER BY " + ", ".join(
                    _quote_identifier(name) for name in table.primary_key
                )
            parameters = (max_cell_chars, max_cell_chars) * len(columns)
            with closing(connection.execute(
                f"SELECT {expressions} FROM {quoted_table}{order_by} LIMIT ?",
                parameters + (sample_rows,),
            )) as cursor:
                rows = tuple(tuple(row) for row in cursor.fetchall())
    except sqlite3.Error as exc:
        raise SchemaInspectionError(
            f"Não foi possível inspecionar os dados da tabela: {table_name}"
        ) from exc
    return TablePreview(row_count=row_count, columns=columns, rows=rows)
