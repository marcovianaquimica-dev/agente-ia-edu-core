# Dashboard SEDUC-PB (v1 simplificada) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir um dashboard gerencial pontual para a SEDUC-PB (Visão
Geral, Ranking, Escola, Turma), isolado do produto principal, que lê um
snapshot próprio exportado sob demanda do banco de produção.

**Architecture:** Projeto novo em `dashboards/seduc-pb/`, com dependências e
ambiente virtual próprios, sem importar nada de `src/agente_ia_edu`. Três
componentes: (1) `export_snapshot.py` lê o Postgres principal (usuário
só-leitura) e grava um snapshot em SQLite; (2) `app.py` (FastAPI + Jinja2)
serve as 4 telas lendo exclusivamente do snapshot; (3) `ia_synthesis.py`
gera e cacheia uma síntese executiva curta por corte.

**Tech Stack:** Python 3.13, FastAPI, Jinja2, SQLite, psycopg (só no export
job), bcrypt, itsdangerous (sessão), openai (síntese), pytest, httpx (testes).

**Spec:** [docs/superpowers/specs/2026-10-01-dashboard-seduc-pb-design.md](../specs/2026-10-01-dashboard-seduc-pb-design.md)

## Global Constraints

- Tudo vive em `dashboards/seduc-pb/`; nenhum arquivo deste projeto importa
  de `src/agente_ia_edu`.
- O app web (`app.py`) lê **somente** do `snapshot.db` (SQLite); nunca
  consulta o Postgres principal em tempo de requisição.
- Só correções com `status = 'APPROVED'` entram no snapshot.
- Classificação por faixa e ranking são determinísticos (sem IA): faixas
  0-399 Muito baixo, 400-599 Baixo, 600-799 Adequado, 800-899 Alto,
  900-1000 Muito alto; ranking com empate = mesma posição.
- IA é usada só para a síntese executiva (1 por corte: geral + por
  escola), com entrada limitada a dados agregados (nunca texto de redação
  ou devolutiva), cacheada por hash no snapshot.
- O export roda manualmente via CLI (`python export_snapshot.py`); sem
  agendamento automático nesta v1.
- Login por usuário, sem escopo por perfil: qualquer usuário autenticado
  vê todos os dados.
- Fora de escopo nesta v1 (não criar tarefas para isso): hierarquia
  GRE/Município, classificação qualitativa via IA, diagnóstico pedagógico
  via IA, central de relatórios/export PDF, exportação Excel, agendamento
  automático do export, escopo de acesso por perfil.

---

### Task 1: Scaffolding do projeto + esquema do snapshot SQLite

**Files:**
- Create: `dashboards/seduc-pb/pyproject.toml`
- Create: `dashboards/seduc-pb/.gitignore`
- Create: `dashboards/seduc-pb/snapshot_db.py`
- Test: `dashboards/seduc-pb/tests/test_snapshot_db.py`

**Interfaces:**
- Consumes: nada de outra task.
- Produces: `snapshot_db.get_connection(db_path: Path) -> sqlite3.Connection`,
  `snapshot_db.create_schema(conn) -> None`,
  `snapshot_db.reset_redacoes(conn) -> None`,
  `snapshot_db.insert_redacoes(conn, redacoes: list[dict]) -> None`.
  Tabelas: `redacoes` (colunas: `id_redacao, id_aluno, nome_aluno, escola_id,
  escola_nome, turma_id, turma_nome, nota_final, c1, c2, c3, c4, c5,
  faixa_classificacao, posicao_geral, data_correcao`), `sinteses` (`corte,
  texto, hash_dados, gerado_em`), `usuarios` (`id, login, senha_hash`).

- [ ] **Step 1: Criar estrutura de pastas e `pyproject.toml`**

```bash
mkdir -p dashboards/seduc-pb/tests
mkdir -p dashboards/seduc-pb/templates
```

`dashboards/seduc-pb/pyproject.toml`:

```toml
[project]
name = "seduc-pb-dashboard"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "fastapi>=0.115,<1.0",
    "uvicorn>=0.32,<1.0",
    "jinja2>=3.1,<4.0",
    "python-multipart>=0.0.12,<1.0",
    "itsdangerous>=2.2,<3.0",
    "bcrypt>=4.2,<5.0",
    "psycopg[binary]>=3.2,<4.0",
    "openai>=1.50,<2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0,<10.0",
    "httpx>=0.27,<1.0",
]

[tool.pytest.ini_options]
pythonpath = ["."]
```

`dashboards/seduc-pb/.gitignore`:

```
.venv/
snapshot.db
__pycache__/
*.pyc
```

- [ ] **Step 2: Criar ambiente virtual e instalar dependências**

```bash
cd dashboards/seduc-pb
python3.13 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cd ../..
```

- [ ] **Step 3: Escrever o teste falho de `create_schema`**

`dashboards/seduc-pb/tests/test_snapshot_db.py`:

```python
import pytest

from snapshot_db import create_schema, get_connection, insert_redacoes, reset_redacoes


@pytest.fixture
def conn(tmp_path):
    return get_connection(tmp_path / "snapshot.db")


def test_create_schema_cria_as_tres_tabelas(conn):
    create_schema(conn)
    tabelas = {
        linha["name"]
        for linha in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {"redacoes", "sinteses", "usuarios"} <= tabelas


def test_create_schema_e_idempotente(conn):
    create_schema(conn)
    create_schema(conn)  # nao deve falhar ao rodar de novo
```

- [ ] **Step 4: Rodar e confirmar que falha**

Run: `cd dashboards/seduc-pb && .venv/bin/pytest tests/test_snapshot_db.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'snapshot_db'`

- [ ] **Step 5: Implementar `get_connection` + `create_schema`**

`dashboards/seduc-pb/snapshot_db.py`:

```python
"""Esquema e operacoes basicas do snapshot SQLite do dashboard SEDUC-PB."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS redacoes (
            id_redacao TEXT PRIMARY KEY,
            id_aluno TEXT NOT NULL,
            nome_aluno TEXT NOT NULL,
            escola_id TEXT NOT NULL,
            escola_nome TEXT NOT NULL,
            turma_id TEXT,
            turma_nome TEXT,
            nota_final INTEGER NOT NULL,
            c1 INTEGER NOT NULL,
            c2 INTEGER NOT NULL,
            c3 INTEGER NOT NULL,
            c4 INTEGER NOT NULL,
            c5 INTEGER NOT NULL,
            faixa_classificacao TEXT NOT NULL,
            posicao_geral INTEGER NOT NULL,
            data_correcao TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sinteses (
            corte TEXT PRIMARY KEY,
            texto TEXT NOT NULL,
            hash_dados TEXT NOT NULL,
            gerado_em TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            login TEXT UNIQUE NOT NULL,
            senha_hash TEXT NOT NULL
        )
        """
    )
    conn.commit()
```

- [ ] **Step 6: Rodar e confirmar que passa**

Run: `cd dashboards/seduc-pb && .venv/bin/pytest tests/test_snapshot_db.py -v`
Expected: PASS (2 testes). `insert_redacoes` e `reset_redacoes` ainda não
existem - os testes que os usam vêm no próximo passo.

- [ ] **Step 7: Escrever o teste falho de `insert_redacoes` + `reset_redacoes`**

Adicionar ao final de `dashboards/seduc-pb/tests/test_snapshot_db.py`:

