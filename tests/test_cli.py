"""CLI com configuração e SQLite reais, sem usar chaves ou serviços externos."""

import builtins
from contextlib import closing
import json
import logging
from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pytest

from cinedata import cli
from cinedata.exceptions import LLMRateLimitError
from cinedata.models import AgentResult


class SimulatedClient:
    def __init__(self) -> None:
        self.complete = Mock()
        self.closed = False
        self.settings = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True


@pytest.fixture
def cli_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for name in ("OPENROUTER_API_KEY", "OPENROUTER_MODEL", "DATABASE_PATH"):
        monkeypatch.delenv(name, raising=False)
    database = tmp_path / "catalog.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(
            "CREATE TABLE dim_movies (sk_movie_id INTEGER PRIMARY KEY, titulo TEXT);"
            "INSERT INTO dim_movies VALUES (1, 'Filme A'), (2, 'Filme B'), (3, 'Filme C');"
        )
    env_file = tmp_path / "test.env"
    env_file.write_text(
        "OPENROUTER_API_KEY=fake-cli-key\nOPENROUTER_MODEL=fake-model\n"
        "DATABASE_PATH=catalog.db\n", encoding="utf-8",
    )
    client = SimulatedClient()

    def make_client(settings):
        client.settings = settings
        return client

    monkeypatch.setattr(cli, "OpenRouterClient", make_client)
    return env_file, database, client


def args_for(env_file, *args):
    return ["--env-file", str(env_file), *args]


def test_one_question_renders_answer_and_closes_client(cli_setup, capsys):
    env_file, database, client = cli_setup
    original = database.read_bytes()
    client.complete.side_effect = ["SELECT COUNT(*) AS total FROM dim_movies", "Existem 3 filmes."]
    status = cli.main(args_for(env_file, "--question", "Quantos filmes?"))
    output = capsys.readouterr()
    assert status == 0
    assert output.out == "Resposta:\nExistem 3 filmes.\n"
    assert not output.err
    assert client.complete.call_count == 2
    assert client.closed
    assert client.settings.database_path == database
    assert database.read_bytes() == original


def test_raw_displays_sql_rows_and_skips_redaction(cli_setup, capsys):
    env_file, _, client = cli_setup
    client.complete.return_value = "SELECT titulo FROM dim_movies ORDER BY sk_movie_id"
    assert cli.main(args_for(env_file, "--raw", "--question", "Filmes")) == 0
    output = capsys.readouterr()
    assert "SQL executado:" in output.out
    assert '["titulo"]' in output.out
    assert '1. ["Filme A"]' in output.out
    assert '3. ["Filme C"]' in output.out
    assert "Resposta:" not in output.out
    assert not output.err
    client.complete.assert_called_once()
    assert client.closed


def test_debug_displays_audit_and_package_logs_without_secrets(cli_setup, capsys):
    env_file, _, client = cli_setup
    client.complete.side_effect = ["SELECT COUNT(*) AS total FROM dim_movies", "Existem 3 filmes."]
    assert cli.main(args_for(env_file, "--debug", "--question", "private-question-marker")) == 0
    output = capsys.readouterr()
    assert "private-question-marker" in output.out
    assert "SQL executado:" in output.out
    assert "Linhas retornadas: 1" in output.out
    assert "Tempo da consulta SQLite final:" in output.out
    assert "Resposta:" in output.out
    assert "INFO:" in output.err
    for secret in ("private-question-marker", "SELECT COUNT", "Existem 3 filmes", "fake-cli-key"):
        assert secret not in output.err


@pytest.mark.parametrize("command", ["sair", "EXIT", "Quit", "  sair  "])
def test_exit_commands_do_not_call_model(cli_setup, monkeypatch, capsys, command):
    env_file, _, client = cli_setup
    monkeypatch.setattr(builtins, "input", Mock(return_value=command))
    assert cli.main(args_for(env_file)) == 0
    assert "Sessão encerrada." in capsys.readouterr().out
    client.complete.assert_not_called()
    assert client.closed


def test_blank_and_invalid_interactive_questions_skip_model(cli_setup, monkeypatch, capsys):
    env_file, _, client = cli_setup
    monkeypatch.setattr(builtins, "input", Mock(side_effect=["", "  ", "x" * 4_001, "x\x00", "sair"]))
    assert cli.main(args_for(env_file)) == 0
    assert capsys.readouterr().err.count("A pergunta deve ter") == 2
    client.complete.assert_not_called()
    assert client.closed


