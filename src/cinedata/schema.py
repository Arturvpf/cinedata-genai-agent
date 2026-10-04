"""Introspecção da estrutura real do banco SQLite."""

import sqlite3

from cinedata.exceptions import SchemaInspectionError


def list_tables(connection: sqlite3.Connection) -> list[str]:
    """Liste as tabelas do banco principal em ordem alfabética.

    Exclui tabelas internas sqlite_*, views, índices e triggers. Tabelas
    de controle da aplicação, como alembic_version, continuam visíveis.
    A conexão é administrada pelo chamador e permanece aberta.
    """
    try:
        cursor = connection.execute(
            "SELECT name FROM main.sqlite_master "
            "WHERE type = 'table' AND name NOT GLOB 'sqlite_*' "
            "ORDER BY name"
        )
        try:
            return [row[0] for row in cursor.fetchall()]
        finally:
            cursor.close()
    except sqlite3.Error as exc:
        raise SchemaInspectionError(
            "Não foi possível listar as tabelas do banco SQLite."
        ) from exc