```python
REDACAO_EXEMPLO = {
    "id_redacao": "r1",
    "id_aluno": "a1",
    "nome_aluno": "Aluno Um",
    "escola_id": "e1",
    "escola_nome": "Escola Um",
    "turma_id": "t1",
    "turma_nome": "Turma A",
    "nota_final": 800,
    "c1": 160, "c2": 160, "c3": 160, "c4": 160, "c5": 160,
    "faixa_classificacao": "Alto",
    "posicao_geral": 1,
    "data_correcao": "2026-09-30T10:00:00",
}


def test_insert_e_reset_redacoes(conn):
    create_schema(conn)
    insert_redacoes(conn, [REDACAO_EXEMPLO])
    total_apos_insert = conn.execute("SELECT count(*) FROM redacoes").fetchone()[0]
    assert total_apos_insert == 1

    reset_redacoes(conn)
    total_apos_reset = conn.execute("SELECT count(*) FROM redacoes").fetchone()[0]
    assert total_apos_reset == 0
```

- [ ] **Step 8: Rodar e confirmar que falha**

Run: `cd dashboards/seduc-pb && .venv/bin/pytest tests/test_snapshot_db.py -v`
Expected: FAIL com `ImportError: cannot import name 'insert_redacoes' from
'snapshot_db'` (a função ainda não existe - foi só importada no topo do
arquivo de teste desde o Step 3)

- [ ] **Step 9: Implementar `insert_redacoes` + `reset_redacoes`**

Adicionar ao final de `dashboards/seduc-pb/snapshot_db.py`:

```python
CAMPOS_REDACAO = [
    "id_redacao", "id_aluno", "nome_aluno", "escola_id", "escola_nome",
    "turma_id", "turma_nome", "nota_final", "c1", "c2", "c3", "c4", "c5",
    "faixa_classificacao", "posicao_geral", "data_correcao",
]


def reset_redacoes(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM redacoes")
    conn.commit()


def insert_redacoes(conn: sqlite3.Connection, redacoes: list[dict]) -> None:
    colunas = ", ".join(CAMPOS_REDACAO)
    marcadores = ", ".join(f":{campo}" for campo in CAMPOS_REDACAO)
    conn.executemany(
        f"INSERT INTO redacoes ({colunas}) VALUES ({marcadores})",
        redacoes,
    )
    conn.commit()
```

- [ ] **Step 10: Rodar tudo e confirmar que passa**

Run: `cd dashboards/seduc-pb && .venv/bin/pytest tests/test_snapshot_db.py -v`
Expected: PASS (3 testes)

- [ ] **Step 11: Commit**

```bash
git add dashboards/seduc-pb/pyproject.toml dashboards/seduc-pb/.gitignore \
        dashboards/seduc-pb/snapshot_db.py dashboards/seduc-pb/tests/test_snapshot_db.py
git commit -m "feat(seduc-pb): scaffolding do projeto e esquema do snapshot SQLite"
```

---

### Task 2: Fixture de banco de teste Postgres

**Files:**
- Create: `dashboards/seduc-pb/tests/conftest.py`
- Test: `dashboards/seduc-pb/tests/test_fixture_postgres.py`

**Interfaces:**
- Consumes: nada de outra task.
- Produces: fixtures pytest `pg_schema_vazio` (conexão `psycopg.Connection`
  já com schema de teste criado) e `pg_com_dados_sinteticos` (retorna
  tupla `(conn, ids: dict[str, uuid.UUID])`), disponíveis para qualquer
  teste em `dashboards/seduc-pb/tests/` via `conftest.py`.

- [ ] **Step 1: Escrever o fixture de schema vazio**

`dashboards/seduc-pb/tests/conftest.py`:

```python
import os
import uuid
from datetime import date, datetime, timezone

import psycopg
import pytest
from psycopg.types.json import Jsonb

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
        CREATE TABLE users (
            id UUID PRIMARY KEY,
            person_id UUID NOT NULL REFERENCES persons(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE student_enrollments (
            id UUID PRIMARY KEY,
            student_id UUID NOT NULL,
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
            student_id UUID NOT NULL REFERENCES users(id),
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
```

- [ ] **Step 2: Escrever o fixture com dados sintéticos**

Adicionar ao final de `dashboards/seduc-pb/tests/conftest.py`:

```python
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
    conn.execute("INSERT INTO users (id, person_id) VALUES (%s, %s)", (IDS["usuario_1"], IDS["pessoa_1"]))
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
    conn.execute("INSERT INTO users (id, person_id) VALUES (%s, %s)", (IDS["usuario_2"], IDS["pessoa_2"]))
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
    conn.execute("INSERT INTO users (id, person_id) VALUES (%s, %s)", (IDS["usuario_3"], IDS["pessoa_3"]))
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
```

- [ ] **Step 3: Escrever e rodar o teste do fixture**

`dashboards/seduc-pb/tests/test_fixture_postgres.py`:

```python
def test_fixture_cria_tabelas_e_dados_esperados(pg_com_dados_sinteticos):
    conn, ids = pg_com_dados_sinteticos
    total_escolas = conn.execute("SELECT count(*) FROM schools").fetchone()[0]
    total_correcoes = conn.execute("SELECT count(*) FROM essay_corrections").fetchone()[0]
    total_aprovadas = conn.execute(
        "SELECT count(*) FROM essay_corrections WHERE status = 'APPROVED'"
    ).fetchone()[0]
    assert total_escolas == 1
    assert total_correcoes == 3
    assert total_aprovadas == 2
```

Run: `cd dashboards/seduc-pb && .venv/bin/pytest tests/test_fixture_postgres.py -v`
Expected: PASS. Requer o Postgres de dev rodando na porta 5433 (já usado
pelo produto principal neste ambiente) - se o teste falhar por conexão
recusada, confirme que o Postgres de dev está no ar antes de prosseguir.

- [ ] **Step 4: Commit**

```bash
git add dashboards/seduc-pb/tests/conftest.py dashboards/seduc-pb/tests/test_fixture_postgres.py
git commit -m "test(seduc-pb): fixture de banco Postgres de teste com dados sinteticos"
```

---

### Task 3: Classificação por faixa

**Files:**
- Create: `dashboards/seduc-pb/faixas.py`
- Test: `dashboards/seduc-pb/tests/test_faixas.py`

**Interfaces:**
- Consumes: nada de outra task.
- Produces: `faixas.classificar(nota: int) -> str` (levanta `ValueError`
  para nota fora de 0-1000).

- [ ] **Step 1: Escrever o teste falho**

`dashboards/seduc-pb/tests/test_faixas.py`:

```python
import pytest

from faixas import classificar


@pytest.mark.parametrize(
    "nota, esperado",
    [
        (0, "Muito baixo"),
        (399, "Muito baixo"),
        (400, "Baixo"),
        (599, "Baixo"),
        (600, "Adequado"),
        (799, "Adequado"),
        (800, "Alto"),
        (899, "Alto"),
        (900, "Muito alto"),
        (1000, "Muito alto"),
    ],
)
def test_classificar_faixas(nota, esperado):
    assert classificar(nota) == esperado


def test_classificar_fora_do_intervalo_leva_erro():
    with pytest.raises(ValueError):
        classificar(1001)
    with pytest.raises(ValueError):
        classificar(-1)
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd dashboards/seduc-pb && .venv/bin/pytest tests/test_faixas.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'faixas'`

