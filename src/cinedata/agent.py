"""Geração, validação e execução de consultas a partir de perguntas."""

from datetime import date
import logging
from pathlib import Path
from typing import Protocol

from cinedata.database import (
    DEFAULT_MAX_ROWS,
    DEFAULT_QUERY_TIMEOUT_SECONDS,
    MAX_QUERY_ROWS,
    execute_readonly,
    readonly_connection,
    validate_query_timeout,
)
from cinedata.exceptions import (
    DatabaseConnectionError,
    InvalidModelResponseError,
    QueryBlockedError,
    QueryExecutionError,
    SchemaInspectionError,
)
from cinedata.guardrails import sanitize_sql, validate_sql
from cinedata.models import AgentResult
from cinedata.prompts import build_sql_prompt
from cinedata.schema import format_schema_for_llm, inspect_schema


logger = logging.getLogger(__name__)


class TextCompletionClient(Protocol):
    """Contrato mínimo compatível com o cliente OpenRouter e clientes de teste."""

    def complete(
        self, system_prompt: str, user_prompt: str, *, max_tokens: int = 2_048,
    ) -> str: ...


class CineDataAgent:
    """Reutilize o esquema real para gerar SQL e executar consultas protegidas.

    O cliente é fornecido pelo chamador, que continua responsável por fechá-lo.
    A inicialização lê somente os metadados e não faz chamadas ao modelo.
    """

    def __init__(
        self, database_path: str | Path, client: TextCompletionClient, *,
        max_rows: int = DEFAULT_MAX_ROWS, reference_date: date | None = None,
        query_timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
    ) -> None:
        if type(max_rows) is not int or not 1 <= max_rows <= MAX_QUERY_ROWS:
            raise ValueError(f"max_rows deve ser inteiro entre 1 e {MAX_QUERY_ROWS}.")
        if reference_date is not None and type(reference_date) is not date:
            raise ValueError("reference_date deve ser uma data sem horário.")
        self.query_timeout_seconds = validate_query_timeout(query_timeout_seconds)
        try:
            self.database_path = Path(database_path).expanduser().resolve()
        except (OSError, ValueError, RuntimeError) as exc:
            raise DatabaseConnectionError("O caminho do banco é inválido.") from exc
        with readonly_connection(self.database_path) as connection:
            schema = tuple(
                table for table in inspect_schema(connection)
                if table.name != "alembic_version"
            )
        if not schema:
            raise SchemaInspectionError(
                "O banco não contém tabelas de dados consultáveis."
            )
        self._schema_context = format_schema_for_llm(schema)
        self._allowed_tables = frozenset(table.name for table in schema)
        self._client = client
        self.max_rows = max_rows
        self.reference_date = reference_date
        logger.info("Agente iniciado com %d tabelas de dados.", len(schema))

    @property
    def schema_context(self) -> str:
        """Contexto sem linhas do banco, coletado uma única vez na inicialização."""
        return self._schema_context

    @property
    def allowed_tables(self) -> frozenset[str]:
        """Tabelas do mesmo esquema enviado ao modelo, para o executor."""
        return self._allowed_tables

    def generate_sql(self, question: str) -> str:
        """Gere SQL com uma chamada, extraia a resposta e aplique os guardrails.

        A validação é textual; sintaxe completa e permissões de acesso serão
        conferidas pelo SQLite durante a execução protegida. Este método não
        executa SQL, não pede correção e não gera resposta em linguagem natural.
        """
        messages = build_sql_prompt(
            question, self._schema_context, reference_date=self.reference_date,
            max_rows=self.max_rows,
        )
        logger.info("Iniciando geração de SQL.")
        response = self._client.complete(messages.system, messages.user)
        try:
            sql = validate_sql(sanitize_sql(response))
        except InvalidModelResponseError:
            logger.warning("O modelo retornou uma resposta SQL inválida.")
            raise
        except QueryBlockedError:
            logger.warning("Consulta gerada bloqueada pelos guardrails.")
            raise
        logger.info("SQL extraído e aprovado pela validação textual.")
        return sql

    def query(self, question: str) -> AgentResult:
        """Gere e execute SQL uma vez, sem correção ou resposta final do modelo."""
        timeout = validate_query_timeout(self.query_timeout_seconds)
        sql = self.generate_sql(question)
        logger.info("Executando consulta gerada em modo somente leitura.")
        try:
            result = execute_readonly(
                self.database_path, sql, allowed_tables=self._allowed_tables,
                max_rows=self.max_rows, timeout_seconds=timeout,
            )
        except QueryBlockedError:
            logger.warning("Consulta gerada bloqueada pelo SQLite.")
            raise
        except QueryExecutionError as exc:
            logger.warning("Falha na execução SQL: %s.", type(exc).__name__)
            raise
        return AgentResult(
            question=question,
            sql=result.sql,
            columns=result.columns,
            rows=result.rows,
            truncated=result.truncated,
            elapsed_seconds=result.elapsed_seconds,
        )
