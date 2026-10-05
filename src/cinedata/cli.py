"""Interface em português para perguntas interativas ou uma consulta única."""

import argparse
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
import json
import logging
import sys
from time import monotonic

from cinedata.agent import CineDataAgent
from cinedata.answers import EMPTY_RESULT_ANSWER, PARTIAL_RESULT_NOTICE
from cinedata.config import load_settings
from cinedata.database import (
    DEFAULT_MAX_ROWS,
    DEFAULT_QUERY_TIMEOUT_SECONDS,
    MAX_QUERY_ROWS,
    validate_query_timeout,
)
from cinedata.exceptions import AnswerGenerationError, CineDataError
from cinedata.llm import OpenRouterClient
from cinedata.models import AgentResult, SQLiteValue
from cinedata.prompts import MAX_QUESTION_CHARS


EXIT_COMMANDS = frozenset({"exit", "quit", "sair"})
MAX_DISPLAY_CELL_CHARS = 240
logger = logging.getLogger(__name__)


class _ArgumentParser(argparse.ArgumentParser):
    def format_usage(self) -> str:
        return super().format_usage().replace("usage: ", "uso: ", 1)

    def format_help(self) -> str:
        return super().format_help().replace("usage: ", "uso: ", 1)

    def error(self, message: str) -> None:
        message = message.replace("unrecognized arguments:", "argumentos não reconhecidos:")
        message = message.replace("expected one argument", "espera um valor")
        message = message.replace("argument ", "opção ")
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: erro: {message}\n")


def _question(value: str) -> str:
    text = value.strip()
    if not text or "\x00" in text or len(text) > MAX_QUESTION_CHARS:
        raise argparse.ArgumentTypeError(
            f"A pergunta deve ter entre 1 e {MAX_QUESTION_CHARS} caracteres, sem NUL."
        )
    return text


def _row_limit(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("O limite de linhas deve ser inteiro.") from error
    if not 1 <= number <= MAX_QUERY_ROWS:
        raise argparse.ArgumentTypeError(f"O limite de linhas deve ser de 1 a {MAX_QUERY_ROWS}.")
    return number


def _timeout(value: str) -> float:
    try:
        return validate_query_timeout(float(value))
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "O prazo da consulta deve ser maior que zero e até 60 segundos."
        ) from error


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="cinedata", description="CineData Analytics — perguntas sobre o catálogo de filmes.",
        allow_abbrev=False, add_help=False,
    )
    parser._optionals.title = "opções"
    parser.add_argument("-h", "--help", action="help", help="mostra esta ajuda e encerra")
    parser.add_argument(
        "--question", type=_question, metavar="PERGUNTA",
        help="executa uma pergunta e encerra; sem esta opção, abre o modo interativo",
    )
    parser.add_argument(
        "--raw", action="store_true", help="mostra SQL e dados sem a chamada de redação",
    )
    parser.add_argument(
        "--debug", action="store_true", help="mostra SQL, dados, tempo e logs das etapas",
    )
    parser.add_argument(
        "--env-file", default=".env", metavar="CAMINHO",
        help="arquivo de configuração (padrão: .env)",
    )
    parser.add_argument(
        "--max-rows", type=_row_limit, default=DEFAULT_MAX_ROWS, metavar="N",
        help=f"limite de linhas de 1 a {MAX_QUERY_ROWS} (padrão: {DEFAULT_MAX_ROWS})",
    )
    parser.add_argument(
        "--query-timeout", type=_timeout, default=DEFAULT_QUERY_TIMEOUT_SECONDS,
        metavar="SEGUNDOS", help="prazo de cada consulta SQLite, até 60 s (padrão: 5)",
    )
    return parser


def _terminal_text(value: str) -> str:
    """Preserve quebras de linha e represente controles de terminal como texto."""
    return "".join(
        f"\\u{ord(character):04x}"
        if (ord(character) < 32 and character != "\n") or 127 <= ord(character) <= 159
        else character
        for character in value
    )


def _cell(value: SQLiteValue) -> tuple[str, bool]:
    if isinstance(value, bytes):
        return f"[BLOB: {len(value)} bytes]", True
    if isinstance(value, str):
        shortened = len(value) > MAX_DISPLAY_CELL_CHARS
        text = value[:MAX_DISPLAY_CELL_CHARS] + ("..." if shortened else "")
        return json.dumps(text, ensure_ascii=False), shortened
    return ("NULL" if value is None else str(value)), False