- [ ] **Step 3: Implementar `faixas.py`**

```python
"""Classificacao de desempenho por faixa de nota - deterministico, sem IA."""
from __future__ import annotations

FAIXAS = [
    (0, 399, "Muito baixo"),
    (400, 599, "Baixo"),
    (600, 799, "Adequado"),
    (800, 899, "Alto"),
    (900, 1000, "Muito alto"),
]


def classificar(nota: int) -> str:
    for minimo, maximo, rotulo in FAIXAS:
        if minimo <= nota <= maximo:
            return rotulo
    raise ValueError(f"nota fora do intervalo esperado (0-1000): {nota}")
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd dashboards/seduc-pb && .venv/bin/pytest tests/test_faixas.py -v`
Expected: PASS (12 casos)

- [ ] **Step 5: Commit**

```bash
git add dashboards/seduc-pb/faixas.py dashboards/seduc-pb/tests/test_faixas.py
git commit -m "feat(seduc-pb): classificacao deterministica por faixa de nota"
```

---

### Task 4: Cálculo de ranking com empates

**Files:**
- Create: `dashboards/seduc-pb/ranking.py`
- Test: `dashboards/seduc-pb/tests/test_ranking.py`

**Interfaces:**
- Consumes: nada de outra task.
- Produces: `ranking.calcular_ranking(notas: list[tuple[str, int]]) ->
  dict[str, int]` (mapa id → posição; empate = mesma posição, próxima
  posição pula o número de empatados).

- [ ] **Step 1: Escrever o teste falho**

`dashboards/seduc-pb/tests/test_ranking.py`:

```python
from ranking import calcular_ranking


def test_ranking_sem_empates():
    notas = [("a", 900), ("b", 800), ("c", 700)]
    assert calcular_ranking(notas) == {"a": 1, "b": 2, "c": 3}


def test_ranking_com_empate_no_topo():
    notas = [("a", 800), ("b", 800), ("c", 600)]
    assert calcular_ranking(notas) == {"a": 1, "b": 1, "c": 3}


def test_ranking_com_empate_no_meio():
    notas = [("a", 900), ("b", 700), ("c", 700), ("d", 500)]
    assert calcular_ranking(notas) == {"a": 1, "b": 2, "c": 2, "d": 4}


def test_ranking_lista_vazia():
    assert calcular_ranking([]) == {}
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd dashboards/seduc-pb && .venv/bin/pytest tests/test_ranking.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'ranking'`

- [ ] **Step 3: Implementar `ranking.py`**

```python
"""Calculo deterministico de ranking com empates - sem IA."""
from __future__ import annotations


def calcular_ranking(notas: list[tuple[str, int]]) -> dict[str, int]:
    """Recebe uma lista de (id, nota) e retorna {id: posicao}.

    Empate = mesma posicao; a proxima posicao pula o numero de
    itens empatados (ex: dois em 1o lugar -> o proximo fica em 3o).
    """
    ordenado = sorted(notas, key=lambda item: item[1], reverse=True)
    posicoes: dict[str, int] = {}
    posicao_atual = 0
    nota_anterior = None
    for indice, (id_item, nota) in enumerate(ordenado, start=1):
        if nota != nota_anterior:
            posicao_atual = indice
            nota_anterior = nota
        posicoes[id_item] = posicao_atual
    return posicoes
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd dashboards/seduc-pb && .venv/bin/pytest tests/test_ranking.py -v`
Expected: PASS (4 testes)

- [ ] **Step 5: Commit**

```bash
git add dashboards/seduc-pb/ranking.py dashboards/seduc-pb/tests/test_ranking.py
git commit -m "feat(seduc-pb): calculo deterministico de ranking com empates"
```

---

### Task 5: Export job (leitura do Postgres + gravação do snapshot)

**Files:**
- Create: `dashboards/seduc-pb/export_snapshot.py`
- Test: `dashboards/seduc-pb/tests/test_export_snapshot.py`

**Interfaces:**
- Consumes: `snapshot_db.get_connection`, `snapshot_db.create_schema`,
  `snapshot_db.reset_redacoes`, `snapshot_db.insert_redacoes` (Task 1);
  `faixas.classificar` (Task 3); `ranking.calcular_ranking` (Task 4);
  fixture `pg_com_dados_sinteticos` (Task 2).
- Produces: `export_snapshot.buscar_redacoes_aprovadas(pg_conn) ->
  list[dict]`, `export_snapshot.montar_redacao_snapshot(linha: dict,
  posicao: int) -> dict`, `export_snapshot.exportar(pg_conn, sqlite_conn)
  -> int` (retorna quantidade de redações exportadas) - usado pelo CLI e
  testável diretamente sem passar por `argparse`.

- [ ] **Step 1: Escrever o teste falho**

`dashboards/seduc-pb/tests/test_export_snapshot.py`:

```python
from export_snapshot import exportar
from snapshot_db import get_connection


def test_exportar_grava_somente_correcoes_aprovadas(tmp_path, pg_com_dados_sinteticos):
    pg_conn, ids = pg_com_dados_sinteticos
    sqlite_conn = get_connection(tmp_path / "snapshot.db")

    total = exportar(pg_conn, sqlite_conn)

    assert total == 2
    linhas = sqlite_conn.execute(
        "SELECT nome_aluno, turma_nome, faixa_classificacao, posicao_geral "
        "FROM redacoes ORDER BY nome_aluno"
    ).fetchall()
    assert [dict(linha) for linha in linhas] == [
        {"nome_aluno": "Aluno Tres", "turma_nome": None, "faixa_classificacao": "Alto", "posicao_geral": 1},
        {"nome_aluno": "Aluno Um", "turma_nome": "3o Ano A", "faixa_classificacao": "Alto", "posicao_geral": 1},
    ]


def test_exportar_e_idempotente(tmp_path, pg_com_dados_sinteticos):
    pg_conn, ids = pg_com_dados_sinteticos
    sqlite_conn = get_connection(tmp_path / "snapshot.db")

    exportar(pg_conn, sqlite_conn)
    total_segunda_vez = exportar(pg_conn, sqlite_conn)

    assert total_segunda_vez == 2
    total_linhas = sqlite_conn.execute("SELECT count(*) FROM redacoes").fetchone()[0]
    assert total_linhas == 2
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd dashboards/seduc-pb && .venv/bin/pytest tests/test_export_snapshot.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'export_snapshot'`

- [ ] **Step 3: Implementar `export_snapshot.py`**

