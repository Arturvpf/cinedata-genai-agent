"""Configuração testada com arquivos temporários e chaves fictícias."""

from dataclasses import FrozenInstanceError
import os
from pathlib import Path

import pytest

from cinedata.config import DEFAULT_MODEL, load_settings
from cinedata.exceptions import ConfigurationError, MissingAPIKeyError


FAKE_KEY = "test-key-for-config-only"


def write_env(tmp_path: Path, content: str, *, encoding: str = "utf-8") -> Path:
    path = tmp_path / ".env"
    path.write_text(content, encoding=encoding)
    return path


def test_reads_defaults_with_environment_only(tmp_path: Path) -> None:
    settings = load_settings(
        tmp_path / ".env", environ={"OPENROUTER_API_KEY": FAKE_KEY},
    )
    assert settings.api_key == FAKE_KEY
    assert settings.model == DEFAULT_MODEL
    assert settings.database_path == (tmp_path / "cinerocket.db").resolve()
    assert not settings.database_path.exists()
    assert not (tmp_path / ".env").exists()


def test_reads_env_quotes_comments_and_unicode_paths(tmp_path: Path) -> None:
    path = write_env(
        tmp_path,
        "# Configuração local\n"
        f"OPENROUTER_API_KEY='{FAKE_KEY}'\n"
        'OPENROUTER_MODEL="provider/model" # Modelo escolhido\n'
        'DATABASE_PATH="dados/catálogo #100%.db"\n',
    )
    settings = load_settings(path, environ={})
    assert settings.api_key == FAKE_KEY
    assert settings.model == "provider/model"
    assert settings.database_path == (tmp_path / "dados/catálogo #100%.db").resolve()


def test_accepts_windows_utf8_bom(tmp_path: Path) -> None:
    path = write_env(
        tmp_path, f"OPENROUTER_API_KEY={FAKE_KEY}\n", encoding="utf-8-sig",
    )
    assert load_settings(path, environ={}).api_key == FAKE_KEY


def test_environment_overrides_all_file_values(tmp_path: Path) -> None:
    path = write_env(
        tmp_path,
        "OPENROUTER_API_KEY=file-key\nOPENROUTER_MODEL=file/model\n"
        "DATABASE_PATH=file.db\n",
    )
    environment = {
        "OPENROUTER_API_KEY": FAKE_KEY,
        "OPENROUTER_MODEL": "environment/model",
        "DATABASE_PATH": "environment.db",
    }
    settings = load_settings(path, environ=environment)
    assert settings.api_key == FAKE_KEY
    assert settings.model == "environment/model"
    assert settings.database_path == (tmp_path / "environment.db").resolve()


def test_combines_file_environment_and_defaults(tmp_path: Path) -> None:
    path = write_env(tmp_path, "DATABASE_PATH=local.db\n")
    settings = load_settings(path, environ={"OPENROUTER_API_KEY": FAKE_KEY})
    assert settings.model == DEFAULT_MODEL
    assert settings.database_path.name == "local.db"


