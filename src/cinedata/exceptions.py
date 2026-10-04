"""Erros da aplicação que podem ser apresentados ao usuário."""


class CineDataError(Exception):
    """Erro esperado durante o uso do agente."""


class ConfigurationError(CineDataError):
    """Configuração local ausente, vazia ou inválida."""


class MissingAPIKeyError(ConfigurationError):
    """A chave OpenRouter não foi configurada."""


class DatabaseConnectionError(CineDataError):
    """O arquivo SQLite não pôde ser aberto para leitura."""


class DatabaseNotFoundError(DatabaseConnectionError):
    """O caminho informado não aponta para um arquivo existente."""


class SchemaInspectionError(CineDataError):
    """Não foi possível consultar a estrutura do banco SQLite."""


class InvalidModelResponseError(CineDataError):
    """A resposta do modelo não segue o formato esperado."""


class LLMServiceError(CineDataError):
    """Falha ao acessar o modelo, sem expor o corpo da resposta HTTP."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMAuthenticationError(LLMServiceError):
    """A API recusou a chave ou o acesso ao modelo."""


class LLMRateLimitError(LLMServiceError):
    """A API informou um limite de requisições ou capacidade."""


class LLMTimeoutError(LLMServiceError):
    """A chamada ao modelo excedeu o timeout de rede."""


class QueryBlockedError(CineDataError):
    """A consulta viola as regras de segurança do agente."""


class QueryExecutionError(CineDataError):
    """Falha SQLite; apenas erros de SQL podem permitir uma correção."""

    def __init__(
        self, message: str, *, sqlite_error: str | None = None,
        recoverable: bool = False,
    ) -> None:
        super().__init__(message)
        self.sqlite_error = sqlite_error
        self.recoverable = recoverable


class QueryTimeoutError(QueryExecutionError):
    """A consulta ultrapassou o tempo permitido."""


class QueryLimitError(QueryExecutionError):
    """A consulta ou o resultado ultrapassou limite de tamanho ou complexidade."""
