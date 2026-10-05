"""Geração, validação e execução de consultas a partir de perguntas."""

from collections import OrderedDict
from dataclasses import replace
from datetime import date
import logging
from pathlib import Path
from time import monotonic
from typing import Protocol

from cinedata.answers import (
    EMPTY_RESULT_ANSWER,
    LIMITED_CONTEXT_NOTICE,
    MAX_ANSWER_CHARS,
    PARTIAL_RESULT_NOTICE,
    build_answer_prompt,
    local_count_answer,
)
from cinedata.database import (
    DEFAULT_MAX_ROWS,
    DEFAULT_QUERY_TIMEOUT_SECONDS,
    MAX_QUERY_ROWS,
    execute_readonly,
    readonly_connection,
    validate_query_timeout,
)
from cinedata.exceptions import (
    AnswerGenerationError,
    DatabaseConnectionError,
    InvalidModelResponseError,
    LLMServiceError,
    QueryBlockedError,
    QueryExecutionError,
    QueryLimitError,
    QueryTimeoutError,
    SchemaInspectionError,
)
from cinedata.guardrails import sanitize_sql, validate_sql
from cinedata.models import AgentResult, PromptMessages, QueryResult
from cinedata.prompts import build_sql_correction_prompt, build_sql_prompt
from cinedata.schema import format_schema_for_llm, inspect_schema


logger = logging.getLogger(__name__)
MAX_CACHED_SQL = 32


def _validated_sql(response: str) -> str:
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
        self._sql_cache: OrderedDict[tuple[str, date, int], str] = OrderedDict()
        logger.info("Agente iniciado com %d tabelas de dados.", len(schema))

    @property
    def schema_context(self) -> str:
        """Contexto sem linhas do banco, coletado uma única vez na inicialização."""
        return self._schema_context

    @property
    def allowed_tables(self) -> frozenset[str]:
        """Tabelas do mesmo esquema enviado ao modelo, para o executor."""
        return self._allowed_tables

    def generate_sql(
        self, question: str, *, reference_date: date | None = None,
    ) -> str:
        """Gere SQL com uma chamada, extraia a resposta e aplique os guardrails.

        A validação é textual; sintaxe completa e permissões de acesso serão
        conferidas pelo SQLite durante a execução protegida. Este método não
        executa SQL, não pede correção e não gera resposta em linguagem natural.
        """
        messages = build_sql_prompt(
            question, self._schema_context,
            reference_date=(
                self.reference_date if reference_date is None else reference_date
            ),
            max_rows=self.max_rows,
        )
        return self._generate_sql(messages)

    def _generate_sql(self, messages: PromptMessages) -> str:
        logger.info("Iniciando geração de SQL.")
        started = monotonic()
        response = self._client.complete(messages.system, messages.user)
        logger.info("Geração de SQL concluída em %.3f s.", monotonic() - started)
        return _validated_sql(response)

    def clear_sql_cache(self) -> None:
        """Descarte o SQL lembrado nesta instância, sem alterar o banco."""
        self._sql_cache.clear()

    def _execute_sql(self, sql: str, timeout: float) -> QueryResult:
        logger.info("Executando consulta gerada em modo somente leitura.")
        try:
            return execute_readonly(
                self.database_path, sql, allowed_tables=self._allowed_tables,
                max_rows=self.max_rows, timeout_seconds=timeout,
            )
        except QueryBlockedError:
            logger.warning("Consulta gerada bloqueada pelo SQLite.")
            raise
        except QueryExecutionError as exc:
            logger.warning("Falha na execução SQL: %s.", type(exc).__name__)
            raise

    def query(self, question: str) -> AgentResult:
        """Gere ou reutilize SQL da sessão e execute com até uma correção."""
        timeout = validate_query_timeout(self.query_timeout_seconds)
        reference = (
            self.reference_date if self.reference_date is not None else date.today()
        )
        # Valide pergunta e opções também quando houver SQL na memória.
        messages = build_sql_prompt(
            question, self._schema_context,
            reference_date=reference, max_rows=self.max_rows,
        )
        cache_key = (question, reference, self.max_rows)
        sql = self._sql_cache.get(cache_key)
        if sql is None:
            sql = self._generate_sql(messages)
        else:
            logger.info("Reutilizando SQL validado desta sessão.")
        corrected = False
        try:
            result = self._execute_sql(sql, timeout)
        except (DatabaseConnectionError, QueryBlockedError, QueryExecutionError) as error:
            self._sql_cache.pop(cache_key, None)
            if (
                not isinstance(error, QueryExecutionError)
                or not error.recoverable
                or isinstance(error, (QueryTimeoutError, QueryLimitError))
            ):
                raise
            logger.info("Iniciando a única tentativa de correção do SQL.")
            messages = build_sql_correction_prompt(
                question, self._schema_context, sql,
                error.sqlite_error or str(error),
                reference_date=reference, max_rows=self.max_rows,
            )
            started = monotonic()
            response = self._client.complete(messages.system, messages.user)
            logger.info("Correção de SQL recebida em %.3f s.", monotonic() - started)
            sql = _validated_sql(response)
            corrected = True
            # Fora do try da primeira execução: uma nova falha encerra o fluxo.
            result = self._execute_sql(sql, timeout)
        # Guarde somente SQL executado com sucesso, nunca linhas ou respostas.
        self._sql_cache[cache_key] = result.sql
        self._sql_cache.move_to_end(cache_key)
        if len(self._sql_cache) > MAX_CACHED_SQL:
            self._sql_cache.popitem(last=False)
        return AgentResult(
            question=question,
            sql=result.sql,
            columns=result.columns,
            rows=result.rows,
            truncated=result.truncated,
            elapsed_seconds=result.elapsed_seconds,
            correction_attempted=corrected,
        )

    def ask(self, question: str) -> AgentResult:
        """Consulte e redija uma resposta, sem repetir a chamada de redação.

        Resultados vazios e contagens escalares recebem uma mensagem local.
        Falhas na redação conservam o resultado em AnswerGenerationError.result.
        Use query para obter os dados sem enviá-los ao modelo de redação.
        """
        result = self.query(question)
        if not result.rows:
            return replace(result, answer=EMPTY_RESULT_ANSWER)
        count_answer = local_count_answer(result)
        if count_answer is not None:
            logger.info("Contagem redigida localmente, sem chamada adicional ao modelo.")
            return replace(result, answer=count_answer)
        try:
            prompt = build_answer_prompt(result)
            logger.info("Iniciando redação com contexto limitado de resultados.")
            started = monotonic()
            answer = self._client.complete(
                prompt.messages.system, prompt.messages.user, max_tokens=2_048,
            )
            logger.info("Redação pelo modelo concluída em %.3f s.", monotonic() - started)
            if (
                not isinstance(answer, str) or not answer.strip()
                or "\x00" in answer or len(answer) > MAX_ANSWER_CHARS
            ):
                raise InvalidModelResponseError("A resposta em português é inválida.")
        except (LLMServiceError, InvalidModelResponseError, ValueError) as error:
            logger.warning("Falha na redação da resposta: %s.", type(error).__name__)
            raise AnswerGenerationError(result) from error
        notices = []
        if result.truncated:
            notices.append(PARTIAL_RESULT_NOTICE)
        if prompt.context_limited:
            notices.append(LIMITED_CONTEXT_NOTICE)
        return replace(
            result,
            answer="\n\n".join([*notices, answer.strip()]),
            answer_context_limited=prompt.context_limited,
        )
