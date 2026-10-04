"""Inspecione o esquema do SQLite local sem depender do OpenRouter."""

import argparse
from pathlib import Path

from cinedata.database import readonly_connection
from cinedata.exceptions import CineDataError
from cinedata.schema import inspect_schema


def main(argv: list[str] | None = None) -> int:
    """Mostre o esquema real do arquivo e retorne o código de saída."""
    parser = argparse.ArgumentParser(
        description="Mostra tabelas, colunas e chaves do SQLite em somente leitura."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "cinerocket.db",
        help="Caminho do banco. Padrão: cinerocket.db na raiz do projeto.",
    )
    args = parser.parse_args(argv)

    try:
        with readonly_connection(args.database) as connection:
            tables = inspect_schema(connection)
    except CineDataError as exc:
        parser.exit(status=1, message=f"Erro: {exc}\n")

    print(f"Tabelas encontradas: {len(tables)}")
    for table in tables:
        print(f"\n[{table.name}]")
        for column in table.columns:
            flags = []
            if column.not_null:
                flags.append("NOT NULL declarado")
            if column.primary_key_position:
                flags.append(f"PK posição {column.primary_key_position}")
            if column.default_value is not None:
                flags.append(f"DEFAULT {column.default_value}")
            if column.hidden:
                flags.append(f"hidden={column.hidden}")
            details = f" | {', '.join(flags)}" if flags else ""
            print(f"  {column.name}: {column.declared_type or '(sem tipo)'}{details}")
        print(f"  Chave primária: {', '.join(table.primary_key) or '(nenhuma)'}")
        if not table.foreign_keys:
            print("  Chaves estrangeiras: nenhuma declarada")
        for foreign_key in table.foreign_keys:
            source = ", ".join(foreign_key.source_columns)
            target = ", ".join(
                name if name is not None else "(PK implícita)"
                for name in foreign_key.target_columns
            )
            print(f"  FK ({source}) -> {foreign_key.target_table} ({target})")
            print(
                f"    ON UPDATE {foreign_key.on_update}; "
                f"ON DELETE {foreign_key.on_delete}"
            )
    if not tables:
        print("Nenhuma tabela de aplicação foi encontrada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
