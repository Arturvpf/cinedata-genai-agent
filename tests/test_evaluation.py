"""Critérios das consultas verificados em dados pequenos com casos adversos."""

from contextlib import closing
from dataclasses import replace
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3

import pytest

from cinedata.database import execute_readonly, readonly_connection
from cinedata.evaluation import (
    EvaluationDatasetError,
    load_evaluation_questions,
    parse_reference_date,
)
from cinedata.schema import inspect_schema
from scripts import evaluate_reference_queries as script


DATASET = Path(__file__).with_name("evaluation_questions.json")
CASES = load_evaluation_questions(DATASET)
BY_ID = {case.id: case for case in CASES}


@pytest.fixture
def evaluation_database(tmp_path: Path) -> Path:
    path = tmp_path / "evaluation.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript("""
            CREATE TABLE dim_movies (
                sk_movie_id INTEGER PRIMARY KEY, titulo TEXT,
                data_lancamento TEXT, ano_lancamento INTEGER
            );
            INSERT INTO dim_movies VALUES
                (1, 'Alpha', '2021-10-04', 2021),
                (2, 'Beta', '2021-10-03', 2021),
                (3, 'Gamma', '2026-10-04', 2026),
                (4, 'Delta', '2026-10-05', 2026),
                (5, 'Epsilon', NULL, NULL),
                (6, 'Zeta', '2025-01-01', 2025),
                (7, 'Eta', '2020-01-01', 2020),
                (8, 'Theta', '2022-01-01', 2022),
                (9, 'Grande perda', '2024-01-01', 2024),
                (10, 'Receita ausente', '2024-02-01', 2024);
            CREATE TABLE fact_movies_performance (
                sk_movie_id INTEGER PRIMARY KEY REFERENCES dim_movies,
                orcamento_usd REAL, receita_usd REAL,
                popularidade REAL, nota_tmdb REAL, nota_imdb REAL
            );
            INSERT INTO fact_movies_performance VALUES
                (1, 100, 200, 10, 8, 8), (2, 200, 50, 30, 9, 6),
                (3, NULL, 500, NULL, NULL, 7), (4, 0, 100, 20, 5, 5),
                (5, 100, NULL, 15, 4, NULL), (6, 100, 100, 25, 8, 9),
                (7, 50, 200, 5, 8, 8), (8, 100, 100, 2, 10, 9),
                (9, 120000000, 50000000, NULL, NULL, NULL),
                (10, 120000000, NULL, NULL, NULL, NULL);
            CREATE TABLE dim_genres (sk_genre_id INTEGER PRIMARY KEY, nome_genero TEXT);
            INSERT INTO dim_genres VALUES (1, 'Drama'), (2, 'Comedia'), (3, 'Sem filmes');
            CREATE TABLE bridge_movie_genre (
                sk_movie_id INTEGER REFERENCES dim_movies,
                sk_genre_id INTEGER REFERENCES dim_genres,
                PRIMARY KEY (sk_movie_id, sk_genre_id)
            );
            INSERT INTO bridge_movie_genre VALUES
                (1,1), (2,1), (3,1), (4,1), (5,1), (1,2), (6,2), (7,2), (8,2);
            CREATE TABLE dim_companies (
                sk_company_id INTEGER PRIMARY KEY, nome_produtora TEXT
            );
            INSERT INTO dim_companies VALUES (1, 'Empresa A'), (2, 'Empresa B');
            CREATE TABLE bridge_movie_company (
                sk_movie_id INTEGER REFERENCES dim_movies,
                sk_company_id INTEGER REFERENCES dim_companies,
                PRIMARY KEY (sk_movie_id, sk_company_id)
            );
            INSERT INTO bridge_movie_company VALUES
                (1,1), (2,1), (3,1), (4,1), (5,1), (6,2), (7,2), (8,2);
            CREATE TABLE dim_people (
                sk_person_id INTEGER PRIMARY KEY, nome_pessoa TEXT, tipo_pessoa TEXT
            );
            INSERT INTO dim_people VALUES
                (101, 'Ator A', 'Ator'), (102, 'Ator B', 'Ator'),
                (201, 'Diretor A', 'Diretor'), (202, 'Diretor B', 'Diretor'),
                (301, 'Roteirista', 'Roteirista');
            CREATE TABLE bridge_movie_person (
                sk_movie_id INTEGER REFERENCES dim_movies,
                sk_person_id INTEGER REFERENCES dim_people,
                PRIMARY KEY (sk_movie_id, sk_person_id)
            );
            INSERT INTO bridge_movie_person VALUES
                (1,101), (2,101), (4,101), (3,102), (6,102), (8,102),
                (1,201), (2,201), (3,201), (4,201), (5,201), (6,201),
                (7,202), (8,202), (1,301);
            CREATE TABLE dim_reviews (
                sk_review_id INTEGER PRIMARY KEY, sk_movie_id INTEGER REFERENCES dim_movies,
                qtd_avaliacoes_usuarios INTEGER, nota_media_usuarios REAL
            );
            INSERT INTO dim_reviews VALUES
                (1,1,100,7), (2,2,80,3), (3,6,1000,9), (4,3,NULL,9), (5,7,50,NULL);
            CREATE TABLE movie_reviews (
                id INTEGER PRIMARY KEY, sk_movie_id INTEGER REFERENCES dim_movies, rating REAL
            );
            INSERT INTO movie_reviews VALUES (1,1,7), (2,1,8), (3,1,9);
        """)
    return path


def run_case(path, case_id, *, reference_date=None):
    with readonly_connection(path) as connection:
        allowed = {table.name for table in inspect_schema(connection)}
    return execute_readonly(
        path, BY_ID[case_id].sql(reference_date=reference_date), allowed_tables=allowed,
        max_rows=1_000, timeout_seconds=5,
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_each_reference_executes_on_real_sqlite_without_mutation(evaluation_database, case):
    original = evaluation_database.read_bytes()
    result = run_case(evaluation_database, case.id)
    assert result.columns
    assert result.rows
    assert not result.truncated
    assert evaluation_database.read_bytes() == original


def test_revenue_synonyms_have_same_values_and_stable_ties(evaluation_database):
    revenue = run_case(evaluation_database, "top_receita").rows
    assert revenue == run_case(evaluation_database, "sinonimo_bilheteria").rows
    assert revenue == run_case(evaluation_database, "sinonimo_faturamento").rows
    assert [row[0] for row in revenue] == [
        "Grande perda", "Gamma", "Alpha", "Eta", "Delta", "Zeta", "Theta", "Beta",
    ]
    assert "Receita ausente" not in [row[0] for row in revenue]


def test_profit_averages_handle_nulls_and_multiple_genres(evaluation_database):
    rows = run_case(evaluation_database, "lucro_medio_genero").rows
    assert rows[0] == ("Comedia", 62.5)
    assert rows[1][0] == "Drama"
    assert rows[1][1] == pytest.approx(50 / 3)


def test_margins_exclude_zero_and_missing_budgets(evaluation_database):
    rows = run_case(evaluation_database, "top_margem").rows
    assert [row[0] for row in rows] == ["Eta", "Alpha", "Zeta", "Theta", "Grande perda", "Beta"]
    assert [row[1] for row in rows] == pytest.approx([300, 100, 0, 0, -100 * 70 / 120, -75])
    assert run_case(evaluation_database, "genero_maior_margem").rows == (("Comedia", 100.0),)


def test_popularity_and_absolute_rating_difference(evaluation_database):
    assert [row[0] for row in run_case(evaluation_database, "top_popularidade").rows] == [
        "Beta", "Zeta", "Delta", "Epsilon", "Alpha",
    ]
    rows = run_case(evaluation_database, "divergencia_tmdb_imdb").rows
    assert rows[0] == ("Beta", 9.0, 6.0, 3.0)
    assert "Gamma" not in [row[0] for row in rows]


def test_actor_window_uses_both_boundaries_and_excludes_future(evaluation_database):
    assert run_case(evaluation_database, "ator_ultimos_cinco_anos").rows == (("Ator B", 3),)
    # Mudar a referência inclui Beta (limite inferior) e exclui Gamma (limite superior).
    assert run_case(evaluation_database, "ator_ultimos_cinco_anos",
                    reference_date=date(2026, 10, 3)).rows == (("Ator A", 2),)


def test_director_having_counts_only_rated_films(evaluation_database):
    assert run_case(evaluation_database, "diretores_nota_minimo_cinco").rows == (
        ("Diretor A", 5, 7.0),
    )
    assert run_case(evaluation_database, "diretores_mais_filmes").rows == (
        ("Diretor A", 6), ("Diretor B", 2),
    )


def test_actor_director_pair_groups_correct_roles(evaluation_database):
    assert run_case(evaluation_database, "dupla_ator_diretor").rows == (
        ("Ator A", "Diretor A", 3),
    )


def test_genre_counts_include_empty_genres_and_do_not_multiply_joins(evaluation_database):
    assert run_case(evaluation_database, "filmes_por_genero").rows == (
        ("Drama", 5), ("Comedia", 4), ("Sem filmes", 0),
    )
    assert run_case(evaluation_database, "produtora_maior_lucro").rows == (("Empresa B", 150.0),)
    assert run_case(evaluation_database, "produtoras_mais_filmes").rows == (
        ("Empresa A", 5), ("Empresa B", 3),
    )


def test_user_reviews_use_aggregated_counts_not_individual_rows(evaluation_database):
    assert run_case(evaluation_database, "mais_avaliados_usuarios").rows[0] == ("Zeta", 1000)
    rows = run_case(evaluation_database, "divergencia_usuarios_imdb").rows
    assert rows[0] == ("Beta", 3.0, 6.0, 3.0)
    assert "Eta" not in [row[0] for row in rows]


def test_year_aggregates_and_unweighted_genre_means(evaluation_database):
    assert run_case(evaluation_database, "ano_maior_receita").rows == ((2024, 50000000.0),)
    assert run_case(evaluation_database, "imdb_por_ano").rows == (
        (2020, 8.0), (2021, 7.0), (2022, 9.0), (2025, 9.0), (2026, 6.0),
    )
    assert run_case(evaluation_database, "generos_nota_imdb").rows == (
        ("Comedia", 8.5), ("Drama", 6.5),
    )


def test_high_budget_question_has_explicit_threshold_and_excludes_missing_revenue(evaluation_database):
    assert run_case(evaluation_database, "orcamento_alto_receita_baixa").rows == (
        ("Grande perda", 120000000.0, 50000000.0),
    )


@pytest.mark.parametrize("mutation", [
    {"id": "bad id"}, {"question": ""}, {"question": "text\x00"},
    {"criteria": []}, {"criteria": [False]}, {"category": None},
    {"reference_sql": "DROP TABLE dim_movies"}, {"reference_sql": "SELECT 1; SELECT 2"},
    {"reference_date": "2026-02-30"}, {"reference_date": "20261004"},
    {"reference_sql": "SELECT '{reference_date}'", "reference_date": None},
])
def test_invalid_dataset_fields_are_rejected_before_execution(tmp_path, mutation):
    item = json.loads(DATASET.read_text(encoding="utf-8"))[0]
    item.update(mutation)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([item]), encoding="utf-8")
    with pytest.raises(EvaluationDatasetError):
        load_evaluation_questions(path)


@pytest.mark.parametrize("data", [[], {}, [False], [{"id": "incomplete"}]])
def test_invalid_dataset_shape(tmp_path, data):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(EvaluationDatasetError):
        load_evaluation_questions(path)


def test_duplicate_ids_are_rejected(tmp_path):
    item = json.loads(DATASET.read_text(encoding="utf-8"))[0]
    path = tmp_path / "duplicates.json"
    path.write_text(json.dumps([item, item]), encoding="utf-8")
    with pytest.raises(EvaluationDatasetError):
        load_evaluation_questions(path)


@pytest.mark.parametrize("content", [b"not json", b"\xff", b"[" * 2_000])
def test_unreadable_or_malformed_json_has_friendly_error(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_bytes(content)
    with pytest.raises(EvaluationDatasetError):
        load_evaluation_questions(path)


def test_missing_and_oversized_dataset(tmp_path):
    with pytest.raises(EvaluationDatasetError):
        load_evaluation_questions(tmp_path / "missing.json")
    path = tmp_path / "large.json"
    path.write_text(" " * 1_000_001, encoding="utf-8")
    with pytest.raises(EvaluationDatasetError, match="tamanho"):
        load_evaluation_questions(path)


@pytest.mark.parametrize("value", ["2026-02-30", "20261004", "2026-W40-7", "2026-10-04T00:00:00", None])
def test_reference_date_is_strict_iso(value):
    with pytest.raises(ValueError):
        parse_reference_date(value)


def test_sql_substitution_only_accepts_date_not_text_or_datetime():
    case = BY_ID["ator_ultimos_cinco_anos"]
    with pytest.raises(ValueError):
        case.sql(reference_date="2026-10-04'; DROP TABLE dim_movies")
    with pytest.raises(ValueError):
        case.sql(reference_date=datetime(2026, 10, 4))
    with pytest.raises(EvaluationDatasetError):
        replace(case, reference_date=None).sql()
    assert "2026-10-04" in case.sql()


def test_script_list_does_not_open_database(tmp_path, monkeypatch, capsys):
    def forbidden(*_):
        raise AssertionError("Listing must not open the database")
    monkeypatch.setattr(script, "readonly_connection", forbidden)
    assert script.main(["--list", "--database", str(tmp_path / "missing.db")]) == 0
    output = capsys.readouterr()
    assert "ator_ultimos_cinco_anos" in output.out
    assert "sinonimo_faturamento" in output.out
    assert not output.err


def test_script_runs_selected_case_and_outputs_reference_data(evaluation_database, capsys):
    assert script.main([
        "--database", str(evaluation_database), "--case", "top_receita", "--show-results",
    ]) == 0
    output = capsys.readouterr()
    assert "1 consultas" in output.out
    data = next(json.loads(line) for line in output.out.splitlines() if line.startswith("{"))
    assert data["id"] == "top_receita"
    assert data["rows"][0] == ["Grande perda", 50000000.0]
    assert not output.err


def test_script_fails_unknown_id_before_opening_database(evaluation_database, capsys):
    with pytest.raises(SystemExit) as caught:
        script.main(["--case", "unknown"])
    assert caught.value.code == 2
    assert "ID de pergunta não encontrado" in capsys.readouterr().err


def test_script_reports_missing_database_without_traceback(tmp_path, capsys):
    assert script.main(["--database", str(tmp_path / "missing.db")]) == 2
    assert "Banco de dados não encontrado" in capsys.readouterr().err


def test_script_continues_after_query_error_and_marks_failure(evaluation_database, tmp_path, capsys):
    data = json.loads(DATASET.read_text(encoding="utf-8"))[:2]
    data[0]["reference_sql"] = "SELECT missing FROM dim_movies"
    path = tmp_path / "missing_column.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert script.main(["--database", str(evaluation_database), "--questions", str(path)]) == 1
    output = capsys.readouterr()
    assert "top_receita: FALHA" in output.err
    assert "lucro_medio_genero: OK" in output.out
    assert "1 falhas" in output.out


@pytest.mark.parametrize("timeout", ["0", "61", "nan", "inf"])
def test_script_rejects_invalid_time_budget(timeout):
    with pytest.raises(SystemExit) as caught:
        script.main(["--timeout", timeout, "--list"])
    assert caught.value.code == 2