def test_interactive_error_allows_next_user_question_without_automatic_retry(
    cli_setup, monkeypatch, capsys,
):
    env_file, _, client = cli_setup
    client.complete.side_effect = [LLMRateLimitError("Limite de requisições."),
                                   "SELECT COUNT(*) AS total FROM dim_movies"]
    monkeypatch.setattr(builtins, "input", Mock(side_effect=["Primeira", "Segunda", "sair"]))
    assert cli.main(args_for(env_file, "--raw")) == 0
    output = capsys.readouterr()
    assert "Limite de requisições." in output.err
    assert "1. [3]" in output.out
    assert "Modo raw:" in output.out
    assert client.complete.call_count == 2
    assert json.loads(client.complete.call_args_list[0].args[1])["question"] == "Primeira"
    assert json.loads(client.complete.call_args_list[1].args[1])["question"] == "Segunda"
    assert client.closed


def test_interactive_eof_is_normal_exit(cli_setup, monkeypatch, capsys):
    env_file, _, client = cli_setup
    monkeypatch.setattr(builtins, "input", Mock(side_effect=EOFError))
    assert cli.main(args_for(env_file)) == 0
    assert "Sessão encerrada." in capsys.readouterr().out
    assert client.closed
    client.complete.assert_not_called()


@pytest.mark.parametrize("during_request", [False, True])
def test_ctrl_c_closes_client_and_returns_130(cli_setup, monkeypatch, capsys, during_request):
    env_file, _, client = cli_setup
    if during_request:
        client.complete.side_effect = KeyboardInterrupt
        args = args_for(env_file, "--question", "Filmes")
    else:
        monkeypatch.setattr(builtins, "input", Mock(side_effect=KeyboardInterrupt))
        args = args_for(env_file)
    assert cli.main(args) == 130
    output = capsys.readouterr()
    assert "Operação interrompida pelo usuário." in output.err
    assert "Traceback" not in output.err
    assert client.closed


def test_missing_api_key_is_reported_before_creating_client(cli_setup, capsys):
    env_file, _, client = cli_setup
    env_file.write_text("DATABASE_PATH=catalog.db\n", encoding="utf-8")
    assert cli.main(args_for(env_file, "--question", "Filmes")) == 2
    output = capsys.readouterr()
    assert "OPENROUTER_API_KEY não está configurada" in output.err
    assert "Traceback" not in output.err
    assert client.settings is None
    client.complete.assert_not_called()


def test_missing_database_closes_client_before_any_request(cli_setup, capsys):
    env_file, database, client = cli_setup
    database.unlink()
    assert cli.main(args_for(env_file, "--question", "Filmes")) == 2
    output = capsys.readouterr()
    assert "Banco de dados não encontrado" in output.err
    assert "Traceback" not in output.err
    assert client.closed
    client.complete.assert_not_called()
    assert not database.exists()


def test_one_question_api_failure_has_error_exit_and_no_retry(cli_setup, capsys):
    env_file, _, client = cli_setup
    client.complete.side_effect = LLMRateLimitError("Aguarde antes de tentar novamente.")
    assert cli.main(args_for(env_file, "--question", "Filmes")) == 1
    output = capsys.readouterr()
    assert "Aguarde antes de tentar novamente." in output.err
    assert "Traceback" not in output.err
    client.complete.assert_called_once()
    assert client.closed


def test_blocked_query_is_reported_and_keeps_database(cli_setup, capsys):
    env_file, database, client = cli_setup
    original = database.read_bytes()
    client.complete.return_value = "DROP TABLE dim_movies"
    assert cli.main(args_for(env_file, "--raw", "--question", "Apague")) == 1
    output = capsys.readouterr()
    assert "Erro:" in output.err
    assert "SQL executado:" not in output.out
    assert database.read_bytes() == original
    client.complete.assert_called_once()
    assert client.closed


def test_failed_answer_shows_saved_result_and_returns_failure(cli_setup, capsys):
    env_file, _, client = cli_setup
    client.complete.side_effect = ["SELECT COUNT(*) AS total FROM dim_movies", LLMRateLimitError("Limite")]
    assert cli.main(args_for(env_file, "--question", "Total")) == 1
    output = capsys.readouterr()
    assert "resultado SQL permanece disponível" in output.err
    assert "SQL executado:" in output.out
    assert "1. [3]" in output.out
    assert "Resposta:" not in output.out
    assert client.complete.call_count == 2
    assert client.closed


def test_empty_result_needs_no_redaction(cli_setup, capsys):
    env_file, _, client = cli_setup
    client.complete.return_value = "SELECT titulo FROM dim_movies WHERE 0"
    assert cli.main(args_for(env_file, "--question", "Nenhum")) == 0
    assert "Nenhum resultado foi encontrado" in capsys.readouterr().out
    client.complete.assert_called_once()


