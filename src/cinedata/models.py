"""Estruturas imutáveis para reutilizar os metadados do SQLite."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnSchema:
    """Coluna conforme o PRAGMA table_xinfo, sem inferir tipos ou restrições."""

    name: str
    declared_type: str
    not_null: bool
    default_value: str | None
    primary_key_position: int
    hidden: int


@dataclass(frozen=True)
class ForeignKeySchema:
    """Uma restrição FK, preservando a ordem das colunas compostas."""

    constraint_id: int
    target_table: str
    source_columns: tuple[str, ...]
    target_columns: tuple[str | None, ...]
    on_update: str
    on_delete: str
    match: str


@dataclass(frozen=True)
class TableSchema:
    """Metadados de uma tabela do banco principal."""

    name: str
    columns: tuple[ColumnSchema, ...]
    foreign_keys: tuple[ForeignKeySchema, ...]

    @property
    def primary_key(self) -> tuple[str, ...]:
        """Retorne a PK na ordem declarada, inclusive para chaves compostas."""
        columns = (column for column in self.columns if column.primary_key_position)
        return tuple(
            column.name
            for column in sorted(columns, key=lambda item: item.primary_key_position)
        )
