import os
import uuid
from datetime import date, datetime, timezone

import psycopg
import pytest
from psycopg.types.json import Jsonb

# app.py recusa subir (RuntimeError) sem SEDUC_DASHBOARD_SECRET_KEY definida,
# a menos que o modo dev esteja explicitamente ligado - ver finding #1 da
# revisao final. Isso precisa ser setado aqui (nivel de modulo do conftest,
# nao dentro de uma fixture) porque `import app` acontece na COLETA dos
# testes (quando test_app.py roda `from app import app`), antes de qualquer
# fixture ser executada - um monkeypatch dentro de uma fixture chegaria
# tarde demais. O pytest sempre importa o conftest.py de um diretorio antes
# de coletar os modulos de teste desse diretorio, entao isto roda a tempo.
os.environ.setdefault("SEDUC_DASHBOARD_DEV", "1")

TEST_DATABASE_URL = os.environ.get(
    "SEDUC_DASHBOARD_TEST_DATABASE_URL",
    "postgresql://agenteedu:agenteedu_dev@localhost:5433/agente_ia_edu",
)
TEST_SCHEMA = "seduc_pb_dashboard_test"


@pytest.fixture
def pg_schema_vazio():
    conn = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
    conn.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
    conn.execute(f"CREATE SCHEMA {TEST_SCHEMA}")
    conn.execute(f"SET search_path TO {TEST_SCHEMA}")
    conn.execute(
        """
        CREATE TABLE schools (
            id UUID PRIMARY KEY,
            name VARCHAR NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE classes (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id),
            name VARCHAR NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE persons (
            id UUID PRIMARY KEY,
            full_name VARCHAR NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE students (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id),
            person_id UUID NOT NULL REFERENCES persons(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE student_enrollments (
            id UUID PRIMARY KEY,
            student_id UUID NOT NULL REFERENCES students(id),
            class_id UUID NOT NULL REFERENCES classes(id),
            enrolled_on DATE NOT NULL,
            status VARCHAR NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE essay_submissions (
            id UUID PRIMARY KEY,
            student_id UUID NOT NULL REFERENCES students(id),
            school_id UUID NOT NULL REFERENCES schools(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE essay_corrections (
            id UUID PRIMARY KEY,
            essay_submission_id UUID NOT NULL REFERENCES essay_submissions(id),
            status VARCHAR NOT NULL,
            final_scores JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    yield conn
    conn.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
    conn.close()


IDS = {
    "escola": uuid.UUID("11111111-1111-1111-1111-111111111111"),
    "turma": uuid.UUID("22222222-2222-2222-2222-222222222222"),
    "pessoa_1": uuid.UUID("33333333-3333-3333-3333-333333333333"),
    "usuario_1": uuid.UUID("44444444-4444-4444-4444-444444444444"),
    "matricula_1": uuid.UUID("55555555-5555-5555-5555-555555555555"),
    "submissao_1": uuid.UUID("66666666-6666-6666-6666-666666666666"),
    "correcao_1_aprovada": uuid.UUID("77777777-7777-7777-7777-777777777777"),
    "pessoa_2": uuid.UUID("88888888-8888-8888-8888-888888888888"),
    "usuario_2": uuid.UUID("99999999-9999-9999-9999-999999999999"),
    "submissao_2": uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    "correcao_2_pendente": uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
    "pessoa_3": uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
    "usuario_3": uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
    "submissao_3": uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
    "correcao_3_aprovada_sem_turma": uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
}


def _nota(total: int) -> Jsonb:
    pontos = total // 5
    return Jsonb(
        {
            "total": total,
            "per_competency": {
                "C1": {"points": pontos, "confidence": 0.95},
                "C2": {"points": pontos, "confidence": 0.95},
                "C3": {"points": pontos, "confidence": 0.95},
                "C4": {"points": pontos, "confidence": 0.95},
                "C5": {"points": pontos, "confidence": 0.95},
            },
        }
    )


@pytest.fixture
def pg_com_dados_sinteticos(pg_schema_vazio):
    conn = pg_schema_vazio
    conn.execute("INSERT INTO schools (id, name) VALUES (%s, %s)", (IDS["escola"], "Escola Estadual Teste"))
    conn.execute(
        "INSERT INTO classes (id, school_id, name) VALUES (%s, %s, %s)",
        (IDS["turma"], IDS["escola"], "3o Ano A"),
    )

    # Aluno 1: APPROVED, com turma (via matricula ativa)
    conn.execute("INSERT INTO persons (id, full_name) VALUES (%s, %s)", (IDS["pessoa_1"], "Aluno Um"))
    conn.execute(
        "INSERT INTO students (id, school_id, person_id) VALUES (%s, %s, %s)",
        (IDS["usuario_1"], IDS["escola"], IDS["pessoa_1"]),
    )
    conn.execute(
        "INSERT INTO student_enrollments (id, student_id, class_id, enrolled_on, status) "
        "VALUES (%s, %s, %s, %s, %s)",
        (IDS["matricula_1"], IDS["usuario_1"], IDS["turma"], date(2026, 2, 1), "ACTIVE"),
    )
    conn.execute(
        "INSERT INTO essay_submissions (id, student_id, school_id) VALUES (%s, %s, %s)",
        (IDS["submissao_1"], IDS["usuario_1"], IDS["escola"]),
    )
    conn.execute(
        "INSERT INTO essay_corrections (id, essay_submission_id, status, final_scores, created_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (IDS["correcao_1_aprovada"], IDS["submissao_1"], "APPROVED", _nota(800),
         datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc)),
    )

    # Aluno 2: PENDING_REVIEW (deve ficar de fora do export)
    conn.execute("INSERT INTO persons (id, full_name) VALUES (%s, %s)", (IDS["pessoa_2"], "Aluno Dois"))
    conn.execute(
        "INSERT INTO students (id, school_id, person_id) VALUES (%s, %s, %s)",
        (IDS["usuario_2"], IDS["escola"], IDS["pessoa_2"]),
    )
    conn.execute(
        "INSERT INTO essay_submissions (id, student_id, school_id) VALUES (%s, %s, %s)",
        (IDS["submissao_2"], IDS["usuario_2"], IDS["escola"]),
    )
    conn.execute(
        "INSERT INTO essay_corrections (id, essay_submission_id, status, final_scores, created_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (IDS["correcao_2_pendente"], IDS["submissao_2"], "PENDING_REVIEW", _nota(500),
         datetime(2026, 9, 30, 11, 0, 0, tzinfo=timezone.utc)),
    )

    # Aluno 3: APPROVED, sem matricula (turma deve vir NULL)
    conn.execute("INSERT INTO persons (id, full_name) VALUES (%s, %s)", (IDS["pessoa_3"], "Aluno Tres"))
    conn.execute(
        "INSERT INTO students (id, school_id, person_id) VALUES (%s, %s, %s)",
        (IDS["usuario_3"], IDS["escola"], IDS["pessoa_3"]),
    )
    conn.execute(
        "INSERT INTO essay_submissions (id, student_id, school_id) VALUES (%s, %s, %s)",
        (IDS["submissao_3"], IDS["usuario_3"], IDS["escola"]),
    )
    conn.execute(
        "INSERT INTO essay_corrections (id, essay_submission_id, status, final_scores, created_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (IDS["correcao_3_aprovada_sem_turma"], IDS["submissao_3"], "APPROVED", _nota(800),
         datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)),
    )

    conn.commit()
    return conn, IDS
