"""Erros da aplicação que podem ser apresentados ao usuário."""


class CineDataError(Exception):
    """Erro esperado durante o uso do agente."""


class DatabaseConnectionError(CineDataError):
    """O arquivo SQLite não pôde ser aberto para leitura."""


class DatabaseNotFoundError(DatabaseConnectionError):
    """O caminho informado não aponta para um arquivo existente."""


class SchemaInspectionError(CineDataError):
    """Não foi possível consultar a estrutura do banco SQLite."""


class InvalidModelResponseError(CineDataError):
    """A resposta do modelo não segue o formato esperado."""


class QueryBlockedError(CineDataError):
    """A consulta viola as regras de segurança do agente."""
