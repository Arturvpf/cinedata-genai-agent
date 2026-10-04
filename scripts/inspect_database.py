"""Inspecione as tabelas do SQLite local sem depender do OpenRouter."""

import argparse
from pathlib import Path

from cinedata.database import readonly_connection
from cinedata.exceptions import CineDataError
from cinedata.schema import list_tables


def main(argv: list[str] | None = None) -> int:
    """Mostre as tabelas reais do arquivo e retorne o código de saída."""
    parser = argparse.ArgumentParser(
        description="Lista as tabelas do banco SQLite em modo somente leitura."
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
            tables = list_tables(connection)
    except CineDataError as exc:
        parser.exit(status=1, message=f"Erro: {exc}\n")

    print(f"Tabelas encontradas: {len(tables)}")
    for table in tables:
        print(f"- {table}")
    if not tables:
        print("Nenhuma tabela de aplicação foi encontrada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
