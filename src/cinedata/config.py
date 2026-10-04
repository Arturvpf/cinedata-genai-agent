"""Configuração local sem alterar o ambiente nem realizar chamadas externas."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from io import StringIO
import os
from pathlib import Path

from dotenv import dotenv_values

from cinedata.exceptions import ConfigurationError, MissingAPIKeyError


DEFAULT_MODEL = "openrouter/free"
DEFAULT_DATABASE_PATH = "cinerocket.db"


@dataclass(frozen=True)
class Settings:
    """Configurações validadas; a representação textual omite a chave."""

    api_key: str = field(repr=False)
    model: str
    database_path: Path


def _read_env_file(path: Path) -> dict[str, str | None]:
    try:
        content = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError) as exc:
        raise ConfigurationError(
            "Não foi possível ler o arquivo .env. "
            "Verifique a permissão de leitura e a codificação UTF-8."
        ) from exc
    return dict(dotenv_values(stream=StringIO(content), interpolate=False))


def _required_text(name: str, value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        if name == "OPENROUTER_API_KEY":
            raise MissingAPIKeyError(
                "A variável OPENROUTER_API_KEY não está configurada. "
                "Preencha o .env local ou defina a variável de ambiente."
            )
        raise ConfigurationError(f"A variável {name} não pode ficar vazia.")
    text = value.strip()
    if any(ord(character) < 32 for character in text):
        raise ConfigurationError(f"A variável {name} contém caracteres inválidos.")
    return text


def load_settings(
    env_file: str | Path = ".env", *, environ: Mapping[str, str] | None = None,
) -> Settings:
    """Leia o arquivo indicado e priorize variáveis já definidas no ambiente.

    Não procura .env em pastas superiores e não altera os.environ. O arquivo
    é opcional; a chave é obrigatória. Caminhos relativos do banco usam a
    pasta do .env, mesmo quando ele não existe. Não abre nem cria o banco.
    """
    try:
        env_path = Path(env_file).expanduser().resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise ConfigurationError("O caminho do arquivo .env é inválido.") from exc
    file_values = _read_env_file(env_path)
    environment = os.environ if environ is None else environ

    def value(name: str, default: str | None = None) -> str:
        selected = (
            environment[name] if name in environment
            else file_values.get(name, default)
        )
        return _required_text(name, selected)

    api_key = value("OPENROUTER_API_KEY")
    model = value("OPENROUTER_MODEL", DEFAULT_MODEL)
    database_value = value("DATABASE_PATH", DEFAULT_DATABASE_PATH)
    try:
        database_path = Path(database_value).expanduser()
        if not database_path.is_absolute():
            database_path = env_path.parent / database_path
        database_path = database_path.resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise ConfigurationError("O caminho em DATABASE_PATH é inválido.") from exc
    return Settings(api_key=api_key, model=model, database_path=database_path)
