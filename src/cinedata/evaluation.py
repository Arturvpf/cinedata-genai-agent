"""Perguntas e SQL de referência para avaliação local e reproduzível."""

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re

from cinedata.exceptions import CineDataError
from cinedata.guardrails import MAX_SQL_RESPONSE_CHARS, validate_sql
from cinedata.prompts import MAX_QUESTION_CHARS


MAX_DATASET_CHARS = 1_000_000
MAX_EVALUATION_QUESTIONS = 100
REFERENCE_DATE_TOKEN = "{reference_date}"


class EvaluationDatasetError(CineDataError):
    """Arquivo de perguntas ausente, malformado ou com critérios inválidos."""


@dataclass(frozen=True)
class EvaluationQuestion:
    """Uma pergunta com critérios de revisão e consulta de referência."""

    id: str
    category: str
    question: str
    criteria: tuple[str, ...]
    reference_sql: str
    reference_date: date | None = None

    def sql(self, *, reference_date: date | None = None) -> str:
        selected = self.reference_date if reference_date is None else reference_date
        if selected is not None and type(selected) is not date:
            raise ValueError("A referência deve ser uma data sem horário.")
        if REFERENCE_DATE_TOKEN in self.reference_sql:
            if selected is None:
                raise EvaluationDatasetError("A consulta exige uma data de referência.")
            # Apenas uma data ISO validada pode substituir o marcador, nunca texto livre.
            return self.reference_sql.replace(REFERENCE_DATE_TOKEN, selected.isoformat())
        return self.reference_sql


def parse_reference_date(value: str) -> date:
    """Aceite somente uma data ISO no formato AAAA-MM-DD."""
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError("A data deve estar no formato AAAA-MM-DD e ser válida.") from error
    if parsed.isoformat() != value:
        raise ValueError("A data deve estar no formato AAAA-MM-DD e ser válida.")
    return parsed


def _text(value: object, limit: int) -> bool:
    return (
        isinstance(value, str) and bool(value.strip())
        and len(value) <= limit and "\x00" not in value
    )


def load_evaluation_questions(path: str | Path) -> tuple[EvaluationQuestion, ...]:
    """Leia e valide o conjunto sem abrir banco, ler .env ou chamar a API."""
    try:
        with Path(path).open(encoding="utf-8-sig") as source:
            text = source.read(MAX_DATASET_CHARS + 1)
    except (OSError, UnicodeError, ValueError) as error:
        raise EvaluationDatasetError(
            "Não foi possível ler o arquivo de perguntas UTF-8."
        ) from error
    if len(text) > MAX_DATASET_CHARS:
        raise EvaluationDatasetError("O arquivo de perguntas excede o limite de tamanho.")
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as error:
        raise EvaluationDatasetError("O arquivo de perguntas deve conter JSON válido.") from error
    if not isinstance(data, list) or not 1 <= len(data) <= MAX_EVALUATION_QUESTIONS:
        raise EvaluationDatasetError("O conjunto deve conter entre 1 e 100 perguntas.")
    questions = []
    ids = set()
    required = {"id", "category", "question", "criteria", "reference_sql"}
    for item in data:
        if not isinstance(item, dict) or not required <= item.keys():
            raise EvaluationDatasetError("Uma pergunta não contém os campos obrigatórios.")
        case_id = item["id"]
        criteria = item["criteria"]
        if (
            not isinstance(case_id, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", case_id) is None
            or case_id in ids
            or not _text(item["category"], 100)
            or not _text(item["question"], MAX_QUESTION_CHARS)
            or not _text(item["reference_sql"], MAX_SQL_RESPONSE_CHARS)
            or not isinstance(criteria, list) or not 1 <= len(criteria) <= 10
            or any(not _text(criterion, 1_000) for criterion in criteria)
        ):
            raise EvaluationDatasetError("Identificador, pergunta, critérios ou SQL inválidos.")
        raw_date = item.get("reference_date")
        try:
            reference = parse_reference_date(raw_date) if raw_date is not None else None
            sql = validate_sql(item["reference_sql"])
        except (ValueError, CineDataError) as error:
            raise EvaluationDatasetError("Data ou SQL de referência inválidos.") from error
        case = EvaluationQuestion(
            case_id, item["category"], item["question"], tuple(criteria), sql, reference,
        )
        case.sql()  # Verifique a presença da data antes de executar qualquer consulta.
        questions.append(case)
        ids.add(case_id)
    return tuple(questions)