```python
"""Export job: le o Postgres principal (somente leitura) e grava um
snapshot em SQLite para o dashboard SEDUC-PB consultar.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from faixas import classificar
from ranking import calcular_ranking
from snapshot_db import create_schema, get_connection, insert_redacoes, reset_redacoes

QUERY_REDACOES_APROVADAS = """
    SELECT
        ec.id AS id_redacao,
        es.student_id AS id_aluno,
        p.full_name AS nome_aluno,
        sc.id AS escola_id,
        sc.name AS escola_nome,
        cl.id AS turma_id,
        cl.name AS turma_nome,
        ec.final_scores AS final_scores,
        ec.created_at AS data_correcao
    FROM essay_corrections ec
    JOIN essay_submissions es ON es.id = ec.essay_submission_id
    JOIN users u ON u.id = es.student_id
    JOIN persons p ON p.id = u.person_id
    JOIN schools sc ON sc.id = es.school_id
    LEFT JOIN LATERAL (
        SELECT se.class_id
        FROM student_enrollments se
        WHERE se.student_id = es.student_id AND se.status = 'ACTIVE'
        ORDER BY se.enrolled_on DESC
        LIMIT 1
    ) matricula ON true
    LEFT JOIN classes cl ON cl.id = matricula.class_id
    WHERE ec.status = 'APPROVED'
    ORDER BY ec.created_at
"""


def buscar_redacoes_aprovadas(pg_conn: psycopg.Connection) -> list[dict]:
    with pg_conn.cursor(row_factory=dict_row) as cur:
        cur.execute(QUERY_REDACOES_APROVADAS)
        return cur.fetchall()


def montar_redacao_snapshot(linha: dict, posicao: int) -> dict:
    notas = linha["final_scores"]["per_competency"]
    nota_final = linha["final_scores"]["total"]
    return {
        "id_redacao": str(linha["id_redacao"]),
        "id_aluno": str(linha["id_aluno"]),
        "nome_aluno": linha["nome_aluno"],
        "escola_id": str(linha["escola_id"]),
        "escola_nome": linha["escola_nome"],
        "turma_id": str(linha["turma_id"]) if linha["turma_id"] else None,
        "turma_nome": linha["turma_nome"],
        "nota_final": nota_final,
        "c1": notas["C1"]["points"],
        "c2": notas["C2"]["points"],
        "c3": notas["C3"]["points"],
        "c4": notas["C4"]["points"],
        "c5": notas["C5"]["points"],
        "faixa_classificacao": classificar(nota_final),
        "posicao_geral": posicao,
        "data_correcao": linha["data_correcao"].isoformat(),
    }


def exportar(pg_conn: psycopg.Connection, sqlite_conn: sqlite3.Connection) -> int:
    linhas = buscar_redacoes_aprovadas(pg_conn)
    ranking = calcular_ranking(
        [(str(linha["id_redacao"]), linha["final_scores"]["total"]) for linha in linhas]
    )
    redacoes = [
        montar_redacao_snapshot(linha, ranking[str(linha["id_redacao"])]) for linha in linhas
    ]
    create_schema(sqlite_conn)
    reset_redacoes(sqlite_conn)
    insert_redacoes(sqlite_conn, redacoes)
    return len(redacoes)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exporta um snapshot do banco principal para o dashboard SEDUC-PB"
    )
    parser.add_argument(
        "--source-database-url",
        default=os.environ.get("SEDUC_DASHBOARD_SOURCE_DATABASE_URL"),
    )
    parser.add_argument(
        "--snapshot-path",
        default=str(Path(__file__).resolve().parent / "snapshot.db"),
    )
    args = parser.parse_args()
    if not args.source_database_url:
        raise SystemExit(
            "defina SEDUC_DASHBOARD_SOURCE_DATABASE_URL ou passe --source-database-url"
        )
    pg_conn = psycopg.connect(args.source_database_url)
    sqlite_conn = get_connection(Path(args.snapshot_path))
    total = exportar(pg_conn, sqlite_conn)
    print(f"{total} redacoes exportadas para {args.snapshot_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd dashboards/seduc-pb && .venv/bin/pytest tests/test_export_snapshot.py -v`
Expected: PASS (2 testes). Aluno Um e Aluno Tres empatam em 800 pontos,
então ambos ficam em `posicao_geral = 1` - é o comportamento esperado do
`calcular_ranking` (Task 4).

- [ ] **Step 5: Commit**

```bash
git add dashboards/seduc-pb/export_snapshot.py dashboards/seduc-pb/tests/test_export_snapshot.py
git commit -m "feat(seduc-pb): export job le o Postgres principal e grava o snapshot"
```

---

### Task 6: Autenticação

**Files:**
- Create: `dashboards/seduc-pb/auth.py`
- Test: `dashboards/seduc-pb/tests/test_auth.py`

**Interfaces:**
- Consumes: `snapshot_db.get_connection`, `snapshot_db.create_schema` (Task 1).
- Produces: `auth.criar_usuario(conn, login: str, senha: str) -> None`,
  `auth.verificar_login(conn, login: str, senha: str) -> bool`. CLI:
  `python auth.py --snapshot-path <path> --login <login> --senha <senha>`.

- [ ] **Step 1: Escrever o teste falho**

`dashboards/seduc-pb/tests/test_auth.py`:

```python
import sqlite3

import pytest

from auth import criar_usuario, verificar_login
from snapshot_db import create_schema, get_connection


@pytest.fixture
def conn(tmp_path):
    conexao = get_connection(tmp_path / "snapshot.db")
    create_schema(conexao)
    return conexao


def test_login_correto_retorna_true(conn):
    criar_usuario(conn, "gestor", "senha-correta")
    assert verificar_login(conn, "gestor", "senha-correta") is True


def test_login_com_senha_errada_retorna_false(conn):
    criar_usuario(conn, "gestor", "senha-correta")
    assert verificar_login(conn, "gestor", "senha-errada") is False


def test_login_inexistente_retorna_false(conn):
    assert verificar_login(conn, "ninguem", "qualquer") is False


def test_criar_usuario_duplicado_falha(conn):
    criar_usuario(conn, "gestor", "senha-1")
    with pytest.raises(sqlite3.IntegrityError):
        criar_usuario(conn, "gestor", "senha-2")
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd dashboards/seduc-pb && .venv/bin/pytest tests/test_auth.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'auth'`

- [ ] **Step 3: Implementar `auth.py`**

```python
"""Autenticacao simples do dashboard SEDUC-PB: usuario/senha, sem escopo por perfil."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import bcrypt

from snapshot_db import create_schema, get_connection


def criar_usuario(conn: sqlite3.Connection, login: str, senha: str) -> None:
    senha_hash = bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    conn.execute(
        "INSERT INTO usuarios (login, senha_hash) VALUES (?, ?)",
        (login, senha_hash),
    )
    conn.commit()


def verificar_login(conn: sqlite3.Connection, login: str, senha: str) -> bool:
    linha = conn.execute(
        "SELECT senha_hash FROM usuarios WHERE login = ?", (login,)
    ).fetchone()
    if linha is None:
        return False
    return bcrypt.checkpw(senha.encode("utf-8"), linha["senha_hash"].encode("utf-8"))


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Cria um usuario no snapshot do dashboard SEDUC-PB"
    )
    parser.add_argument("--snapshot-path", required=True)
    parser.add_argument("--login", required=True)
    parser.add_argument("--senha", required=True)
    args = parser.parse_args()

    conexao = get_connection(Path(args.snapshot_path))
    create_schema(conexao)
    criar_usuario(conexao, args.login, args.senha)
    print(f"usuario '{args.login}' criado em {args.snapshot_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd dashboards/seduc-pb && .venv/bin/pytest tests/test_auth.py -v`
Expected: PASS (4 testes)

- [ ] **Step 5: Commit**

```bash
git add dashboards/seduc-pb/auth.py dashboards/seduc-pb/tests/test_auth.py
git commit -m "feat(seduc-pb): autenticacao simples por usuario/senha"
```

---

### Task 7: Síntese executiva com cache

