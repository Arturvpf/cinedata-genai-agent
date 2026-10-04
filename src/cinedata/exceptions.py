"""Erros da aplicação que podem ser apresentados ao usuário."""


class CineDataError(Exception):
    """Erro esperado durante o uso do agente."""


class DatabaseConnectionError(CineDataError):
    """O arquivo SQLite não pôde ser aberto para leitura."""


class DatabaseNotFoundError(DatabaseConnectionError):
    """O caminho informado não aponta para um arquivo existente."""