def test_reads_actual_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = write_env(tmp_path, "OPENROUTER_API_KEY=file-key\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    monkeypatch.setenv("OPENROUTER_MODEL", "process/model")
    monkeypatch.setenv("DATABASE_PATH", "process.db")
    settings = load_settings(path)
    assert settings.api_key == FAKE_KEY
    assert settings.model == "process/model"
    assert settings.database_path == (tmp_path / "process.db").resolve()


@pytest.mark.parametrize("key", [None, "", " ", "\t"])
def test_missing_or_empty_api_key_has_friendly_error(
    tmp_path: Path, key: str | None,
) -> None:
    environment = {} if key is None else {"OPENROUTER_API_KEY": key}
    with pytest.raises(MissingAPIKeyError, match="não está configurada"):
        load_settings(tmp_path / ".env", environ=environment)


def test_empty_environment_key_does_not_fall_back_to_file(tmp_path: Path) -> None:
    path = write_env(tmp_path, f"OPENROUTER_API_KEY={FAKE_KEY}\n")
    with pytest.raises(MissingAPIKeyError):
        load_settings(path, environ={"OPENROUTER_API_KEY": ""})


@pytest.mark.parametrize("name", ["OPENROUTER_MODEL", "DATABASE_PATH"])
@pytest.mark.parametrize("empty", ["", " ", "\t"])
def test_explicit_empty_settings_are_rejected(
    tmp_path: Path, name: str, empty: str,
) -> None:
    with pytest.raises(ConfigurationError, match=name):
        load_settings(
            tmp_path / ".env",
            environ={"OPENROUTER_API_KEY": FAKE_KEY, name: empty},
        )


@pytest.mark.parametrize("name", ["OPENROUTER_MODEL", "DATABASE_PATH"])
def test_bare_env_variables_are_rejected(tmp_path: Path, name: str) -> None:
    path = write_env(tmp_path, f"OPENROUTER_API_KEY={FAKE_KEY}\n{name}\n")
    with pytest.raises(ConfigurationError, match=name):
        load_settings(path, environ={})


@pytest.mark.parametrize(
    "name", ["OPENROUTER_API_KEY", "OPENROUTER_MODEL", "DATABASE_PATH"],
)
@pytest.mark.parametrize("invalid", ["hidden\x00value", "hidden\nvalue"])
def test_control_characters_are_rejected_without_exposing_values(
    tmp_path: Path, name: str, invalid: str,
) -> None:
    with pytest.raises(ConfigurationError) as error:
        load_settings(
            tmp_path / ".env",
            environ={"OPENROUTER_API_KEY": FAKE_KEY, name: invalid},
        )
    assert name in str(error.value)
    assert "hidden" not in str(error.value)


def test_trims_surrounding_whitespace(tmp_path: Path) -> None:
    settings = load_settings(
        tmp_path / ".env",
        environ={"OPENROUTER_API_KEY": f" {FAKE_KEY} ",
                 "OPENROUTER_MODEL": " provider/model ",
                 "DATABASE_PATH": " movies.db "},
    )
    assert settings.api_key == FAKE_KEY
    assert settings.model == "provider/model"
    assert settings.database_path.name == "movies.db"


def test_default_env_file_uses_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_env(tmp_path, f"OPENROUTER_API_KEY={FAKE_KEY}\n")
    monkeypatch.chdir(tmp_path)
    settings = load_settings(environ={})
    assert settings.api_key == FAKE_KEY
    assert settings.database_path == (tmp_path / "cinerocket.db").resolve()


def test_relative_database_uses_env_directory_even_from_another_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_directory = tmp_path / "configuration"
    config_directory.mkdir()
    path = write_env(
        config_directory,
        f"OPENROUTER_API_KEY={FAKE_KEY}\nDATABASE_PATH=../data/movies.db\n",
    )
    other_directory = tmp_path / "other"
    other_directory.mkdir()
    monkeypatch.chdir(other_directory)
    settings = load_settings(path, environ={})
    assert settings.database_path == (tmp_path / "data/movies.db").resolve()


def test_preserves_absolute_database_path(tmp_path: Path) -> None:
    absolute = (tmp_path / "data/movies.db").resolve()
    settings = load_settings(
        tmp_path / ".env",
        environ={"OPENROUTER_API_KEY": FAKE_KEY, "DATABASE_PATH": str(absolute)},
    )
    assert settings.database_path == absolute


def test_expands_home_directory_in_database_path(tmp_path: Path) -> None:
    settings = load_settings(
        tmp_path / ".env",
        environ={"OPENROUTER_API_KEY": FAKE_KEY, "DATABASE_PATH": "~/movies.db"},
    )
    assert settings.database_path == (Path.home() / "movies.db").resolve()


def test_does_not_search_parent_env_files(tmp_path: Path) -> None:
    write_env(tmp_path, f"OPENROUTER_API_KEY={FAKE_KEY}\n")
    nested = tmp_path / "nested"
    nested.mkdir()
    with pytest.raises(MissingAPIKeyError):
        load_settings(nested / ".env", environ={})


def test_does_not_mutate_process_or_supplied_environment(tmp_path: Path) -> None:
    path = write_env(tmp_path, f"OPENROUTER_API_KEY={FAKE_KEY}\nUNRELATED=value\n")
    environment = {}
    before = dict(os.environ)
    load_settings(path, environ=environment)
    assert dict(os.environ) == before
    assert environment == {}


def test_does_not_expand_environment_references(tmp_path: Path) -> None:
    path = write_env(
        tmp_path, f"OPENROUTER_API_KEY={FAKE_KEY}\nDATABASE_PATH='${{OTHER}}.db'\n",
    )
    settings = load_settings(path, environ={"OTHER": "unrelated-value"})
    assert settings.database_path.name == "${OTHER}.db"


def test_key_is_hidden_in_text_and_no_config_values_are_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("DEBUG"):
        settings = load_settings(
            tmp_path / ".env", environ={"OPENROUTER_API_KEY": FAKE_KEY},
        )
    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in str(settings)
    assert FAKE_KEY not in caplog.text
    assert settings.api_key == FAKE_KEY


def test_settings_are_immutable(tmp_path: Path) -> None:
    settings = load_settings(
        tmp_path / ".env", environ={"OPENROUTER_API_KEY": FAKE_KEY},
    )
    with pytest.raises(FrozenInstanceError):
        settings.model = "other/model"


def test_env_directory_is_not_treated_as_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="ler o arquivo"):
        load_settings(tmp_path, environ={"OPENROUTER_API_KEY": FAKE_KEY})


def test_invalid_utf8_has_friendly_error(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(ConfigurationError, match="UTF-8"):
        load_settings(path, environ={"OPENROUTER_API_KEY": FAKE_KEY})


def test_unreadable_env_has_friendly_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreadable(*args, **kwargs):
        raise PermissionError("access denied")

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(ConfigurationError, match="permissão de leitura"):
        load_settings(tmp_path / ".env", environ={"OPENROUTER_API_KEY": FAKE_KEY})