**Files:**
- Create: `dashboards/seduc-pb/ia_synthesis.py`
- Test: `dashboards/seduc-pb/tests/test_ia_synthesis.py`

**Interfaces:**
- Consumes: `snapshot_db.get_connection`, `snapshot_db.create_schema` (Task 1).
- Produces: `ia_synthesis.calcular_hash(dados: dict) -> str`,
  `ia_synthesis.obter_ou_gerar_sintese(conn, corte: str, dados_agregados:
  dict, gerar: Callable[[dict], str]) -> str`,
  `ia_synthesis.gerar_sintese_via_openai(dados_agregados: dict) -> str`
  (provider real, usado em produção, não exercitado em teste automatizado
  porque chamaria a API de verdade).

- [ ] **Step 1: Escrever o teste falho**

`dashboards/seduc-pb/tests/test_ia_synthesis.py`:

```python
from ia_synthesis import obter_ou_gerar_sintese
from snapshot_db import create_schema, get_connection


def test_sintese_so_regenera_quando_hash_muda(tmp_path):
    conn = get_connection(tmp_path / "snapshot.db")
    create_schema(conn)
    chamadas = []

    def gerador_falso(dados: dict) -> str:
        chamadas.append(dados)
        return f"sintese para {dados['total']}"

    texto_1 = obter_ou_gerar_sintese(conn, "geral", {"total": 10}, gerador_falso)
    texto_2 = obter_ou_gerar_sintese(conn, "geral", {"total": 10}, gerador_falso)
    texto_3 = obter_ou_gerar_sintese(conn, "geral", {"total": 20}, gerador_falso)

    assert texto_1 == "sintese para 10"
    assert texto_2 == "sintese para 10"
    assert texto_3 == "sintese para 20"
    assert len(chamadas) == 2
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd dashboards/seduc-pb && .venv/bin/pytest tests/test_ia_synthesis.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'ia_synthesis'`

- [ ] **Step 3: Implementar `ia_synthesis.py`**

```python
"""Sintese executiva curta via IA, cacheada por corte e hash dos dados
agregados de entrada. A entrada e sempre dados ja agregados (nunca texto
de redacao ou devolutiva) - ver Global Constraints do plano.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Callable

GeradorDeSintese = Callable[[dict], str]

MODELO_SINTESE = "gpt-5.6-luna"

PROMPT_SISTEMA = (
    "Voce resume indicadores educacionais agregados em um texto executivo "
    "curto (2 a 4 frases), em portugues, para gestores da SEDUC-PB. Use "
    "somente os numeros fornecidos, nao invente dados."
)


def calcular_hash(dados: dict) -> str:
    serializado = json.dumps(dados, sort_keys=True, default=str)
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def obter_ou_gerar_sintese(
    conn: sqlite3.Connection,
    corte: str,
    dados_agregados: dict,
    gerar: GeradorDeSintese,
) -> str:
    hash_atual = calcular_hash(dados_agregados)
    linha = conn.execute(
        "SELECT texto, hash_dados FROM sinteses WHERE corte = ?", (corte,)
    ).fetchone()
    if linha is not None and linha["hash_dados"] == hash_atual:
        return linha["texto"]

    texto = gerar(dados_agregados)
    agora = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO sinteses (corte, texto, hash_dados, gerado_em)
        VALUES (:corte, :texto, :hash_dados, :gerado_em)
        ON CONFLICT(corte) DO UPDATE SET
            texto = excluded.texto,
            hash_dados = excluded.hash_dados,
            gerado_em = excluded.gerado_em
        """,
        {"corte": corte, "texto": texto, "hash_dados": hash_atual, "gerado_em": agora},
    )
    conn.commit()
    return texto


def gerar_sintese_via_openai(dados_agregados: dict) -> str:
    from openai import OpenAI

    client = OpenAI()
    resposta = client.chat.completions.create(
        model=MODELO_SINTESE,
        messages=[
            {"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user", "content": json.dumps(dados_agregados, ensure_ascii=False)},
        ],
    )
    return resposta.choices[0].message.content.strip()
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd dashboards/seduc-pb && .venv/bin/pytest tests/test_ia_synthesis.py -v`
Expected: PASS (1 teste, `gerador_falso` chamado 2 vezes)

- [ ] **Step 5: Commit**

```bash
git add dashboards/seduc-pb/ia_synthesis.py dashboards/seduc-pb/tests/test_ia_synthesis.py
git commit -m "feat(seduc-pb): sintese executiva via IA cacheada por hash"
```

---

### Task 8: Consultas de leitura do snapshot

**Files:**
- Create: `dashboards/seduc-pb/consultas.py`
- Test: `dashboards/seduc-pb/tests/test_consultas.py`

**Interfaces:**
- Consumes: `snapshot_db.get_connection`, `snapshot_db.create_schema`,
  `snapshot_db.insert_redacoes` (Task 1).
- Produces: `consultas.buscar_metricas_gerais(conn) -> dict` (chaves:
  `total, media, mediana, desvio_padrao, distribuicao_faixas`),
  `consultas.buscar_metricas_por_escola(conn, escola_id: str) -> dict`
  (mesmas chaves), `consultas.buscar_escolas(conn) -> list[dict]`,
  `consultas.buscar_turmas_por_escola(conn, escola_id: str) -> list[dict]`,
  `consultas.buscar_ranking(conn, escola_id: str | None = None, turma_id:
  str | None = None, ordenar_por: str = "nota_final") -> list[dict]`,
  `consultas.buscar_redacoes_por_turma(conn, turma_id: str) -> list[dict]`.

- [ ] **Step 1: Escrever o teste falho**

`dashboards/seduc-pb/tests/test_consultas.py`:

