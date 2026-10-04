"""Inspecione o esquema do SQLite local sem depender do OpenRouter."""

import argparse
import json
from pathlib import Path

from cinedata.database import readonly_connection
from cinedata.exceptions import CineDataError
from cinedata.inspection import (
    DEFAULT_CELL_CHARS,
    DEFAULT_SAMPLE_ROWS,
    MAX_CELL_CHARS,
    MAX_SAMPLE_ROWS,
    inspect_table_data,
)
from cinedata.schema import format_schema_for_llm, inspect_schema


def main(argv: list[str] | None = None) -> int:
    """Mostre o esquema real do arquivo e retorne o código de saída."""
    parser = argparse.ArgumentParser(
        description="Mostra esquema, contagens e amostras do SQLite em leitura."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "cinerocket.db",
        help="Caminho do banco. Padrão: cinerocket.db na raiz do projeto.",
    )
    output_options = parser.add_mutually_exclusive_group()
    output_options.add_argument(
        "--llm-context",
        action="store_true",
        help="Mostra somente o JSON compacto do esquema destinado ao modelo.",
    )
    output_options.add_argument(
        "--schema-only",
        action="store_true",
        help="Mostra o esquema sem executar contagens ou ler amostras.",
    )
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=DEFAULT_SAMPLE_ROWS,
        help=f"Linhas por tabela: 0 a {MAX_SAMPLE_ROWS} (padrão: 3).",
    )
    parser.add_argument(
        "--max-cell-chars",
        type=int,
        default=DEFAULT_CELL_CHARS,
        help=f"Caracteres por texto: 1 a {MAX_CELL_CHARS} (padrão: 160).",
    )
    args = parser.parse_args(argv)
    if not 0 <= args.sample_rows <= MAX_SAMPLE_ROWS:
        parser.error(f"--sample-rows deve ser de 0 a {MAX_SAMPLE_ROWS}.")
    if not 1 <= args.max_cell_chars <= MAX_CELL_CHARS:
        parser.error(f"--max-cell-chars deve ser de 1 a {MAX_CELL_CHARS}.")

    try:
        with readonly_connection(args.database) as connection:
            tables = inspect_schema(connection)
            previews = {}
            if not args.llm_context and not args.schema_only:
                previews = {
                    table.name: inspect_table_data(
                        connection, table.name,
                        sample_rows=args.sample_rows,
                        max_cell_chars=args.max_cell_chars,
                    )
                    for table in tables
                }
    except CineDataError as exc:
        parser.exit(status=1, message=f"Erro: {exc}\n")

    if args.llm_context:
        print(format_schema_for_llm(tables))
        return 0

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
        if table.name in previews:
            preview = previews[table.name]
            print(f"  Registros: {preview.row_count}")
            if preview.rows:
                label = "linha" if len(preview.rows) == 1 else "linhas"
                print(f"  Amostra ({len(preview.rows)} {label}):")
                for row in preview.rows:
                    print("    " + json.dumps(
                        dict(zip(preview.columns, row)), ensure_ascii=False,
                    ))
            elif args.sample_rows:
                print("  Nenhuma linha disponível para amostra.")
    if not tables:
        print("Nenhuma tabela de aplicação foi encontrada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
