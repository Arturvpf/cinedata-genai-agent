"""Conexão com o arquivo SQLite local em modo somente leitura."""

from collections.abc import Collection, Iterator
from contextlib import closing, contextmanager
import logging
import math
from pathlib import Path
import sqlite3
from time import monotonic

from cinedata.exceptions import (
    DatabaseConnectionError,
    DatabaseNotFoundError,
    QueryBlockedError,
    QueryExecutionError,
    QueryLimitError,
    QueryTimeoutError,
)
from cinedata.guardrails import install_readonly_authorizer, validate_sql
from cinedata.models import QueryResult, SQLiteValue


logger = logging.getLogger(__name__)
DEFAULT_MAX_ROWS = 100
MAX_QUERY_ROWS = 1_000
MAX_VALUE_BYTES = 1_000_000
MAX_RESULT_BYTES = 1_000_000
MAX_QUERY_COLUMNS = 100
MAX_SQL_BYTES = 80_000  # Até 20 mil caracteres UTF-8 de quatro bytes.
PROGRESS_INTERVAL = 1_000


@contextmanager
def readonly_connection(database_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Abra um SQLite existente e feche a conexão ao sair do bloco with.

    O caminho é convertido em URI para preservar espaços e caracteres especiais.
    mode=ro impede escrita no arquivo, mesmo se query_only for desativado.
    Erros de SQL executado pelo chamador são propagados sem alteração.
    """
    try:
        path = Path(database_path).expanduser().resolve()
        exists = path.is_file()
    except OSError as exc:
        raise DatabaseConnectionError(
            "Não foi possível acessar o caminho do banco de dados."
        ) from exc

    if not exists:
        raise DatabaseNotFoundError(f"Banco de dados não encontrado: {path}")

    connection = None
    try:
        connection = sqlite3.connect(
            path.as_uri() + "?mode=ro",
            uri=True,
            timeout=5.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        # Ler o catálogo força a validação do arquivo antes de entregá-lo.
        connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise DatabaseConnectionError(
            "Não foi possível abrir o banco SQLite. "
            "Verifique se o arquivo é válido e se você tem permissão de leitura."
        ) from exc

    logger.info("Conexão SQLite aberta em modo somente leitura.")
    try:
        yield connection
    finally:
        connection.close()


def _value_size(value: SQLiteValue) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, bytes):
        return len(value)
    return 8  # Estimativa fixa para valores numéricos e NULL.


def execute_readonly(
    database_path: str | Path, sql: str, *, allowed_tables: Collection[str],
    max_rows: int = DEFAULT_MAX_ROWS, timeout_seconds: float = 5.0,
) -> QueryResult:
    """Valide e execute SQL em uma conexão exclusiva, com limites de leitura.

    Recebe SQL já extraído e as tabelas permitidas do esquema da aplicação.
    Busca no máximo max_rows + 1 linhas para detectar resultado parcial, sem
    reescrever a consulta. O prazo cobre preparação, execução e leitura do
    SQL gerado; abertura e configuração da conexão ficam fora desse prazo.
    """
    if type(max_rows) is not int or not 1 <= max_rows <= MAX_QUERY_ROWS:
        raise ValueError(f"max_rows deve ser inteiro entre 1 e {MAX_QUERY_ROWS}.")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 60
    ):
        raise ValueError("timeout_seconds deve ser um número entre 0 e 60 segundos.")
    sql = validate_sql(sql)

    with readonly_connection(database_path) as connection:
        # O progress handler não interrompe esperas por locks; limite-as também.
        wait_ms = max(1, math.ceil(min(timeout_seconds, 5.0) * 1_000))
        with closing(connection.execute(f"PRAGMA busy_timeout = {wait_ms}")):
            pass
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL_BYTES)
        connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, MAX_QUERY_COLUMNS)
        authorizer = install_readonly_authorizer(connection, allowed_tables)
        started = monotonic()
        deadline = started + timeout_seconds

        def expired() -> bool:
            return monotonic() >= deadline

        connection.set_progress_handler(expired, PROGRESS_INTERVAL)
        try:
            with closing(connection.execute(sql)) as cursor:
                columns = tuple(column[0] for column in cursor.description)
                result_bytes = sum(_value_size(column) for column in columns)
                rows = []
                truncated = False
                for index in range(max_rows + 1):
                    if expired():
                        raise QueryTimeoutError("A consulta excedeu o tempo permitido.")
                    row = cursor.fetchone()
                    if expired():
                        raise QueryTimeoutError("A consulta excedeu o tempo permitido.")
                    if row is None:
                        break
                    if index == max_rows:
                        truncated = True
                        break
                    values = tuple(row)
                    result_bytes += sum(_value_size(value) for value in values)
                    if result_bytes > MAX_RESULT_BYTES:
                        raise QueryLimitError(
                            "O resultado excedeu o limite de tamanho permitido."
                        )
                    rows.append(values)
            elapsed = monotonic() - started
        except sqlite3.Error as exc:
            if authorizer.denied_reason is not None:
                raise QueryBlockedError(authorizer.denied_reason) from exc
            code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            if code == sqlite3.SQLITE_INTERRUPT or expired():
                raise QueryTimeoutError(
                    "A consulta excedeu o tempo permitido."
                ) from exc
            if code == sqlite3.SQLITE_TOOBIG:
                raise QueryLimitError(
                    "Um valor da consulta excedeu o limite de tamanho permitido."
                ) from exc
            recoverable = code == sqlite3.SQLITE_ERROR
            message = (
                "O SQL gerado é inválido. Verifique tabelas, colunas e sintaxe."
                if recoverable else "Não foi possível executar a consulta SQLite."
            )
            raise QueryExecutionError(
                message, sqlite_error=str(exc), recoverable=recoverable,
            ) from exc
        finally:
            connection.set_progress_handler(None, 0)

    logger.info(
        "Consulta concluída: %d linhas, resultado parcial=%s, tempo=%.3fs.",
        len(rows), truncated, elapsed,
    )
    return QueryResult(sql, columns, tuple(rows), truncated, elapsed)