```python
import pytest

from consultas import (
    buscar_escolas,
    buscar_metricas_gerais,
    buscar_metricas_por_escola,
    buscar_ranking,
    buscar_redacoes_por_turma,
    buscar_turmas_por_escola,
)
from snapshot_db import create_schema, get_connection, insert_redacoes

REDACOES = [
    {
        "id_redacao": "r1", "id_aluno": "a1", "nome_aluno": "Aluno Um",
        "escola_id": "e1", "escola_nome": "Escola Um",
        "turma_id": "t1", "turma_nome": "Turma A",
        "nota_final": 900, "c1": 180, "c2": 180, "c3": 180, "c4": 180, "c5": 180,
        "faixa_classificacao": "Muito alto", "posicao_geral": 1,
        "data_correcao": "2026-09-30T10:00:00",
    },
    {
        "id_redacao": "r2", "id_aluno": "a2", "nome_aluno": "Aluno Dois",
        "escola_id": "e1", "escola_nome": "Escola Um",
        "turma_id": "t1", "turma_nome": "Turma A",
        "nota_final": 700, "c1": 140, "c2": 140, "c3": 140, "c4": 140, "c5": 140,
        "faixa_classificacao": "Adequado", "posicao_geral": 2,
        "data_correcao": "2026-09-30T10:05:00",
    },
    {
        "id_redacao": "r3", "id_aluno": "a3", "nome_aluno": "Aluno Tres",
        "escola_id": "e2", "escola_nome": "Escola Dois",
        "turma_id": None, "turma_nome": None,
        "nota_final": 500, "c1": 100, "c2": 100, "c3": 100, "c4": 100, "c5": 100,
        "faixa_classificacao": "Baixo", "posicao_geral": 3,
        "data_correcao": "2026-09-30T10:10:00",
    },
]


@pytest.fixture
def conn(tmp_path):
    conexao = get_connection(tmp_path / "snapshot.db")
    create_schema(conexao)
    insert_redacoes(conexao, REDACOES)
    return conexao


def test_buscar_metricas_gerais(conn):
    metricas = buscar_metricas_gerais(conn)
    assert metricas["total"] == 3
    assert metricas["media"] == pytest.approx(700.0, abs=0.1)
    assert metricas["mediana"] == 700
    assert metricas["distribuicao_faixas"] == {
        "Muito alto": 1, "Adequado": 1, "Baixo": 1,
    }


def test_buscar_metricas_por_escola(conn):
    metricas = buscar_metricas_por_escola(conn, "e1")
    assert metricas["total"] == 2
    assert metricas["media"] == pytest.approx(800.0, abs=0.1)


def test_buscar_escolas(conn):
    escolas = buscar_escolas(conn)
    assert escolas == [
        {"escola_id": "e2", "escola_nome": "Escola Dois"},
        {"escola_id": "e1", "escola_nome": "Escola Um"},
    ]


def test_buscar_turmas_por_escola(conn):
    turmas = buscar_turmas_por_escola(conn, "e1")
    assert turmas == [{"turma_id": "t1", "turma_nome": "Turma A"}]


def test_buscar_ranking_filtra_por_escola_e_ordena(conn):
    linhas = buscar_ranking(conn, escola_id="e1", ordenar_por="nota_final")
    assert [linha["nome_aluno"] for linha in linhas] == ["Aluno Um", "Aluno Dois"]


def test_buscar_redacoes_por_turma(conn):
    alunos = buscar_redacoes_por_turma(conn, "t1")
    assert [a["nome_aluno"] for a in alunos] == ["Aluno Um", "Aluno Dois"]
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd dashboards/seduc-pb && .venv/bin/pytest tests/test_consultas.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'consultas'`

- [ ] **Step 3: Implementar `consultas.py`**

```python
"""Consultas de leitura sobre o snapshot - usadas pelas telas do app web."""
from __future__ import annotations

import sqlite3

ORDENACOES_VALIDAS = {"nota_final", "c1", "c2", "c3", "c4", "c5"}


def _calcular_metricas(linhas: list[sqlite3.Row]) -> dict:
    notas = [linha["nota_final"] for linha in linhas]
    total = len(notas)
    if total == 0:
        return {"total": 0, "media": 0, "mediana": 0, "desvio_padrao": 0, "distribuicao_faixas": {}}
    media = sum(notas) / total
    ordenadas = sorted(notas)
    meio = total // 2
    mediana = ordenadas[meio] if total % 2 == 1 else (ordenadas[meio - 1] + ordenadas[meio]) / 2
    variancia = sum((n - media) ** 2 for n in notas) / total
    desvio_padrao = variancia ** 0.5
    distribuicao: dict[str, int] = {}
    for linha in linhas:
        distribuicao[linha["faixa_classificacao"]] = distribuicao.get(linha["faixa_classificacao"], 0) + 1
    return {
        "total": total,
        "media": round(media, 1),
        "mediana": mediana,
        "desvio_padrao": round(desvio_padrao, 1),
        "distribuicao_faixas": distribuicao,
    }


def buscar_metricas_gerais(conn: sqlite3.Connection) -> dict:
    linhas = conn.execute("SELECT nota_final, faixa_classificacao FROM redacoes").fetchall()
    return _calcular_metricas(linhas)


def buscar_metricas_por_escola(conn: sqlite3.Connection, escola_id: str) -> dict:
    linhas = conn.execute(
        "SELECT nota_final, faixa_classificacao FROM redacoes WHERE escola_id = ?",
        (escola_id,),
    ).fetchall()
    return _calcular_metricas(linhas)


def buscar_escolas(conn: sqlite3.Connection) -> list[dict]:
    linhas = conn.execute(
        "SELECT DISTINCT escola_id, escola_nome FROM redacoes ORDER BY escola_nome"
    ).fetchall()
    return [dict(linha) for linha in linhas]


def buscar_turmas_por_escola(conn: sqlite3.Connection, escola_id: str) -> list[dict]:
    linhas = conn.execute(
        """
        SELECT DISTINCT turma_id, turma_nome
        FROM redacoes
        WHERE escola_id = ? AND turma_id IS NOT NULL
        ORDER BY turma_nome
        """,
        (escola_id,),
    ).fetchall()
    return [dict(linha) for linha in linhas]


def buscar_ranking(
    conn: sqlite3.Connection,
    escola_id: str | None = None,
    turma_id: str | None = None,
    ordenar_por: str = "nota_final",
) -> list[dict]:
    if ordenar_por not in ORDENACOES_VALIDAS:
        ordenar_por = "nota_final"
    condicoes = []
    parametros: list[str] = []
    if escola_id:
        condicoes.append("escola_id = ?")
        parametros.append(escola_id)
    if turma_id:
        condicoes.append("turma_id = ?")
        parametros.append(turma_id)
    where = f"WHERE {' AND '.join(condicoes)}" if condicoes else ""
    linhas = conn.execute(
        f"""
        SELECT posicao_geral, nome_aluno, escola_nome, turma_nome,
               nota_final, c1, c2, c3, c4, c5, faixa_classificacao
        FROM redacoes
        {where}
        ORDER BY {ordenar_por} DESC
        """,
        parametros,
    ).fetchall()
    return [dict(linha) for linha in linhas]


def buscar_redacoes_por_turma(conn: sqlite3.Connection, turma_id: str) -> list[dict]:
    linhas = conn.execute(
        """
        SELECT nome_aluno, nota_final, c1, c2, c3, c4, c5, faixa_classificacao
        FROM redacoes
        WHERE turma_id = ?
        ORDER BY nota_final DESC
        """,
        (turma_id,),
    ).fetchall()
    return [dict(linha) for linha in linhas]
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd dashboards/seduc-pb && .venv/bin/pytest tests/test_consultas.py -v`
Expected: PASS (6 testes)

- [ ] **Step 5: Commit**

```bash
git add dashboards/seduc-pb/consultas.py dashboards/seduc-pb/tests/test_consultas.py
git commit -m "feat(seduc-pb): consultas de leitura do snapshot para as telas"
```

---

### Task 9: App web + templates

**Files:**
- Create: `dashboards/seduc-pb/app.py`
- Create: `dashboards/seduc-pb/templates/base.html`
- Create: `dashboards/seduc-pb/templates/login.html`
- Create: `dashboards/seduc-pb/templates/visao_geral.html`
- Create: `dashboards/seduc-pb/templates/ranking.html`
- Create: `dashboards/seduc-pb/templates/escola.html`
- Create: `dashboards/seduc-pb/templates/turma.html`
- Test: `dashboards/seduc-pb/tests/test_app.py`

**Interfaces:**
- Consumes: `auth.verificar_login` (Task 6); `consultas.buscar_metricas_gerais`,
  `consultas.buscar_metricas_por_escola`, `consultas.buscar_escolas`,
  `consultas.buscar_turmas_por_escola`, `consultas.buscar_ranking`,
  `consultas.buscar_redacoes_por_turma` (Task 8); `snapshot_db.get_connection`,
  `snapshot_db.create_schema`, `snapshot_db.insert_redacoes` (Task 1);
  `auth.criar_usuario` (Task 6, usado só nos testes).
