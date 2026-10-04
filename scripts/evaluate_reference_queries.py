"""Execute SQL de referência no SQLite protegido, sem chave nem API."""

import argparse
from datetime import date
import json
import math
from pathlib import Path
import sys

from cinedata.database import execute_readonly, readonly_connection, validate_query_timeout
from cinedata.evaluation import load_evaluation_questions, parse_reference_date
from cinedata.exceptions import CineDataError
from cinedata.models import SQLiteValue
from cinedata.schema import inspect_schema


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _json_value(value: SQLiteValue) -> str | int | float | None | dict[str, str | int]:
    if isinstance(value, bytes):
        return {"binary_bytes": len(value)}
    if isinstance(value, float) and not math.isfinite(value):
        return {"unavailable": "non_finite_number"}
    return value


def _date(value: str) -> date:
    try:
        return parse_reference_date(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def main(argv: list[str] | None = None) -> int:
    """Liste perguntas ou execute referências; qualquer consulta com falha retorna 1."""
    parser = argparse.ArgumentParser(
        description="Avaliação local das consultas de referência, sem chamadas ao modelo."
    )
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "cinerocket.db")
    parser.add_argument(
        "--questions", type=Path, default=PROJECT_ROOT / "tests" / "evaluation_questions.json",
    )
    parser.add_argument("--list", action="store_true", help="lista perguntas sem abrir o banco")
    parser.add_argument("--case", action="append", default=[], help="ID; pode ser repetido")
    parser.add_argument("--show-results", action="store_true", help="mostra SQL, colunas e linhas")
    parser.add_argument(
        "--reference-date", type=_date, help="substitui a data fixa das consultas temporais",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="prazo por consulta (padrão: 30 s)",
    )
    args = parser.parse_args(argv)
    try:
        timeout = validate_query_timeout(args.timeout)
    except ValueError:
        parser.error("--timeout deve ser maior que zero e até 60 segundos.")
    try:
        questions = load_evaluation_questions(args.questions)
        selected = set(args.case)
        unknown = selected - {case.id for case in questions}
        if unknown:
            parser.error("ID de pergunta não encontrado no conjunto selecionado.")
        cases = [case for case in questions if not selected or case.id in selected]
        if args.list:
            for case in cases:
                print(f"{case.id} [{case.category}]: {case.question}")
            return 0
        with readonly_connection(args.database) as connection:
            allowed = frozenset(
                table.name for table in inspect_schema(connection)
                if table.name != "alembic_version"
            )
        failures = 0
        print(
            f"Referências locais: {len(cases)} consultas; nenhuma chamada ao modelo.",
            flush=True,
        )
        for case in cases:
            try:
                result = execute_readonly(
                    args.database, case.sql(reference_date=args.reference_date),
                    allowed_tables=allowed, max_rows=1_000, timeout_seconds=timeout,
                )
            except CineDataError as error:
                failures += 1
                print(f"{case.id}: FALHA — {error}", file=sys.stderr, flush=True)
                continue
            partial = "; resultado parcial" if result.truncated else ""
            print(
                f"{case.id}: OK; {len(result.rows)} linhas; "
                f"{result.elapsed_seconds:.3f} s{partial}", flush=True,
            )
            if args.show_results:
                print(json.dumps({
                    "id": case.id, "question": case.question, "reference_sql": result.sql,
                    "reference_date": (
                        (args.reference_date or case.reference_date).isoformat()
                        if args.reference_date or case.reference_date else None
                    ),
                    "columns": result.columns,
                    "rows": [[_json_value(value) for value in row] for row in result.rows],
                    "truncated": result.truncated,
                }, ensure_ascii=True, allow_nan=False))
        print(f"Resumo: {len(cases) - failures} consultas executadas; {failures} falhas.")
        return 1 if failures else 0
    except (CineDataError, ValueError) as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
