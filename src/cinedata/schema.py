"""Introspecção da estrutura real do banco SQLite."""

from contextlib import closing
import sqlite3

from cinedata.exceptions import SchemaInspectionError
from cinedata.models import ColumnSchema, ForeignKeySchema, TableSchema


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


def inspect_table(connection: sqlite3.Connection, table_name: str) -> TableSchema:
    """Leia colunas, PK e FKs de uma tabela real do banco principal.

    Os nomes são parâmetros das funções PRAGMA, sem interpolação de SQL.
    Uma referência à PK implícita mantém target_columns com None, conforme
    o SQLite, em vez de inventar a coluna de destino.
    """
    try:
        with closing(connection.execute(
            "SELECT name FROM main.sqlite_master "
            "WHERE type = 'table' AND name = ? "
            "AND name NOT GLOB 'sqlite_*'",
            (table_name,),
        )) as cursor:
            exists = cursor.fetchone() is not None
        if not exists:
            raise SchemaInspectionError(
                f"Tabela não encontrada no banco principal: {table_name}"
            )

        with closing(connection.execute(
            'SELECT name, type, "notnull", dflt_value, pk, hidden '
            "FROM pragma_table_xinfo(?, 'main') ORDER BY cid",
            (table_name,),
        )) as cursor:
            columns = tuple(
                ColumnSchema(
                    name=row[0], declared_type=row[1], not_null=bool(row[2]),
                    default_value=row[3], primary_key_position=row[4], hidden=row[5],
                )
                for row in cursor.fetchall()
            )

        with closing(connection.execute(
            'SELECT id, seq, "table", "from", "to", '
            'on_update, on_delete, "match" '
            "FROM pragma_foreign_key_list(?, 'main') ORDER BY id, seq",
            (table_name,),
        )) as cursor:
            foreign_key_rows = cursor.fetchall()
    except sqlite3.Error as exc:
        raise SchemaInspectionError(
            f"Não foi possível inspecionar a tabela: {table_name}"
        ) from exc

    groups: dict[int, list[tuple]] = {}
    for row in foreign_key_rows:
        groups.setdefault(row[0], []).append(tuple(row))
    foreign_keys = tuple(
        ForeignKeySchema(
            constraint_id=constraint_id,
            target_table=rows[0][2],
            source_columns=tuple(row[3] for row in rows),
            target_columns=tuple(row[4] for row in rows),
            on_update=rows[0][5], on_delete=rows[0][6], match=rows[0][7],
        )
        for constraint_id, rows in groups.items()
    )
    return TableSchema(table_name, columns, foreign_keys)


def inspect_schema(connection: sqlite3.Connection) -> tuple[TableSchema, ...]:
    """Colete o esquema real das tabelas, sem ler linhas dos dados."""
    return tuple(inspect_table(connection, name) for name in list_tables(connection))