def test_cli_passes_execution_limits_and_displays_partial_result(cli_setup, capsys):
    env_file, _, client = cli_setup
    client.complete.return_value = "SELECT titulo FROM dim_movies ORDER BY sk_movie_id"
    assert cli.main(args_for(env_file, "--raw", "--max-rows", "1", "--query-timeout", "0.5",
                             "--question", "Filmes")) == 0
    output = capsys.readouterr()
    assert '1. ["Filme A"]' in output.out
    assert "Filme B" not in output.out
    assert "Resultado parcial:" in output.out
    assert json.loads(client.complete.call_args.args[1])["result_row_limit"] == 1


def test_cli_shows_one_correction_and_no_answer_call_in_raw_mode(cli_setup, capsys):
    env_file, _, client = cli_setup
    client.complete.side_effect = ["SELECT missing FROM dim_movies", "SELECT COUNT(*) FROM dim_movies"]
    assert cli.main(args_for(env_file, "--raw", "--question", "Total")) == 0
    assert "Foi utilizada uma tentativa de correção do SQL." in capsys.readouterr().out
    assert client.complete.call_count == 2


@pytest.mark.parametrize("arguments", [
    ["--max-rows", "0"], ["--max-rows", "1001"], ["--max-rows", "abc"],
    ["--query-timeout", "nan"], ["--query-timeout", "inf"], ["--query-timeout", "0"],
    ["--query-timeout", "61"], ["--query-timeout", "abc"],
    ["--question", ""], pytest.param(["--question", "x" * 4001], id="long-question"),
    ["--quest", "Filmes"], ["--unknown"], ["--question"],
])
def test_invalid_arguments_fail_before_configuration_or_api(cli_setup, capsys, arguments):
    _, _, client = cli_setup
    with pytest.raises(SystemExit) as caught:
        cli.main(arguments)
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert "uso: cinedata" in output.err
    assert "erro:" in output.err
    assert client.settings is None
    client.complete.assert_not_called()


def test_help_does_not_read_configuration_or_open_client(cli_setup, monkeypatch, capsys):
    _, _, client = cli_setup
    settings = Mock(side_effect=AssertionError("Should not read env"))
    monkeypatch.setattr(cli, "load_settings", settings)
    with pytest.raises(SystemExit) as caught:
        cli.main(["--help"])
    assert caught.value.code == 0
    output = capsys.readouterr()
    assert "opções:" in output.out
    assert "--raw" in output.out
    assert "--question" in output.out
    assert not output.err
    settings.assert_not_called()
    assert client.settings is None


def test_display_limits_text_and_binary_without_changing_raw_values(capsys):
    text = "x" * 1_000
    result = AgentResult(
        question="Valores", sql="SELECT a, a, b FROM dados", columns=("a", "a", "b"),
        rows=((text, b"private-binary-value", None),), truncated=False, elapsed_seconds=0.1,
    )
    cli.show_result(result, raw=True, debug=False)
    output = capsys.readouterr().out
    assert '["a", "a", "b"]' in output
    assert '[BLOB: 20 bytes]' in output
    assert "NULL" in output
    assert "Exibição abreviada:" in output
    assert "private-binary-value" not in output
    assert text not in output
    assert result.rows == ((text, b"private-binary-value", None),)


def test_terminal_controls_in_sql_columns_and_answer_are_escaped(capsys):
    control = "\x1b[2J\x9b"
    result = AgentResult(
        question=control, sql="SELECT '" + control + "'", columns=(control,), rows=((control,),),
        truncated=False, elapsed_seconds=0.1, answer="Resposta " + control + "\nFim.",
    )
    cli.show_result(result, raw=False, debug=True)
    output = capsys.readouterr().out
    assert "\x1b" not in output
    assert "\x9b" not in output
    assert "\\u001b" in output
    assert "\\u009b" in output
    assert "\nFim." in output


def test_cli_restores_logger_state_after_running(cli_setup, capsys):
    env_file, _, client = cli_setup
    client.complete.return_value = "SELECT COUNT(*) FROM dim_movies"
    package_logger = logging.getLogger("cinedata")
    before = package_logger.level, package_logger.propagate, package_logger.handlers[:]
    assert cli.main(args_for(env_file, "--raw", "--debug", "--question", "Total")) == 0
    assert (package_logger.level, package_logger.propagate, package_logger.handlers) == before


def test_unexpected_error_has_no_traceback_or_sensitive_message(cli_setup, capsys):
    env_file, _, client = cli_setup
    client.complete.side_effect = RuntimeError("private-unexpected-marker")
    assert cli.main(args_for(env_file, "--question", "Total")) == 1
    output = capsys.readouterr()
    assert "falha inesperada" in output.err
    assert "RuntimeError" in output.err
    assert "private-unexpected-marker" not in output.err
    assert "Traceback" not in output.err
    assert client.closed