- Produces: `app.app` (instância FastAPI, importável para testes e para
  `uvicorn app:app`).

- [ ] **Step 1: Escrever os templates**

`dashboards/seduc-pb/templates/base.html`:

```html
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <title>Dashboard SEDUC-PB</title>
    <style>
        body { font-family: sans-serif; margin: 2rem; }
        table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
        th, td { border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; }
        nav a { margin-right: 1rem; }
    </style>
</head>
<body>
    {% if login %}
    <nav>
        <a href="/">Visao Geral</a>
        <a href="/ranking">Ranking</a>
        <span style="float:right">{{ login }} | <a href="/logout">Sair</a></span>
    </nav>
    <hr>
    {% endif %}
    {% block conteudo %}{% endblock %}
</body>
</html>
```

`dashboards/seduc-pb/templates/login.html`:

```html
{% extends "base.html" %}
{% block conteudo %}
<h1>Entrar</h1>
{% if erro %}<p style="color:red">{{ erro }}</p>{% endif %}
<form method="post" action="/login">
    <label>Login <input type="text" name="login" required></label><br>
    <label>Senha <input type="password" name="senha" required></label><br>
    <button type="submit">Entrar</button>
</form>
{% endblock %}
```

`dashboards/seduc-pb/templates/visao_geral.html`:

```html
{% extends "base.html" %}
{% block conteudo %}
<h1>Visao Geral</h1>
<p>Total de redacoes corrigidas: {{ metricas.total }}</p>
<p>Media: {{ metricas.media }} | Mediana: {{ metricas.mediana }} | Desvio-padrao: {{ metricas.desvio_padrao }}</p>
<h2>Distribuicao por faixa</h2>
<table>
    <tr><th>Faixa</th><th>Quantidade</th></tr>
    {% for faixa, quantidade in metricas.distribuicao_faixas.items() %}
    <tr><td>{{ faixa }}</td><td>{{ quantidade }}</td></tr>
    {% endfor %}
</table>
{% endblock %}
```

`dashboards/seduc-pb/templates/ranking.html`:

```html
{% extends "base.html" %}
{% block conteudo %}
<h1>Ranking</h1>
<form method="get" action="/ranking">
    <select name="escola_id">
        <option value="">Todas as escolas</option>
        {% for escola in escolas %}
        <option value="{{ escola.escola_id }}">{{ escola.escola_nome }}</option>
        {% endfor %}
    </select>
    <select name="ordenar_por">
        <option value="nota_final" {% if ordenar_por == "nota_final" %}selected{% endif %}>Nota</option>
        <option value="c1" {% if ordenar_por == "c1" %}selected{% endif %}>C1</option>
        <option value="c2" {% if ordenar_por == "c2" %}selected{% endif %}>C2</option>
        <option value="c3" {% if ordenar_por == "c3" %}selected{% endif %}>C3</option>
        <option value="c4" {% if ordenar_por == "c4" %}selected{% endif %}>C4</option>
        <option value="c5" {% if ordenar_por == "c5" %}selected{% endif %}>C5</option>
    </select>
    <button type="submit">Filtrar</button>
</form>
<table>
    <tr>
        <th>Posicao</th><th>Aluno</th><th>Escola</th><th>Turma</th><th>Nota</th>
        <th>C1</th><th>C2</th><th>C3</th><th>C4</th><th>C5</th><th>Classificacao</th>
    </tr>
    {% for linha in linhas %}
    <tr>
        <td>{{ linha.posicao_geral }}</td>
        <td>{{ linha.nome_aluno }}</td>
        <td>{{ linha.escola_nome }}</td>
        <td>{{ linha.turma_nome or "-" }}</td>
        <td>{{ linha.nota_final }}</td>
        <td>{{ linha.c1 }}</td>
        <td>{{ linha.c2 }}</td>
        <td>{{ linha.c3 }}</td>
        <td>{{ linha.c4 }}</td>
        <td>{{ linha.c5 }}</td>
        <td>{{ linha.faixa_classificacao }}</td>
    </tr>
    {% endfor %}
</table>
{% endblock %}
```

`dashboards/seduc-pb/templates/escola.html`:

```html
{% extends "base.html" %}
{% block conteudo %}
<h1>Escola</h1>
<p>Total de redacoes: {{ metricas.total }}</p>
<p>Media: {{ metricas.media }} | Mediana: {{ metricas.mediana }} | Desvio-padrao: {{ metricas.desvio_padrao }}</p>
<h2>Turmas</h2>
<ul>
    {% for turma in turmas %}
    <li><a href="/turma/{{ turma.turma_id }}">{{ turma.turma_nome }}</a></li>
    {% endfor %}
</ul>
{% endblock %}
```

`dashboards/seduc-pb/templates/turma.html`:

```html
{% extends "base.html" %}
{% block conteudo %}
<h1>Turma</h1>
<table>
    <tr><th>Aluno</th><th>Nota</th><th>C1</th><th>C2</th><th>C3</th><th>C4</th><th>C5</th><th>Classificacao</th></tr>
    {% for aluno in alunos %}
    <tr>
        <td>{{ aluno.nome_aluno }}</td>
        <td>{{ aluno.nota_final }}</td>
        <td>{{ aluno.c1 }}</td>
        <td>{{ aluno.c2 }}</td>
        <td>{{ aluno.c3 }}</td>
        <td>{{ aluno.c4 }}</td>
        <td>{{ aluno.c5 }}</td>
        <td>{{ aluno.faixa_classificacao }}</td>
    </tr>
    {% endfor %}
</table>
{% endblock %}
```

- [ ] **Step 2: Escrever o teste falho**

`dashboards/seduc-pb/tests/test_app.py`:

