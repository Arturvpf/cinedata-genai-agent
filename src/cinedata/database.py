"""Conexão com o arquivo SQLite local em modo somente leitura."""

from collections.abc import Iterator
from contextlib import contextmanager
import logging
from pathlib import Path
import sqlite3

from cinedata.exceptions import DatabaseConnectionError, DatabaseNotFoundError


logger = logging.getLogger(__name__)


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