def show_result(result: AgentResult, *, raw: bool, debug: bool) -> None:
    """Exiba auditoria quando pedida e mantenha os valores originais no retorno."""
    if debug:
        print("Pergunta:")
        print(_terminal_text(result.question))
    if raw or debug:
        print("SQL executado:")
        print(_terminal_text(result.sql))
        print("Resultado (colunas e linhas na mesma ordem):")
        print(_terminal_text(json.dumps(result.columns, ensure_ascii=False)))
        abbreviated = False
        for index, row in enumerate(result.rows, start=1):
            cells = [_cell(value) for value in row]
            abbreviated = abbreviated or any(changed for _, changed in cells)
            print(_terminal_text(f"{index}. [" + ", ".join(text for text, _ in cells) + "]"))
        if not result.rows:
            print(EMPTY_RESULT_ANSWER)
        if abbreviated:
            print("Exibição abreviada: textos longos e BLOBs não aparecem integralmente.")
        if result.truncated:
            print(PARTIAL_RESULT_NOTICE)
        if result.correction_attempted:
            print("Foi utilizada uma tentativa de correção do SQL.")
    if debug:
        print(f"Linhas retornadas: {len(result.rows)}")
        print(f"Tempo da consulta SQLite final: {result.elapsed_seconds:.3f} s")
    if not raw and result.answer is not None:
        print("Resposta:")
        print(_terminal_text(result.answer))


@contextmanager
def _cli_logging(debug: bool) -> Iterator[None]:
    """Configure apenas logs da aplicação e restaure o estado após encerrar."""
    package_logger = logging.getLogger("cinedata")
    previous_level, previous_propagate = package_logger.level, package_logger.propagate
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    package_logger.addHandler(handler)
    package_logger.setLevel(logging.INFO if debug else logging.WARNING)
    package_logger.propagate = False
    try:
        yield
    finally:
        package_logger.removeHandler(handler)
        handler.close()
        package_logger.setLevel(previous_level)
        package_logger.propagate = previous_propagate


def _run_question(agent: CineDataAgent, question: str, *, raw: bool, debug: bool) -> int:
    started = monotonic()
    try:
        result = agent.query(question) if raw else agent.ask(question)
    except AnswerGenerationError as error:
        print(f"Erro: {_terminal_text(str(error))}", file=sys.stderr)
        # A consulta já foi concluída; exiba os dados sem solicitar outra chamada.
        show_result(error.result, raw=True, debug=debug)
        return 1
    except (CineDataError, ValueError) as error:
        print(f"Erro: {_terminal_text(str(error))}", file=sys.stderr)
        return 1
    finally:
        logger.info("Tempo total da pergunta: %.3f s.", monotonic() - started)
    show_result(result, raw=raw, debug=debug)
    return 0


def _interactive(agent: CineDataAgent, *, raw: bool, debug: bool) -> int:
    print("CineData Analytics — GenAI Agent")
    print("Digite sua pergunta. Para encerrar: sair, exit ou quit.")
    if raw:
        print("Modo raw: SQL e dados, sem redação pelo modelo.")
    while True:
        try:
            question = input("\n> ").strip()
        except EOFError:
            print("\nSessão encerrada.")
            return 0
        if question.casefold() in EXIT_COMMANDS:
            print("Sessão encerrada.")
            return 0
        if not question:
            continue
        try:
            question = _question(question)
        except argparse.ArgumentTypeError as error:
            print(f"Erro: {error}", file=sys.stderr)
            continue
        _run_question(agent, question, raw=raw, debug=debug)


def main(argv: Sequence[str] | None = None) -> int:
    """Execute a CLI; erros de uso retornam 2 e consultas com falha retornam 1."""
    args = build_parser().parse_args(argv)
    with _cli_logging(args.debug):
        try:
            settings = load_settings(args.env_file)
            with OpenRouterClient(settings) as client:
                agent = CineDataAgent(
                    settings.database_path, client, max_rows=args.max_rows,
                    query_timeout_seconds=args.query_timeout,
                )
                if args.question is not None:
                    return _run_question(agent, args.question, raw=args.raw, debug=args.debug)
                return _interactive(agent, raw=args.raw, debug=args.debug)
        except KeyboardInterrupt:
            print("\nOperação interrompida pelo usuário.", file=sys.stderr)
            return 130
        except (CineDataError, ValueError) as error:
            print(f"Erro: {_terminal_text(str(error))}", file=sys.stderr)
            return 2
        except Exception as error:
            logger.error("Falha inesperada na CLI: %s.", type(error).__name__)
            print("Erro: ocorreu uma falha inesperada ao executar a aplicação.", file=sys.stderr)
            return 1