```python
import pytest
from fastapi.testclient import TestClient

from app import app
from auth import criar_usuario
from snapshot_db import create_schema, get_connection, insert_redacoes

REDACAO_EXEMPLO = {
    "id_redacao": "r1", "id_aluno": "a1", "nome_aluno": "Aluno Um",
    "escola_id": "e1", "escola_nome": "Escola Um",
    "turma_id": "t1", "turma_nome": "Turma A",
    "nota_final": 800, "c1": 160, "c2": 160, "c3": 160, "c4": 160, "c5": 160,
    "faixa_classificacao": "Alto", "posicao_geral": 1,
    "data_correcao": "2026-09-30T10:00:00",
}


@pytest.fixture
def snapshot_populado(tmp_path, monkeypatch):
    caminho = tmp_path / "snapshot.db"
    monkeypatch.setenv("SEDUC_DASHBOARD_SNAPSHOT_PATH", str(caminho))
    conn = get_connection(caminho)
    create_schema(conn)
    criar_usuario(conn, "gestor", "senha-teste")
    insert_redacoes(conn, [REDACAO_EXEMPLO])
    conn.close()
    return caminho


def test_rota_protegida_redireciona_para_login_sem_sessao(snapshot_populado):
    cliente = TestClient(app)
    resposta = cliente.get("/", follow_redirects=False)
    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/login"


def test_login_invalido_mostra_erro(snapshot_populado):
    cliente = TestClient(app)
    resposta = cliente.post("/login", data={"login": "gestor", "senha": "errada"})
    assert resposta.status_code == 401
    assert "invalidos" in resposta.text


def test_login_valido_permite_acessar_visao_geral(snapshot_populado):
    cliente = TestClient(app)
    resposta_login = cliente.post(
        "/login", data={"login": "gestor", "senha": "senha-teste"}, follow_redirects=False
    )
    assert resposta_login.status_code == 303
    resposta = cliente.get("/")
    assert resposta.status_code == 200
    assert "Total de redacoes corrigidas: 1" in resposta.text


def test_ranking_lista_a_redacao_cadastrada(snapshot_populado):
    cliente = TestClient(app)
    cliente.post("/login", data={"login": "gestor", "senha": "senha-teste"})
    resposta = cliente.get("/ranking")
    assert resposta.status_code == 200
    assert "Aluno Um" in resposta.text
```

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `cd dashboards/seduc-pb && .venv/bin/pytest tests/test_app.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 4: Implementar `app.py`**

```python
"""App web do dashboard SEDUC-PB - le exclusivamente do snapshot SQLite."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from auth import verificar_login
from consultas import (
    buscar_escolas,
    buscar_metricas_gerais,
    buscar_metricas_por_escola,
    buscar_ranking,
    buscar_redacoes_por_turma,
    buscar_turmas_por_escola,
)
from snapshot_db import get_connection

BASE_DIR = Path(__file__).resolve().parent
SECRET_KEY = os.environ.get("SEDUC_DASHBOARD_SECRET_KEY", "dev-secret-trocar-em-producao")

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def obter_snapshot():
    snapshot_path = Path(
        os.environ.get("SEDUC_DASHBOARD_SNAPSHOT_PATH", str(BASE_DIR / "snapshot.db"))
    )
    conn = get_connection(snapshot_path)
    try:
        yield conn
    finally:
        conn.close()


def usuario_logado(request: Request) -> str | None:
    return request.session.get("login")


@app.get("/login", response_class=HTMLResponse)
def tela_login(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={"erro": None})


@app.post("/login")
def processar_login(
    request: Request,
    login: str = Form(...),
    senha: str = Form(...),
    conn: sqlite3.Connection = Depends(obter_snapshot),
):
    if verificar_login(conn, login, senha):
        request.session["login"] = login
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"erro": "Login ou senha invalidos"},
        status_code=401,
    )


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def visao_geral(
    request: Request,
    login: str | None = Depends(usuario_logado),
    conn: sqlite3.Connection = Depends(obter_snapshot),
):
    if login is None:
        return RedirectResponse("/login", status_code=303)
    metricas = buscar_metricas_gerais(conn)
    return templates.TemplateResponse(
        request=request, name="visao_geral.html", context={"login": login, "metricas": metricas}
    )


@app.get("/ranking", response_class=HTMLResponse)
def ranking(
    request: Request,
    login: str | None = Depends(usuario_logado),
    conn: sqlite3.Connection = Depends(obter_snapshot),
    escola_id: str | None = None,
    turma_id: str | None = None,
    ordenar_por: str = "nota_final",
):
    if login is None:
        return RedirectResponse("/login", status_code=303)
    linhas = buscar_ranking(conn, escola_id=escola_id, turma_id=turma_id, ordenar_por=ordenar_por)
    escolas = buscar_escolas(conn)
    return templates.TemplateResponse(
        request=request,
        name="ranking.html",
        context={"login": login, "linhas": linhas, "escolas": escolas, "ordenar_por": ordenar_por},
    )


@app.get("/escola/{escola_id}", response_class=HTMLResponse)
def escola(
    escola_id: str,
    request: Request,
    login: str | None = Depends(usuario_logado),
    conn: sqlite3.Connection = Depends(obter_snapshot),
):
    if login is None:
        return RedirectResponse("/login", status_code=303)
    metricas = buscar_metricas_por_escola(conn, escola_id)
    turmas = buscar_turmas_por_escola(conn, escola_id)
    return templates.TemplateResponse(
        request=request,
        name="escola.html",
        context={"login": login, "metricas": metricas, "turmas": turmas},
    )


@app.get("/turma/{turma_id}", response_class=HTMLResponse)
def turma(
    turma_id: str,
    request: Request,
    login: str | None = Depends(usuario_logado),
    conn: sqlite3.Connection = Depends(obter_snapshot),
):
    if login is None:
        return RedirectResponse("/login", status_code=303)
    alunos = buscar_redacoes_por_turma(conn, turma_id)
    return templates.TemplateResponse(
        request=request, name="turma.html", context={"login": login, "alunos": alunos}
    )
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `cd dashboards/seduc-pb && .venv/bin/pytest tests/test_app.py -v`
Expected: PASS (4 testes)

- [ ] **Step 6: Rodar a suíte completa do projeto**

Run: `cd dashboards/seduc-pb && .venv/bin/pytest -v`
Expected: PASS (todos os testes de todas as tasks, ~22 testes no total)

- [ ] **Step 7: Escrever o README de execução**

`dashboards/seduc-pb/README.md`:

```markdown
# Dashboard SEDUC-PB (v1)

Dashboard gerencial pontual para o Simulado de Redacao da SEDUC-PB.
Isolado do produto principal - nao importa nada de `src/agente_ia_edu`.

## Setup

    cd dashboards/seduc-pb
    python3.13 -m venv .venv
    .venv/bin/pip install -e ".[dev]"

## Rodar os testes

    .venv/bin/pytest -v

Os testes do export job precisam de um Postgres acessivel em
`SEDUC_DASHBOARD_TEST_DATABASE_URL` (por padrao, o Postgres de dev local
na porta 5433) - eles criam e destroem um schema proprio de teste
(`seduc_pb_dashboard_test`), sem tocar nos dados reais.

## Exportar um snapshot

    export SEDUC_DASHBOARD_SOURCE_DATABASE_URL="postgresql://usuario_so_leitura:senha@host:porta/banco"
    .venv/bin/python export_snapshot.py

Gera (ou atualiza) `snapshot.db` na mesma pasta. Rodar de novo a qualquer
momento atualiza o snapshot - e idempotente.

## Criar o primeiro usuario

    .venv/bin/python auth.py --snapshot-path snapshot.db --login gestor --senha "troque-esta-senha"

## Subir o app

    export SEDUC_DASHBOARD_SECRET_KEY="troque-por-uma-chave-aleatoria-em-producao"
    .venv/bin/uvicorn app:app --reload

Acesse http://localhost:8000/login
```

- [ ] **Step 8: Commit**

```bash
git add dashboards/seduc-pb/app.py dashboards/seduc-pb/templates dashboards/seduc-pb/tests/test_app.py dashboards/seduc-pb/README.md
git commit -m "feat(seduc-pb): app web com as 4 telas (visao geral, ranking, escola, turma)"
```

---

## Nota final (não é uma tarefa a executar agora)

Esta v1 funciona com os dados que existem hoje no banco principal (hoje,
poucas dezenas de correções aprovadas). Crescer para os ~50.000 registros
esperados depois que a correção em massa rodar em volume é só rodar
`export_snapshot.py` de novo - SQLite comporta essa escala sem mudança de
código. Hierarquia GRE/Município, classificação qualitativa via IA,
diagnóstico pedagógico, central de relatórios, exportação Excel e
agendamento automático do export ficam fora desta v1, como combinado - uma
v2 fica para se e quando forem pedidas.
