# Simulado Química ENEM (captação de leads) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir, do zero, o repositório `simulado-quimica-enem`: uma plataforma pública de simulados de Química ENEM que captura leads (nome/e-mail/WhatsApp) antes do simulado, calcula uma nota estilo ENEM, gera feedback por assunto interno, e envia o resultado por WhatsApp via Z-API — sem qualquer dependência do `agente-ia-edu-core`.

**Architecture:** FastAPI (Python 3.13) com SQLAlchemy 2.0, frontend server-rendido em Jinja2 + JS vanilla, Postgres em produção (SQLite em memória nos testes), sessão do aluno via cookie por tentativa, admin único autenticado por sessão assinada. Docker Compose para deploy no Hostinger VPS.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Pydantic v2 / pydantic-settings, Jinja2, httpx (cliente Z-API), Alembic, pytest, Docker Compose.

**Spec:** [docs/superpowers/specs/2026-09-18-simulado-aberto-leads-design.md](../specs/2026-09-18-simulado-aberto-leads-design.md)

**Repositório novo:** `/Users/marcoviana/simulado-quimica-enem` (todas as tasks abaixo criam arquivos dentro dele, não no `agente-ia-edu-core`). GitHub: repositório privado `simulado-quimica-enem`.

## Global Constraints

- Nota: `score = score_min + (acertos / total_questoes) * (score_max - score_min)`, com `score_min`/`score_max` padrão 308,6/858,7, configuráveis por Simulado.
- Uma `Attempt` finalizada não aceita mais respostas; o mesmo e-mail pode iniciar quantas tentativas novas quiser (retomada/repetição ilimitada).
- Sessão do aluno é resolvida por cookie (um por simulado em andamento, não por login).
- Admin é um único usuário fixo via variável de ambiente — sem tela de cadastro de outros admins.
- `assunto` da questão nunca é serializado nas respostas públicas (catálogo, questão, resultado) — só usado para agregação interna do feedback.
- Envio de WhatsApp (Z-API) roda em background após finalizar; falha não bloqueia nem aparece como erro pro aluno.
- Sem integração com `agente-ia-edu-core`, sem múltiplos admins, sem CRM externo no v1.

## Estrutura de arquivos (visão geral)

```
simulado-quimica-enem/
  pyproject.toml
  alembic.ini
  .gitignore
  .env.example
  README.md
  docker-compose.yml
  docker/Dockerfile
  migrations/
    env.py
    versions/
  src/simulado_quimica_enem/
    __init__.py
    config.py
    db.py
    models.py
    scoring.py
    services/
      __init__.py
      leads.py
      attempts.py
      images.py
      auth.py
      whatsapp.py
    routers/
      __init__.py
      public.py
      admin.py
    templates/
      base.html
      public/{catalog,lead_form,question,result}.html
      admin/{login,simulados_list,simulado_form,questoes_list,questao_form,leads_list}.html
    static/
      css/styles.css
      js/attempt.js
      js/admin.js
    main.py
  tests/
    conftest.py
    test_scoring.py
    test_leads_service.py
    test_attempts_service.py
    test_images_service.py
    test_auth_service.py
    test_whatsapp_service.py
    test_public_router.py
    test_admin_router.py
```

---

### Task 1: Scaffold do repositório

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `README.md`
- Create: `.env.example`
- Create: `src/simulado_quimica_enem/__init__.py`
- Create: `tests/__init__.py`

**Interfaces:**
- Produces: pacote instalável `simulado_quimica_enem` (import root para todas as tasks seguintes).

- [ ] **Step 1: Criar a pasta do repositório e inicializar git**

```bash
mkdir -p /Users/marcoviana/simulado-quimica-enem
cd /Users/marcoviana/simulado-quimica-enem
git init
```

- [ ] **Step 2: Criar `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "simulado-quimica-enem"
version = "0.1.0"
description = "Simulado aberto de Quimica ENEM para captacao de leads"
requires-python = ">=3.13,<3.14"
dependencies = [
    "fastapi>=0.115,<1.0",
    "uvicorn[standard]>=0.30,<1.0",
    "SQLAlchemy>=2.0,<3.0",
    "pydantic>=2.0,<3.0",
    "pydantic-settings>=2.0,<3.0",
    "jinja2>=3.1,<4.0",
    "python-multipart>=0.0.9,<1.0",
    "itsdangerous>=2.2,<3.0",
    "httpx>=0.27,<1.0",
    "psycopg[binary]>=3.2,<4.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0,<9.0",
    "alembic>=1.13,<2.0",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
```

- [ ] **Step 3: Criar `.gitignore`**

```
__pycache__/
*.pyc
.venv/
*.egg-info/
.env
var/uploads/
.pytest_cache/
```

- [ ] **Step 4: Criar `.env.example`**

```
DATABASE_URL=postgresql+psycopg://simulado:simulado@localhost:5432/simulado_quimica_enem
ADMIN_USERNAME=admin
ADMIN_PASSWORD=troque-esta-senha
SESSION_SECRET=troque-este-segredo
UPLOAD_DIR=var/uploads
ZAPI_INSTANCE_ID=
ZAPI_TOKEN=
ZAPI_CLIENT_TOKEN=
```

- [ ] **Step 5: Criar `README.md`**

```markdown
# Simulado Química ENEM

Plataforma pública de simulados de Química para captação de leads. Ver spec em
`docs/superpowers/specs/2026-09-18-simulado-aberto-leads-design.md` no repo
`agente-ia-edu-core` para o desenho completo.

## Setup local

\`\`\`bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
\`\`\`

## Testes

\`\`\`bash
pytest
\`\`\`
```

- [ ] **Step 6: Criar pacotes vazios e instalar**

```bash
mkdir -p src/simulado_quimica_enem tests
touch src/simulado_quimica_enem/__init__.py tests/__init__.py
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -c "import simulado_quimica_enem"
```

Expected: nenhum erro de import.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore: scaffold do projeto simulado-quimica-enem"
```

---

### Task 2: Config (variáveis de ambiente)

**Files:**
- Create: `src/simulado_quimica_enem/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings` (pydantic-settings `BaseSettings`) com campos `database_url: str`, `admin_username: str`, `admin_password: str`, `session_secret: str`, `upload_dir: str`, `zapi_instance_id: str`, `zapi_token: str`, `zapi_client_token: str`; e `get_settings() -> Settings` (cacheado via `functools.lru_cache`).

- [ ] **Step 1: Escrever o teste**

```python
# tests/test_config.py
import os

from simulado_quimica_enem.config import Settings, get_settings


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("SESSION_SECRET", "abc123")
    monkeypatch.setenv("UPLOAD_DIR", "var/uploads")
    monkeypatch.setenv("ZAPI_INSTANCE_ID", "inst")
    monkeypatch.setenv("ZAPI_TOKEN", "tok")
    monkeypatch.setenv("ZAPI_CLIENT_TOKEN", "client-tok")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.database_url == "sqlite:///:memory:"
    assert settings.admin_username == "admin"
    assert settings.zapi_instance_id == "inst"
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_config.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'simulado_quimica_enem.config'`

- [ ] **Step 3: Implementar**

```python
# src/simulado_quimica_enem/config.py
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    admin_username: str
    admin_password: str
    session_secret: str
    upload_dir: str = "var/uploads"
    zapi_instance_id: str = ""
    zapi_token: str = ""
    zapi_client_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/simulado_quimica_enem/config.py tests/test_config.py
git commit -m "feat: config via variaveis de ambiente"
```

---

### Task 3: Modelos de dados + sessão de banco

**Files:**
- Create: `src/simulado_quimica_enem/db.py`
- Create: `src/simulado_quimica_enem/models.py`
- Create: `tests/conftest.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `Base` (declarative base), `Simulado`, `Questao`, `Lead`, `Attempt`, `Answer` (modelos SQLAlchemy 2.0, `Mapped`/`mapped_column`); `db.py` expõe `create_engine_from_url(url: str)`, `SessionLocal_for(engine)`, `get_db()` (dependency FastAPI que usa `get_settings().database_url`). `Answer` tem `UniqueConstraint("attempt_id", "questao_id")` — Task 7 depende disso pra fazer upsert de resposta.
- Produces (test fixture, reusado por todas as tasks seguintes): `tests/conftest.py::db_session` — sessão SQLAlchemy contra SQLite em memória com todas as tabelas criadas.

IDs são `String(36)` (uuid4 hex), não `UUID` nativo — mantém os testes rodando em SQLite em memória sem precisar de Postgres.

- [ ] **Step 1: Escrever `tests/conftest.py`**

```python
# tests/conftest.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from simulado_quimica_enem.models import Base


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)
    session: Session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
```

- [ ] **Step 2: Escrever `tests/test_models.py`**

```python
# tests/test_models.py
from simulado_quimica_enem.models import Answer, Attempt, Lead, Questao, Simulado


def test_create_simulado_com_questoes_lead_attempt_answer(db_session):
    simulado = Simulado(titulo="Simulado Geral", descricao="Teste", score_min=308.6, score_max=858.7)
    questao = Questao(
        simulado=simulado,
        ordem=1,
        enunciado="Qual a formula da agua?",
        alternativas=[
            {"letra": "A", "texto": "H2O", "imagem_url": None},
            {"letra": "B", "texto": "CO2", "imagem_url": None},
            {"letra": "C", "texto": "O2", "imagem_url": None},
            {"letra": "D", "texto": "NaCl", "imagem_url": None},
            {"letra": "E", "texto": "CH4", "imagem_url": None},
        ],
        gabarito="A",
        resolucao="Agua e H2O.",
        assunto="Ligacoes quimicas",
    )
    lead = Lead(nome="Fulano", email="fulano@example.com", whatsapp="+5511999990000")
    attempt = Attempt(simulado=simulado, lead=lead, session_token="tok-123", status="em_andamento")
    answer = Answer(attempt=attempt, questao=questao, alternativa_selecionada="A", is_correct=True)

    db_session.add_all([simulado, questao, lead, attempt, answer])
    db_session.commit()

    loaded = db_session.query(Simulado).one()
    assert loaded.questoes[0].assunto == "Ligacoes quimicas"
    assert loaded.questoes[0].alternativas[0]["letra"] == "A"
    assert loaded.tentativas[0].respostas[0].alternativa_selecionada == "A"
    assert loaded.tentativas[0].lead.email == "fulano@example.com"
```

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `pytest tests/test_models.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'simulado_quimica_enem.models'`

- [ ] **Step 4: Implementar `src/simulado_quimica_enem/models.py`**

```python
# src/simulado_quimica_enem/models.py
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, Float, ForeignKey, JSON, String, Boolean, Integer, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Simulado(Base):
    __tablename__ = "simulados"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    titulo: Mapped[str] = mapped_column(String(200))
    descricao: Mapped[str] = mapped_column(String(2000), default="")
    score_min: Mapped[float] = mapped_column(Float, default=308.6)
    score_max: Mapped[float] = mapped_column(Float, default=858.7)
    status: Mapped[str] = mapped_column(String(20), default="rascunho")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    questoes: Mapped[list["Questao"]] = relationship(
        back_populates="simulado", order_by="Questao.ordem", cascade="all, delete-orphan"
    )
    tentativas: Mapped[list["Attempt"]] = relationship(back_populates="simulado")


class Questao(Base):
    __tablename__ = "questoes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    simulado_id: Mapped[str] = mapped_column(ForeignKey("simulados.id"))
    ordem: Mapped[int] = mapped_column(Integer, default=0)
    enunciado: Mapped[str] = mapped_column(String)
    enunciado_imagem_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    alternativas: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    gabarito: Mapped[str] = mapped_column(String(1))
    resolucao: Mapped[str] = mapped_column(String, default="")
    resolucao_imagem_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    assunto: Mapped[str] = mapped_column(String(200))

    simulado: Mapped["Simulado"] = relationship(back_populates="questoes")


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    nome: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    whatsapp: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tentativas: Mapped[list["Attempt"]] = relationship(back_populates="lead")


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    simulado_id: Mapped[str] = mapped_column(ForeignKey("simulados.id"))
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"))
    session_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="em_andamento")
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    whatsapp_status: Mapped[str] = mapped_column(String(20), default="pendente")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    simulado: Mapped["Simulado"] = relationship(back_populates="tentativas")
    lead: Mapped["Lead"] = relationship(back_populates="tentativas")
    respostas: Mapped[list["Answer"]] = relationship(back_populates="attempt", cascade="all, delete-orphan")


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (UniqueConstraint("attempt_id", "questao_id", name="uq_answer_attempt_questao"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id"))
    questao_id: Mapped[str] = mapped_column(ForeignKey("questoes.id"))
    alternativa_selecionada: Mapped[str] = mapped_column(String(1))
    is_correct: Mapped[bool] = mapped_column(Boolean)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    attempt: Mapped["Attempt"] = relationship(back_populates="respostas")
    questao: Mapped["Questao"] = relationship()
```

- [ ] **Step 5: Implementar `src/simulado_quimica_enem/db.py`**

```python
# src/simulado_quimica_enem/db.py
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from simulado_quimica_enem.config import get_settings

_engine = None
_SessionLocal = None


def _init() -> None:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(get_settings().database_url)
        _SessionLocal = sessionmaker(bind=_engine)


def get_db() -> Generator[Session, None, None]:
    _init()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 6: Rodar e confirmar que passa**

Run: `pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/simulado_quimica_enem/models.py src/simulado_quimica_enem/db.py tests/conftest.py tests/test_models.py
git commit -m "feat: modelos de dados (Simulado, Questao, Lead, Attempt, Answer)"
```

---

### Task 4: Alembic (migração inicial)

**Files:**
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/0001_initial.py`

**Interfaces:**
- Consumes: `simulado_quimica_enem.models.Base` (Task 3), `simulado_quimica_enem.config.get_settings` (Task 2).
- Produces: comando `alembic upgrade head` cria todas as tabelas de `models.py` num Postgres/SQLite apontado por `DATABASE_URL`.

- [ ] **Step 1: Criar `alembic.ini`**

```ini
[alembic]
script_location = migrations
prepend_sys_path = .

[loggers]
keys = root,sqlalchemy,alembic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handlers]
keys = console

[handler_console]
class = StreamHandler
args = (sys.stderr,)
formatter = generic

[formatters]
keys = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

- [ ] **Step 2: Criar `migrations/env.py`**

```python
# migrations/env.py
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from simulado_quimica_enem.config import get_settings
from simulado_quimica_enem.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 3: Criar `migrations/script.py.mako`**

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 4: Gerar a migração inicial (autogenerate) contra Postgres local de teste**

```bash
export DATABASE_URL=postgresql+psycopg://simulado:simulado@localhost:5432/simulado_quimica_enem
alembic revision --autogenerate -m "initial"
```

Expected: cria `migrations/versions/<hash>_initial.py` com `op.create_table(...)` para `simulados`, `questoes`, `leads`, `attempts`, `answers`. Renomeie o arquivo gerado para `migrations/versions/0001_initial.py` e confira manualmente que as 5 tabelas aparecem no `upgrade()`.

- [ ] **Step 5: Aplicar e verificar**

```bash
alembic upgrade head
```

Expected: sem erro; `psql $DATABASE_URL -c '\dt'` lista as 5 tabelas mais `alembic_version`.

- [ ] **Step 6: Commit**

```bash
git add alembic.ini migrations/
git commit -m "feat: migracao inicial do banco (alembic)"
```

---

## Fan-out paralelizável

As Tasks 5 a 10 abaixo só dependem da Task 3 (modelos) e Task 2 (config) — nenhuma depende
das outras deste grupo. Ao executar com `subagent-driven-development`, despache um subagente
por task deste grupo **em paralelo**; só as Tasks 11-14 (routers e integração final) precisam
esperar o grupo inteiro terminar.

---

### Task 5: Pontuação e feedback por assunto

**Files:**
- Create: `src/simulado_quimica_enem/scoring.py`
- Test: `tests/test_scoring.py`

**Interfaces:**
- Produces: `calcular_score(acertos: int, total_questoes: int, score_min: float, score_max: float) -> float`; `RespostaParaFeedback = tuple[str, bool]` (assunto, acertou); `FeedbackAssunto` (dataclass: `assunto: str`, `acertos: int`, `total: int`, `percentual: float`, `classificacao: str`); `gerar_feedback_por_assunto(respostas: list[RespostaParaFeedback]) -> list[FeedbackAssunto]`.
- Classificação: `percentual >= 70` → `"forte"`; `40 <= percentual < 70` → `"mediano"`; `percentual < 40` → `"atencao"`.

- [ ] **Step 1: Escrever o teste**

```python
# tests/test_scoring.py
import pytest

from simulado_quimica_enem.scoring import calcular_score, gerar_feedback_por_assunto


@pytest.mark.parametrize(
    "acertos,total,esperado",
    [
        (0, 10, 308.6),
        (10, 10, 858.7),
        (5, 10, 308.6 + 0.5 * (858.7 - 308.6)),
    ],
)
def test_calcular_score(acertos, total, esperado):
    assert calcular_score(acertos, total, 308.6, 858.7) == pytest.approx(esperado)


def test_calcular_score_zero_questoes_levanta_erro():
    with pytest.raises(ValueError):
        calcular_score(0, 0, 308.6, 858.7)


def test_gerar_feedback_por_assunto_classifica_faixas():
    respostas = [
        ("Estequiometria", True),
        ("Estequiometria", True),
        ("Estequiometria", True),
        ("Estequiometria", True),
        ("Eletroquimica", True),
        ("Eletroquimica", False),
        ("Eletroquimica", False),
        ("Eletroquimica", False),
        ("Eletroquimica", False),
    ]

    feedback = gerar_feedback_por_assunto(respostas)
    por_assunto = {f.assunto: f for f in feedback}

    assert por_assunto["Estequiometria"].percentual == pytest.approx(100.0)
    assert por_assunto["Estequiometria"].classificacao == "forte"
    assert por_assunto["Eletroquimica"].percentual == pytest.approx(20.0)
    assert por_assunto["Eletroquimica"].classificacao == "atencao"
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_scoring.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'simulado_quimica_enem.scoring'`

- [ ] **Step 3: Implementar**

```python
# src/simulado_quimica_enem/scoring.py
from dataclasses import dataclass


def calcular_score(acertos: int, total_questoes: int, score_min: float, score_max: float) -> float:
    if total_questoes <= 0:
        raise ValueError("total_questoes deve ser maior que zero")
    delta = score_max - score_min
    return score_min + (acertos / total_questoes) * delta


@dataclass
class FeedbackAssunto:
    assunto: str
    acertos: int
    total: int
    percentual: float
    classificacao: str


def _classificar(percentual: float) -> str:
    if percentual >= 70:
        return "forte"
    if percentual >= 40:
        return "mediano"
    return "atencao"


def gerar_feedback_por_assunto(respostas: list[tuple[str, bool]]) -> list[FeedbackAssunto]:
    agregados: dict[str, list[int]] = {}
    ordem: list[str] = []
    for assunto, acertou in respostas:
        if assunto not in agregados:
            agregados[assunto] = [0, 0]
            ordem.append(assunto)
        agregados[assunto][1] += 1
        if acertou:
            agregados[assunto][0] += 1

    resultado = []
    for assunto in ordem:
        acertos, total = agregados[assunto]
        percentual = (acertos / total) * 100
        resultado.append(
            FeedbackAssunto(
                assunto=assunto,
                acertos=acertos,
                total=total,
                percentual=percentual,
                classificacao=_classificar(percentual),
            )
        )
    return resultado
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_scoring.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/simulado_quimica_enem/scoring.py tests/test_scoring.py
git commit -m "feat: calculo de score e feedback por assunto"
```

---

### Task 6: Serviço de Leads

**Files:**
- Create: `src/simulado_quimica_enem/services/__init__.py`
- Create: `src/simulado_quimica_enem/services/leads.py`
- Test: `tests/test_leads_service.py`

**Interfaces:**
- Consumes: `simulado_quimica_enem.models.Lead` (Task 3), fixture `db_session` (Task 3, `tests/conftest.py`).
- Produces: `get_or_create_lead(db: Session, nome: str, email: str, whatsapp: str) -> Lead` — se já existe lead com esse e-mail, atualiza nome/whatsapp e devolve o existente; senão cria um novo.

- [ ] **Step 1: Escrever o teste**

```python
# tests/test_leads_service.py
from simulado_quimica_enem.services.leads import get_or_create_lead


def test_cria_lead_novo(db_session):
    lead = get_or_create_lead(db_session, nome="Fulano", email="fulano@example.com", whatsapp="+5511999990000")

    assert lead.id is not None
    assert lead.email == "fulano@example.com"


def test_reaproveita_lead_existente_pelo_email(db_session):
    primeiro = get_or_create_lead(db_session, nome="Fulano", email="fulano@example.com", whatsapp="+5511999990000")
    segundo = get_or_create_lead(db_session, nome="Fulano da Silva", email="fulano@example.com", whatsapp="+5511888880000")

    assert segundo.id == primeiro.id
    assert segundo.nome == "Fulano da Silva"
    assert segundo.whatsapp == "+5511888880000"
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_leads_service.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'simulado_quimica_enem.services'`

- [ ] **Step 3: Implementar**

```python
# src/simulado_quimica_enem/services/__init__.py
```

```python
# src/simulado_quimica_enem/services/leads.py
from sqlalchemy import select
from sqlalchemy.orm import Session

from simulado_quimica_enem.models import Lead


def get_or_create_lead(db: Session, nome: str, email: str, whatsapp: str) -> Lead:
    existente = db.execute(select(Lead).where(Lead.email == email)).scalar_one_or_none()
    if existente is not None:
        existente.nome = nome
        existente.whatsapp = whatsapp
        db.commit()
        db.refresh(existente)
        return existente

    lead = Lead(nome=nome, email=email, whatsapp=whatsapp)
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_leads_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/simulado_quimica_enem/services/ tests/test_leads_service.py
git commit -m "feat: servico de leads (get_or_create por email)"
```

---

### Task 7: Serviço de Tentativas (start / responder / retomar / finalizar)

**Files:**
- Create: `src/simulado_quimica_enem/services/attempts.py`
- Test: `tests/test_attempts_service.py`

**Interfaces:**
- Consumes: `Simulado`, `Questao`, `Lead`, `Attempt`, `Answer` (Task 3); `calcular_score`, `gerar_feedback_por_assunto`, `FeedbackAssunto` (Task 5); fixture `db_session` (Task 3).
- Produces:
  - `start_attempt(db: Session, simulado: Simulado, lead: Lead) -> Attempt`
  - `get_attempt_by_token(db: Session, token: str) -> Attempt | None`
  - `record_answer(db: Session, attempt: Attempt, questao_id: str, alternativa: str) -> Answer` (upsert — chamar de novo pra mesma questão atualiza a resposta, não duplica)
  - `is_attempt_complete(attempt: Attempt) -> bool` (todas as questões do simulado têm resposta)
  - `finalize_attempt(db: Session, attempt: Attempt) -> Attempt` (levanta `ValueError` se `attempt.status == "finalizada"` ou se `not is_attempt_complete(attempt)`; senão calcula `score`, seta `status = "finalizada"`, `finished_at`)
  - `build_feedback(attempt: Attempt) -> list[FeedbackAssunto]` (usa `attempt.respostas` + `questao.assunto`, só deve ser chamado com a tentativa já finalizada)

- [ ] **Step 1: Escrever o teste**

```python
# tests/test_attempts_service.py
import pytest

from simulado_quimica_enem.models import Attempt, Questao, Simulado
from simulado_quimica_enem.services.attempts import (
    build_feedback,
    finalize_attempt,
    get_attempt_by_token,
    is_attempt_complete,
    record_answer,
    start_attempt,
)
from simulado_quimica_enem.services.leads import get_or_create_lead


def _make_simulado_com_duas_questoes(db_session) -> Simulado:
    simulado = Simulado(titulo="Simulado Teste", score_min=308.6, score_max=858.7)
    q1 = Questao(
        simulado=simulado, ordem=1, enunciado="Q1",
        alternativas=[{"letra": l, "texto": l, "imagem_url": None} for l in "ABCDE"],
        gabarito="A", resolucao="r1", assunto="Estequiometria",
    )
    q2 = Questao(
        simulado=simulado, ordem=2, enunciado="Q2",
        alternativas=[{"letra": l, "texto": l, "imagem_url": None} for l in "ABCDE"],
        gabarito="B", resolucao="r2", assunto="Eletroquimica",
    )
    db_session.add_all([simulado, q1, q2])
    db_session.commit()
    return simulado


def test_start_attempt_cria_tentativa_em_andamento_com_token(db_session):
    simulado = _make_simulado_com_duas_questoes(db_session)
    lead = get_or_create_lead(db_session, nome="Fulano", email="fulano@example.com", whatsapp="+5511999990000")

    attempt = start_attempt(db_session, simulado, lead)

    assert attempt.status == "em_andamento"
    assert attempt.session_token
    assert get_attempt_by_token(db_session, attempt.session_token).id == attempt.id


def test_record_answer_e_upsert(db_session):
    simulado = _make_simulado_com_duas_questoes(db_session)
    lead = get_or_create_lead(db_session, nome="Fulano", email="fulano@example.com", whatsapp="+5511999990000")
    attempt = start_attempt(db_session, simulado, lead)
    questao = simulado.questoes[0]

    record_answer(db_session, attempt, questao.id, "B")
    record_answer(db_session, attempt, questao.id, "A")

    assert len(attempt.respostas) == 1
    assert attempt.respostas[0].alternativa_selecionada == "A"
    assert attempt.respostas[0].is_correct is True


def test_is_attempt_complete_e_finalize_attempt(db_session):
    simulado = _make_simulado_com_duas_questoes(db_session)
    lead = get_or_create_lead(db_session, nome="Fulano", email="fulano@example.com", whatsapp="+5511999990000")
    attempt = start_attempt(db_session, simulado, lead)
    q1, q2 = simulado.questoes

    assert is_attempt_complete(attempt) is False

    record_answer(db_session, attempt, q1.id, "A")  # correta
    record_answer(db_session, attempt, q2.id, "C")  # errada (gabarito B)
    assert is_attempt_complete(attempt) is True

    finalized = finalize_attempt(db_session, attempt)

    assert finalized.status == "finalizada"
    assert finalized.finished_at is not None
    assert finalized.score == pytest.approx(308.6 + 0.5 * (858.7 - 308.6))

    feedback = build_feedback(finalized)
    por_assunto = {f.assunto: f for f in feedback}
    assert por_assunto["Estequiometria"].classificacao == "forte"
    assert por_assunto["Eletroquimica"].classificacao == "atencao"


def test_finalize_attempt_incompleta_levanta_erro(db_session):
    simulado = _make_simulado_com_duas_questoes(db_session)
    lead = get_or_create_lead(db_session, nome="Fulano", email="fulano@example.com", whatsapp="+5511999990000")
    attempt = start_attempt(db_session, simulado, lead)

    with pytest.raises(ValueError):
        finalize_attempt(db_session, attempt)


def test_finalize_attempt_ja_finalizada_levanta_erro(db_session):
    simulado = _make_simulado_com_duas_questoes(db_session)
    lead = get_or_create_lead(db_session, nome="Fulano", email="fulano@example.com", whatsapp="+5511999990000")
    attempt = start_attempt(db_session, simulado, lead)
    for questao in simulado.questoes:
        record_answer(db_session, attempt, questao.id, questao.gabarito)
    finalize_attempt(db_session, attempt)

    with pytest.raises(ValueError):
        finalize_attempt(db_session, attempt)


def test_record_answer_apos_finalizar_levanta_erro(db_session):
    simulado = _make_simulado_com_duas_questoes(db_session)
    lead = get_or_create_lead(db_session, nome="Fulano", email="fulano@example.com", whatsapp="+5511999990000")
    attempt = start_attempt(db_session, simulado, lead)
    for questao in simulado.questoes:
        record_answer(db_session, attempt, questao.id, questao.gabarito)
    finalize_attempt(db_session, attempt)

    with pytest.raises(ValueError):
        record_answer(db_session, attempt, simulado.questoes[0].id, "A")
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_attempts_service.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'simulado_quimica_enem.services.attempts'`

- [ ] **Step 3: Implementar**

```python
# src/simulado_quimica_enem/services/attempts.py
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from simulado_quimica_enem.models import Answer, Attempt, Lead, Questao, Simulado
from simulado_quimica_enem.scoring import FeedbackAssunto, calcular_score, gerar_feedback_por_assunto


def start_attempt(db: Session, simulado: Simulado, lead: Lead) -> Attempt:
    attempt = Attempt(
        simulado=simulado,
        lead=lead,
        session_token=secrets.token_urlsafe(32),
        status="em_andamento",
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def get_attempt_by_token(db: Session, token: str) -> Attempt | None:
    return db.execute(select(Attempt).where(Attempt.session_token == token)).scalar_one_or_none()


def record_answer(db: Session, attempt: Attempt, questao_id: str, alternativa: str) -> Answer:
    if attempt.status == "finalizada":
        raise ValueError("tentativa ja finalizada nao aceita respostas")

    questao = db.get(Questao, questao_id)
    if questao is None:
        raise ValueError(f"questao {questao_id} nao encontrada")

    existente = db.execute(
        select(Answer).where(Answer.attempt_id == attempt.id, Answer.questao_id == questao_id)
    ).scalar_one_or_none()

    is_correct = alternativa == questao.gabarito
    if existente is not None:
        existente.alternativa_selecionada = alternativa
        existente.is_correct = is_correct
        db.commit()
        db.refresh(existente)
        return existente

    answer = Answer(attempt=attempt, questao=questao, alternativa_selecionada=alternativa, is_correct=is_correct)
    db.add(answer)
    db.commit()
    db.refresh(answer)
    return answer


def is_attempt_complete(attempt: Attempt) -> bool:
    respondidas = {answer.questao_id for answer in attempt.respostas}
    todas = {questao.id for questao in attempt.simulado.questoes}
    return respondidas == todas


def finalize_attempt(db: Session, attempt: Attempt) -> Attempt:
    if attempt.status == "finalizada":
        raise ValueError("tentativa ja finalizada")
    if not is_attempt_complete(attempt):
        raise ValueError("tentativa incompleta, ha questoes sem resposta")

    acertos = sum(1 for answer in attempt.respostas if answer.is_correct)
    total = len(attempt.simulado.questoes)
    attempt.score = calcular_score(acertos, total, attempt.simulado.score_min, attempt.simulado.score_max)
    attempt.status = "finalizada"
    attempt.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(attempt)
    return attempt


def build_feedback(attempt: Attempt) -> list[FeedbackAssunto]:
    respostas = [(answer.questao.assunto, answer.is_correct) for answer in attempt.respostas]
    return gerar_feedback_por_assunto(respostas)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_attempts_service.py -v`
Expected: PASS (6 testes)

- [ ] **Step 5: Commit**

```bash
git add src/simulado_quimica_enem/services/attempts.py tests/test_attempts_service.py
git commit -m "feat: servico de tentativas (start/responder/retomar/finalizar)"
```

---

### Task 8: Serviço de upload de imagem

**Files:**
- Create: `src/simulado_quimica_enem/services/images.py`
- Test: `tests/test_images_service.py`

**Interfaces:**
- Produces: `ALLOWED_CONTENT_TYPES: set[str]` (`{"image/png", "image/jpeg", "image/webp"}`), `MAX_IMAGE_BYTES = 5 * 1024 * 1024`, `save_image_bytes(upload_dir: str, filename: str, content_type: str, data: bytes) -> str` (grava em `<upload_dir>/<uuid4hex><ext>`, devolve caminho relativo `uploads/<uuid4hex><ext>`; levanta `ValueError` se tipo não permitido ou `len(data) > MAX_IMAGE_BYTES`), `save_uploaded_image(upload_dir: str, file) -> str` (wrapper que lê um `fastapi.UploadFile` de forma síncrona via `file.file.read()` e delega pra `save_image_bytes`).

- [ ] **Step 1: Escrever o teste**

```python
# tests/test_images_service.py
import pytest

from simulado_quimica_enem.services.images import MAX_IMAGE_BYTES, save_image_bytes


def test_salva_imagem_valida_e_devolve_caminho_relativo(tmp_path):
    caminho = save_image_bytes(str(tmp_path), "foto.png", "image/png", b"conteudo-fake")

    assert caminho.startswith("uploads/")
    assert caminho.endswith(".png")
    assert (tmp_path / caminho).read_bytes() == b"conteudo-fake"


def test_rejeita_content_type_nao_permitido(tmp_path):
    with pytest.raises(ValueError):
        save_image_bytes(str(tmp_path), "arquivo.pdf", "application/pdf", b"conteudo-fake")


def test_rejeita_arquivo_maior_que_o_limite(tmp_path):
    dados_grandes = b"x" * (MAX_IMAGE_BYTES + 1)
    with pytest.raises(ValueError):
        save_image_bytes(str(tmp_path), "foto.png", "image/png", dados_grandes)
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_images_service.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'simulado_quimica_enem.services.images'`

- [ ] **Step 3: Implementar**

```python
# src/simulado_quimica_enem/services/images.py
import os
import uuid
from pathlib import Path
from typing import Any

ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024

_EXT_BY_CONTENT_TYPE = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


def save_image_bytes(upload_dir: str, filename: str, content_type: str, data: bytes) -> str:
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError(f"tipo de arquivo nao permitido: {content_type}")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("arquivo maior que o limite permitido (5MB)")

    uploads_dir = Path(upload_dir) / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    ext = _EXT_BY_CONTENT_TYPE[content_type]
    novo_nome = f"{uuid.uuid4().hex}{ext}"
    destino = uploads_dir / novo_nome
    destino.write_bytes(data)

    return os.path.join("uploads", novo_nome)


def save_uploaded_image(upload_dir: str, file: Any) -> str:
    data = file.file.read()
    return save_image_bytes(upload_dir, file.filename, file.content_type, data)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_images_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/simulado_quimica_enem/services/images.py tests/test_images_service.py
git commit -m "feat: servico de upload de imagem"
```

---

### Task 9: Autenticação do admin

**Files:**
- Create: `src/simulado_quimica_enem/services/auth.py`
- Test: `tests/test_auth_service.py`

**Interfaces:**
- Consumes: `simulado_quimica_enem.config.Settings` (Task 2).
- Produces: `verify_credentials(settings: Settings, username: str, password: str) -> bool`, `create_admin_session_token(settings: Settings) -> str`, `verify_admin_session_token(settings: Settings, token: str) -> bool`. Usa `itsdangerous.URLSafeTimedSerializer` com `settings.session_secret`; token expira em 8 horas (`max_age=28800`).

- [ ] **Step 1: Escrever o teste**

```python
# tests/test_auth_service.py
from simulado_quimica_enem.config import Settings
from simulado_quimica_enem.services.auth import (
    create_admin_session_token,
    verify_admin_session_token,
    verify_credentials,
)


def _settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        admin_username="admin",
        admin_password="s3nha-forte",
        session_secret="segredo-de-teste",
    )


def test_verify_credentials_aceita_login_correto():
    assert verify_credentials(_settings(), "admin", "s3nha-forte") is True


def test_verify_credentials_rejeita_senha_errada():
    assert verify_credentials(_settings(), "admin", "senha-errada") is False


def test_token_criado_e_valido():
    settings = _settings()
    token = create_admin_session_token(settings)

    assert verify_admin_session_token(settings, token) is True


def test_token_invalido_e_rejeitado():
    settings = _settings()

    assert verify_admin_session_token(settings, "token-forjado") is False


def test_token_assinado_com_outro_segredo_e_rejeitado():
    settings = _settings()
    token = create_admin_session_token(settings)
    outro_settings = Settings(
        database_url="sqlite:///:memory:",
        admin_username="admin",
        admin_password="s3nha-forte",
        session_secret="outro-segredo",
    )

    assert verify_admin_session_token(outro_settings, token) is False
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_auth_service.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'simulado_quimica_enem.services.auth'`

- [ ] **Step 3: Implementar**

```python
# src/simulado_quimica_enem/services/auth.py
import hmac

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from simulado_quimica_enem.config import Settings

SESSION_MAX_AGE_SECONDS = 8 * 60 * 60


def verify_credentials(settings: Settings, username: str, password: str) -> bool:
    return hmac.compare_digest(username, settings.admin_username) and hmac.compare_digest(
        password, settings.admin_password
    )


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="admin-session")


def create_admin_session_token(settings: Settings) -> str:
    return _serializer(settings).dumps({"admin": True})


def verify_admin_session_token(settings: Settings, token: str) -> bool:
    try:
        data = _serializer(settings).loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return False
    return bool(data.get("admin"))
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_auth_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/simulado_quimica_enem/services/auth.py tests/test_auth_service.py
git commit -m "feat: autenticacao do admin (sessao assinada)"
```

---

### Task 10: Cliente WhatsApp (Z-API)

**Files:**
- Create: `src/simulado_quimica_enem/services/whatsapp.py`
- Test: `tests/test_whatsapp_service.py`

**Interfaces:**
- Consumes: `Attempt`, `Simulado` (Task 3), `FeedbackAssunto` (Task 5), `Settings` (Task 2).
- Produces: `build_result_message(lead_nome: str, simulado_titulo: str, score: float, feedback: list[FeedbackAssunto]) -> str`; `send_whatsapp_message(settings: Settings, phone: str, message: str, client: httpx.Client | None = None) -> bool` (POST pra `https://api.z-api.io/instances/{instance_id}/token/{token}/send-text`, header `Client-Token`, corpo `{"phone": phone, "message": message}`; devolve `True` em `2xx`, `False` em qualquer outra resposta ou exceção de rede — nunca propaga exceção).

- [ ] **Step 1: Escrever o teste**

```python
# tests/test_whatsapp_service.py
import httpx

from simulado_quimica_enem.config import Settings
from simulado_quimica_enem.scoring import FeedbackAssunto
from simulado_quimica_enem.services.whatsapp import build_result_message, send_whatsapp_message


def _settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        admin_username="admin",
        admin_password="senha",
        session_secret="segredo",
        zapi_instance_id="inst123",
        zapi_token="tok123",
        zapi_client_token="client-tok123",
    )


def test_build_result_message_inclui_nota_e_feedback():
    feedback = [
        FeedbackAssunto(assunto="Estequiometria", acertos=4, total=4, percentual=100.0, classificacao="forte"),
        FeedbackAssunto(assunto="Eletroquimica", acertos=1, total=5, percentual=20.0, classificacao="atencao"),
    ]

    mensagem = build_result_message("Fulano", "Simulado Geral", 620.5, feedback)

    assert "Fulano" in mensagem
    assert "620.5" in mensagem or "620,5" in mensagem
    assert "Estequiometria" in mensagem
    assert "Eletroquimica" in mensagem


def test_send_whatsapp_message_devolve_true_em_sucesso():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/instances/inst123/token/tok123/send-text"
        assert request.headers["Client-Token"] == "client-tok123"
        return httpx.Response(200, json={"messageId": "abc"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.z-api.io")

    assert send_whatsapp_message(_settings(), "+5511999990000", "ola", client=client) is True


def test_send_whatsapp_message_devolve_false_em_erro_http():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "falha"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.z-api.io")

    assert send_whatsapp_message(_settings(), "+5511999990000", "ola", client=client) is False


def test_send_whatsapp_message_devolve_false_em_excecao_de_rede():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("falha de rede", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.z-api.io")

    assert send_whatsapp_message(_settings(), "+5511999990000", "ola", client=client) is False
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_whatsapp_service.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'simulado_quimica_enem.services.whatsapp'`

- [ ] **Step 3: Implementar**

```python
# src/simulado_quimica_enem/services/whatsapp.py
import httpx

from simulado_quimica_enem.config import Settings
from simulado_quimica_enem.scoring import FeedbackAssunto

ZAPI_BASE_URL = "https://api.z-api.io"


def build_result_message(lead_nome: str, simulado_titulo: str, score: float, feedback: list[FeedbackAssunto]) -> str:
    linhas = [
        f"Ola {lead_nome}! Seu resultado no {simulado_titulo}:",
        f"Nota: {score:.1f}",
        "",
        "Desempenho por assunto:",
    ]
    for item in feedback:
        linhas.append(f"- {item.assunto}: {item.acertos}/{item.total} ({item.classificacao})")
    return "\n".join(linhas)


def send_whatsapp_message(settings: Settings, phone: str, message: str, client: httpx.Client | None = None) -> bool:
    owns_client = client is None
    http_client = client or httpx.Client(base_url=ZAPI_BASE_URL, timeout=10.0)
    try:
        response = http_client.post(
            f"/instances/{settings.zapi_instance_id}/token/{settings.zapi_token}/send-text",
            json={"phone": phone, "message": message},
            headers={"Client-Token": settings.zapi_client_token},
        )
        return response.status_code < 300
    except httpx.HTTPError:
        return False
    finally:
        if owns_client:
            http_client.close()
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `pytest tests/test_whatsapp_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/simulado_quimica_enem/services/whatsapp.py tests/test_whatsapp_service.py
git commit -m "feat: cliente whatsapp via z-api"
```

---

## Fim do fan-out

A partir daqui as tasks dependem de várias do grupo acima ao mesmo tempo — execute em
sequência (Task 11 → 12 → 13 → 14).

---

### Task 11: Router público (catálogo, lead, questões, progresso, finalizar, resultado)

**Files:**
- Create: `src/simulado_quimica_enem/routers/__init__.py`
- Create: `src/simulado_quimica_enem/routers/public.py`
- Create: `src/simulado_quimica_enem/templates/base.html`
- Create: `src/simulado_quimica_enem/templates/public/catalog.html`
- Create: `src/simulado_quimica_enem/templates/public/lead_form.html`
- Create: `src/simulado_quimica_enem/templates/public/question.html`
- Create: `src/simulado_quimica_enem/templates/public/progresso.html`
- Create: `src/simulado_quimica_enem/templates/public/result.html`
- Test: `tests/test_public_router.py`

**Interfaces:**
- Consumes: `get_db` (Task 3), `get_settings` (Task 2), `Simulado`/`Questao` (Task 3), `get_or_create_lead` (Task 6), `start_attempt`/`get_attempt_by_token`/`record_answer`/`is_attempt_complete`/`finalize_attempt`/`build_feedback` (Task 7), `build_result_message`/`send_whatsapp_message` (Task 10).
- Produces: `router: APIRouter` exportado por `routers/public.py`, montado depois pela Task 13. Cookie de sessão: `sqe_attempt_{simulado_id}`.

- [ ] **Step 1: Escrever os templates**

```html
{# src/simulado_quimica_enem/templates/base.html #}
<!DOCTYPE html>
<html lang="pt-br">
<head>
  <meta charset="utf-8">
  <title>{% block title %}Simulado Química ENEM{% endblock %}</title>
  <link rel="stylesheet" href="/static/css/styles.css">
</head>
<body>
  <main>{% block content %}{% endblock %}</main>
</body>
</html>
```

```html
{# src/simulado_quimica_enem/templates/public/catalog.html #}
{% extends "base.html" %}
{% block content %}
<h1>Simulados disponíveis</h1>
<ul>
  {% for simulado in simulados %}
  <li><a href="/simulados/{{ simulado.id }}">{{ simulado.titulo }}</a></li>
  {% endfor %}
</ul>
{% endblock %}
```

```html
{# src/simulado_quimica_enem/templates/public/lead_form.html #}
{% extends "base.html" %}
{% block content %}
<h1>{{ simulado.titulo }}</h1>
<p>{{ simulado.descricao }}</p>
<form method="post" action="/simulados/{{ simulado.id }}/iniciar">
  <label>Nome <input type="text" name="nome" required></label>
  <label>E-mail <input type="email" name="email" required></label>
  <label>WhatsApp <input type="tel" name="whatsapp" required></label>
  <button type="submit">Começar simulado</button>
</form>
{% endblock %}
```

```html
{# src/simulado_quimica_enem/templates/public/question.html #}
{% extends "base.html" %}
{% block content %}
<h1>{{ simulado.titulo }} — Questão {{ questao.ordem }} de {{ total_questoes }}</h1>
<p>{{ questao.enunciado }}</p>
{% if questao.enunciado_imagem_url %}<img src="/media/{{ questao.enunciado_imagem_url }}" alt="">{% endif %}
<form method="post" action="/simulados/{{ simulado.id }}/questoes/{{ questao.ordem }}/responder">
  {% for alt in questao.alternativas %}
  <label>
    <input type="radio" name="alternativa" value="{{ alt.letra }}" {% if resposta_atual == alt.letra %}checked{% endif %}>
    {{ alt.letra }}) {{ alt.texto }}
  </label>
  {% endfor %}
  <button type="submit">Responder</button>
</form>
<a href="/simulados/{{ simulado.id }}/progresso">Ver mapa de questões</a>
{% endblock %}
```

```html
{# src/simulado_quimica_enem/templates/public/progresso.html #}
{% extends "base.html" %}
{% block content %}
<h1>Seu progresso — {{ simulado.titulo }}</h1>
<ol>
  {% for questao in simulado.questoes %}
  <li>
    <a href="/simulados/{{ simulado.id }}/questoes/{{ questao.ordem }}">Questão {{ questao.ordem }}</a>
    {% if questao.id in respondidas %}(respondida){% else %}(pendente){% endif %}
  </li>
  {% endfor %}
</ol>
{% if completo %}
<form method="post" action="/simulados/{{ simulado.id }}/finalizar"
      onsubmit="return confirm('Você respondeu todas as questões. Deseja finalizar o simulado?');">
  <button type="submit">Finalizar simulado</button>
</form>
{% endif %}
{% endblock %}
```

```html
{# src/simulado_quimica_enem/templates/public/result.html #}
{% extends "base.html" %}
{% block content %}
<h1>Seu resultado — {{ simulado.titulo }}</h1>
<p class="score">Nota: {{ "%.1f"|format(attempt.score) }}</p>
<h2>Desempenho por assunto</h2>
<ul>
  {% for item in feedback %}
  <li>{{ item.assunto }}: {{ item.acertos }}/{{ item.total }} ({{ item.classificacao }})</li>
  {% endfor %}
</ul>
<section class="oferta">
  <h2>Quer ir além?</h2>
  <p>Matricule-se na plataforma Química do ENEM e continue evoluindo.</p>
  <a href="https://quimicadoenem.com.br/matricula">Quero me matricular</a>
</section>
{% endblock %}
```

- [ ] **Step 2: Escrever o teste**

```python
# tests/test_public_router.py
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from simulado_quimica_enem.db import get_db
from simulado_quimica_enem.models import Base, Questao, Simulado
from simulado_quimica_enem.routers import public


def _alternativas():
    return [{"letra": letra, "texto": letra, "imagem_url": None} for letra in "ABCDE"]


def _make_client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(public.router)
    app.dependency_overrides[get_db] = override_get_db

    enviados = []

    def fake_send_whatsapp_message(settings, phone, message):
        enviados.append((phone, message))
        return True

    monkeypatch.setattr(public, "send_whatsapp_message", fake_send_whatsapp_message)

    return TestClient(app), TestingSession, enviados


def _seed_simulado(TestingSession) -> str:
    session = TestingSession()
    simulado = Simulado(titulo="Simulado Geral", status="publicado", score_min=308.6, score_max=858.7)
    q1 = Questao(
        simulado=simulado, ordem=1, enunciado="Questao 1", alternativas=_alternativas(),
        gabarito="A", resolucao="r1", assunto="Estequiometria",
    )
    q2 = Questao(
        simulado=simulado, ordem=2, enunciado="Questao 2", alternativas=_alternativas(),
        gabarito="B", resolucao="r2", assunto="Eletroquimica",
    )
    session.add_all([simulado, q1, q2])
    session.commit()
    simulado_id = simulado.id
    session.close()
    return simulado_id


def test_fluxo_completo_do_aluno(monkeypatch):
    client, TestingSession, enviados = _make_client(monkeypatch)
    simulado_id = _seed_simulado(TestingSession)

    catalogo = client.get("/simulados", follow_redirects=True)
    assert "Simulado Geral" in catalogo.text

    lead_page = client.get(f"/simulados/{simulado_id}", follow_redirects=True)
    assert "Começar simulado" in lead_page.text

    inicio = client.post(
        f"/simulados/{simulado_id}/iniciar",
        data={"nome": "Fulano", "email": "fulano@example.com", "whatsapp": "+5511999990000"},
        follow_redirects=True,
    )
    assert "Questao 1" in inicio.text

    resp_q1 = client.post(
        f"/simulados/{simulado_id}/questoes/1/responder", data={"alternativa": "A"}, follow_redirects=True
    )
    assert "Questao 2" in resp_q1.text

    resp_q2 = client.post(
        f"/simulados/{simulado_id}/questoes/2/responder", data={"alternativa": "C"}, follow_redirects=True
    )
    assert "Finalizar simulado" in resp_q2.text

    resultado = client.post(f"/simulados/{simulado_id}/finalizar", follow_redirects=True)
    assert "Nota" in resultado.text
    assert "Estequiometria" in resultado.text
    assert "Eletroquimica" in resultado.text

    assert enviados == [("+5511999990000", enviados[0][1])]
    assert "Fulano" in enviados[0][1]


def test_ver_questao_sem_cookie_redireciona_para_lead_form(monkeypatch):
    client, TestingSession, _ = _make_client(monkeypatch)
    simulado_id = _seed_simulado(TestingSession)

    resp = client.get(f"/simulados/{simulado_id}/questoes/1", follow_redirects=True)

    assert "Começar simulado" in resp.text


def test_resultado_antes_de_finalizar_redireciona_para_lead_form(monkeypatch):
    client, TestingSession, _ = _make_client(monkeypatch)
    simulado_id = _seed_simulado(TestingSession)
    client.post(
        f"/simulados/{simulado_id}/iniciar",
        data={"nome": "Fulano", "email": "fulano@example.com", "whatsapp": "+5511999990000"},
        follow_redirects=True,
    )

    resp = client.get(f"/simulados/{simulado_id}/resultado", follow_redirects=True)

    assert "Começar simulado" in resp.text


def test_finalizar_incompleto_nao_quebra_redireciona_para_progresso(monkeypatch):
    client, TestingSession, _ = _make_client(monkeypatch)
    simulado_id = _seed_simulado(TestingSession)
    client.post(
        f"/simulados/{simulado_id}/iniciar",
        data={"nome": "Fulano", "email": "fulano@example.com", "whatsapp": "+5511999990000"},
        follow_redirects=True,
    )

    resp = client.post(f"/simulados/{simulado_id}/finalizar", follow_redirects=True)

    assert resp.status_code == 200
    assert "Seu progresso" in resp.text


def test_responder_apos_finalizar_nao_quebra_redireciona_para_resultado(monkeypatch):
    client, TestingSession, _ = _make_client(monkeypatch)
    simulado_id = _seed_simulado(TestingSession)
    client.post(
        f"/simulados/{simulado_id}/iniciar",
        data={"nome": "Fulano", "email": "fulano@example.com", "whatsapp": "+5511999990000"},
        follow_redirects=True,
    )
    client.post(f"/simulados/{simulado_id}/questoes/1/responder", data={"alternativa": "A"}, follow_redirects=True)
    client.post(f"/simulados/{simulado_id}/questoes/2/responder", data={"alternativa": "B"}, follow_redirects=True)
    client.post(f"/simulados/{simulado_id}/finalizar", follow_redirects=True)

    resp = client.post(
        f"/simulados/{simulado_id}/questoes/1/responder", data={"alternativa": "B"}, follow_redirects=True
    )

    assert resp.status_code == 200
    assert "Nota" in resp.text
```

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `pytest tests/test_public_router.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'simulado_quimica_enem.routers'`

- [ ] **Step 4: Implementar `src/simulado_quimica_enem/routers/__init__.py`**

```python
# src/simulado_quimica_enem/routers/__init__.py
```

- [ ] **Step 5: Implementar `src/simulado_quimica_enem/routers/public.py`**

```python
# src/simulado_quimica_enem/routers/public.py
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from simulado_quimica_enem.config import get_settings
from simulado_quimica_enem.db import get_db
from simulado_quimica_enem.models import Simulado
from simulado_quimica_enem.services.attempts import (
    build_feedback,
    finalize_attempt,
    get_attempt_by_token,
    is_attempt_complete,
    record_answer,
    start_attempt,
)
from simulado_quimica_enem.services.leads import get_or_create_lead
from simulado_quimica_enem.services.whatsapp import build_result_message, send_whatsapp_message

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _cookie_name(simulado_id: str) -> str:
    return f"sqe_attempt_{simulado_id}"


def _get_current_attempt(request: Request, db: Session, simulado_id: str):
    token = request.cookies.get(_cookie_name(simulado_id))
    if not token:
        return None
    return get_attempt_by_token(db, token)


@router.get("/simulados")
def listar_simulados(request: Request, db: Session = Depends(get_db)):
    simulados = db.execute(select(Simulado).where(Simulado.status == "publicado")).scalars().all()
    return templates.TemplateResponse("public/catalog.html", {"request": request, "simulados": simulados})


@router.get("/simulados/{simulado_id}")
def abrir_simulado(simulado_id: str, request: Request, db: Session = Depends(get_db)):
    simulado = db.get(Simulado, simulado_id)
    if simulado is None or simulado.status != "publicado":
        return RedirectResponse("/simulados", status_code=303)

    attempt = _get_current_attempt(request, db, simulado_id)
    if attempt is not None and attempt.status == "finalizada":
        return RedirectResponse(f"/simulados/{simulado_id}/resultado", status_code=303)
    if attempt is not None:
        return RedirectResponse(f"/simulados/{simulado_id}/progresso", status_code=303)

    return templates.TemplateResponse("public/lead_form.html", {"request": request, "simulado": simulado})


@router.post("/simulados/{simulado_id}/iniciar")
def iniciar_simulado(
    simulado_id: str,
    nome: str = Form(...),
    email: str = Form(...),
    whatsapp: str = Form(...),
    db: Session = Depends(get_db),
):
    simulado = db.get(Simulado, simulado_id)
    if simulado is None or simulado.status != "publicado":
        return RedirectResponse("/simulados", status_code=303)

    lead = get_or_create_lead(db, nome=nome, email=email, whatsapp=whatsapp)
    attempt = start_attempt(db, simulado, lead)

    response = RedirectResponse(f"/simulados/{simulado_id}/questoes/1", status_code=303)
    response.set_cookie(_cookie_name(simulado_id), attempt.session_token, httponly=True, max_age=60 * 60 * 24 * 30)
    return response


@router.get("/simulados/{simulado_id}/questoes/{ordem}")
def ver_questao(simulado_id: str, ordem: int, request: Request, db: Session = Depends(get_db)):
    simulado = db.get(Simulado, simulado_id)
    attempt = _get_current_attempt(request, db, simulado_id)
    if simulado is None or attempt is None:
        return RedirectResponse(f"/simulados/{simulado_id}", status_code=303)
    if attempt.status == "finalizada":
        return RedirectResponse(f"/simulados/{simulado_id}/resultado", status_code=303)

    questao = next((q for q in simulado.questoes if q.ordem == ordem), None)
    if questao is None:
        return RedirectResponse(f"/simulados/{simulado_id}/progresso", status_code=303)

    resposta_atual = next(
        (a.alternativa_selecionada for a in attempt.respostas if a.questao_id == questao.id), None
    )
    return templates.TemplateResponse(
        "public/question.html",
        {
            "request": request,
            "simulado": simulado,
            "questao": questao,
            "total_questoes": len(simulado.questoes),
            "resposta_atual": resposta_atual,
        },
    )


@router.post("/simulados/{simulado_id}/questoes/{ordem}/responder")
def responder_questao(
    simulado_id: str,
    ordem: int,
    request: Request,
    alternativa: str = Form(...),
    db: Session = Depends(get_db),
):
    simulado = db.get(Simulado, simulado_id)
    attempt = _get_current_attempt(request, db, simulado_id)
    if simulado is None or attempt is None:
        return RedirectResponse(f"/simulados/{simulado_id}", status_code=303)
    if attempt.status == "finalizada":
        return RedirectResponse(f"/simulados/{simulado_id}/resultado", status_code=303)

    questao = next((q for q in simulado.questoes if q.ordem == ordem), None)
    if questao is None:
        return RedirectResponse(f"/simulados/{simulado_id}/progresso", status_code=303)

    record_answer(db, attempt, questao.id, alternativa)

    proxima = ordem + 1
    if any(q.ordem == proxima for q in simulado.questoes):
        return RedirectResponse(f"/simulados/{simulado_id}/questoes/{proxima}", status_code=303)
    return RedirectResponse(f"/simulados/{simulado_id}/progresso", status_code=303)


@router.get("/simulados/{simulado_id}/progresso")
def ver_progresso(simulado_id: str, request: Request, db: Session = Depends(get_db)):
    simulado = db.get(Simulado, simulado_id)
    attempt = _get_current_attempt(request, db, simulado_id)
    if simulado is None or attempt is None:
        return RedirectResponse(f"/simulados/{simulado_id}", status_code=303)
    if attempt.status == "finalizada":
        return RedirectResponse(f"/simulados/{simulado_id}/resultado", status_code=303)

    respondidas = {a.questao_id for a in attempt.respostas}
    return templates.TemplateResponse(
        "public/progresso.html",
        {
            "request": request,
            "simulado": simulado,
            "respondidas": respondidas,
            "completo": is_attempt_complete(attempt),
        },
    )


@router.post("/simulados/{simulado_id}/finalizar")
def finalizar_simulado(
    simulado_id: str, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    simulado = db.get(Simulado, simulado_id)
    attempt = _get_current_attempt(request, db, simulado_id)
    if simulado is None or attempt is None:
        return RedirectResponse(f"/simulados/{simulado_id}", status_code=303)
    if attempt.status == "finalizada":
        return RedirectResponse(f"/simulados/{simulado_id}/resultado", status_code=303)
    if not is_attempt_complete(attempt):
        return RedirectResponse(f"/simulados/{simulado_id}/progresso", status_code=303)

    finalize_attempt(db, attempt)

    feedback = build_feedback(attempt)
    mensagem = build_result_message(attempt.lead.nome, simulado.titulo, attempt.score, feedback)
    settings = get_settings()
    background_tasks.add_task(send_whatsapp_message, settings, attempt.lead.whatsapp, mensagem)

    return RedirectResponse(f"/simulados/{simulado_id}/resultado", status_code=303)


@router.get("/simulados/{simulado_id}/resultado")
def ver_resultado(simulado_id: str, request: Request, db: Session = Depends(get_db)):
    simulado = db.get(Simulado, simulado_id)
    attempt = _get_current_attempt(request, db, simulado_id)
    if simulado is None or attempt is None or attempt.status != "finalizada":
        return RedirectResponse(f"/simulados/{simulado_id}", status_code=303)

    feedback = build_feedback(attempt)
    return templates.TemplateResponse(
        "public/result.html", {"request": request, "simulado": simulado, "attempt": attempt, "feedback": feedback}
    )
```

- [ ] **Step 6: Rodar e confirmar que passa**

Run: `pytest tests/test_public_router.py -v`
Expected: PASS (5 testes)

- [ ] **Step 7: Commit**

```bash
git add src/simulado_quimica_enem/routers/ src/simulado_quimica_enem/templates/ tests/test_public_router.py
git commit -m "feat: fluxo publico do aluno (catalogo, lead, questoes, progresso, resultado)"
```

---

### Task 12: Router admin (login, CRUD de simulados/questões, leads)

**Escopo do v1:** login, criar/listar simulados, publicar/despublicar, criar/listar questões
(com upload de imagem em enunciado/alternativas/resolução), listar leads e exportar CSV.
Edição e reordenação de questões ficam para uma iteração seguinte — não bloqueiam o objetivo
central (captar lead e aplicar o simulado).

**Files:**
- Create: `src/simulado_quimica_enem/routers/admin.py`
- Create: `src/simulado_quimica_enem/templates/admin/login.html`
- Create: `src/simulado_quimica_enem/templates/admin/simulados_list.html`
- Create: `src/simulado_quimica_enem/templates/admin/questoes_list.html`
- Create: `src/simulado_quimica_enem/templates/admin/leads_list.html`
- Test: `tests/test_admin_router.py`

**Interfaces:**
- Consumes: `get_db` (Task 3), `get_settings` (Task 2), `verify_credentials`/`create_admin_session_token`/`verify_admin_session_token` (Task 9), `save_uploaded_image` (Task 8), `Simulado`/`Questao`/`Lead`/`Attempt` (Task 3).
- Produces: `router: APIRouter` exportado por `routers/admin.py`, montado pela Task 13. Cookie `sqe_admin_session`.

- [ ] **Step 1: Escrever os templates**

```html
{# src/simulado_quimica_enem/templates/admin/login.html #}
{% extends "base.html" %}
{% block content %}
<h1>Admin — Login</h1>
{% if erro %}<p class="erro">{{ erro }}</p>{% endif %}
<form method="post" action="/admin/login">
  <label>Usuário <input type="text" name="username" required></label>
  <label>Senha <input type="password" name="password" required></label>
  <button type="submit">Entrar</button>
</form>
{% endblock %}
```

```html
{# src/simulado_quimica_enem/templates/admin/simulados_list.html #}
{% extends "base.html" %}
{% block content %}
<h1>Simulados</h1>
<form method="post" action="/admin/simulados">
  <label>Título <input type="text" name="titulo" required></label>
  <label>Descrição <textarea name="descricao"></textarea></label>
  <label>Nota mínima <input type="number" step="0.1" name="score_min" value="308.6" required></label>
  <label>Nota máxima <input type="number" step="0.1" name="score_max" value="858.7" required></label>
  <button type="submit">Criar simulado</button>
</form>
<table>
  {% for simulado in simulados %}
  <tr>
    <td>{{ simulado.titulo }}</td>
    <td>{{ simulado.status }}</td>
    <td><a href="/admin/simulados/{{ simulado.id }}/questoes">Questões</a></td>
    <td>
      <form method="post" action="/admin/simulados/{{ simulado.id }}/publicar">
        <button type="submit">{% if simulado.status == "publicado" %}Despublicar{% else %}Publicar{% endif %}</button>
      </form>
    </td>
  </tr>
  {% endfor %}
</table>
{% endblock %}
```

```html
{# src/simulado_quimica_enem/templates/admin/questoes_list.html #}
{% extends "base.html" %}
{% block content %}
<h1>Questões — {{ simulado.titulo }}</h1>
<form method="post" action="/admin/simulados/{{ simulado.id }}/questoes" enctype="multipart/form-data">
  <label>Enunciado <textarea name="enunciado" required></textarea></label>
  <label>Imagem do enunciado <input type="file" name="enunciado_imagem"></label>
  {% for letra in ["A", "B", "C", "D", "E"] %}
  <label>Alternativa {{ letra }} <input type="text" name="alt_{{ letra }}_texto" required></label>
  <label>Imagem alternativa {{ letra }} <input type="file" name="alt_{{ letra }}_imagem"></label>
  {% endfor %}
  <label>Gabarito
    <select name="gabarito" required>
      {% for letra in ["A", "B", "C", "D", "E"] %}<option value="{{ letra }}">{{ letra }}</option>{% endfor %}
    </select>
  </label>
  <label>Resolução <textarea name="resolucao" required></textarea></label>
  <label>Imagem da resolução <input type="file" name="resolucao_imagem"></label>
  <label>Assunto interno <input type="text" name="assunto" required></label>
  <button type="submit">Adicionar questão</button>
</form>
<ol>
  {% for questao in simulado.questoes %}
  <li>{{ questao.enunciado }} — assunto: {{ questao.assunto }} — gabarito: {{ questao.gabarito }}</li>
  {% endfor %}
</ol>
{% endblock %}
```

```html
{# src/simulado_quimica_enem/templates/admin/leads_list.html #}
{% extends "base.html" %}
{% block content %}
<h1>Leads</h1>
<p><a href="/admin/leads/export.csv">Exportar CSV</a></p>
<table>
  {% for attempt in attempts %}
  <tr>
    <td>{{ attempt.lead.nome }}</td>
    <td>{{ attempt.lead.email }}</td>
    <td>{{ attempt.lead.whatsapp }}</td>
    <td>{{ attempt.simulado.titulo }}</td>
    <td>{{ attempt.status }}</td>
    <td>{{ attempt.score if attempt.score is not none else "-" }}</td>
  </tr>
  {% endfor %}
</table>
{% endblock %}
```

- [ ] **Step 2: Escrever o teste**

```python
# tests/test_admin_router.py
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from simulado_quimica_enem.config import Settings, get_settings
from simulado_quimica_enem.db import get_db
from simulado_quimica_enem.models import Base
from simulado_quimica_enem.routers import admin


def _make_client(tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    settings = Settings(
        database_url="sqlite:///:memory:",
        admin_username="admin",
        admin_password="s3nha-forte",
        session_secret="segredo-de-teste",
        upload_dir=str(tmp_path),
    )

    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: settings

    return TestClient(app), TestingSession


def _login(client) -> None:
    client.post("/admin/login", data={"username": "admin", "password": "s3nha-forte"}, follow_redirects=True)


def test_login_com_credenciais_erradas_mostra_erro(tmp_path):
    client, _ = _make_client(tmp_path)

    resp = client.post("/admin/login", data={"username": "admin", "password": "errada"})

    assert resp.status_code == 200
    assert "Login" in resp.text


def test_paginas_admin_sem_login_redirecionam_para_login(tmp_path):
    client, _ = _make_client(tmp_path)

    resp = client.get("/admin/simulados", follow_redirects=True)

    assert "Admin — Login" in resp.text


def test_login_cria_simulado_publica_e_cria_questao_com_imagem(tmp_path):
    client, TestingSession = _make_client(tmp_path)
    _login(client)

    resp = client.post(
        "/admin/simulados",
        data={"titulo": "Simulado Geral", "descricao": "desc", "score_min": "308.6", "score_max": "858.7"},
        follow_redirects=True,
    )
    assert "Simulado Geral" in resp.text

    session = TestingSession()
    from simulado_quimica_enem.models import Simulado

    simulado_id = session.query(Simulado).one().id
    session.close()

    resp = client.post(f"/admin/simulados/{simulado_id}/publicar", follow_redirects=True)
    assert "Despublicar" in resp.text

    form_data = {
        "enunciado": "Qual a formula da agua?",
        "gabarito": "A",
        "resolucao": "Agua e H2O.",
        "assunto": "Ligacoes quimicas",
        "alt_A_texto": "H2O", "alt_B_texto": "CO2", "alt_C_texto": "O2",
        "alt_D_texto": "NaCl", "alt_E_texto": "CH4",
    }
    files = {"enunciado_imagem": ("grafico.png", b"fake-png-bytes", "image/png")}
    resp = client.post(f"/admin/simulados/{simulado_id}/questoes", data=form_data, files=files, follow_redirects=True)

    assert "Qual a formula da agua?" in resp.text
    assert "Ligacoes quimicas" in resp.text

    session = TestingSession()
    questao = session.query(Simulado).one().questoes[0]
    assert questao.enunciado_imagem_url is not None
    assert (tmp_path / questao.enunciado_imagem_url).exists()
    session.close()


def test_leads_list_e_export_csv(tmp_path):
    client, TestingSession = _make_client(tmp_path)
    _login(client)

    resp = client.get("/admin/leads", follow_redirects=True)
    assert "Leads" in resp.text

    resp = client.get("/admin/leads/export.csv", follow_redirects=True)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
```

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `pytest tests/test_admin_router.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'simulado_quimica_enem.routers.admin'`

- [ ] **Step 4: Implementar**

```python
# src/simulado_quimica_enem/routers/admin.py
import csv
import io
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from simulado_quimica_enem.config import Settings, get_settings
from simulado_quimica_enem.db import get_db
from simulado_quimica_enem.models import Attempt, Questao, Simulado
from simulado_quimica_enem.services.auth import (
    create_admin_session_token,
    verify_admin_session_token,
    verify_credentials,
)
from simulado_quimica_enem.services.images import save_uploaded_image

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

ADMIN_COOKIE_NAME = "sqe_admin_session"
ALTERNATIVA_LETRAS = ["A", "B", "C", "D", "E"]


def require_admin(request: Request, settings: Settings = Depends(get_settings)) -> bool:
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    return bool(token) and verify_admin_session_token(settings, token)


@router.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse("admin/login.html", {"request": request, "erro": None})


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    settings: Settings = Depends(get_settings),
):
    if not verify_credentials(settings, username, password):
        return templates.TemplateResponse(
            "admin/login.html", {"request": request, "erro": "Usuário ou senha inválidos"}
        )

    response = RedirectResponse("/admin/simulados", status_code=303)
    response.set_cookie(
        ADMIN_COOKIE_NAME, create_admin_session_token(settings), httponly=True, max_age=8 * 60 * 60
    )
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(ADMIN_COOKIE_NAME)
    return response


@router.get("/simulados")
def listar_simulados(request: Request, db: Session = Depends(get_db), admin_ok: bool = Depends(require_admin)):
    if not admin_ok:
        return RedirectResponse("/admin/login", status_code=303)

    simulados = db.execute(select(Simulado)).scalars().all()
    return templates.TemplateResponse("admin/simulados_list.html", {"request": request, "simulados": simulados})


@router.post("/simulados")
def criar_simulado(
    titulo: str = Form(...),
    descricao: str = Form(""),
    score_min: float = Form(...),
    score_max: float = Form(...),
    db: Session = Depends(get_db),
    admin_ok: bool = Depends(require_admin),
):
    if not admin_ok:
        return RedirectResponse("/admin/login", status_code=303)

    simulado = Simulado(titulo=titulo, descricao=descricao, score_min=score_min, score_max=score_max)
    db.add(simulado)
    db.commit()
    return RedirectResponse("/admin/simulados", status_code=303)


@router.post("/simulados/{simulado_id}/publicar")
def alternar_publicacao(simulado_id: str, db: Session = Depends(get_db), admin_ok: bool = Depends(require_admin)):
    if not admin_ok:
        return RedirectResponse("/admin/login", status_code=303)

    simulado = db.get(Simulado, simulado_id)
    if simulado is not None:
        simulado.status = "rascunho" if simulado.status == "publicado" else "publicado"
        db.commit()
    return RedirectResponse("/admin/simulados", status_code=303)


@router.get("/simulados/{simulado_id}/questoes")
def listar_questoes(
    simulado_id: str, request: Request, db: Session = Depends(get_db), admin_ok: bool = Depends(require_admin)
):
    if not admin_ok:
        return RedirectResponse("/admin/login", status_code=303)

    simulado = db.get(Simulado, simulado_id)
    if simulado is None:
        return RedirectResponse("/admin/simulados", status_code=303)
    return templates.TemplateResponse("admin/questoes_list.html", {"request": request, "simulado": simulado})


@router.post("/simulados/{simulado_id}/questoes")
def criar_questao(
    simulado_id: str,
    enunciado: str = Form(...),
    gabarito: str = Form(...),
    resolucao: str = Form(...),
    assunto: str = Form(...),
    alt_A_texto: str = Form(...),
    alt_B_texto: str = Form(...),
    alt_C_texto: str = Form(...),
    alt_D_texto: str = Form(...),
    alt_E_texto: str = Form(...),
    enunciado_imagem: UploadFile | None = None,
    resolucao_imagem: UploadFile | None = None,
    alt_A_imagem: UploadFile | None = None,
    alt_B_imagem: UploadFile | None = None,
    alt_C_imagem: UploadFile | None = None,
    alt_D_imagem: UploadFile | None = None,
    alt_E_imagem: UploadFile | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    admin_ok: bool = Depends(require_admin),
):
    if not admin_ok:
        return RedirectResponse("/admin/login", status_code=303)

    simulado = db.get(Simulado, simulado_id)
    if simulado is None:
        return RedirectResponse("/admin/simulados", status_code=303)

    def _salvar_se_houver(arquivo: UploadFile | None) -> str | None:
        if arquivo is None or not arquivo.filename:
            return None
        return save_uploaded_image(settings.upload_dir, arquivo)

    textos = {"A": alt_A_texto, "B": alt_B_texto, "C": alt_C_texto, "D": alt_D_texto, "E": alt_E_texto}
    imagens = {
        "A": alt_A_imagem, "B": alt_B_imagem, "C": alt_C_imagem, "D": alt_D_imagem, "E": alt_E_imagem,
    }
    alternativas = [
        {"letra": letra, "texto": textos[letra], "imagem_url": _salvar_se_houver(imagens[letra])}
        for letra in ALTERNATIVA_LETRAS
    ]

    questao = Questao(
        simulado=simulado,
        ordem=len(simulado.questoes) + 1,
        enunciado=enunciado,
        enunciado_imagem_url=_salvar_se_houver(enunciado_imagem),
        alternativas=alternativas,
        gabarito=gabarito,
        resolucao=resolucao,
        resolucao_imagem_url=_salvar_se_houver(resolucao_imagem),
        assunto=assunto,
    )
    db.add(questao)
    db.commit()
    return RedirectResponse(f"/admin/simulados/{simulado_id}/questoes", status_code=303)


@router.get("/leads")
def listar_leads(request: Request, db: Session = Depends(get_db), admin_ok: bool = Depends(require_admin)):
    if not admin_ok:
        return RedirectResponse("/admin/login", status_code=303)

    attempts = db.execute(select(Attempt)).scalars().all()
    return templates.TemplateResponse("admin/leads_list.html", {"request": request, "attempts": attempts})


@router.get("/leads/export.csv")
def exportar_leads_csv(db: Session = Depends(get_db), admin_ok: bool = Depends(require_admin)):
    if not admin_ok:
        return RedirectResponse("/admin/login", status_code=303)

    attempts = db.execute(select(Attempt)).scalars().all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["nome", "email", "whatsapp", "simulado", "status", "score"])
    for attempt in attempts:
        writer.writerow(
            [
                attempt.lead.nome,
                attempt.lead.email,
                attempt.lead.whatsapp,
                attempt.simulado.titulo,
                attempt.status,
                attempt.score if attempt.score is not None else "",
            ]
        )

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `pytest tests/test_admin_router.py -v`
Expected: PASS (4 testes)

- [ ] **Step 6: Commit**

```bash
git add src/simulado_quimica_enem/routers/admin.py src/simulado_quimica_enem/templates/admin/ tests/test_admin_router.py
git commit -m "feat: painel admin (login, simulados, questoes, leads)"
```

---

### Task 13: Montagem do app (`main.py`)

**Files:**
- Create: `src/simulado_quimica_enem/main.py`
- Create: `src/simulado_quimica_enem/static/css/styles.css`
- Test: `tests/test_main_app.py`

**Interfaces:**
- Consumes: `public.router`, `admin.router` (Tasks 11-12), `get_settings` (Task 2).
- Produces: `app: FastAPI` — ponto de entrada ASGI (`uvicorn simulado_quimica_enem.main:app`). Monta `/static` (assets do pacote: css) e `/media` (uploads de imagem, apontando para `settings.upload_dir`, onde `save_uploaded_image` já grava em `<upload_dir>/uploads/...` — a URL final de uma imagem é `/media/uploads/<arquivo>`).

- [ ] **Step 1: Escrever `static/css/styles.css`**

```css
/* src/simulado_quimica_enem/static/css/styles.css */
body {
  font-family: system-ui, sans-serif;
  max-width: 720px;
  margin: 0 auto;
  padding: 1.5rem;
  line-height: 1.5;
}

form label {
  display: block;
  margin-bottom: 0.75rem;
}

.oferta {
  margin-top: 2rem;
  padding: 1rem;
  border: 1px solid #ccc;
  border-radius: 8px;
}
```

- [ ] **Step 2: Escrever o teste**

```python
# tests/test_main_app.py
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from simulado_quimica_enem.config import get_settings
from simulado_quimica_enem.db import get_db
from simulado_quimica_enem.models import Base


def test_app_monta_rotas_publicas_admin_estaticos_e_cria_upload_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "senha")
    monkeypatch.setenv("SESSION_SECRET", "segredo")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()

    from simulado_quimica_enem.main import app

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(app) as client:
            assert client.get("/simulados").status_code == 200
            assert client.get("/admin/login").status_code == 200
            css = client.get("/static/css/styles.css")
            assert css.status_code == 200
            assert css.headers["content-type"].startswith("text/css")

        assert (tmp_path / "uploads").exists()
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_media_serve_arquivo_valido_e_bloqueia_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "senha")
    monkeypatch.setenv("SESSION_SECRET", "segredo")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()

    from simulado_quimica_enem.main import app

    (tmp_path / "uploads").mkdir(parents=True, exist_ok=True)
    (tmp_path / "uploads" / "foto.png").write_bytes(b"fake-png")
    segredo_fora = tmp_path.parent / "segredo.txt"
    segredo_fora.write_text("nao deveria ser servido")

    with TestClient(app) as client:
        ok = client.get("/media/uploads/foto.png")
        assert ok.status_code == 200

        traversal = client.get("/media/../segredo.txt")
        assert traversal.status_code == 404

    get_settings.cache_clear()
```

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `pytest tests/test_main_app.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'simulado_quimica_enem.main'`

- [ ] **Step 4: Implementar**

```python
# src/simulado_quimica_enem/main.py
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from simulado_quimica_enem.config import get_settings
from simulado_quimica_enem.routers import admin, public

PACKAGE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    Path(settings.upload_dir, "uploads").mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Simulado Química ENEM", lifespan=lifespan)
app.include_router(public.router)
app.include_router(admin.router)
app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")


@app.get("/media/{path:path}")
def servir_media(path: str):
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    settings = get_settings()
    base_dir = Path(settings.upload_dir).resolve()
    arquivo = (base_dir / path).resolve()
    if base_dir not in arquivo.parents or not arquivo.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(arquivo)
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `pytest tests/test_main_app.py -v`
Expected: PASS

- [ ] **Step 6: Rodar a suíte inteira**

Run: `pytest -v`
Expected: todos os testes de todas as tasks anteriores continuam passando.

- [ ] **Step 7: Commit**

```bash
git add src/simulado_quimica_enem/main.py src/simulado_quimica_enem/static/ tests/test_main_app.py
git commit -m "feat: monta app FastAPI (rotas, estaticos, media, upload dir)"
```

---

### Task 14: Docker Compose, deploy e publicação no GitHub

**Files:**
- Create: `docker/Dockerfile`
- Create: `docker-compose.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: `app` (Task 13), `alembic.ini`/`migrations/` (Task 4), `.env.example` (Task 1).
- Produces: `docker compose up` sobe Postgres + app FastAPI já migrado, servindo em `http://localhost:8000`.

**Atenção (ação sensível):** os últimos passos (criar o repositório no GitHub e dar `git push`)
publicam código fora da sua máquina. Confirme com o usuário antes de rodar `gh repo create` e
`git push` — não são passos para automatizar sem essa confirmação.

- [ ] **Step 1: Criar `docker/Dockerfile`**

```dockerfile
# docker/Dockerfile
FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn simulado_quimica_enem.main:app --host 0.0.0.0 --port 8000"]
```

- [ ] **Step 2: Criar `docker-compose.yml`**

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: simulado
      POSTGRES_PASSWORD: simulado
      POSTGRES_DB: simulado_quimica_enem
    volumes:
      - db_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  app:
    build:
      context: .
      dockerfile: docker/Dockerfile
    env_file: .env
    environment:
      DATABASE_URL: postgresql+psycopg://simulado:simulado@db:5432/simulado_quimica_enem
    depends_on:
      - db
    volumes:
      - uploads_data:/app/var/uploads
    ports:
      - "8000:8000"

volumes:
  db_data:
  uploads_data:
```

- [ ] **Step 3: Atualizar `README.md` com instruções de deploy**

Adicionar ao final do `README.md` (criado na Task 1):

```markdown
## Deploy (Hostinger VPS via Docker Compose)

\`\`\`bash
cp .env.example .env   # editar com credenciais reais (admin, Z-API, segredo de sessao)
docker compose up -d --build
\`\`\`

A aplicação sobe em \`http://<ip-do-vps>:8000\`. \`alembic upgrade head\` roda automaticamente
no start do container \`app\` antes do \`uvicorn\`.
```

- [ ] **Step 4: Verificar o build localmente**

```bash
cd /Users/marcoviana/simulado-quimica-enem
cp .env.example .env
docker compose build
docker compose up -d
curl -sf http://localhost:8000/simulados
docker compose down
```

Expected: build sem erro; `curl` devolve HTML 200 da página de catálogo (vazia, sem simulados
ainda, mas sem erro de servidor).

- [ ] **Step 5: Commit**

```bash
git add docker/ docker-compose.yml README.md
git commit -m "feat: docker compose para deploy (app + postgres)"
```

- [ ] **Step 6: Criar o repositório no GitHub e publicar (confirmar com o usuário antes)**

```bash
gh repo create simulado-quimica-enem --private --source=. --remote=origin
git push -u origin main
```

---

## Self-review

- **Cobertura do spec:** modelo de dados (§3 do spec → Task 3), fluxo do aluno com
  persistência por cookie (§4 → Tasks 7 e 11), pontuação e feedback (§5 → Task 5), admin e
  cadastro de conteúdo com imagens (§6 → Tasks 8 e 12), integração Z-API (§7 → Tasks 10 e 11),
  testes (§8 → cada task tem seu próprio TDD), deploy (§9 → Task 14). Nenhuma seção do spec
  ficou sem task correspondente.
- **Placeholders:** nenhum "TBD"/"implementar depois" — a única simplificação deliberada e
  documentada é editar/reordenar questões no admin, adiada para depois do v1 (Task 12).
- **Consistência de tipos:** `calcular_score`, `gerar_feedback_por_assunto`, `FeedbackAssunto`
  (Task 5) são usados com a mesma assinatura em Task 7 (`build_feedback`) e Task 11
  (`ver_resultado`, `finalizar_simulado`). `get_attempt_by_token`/`start_attempt`/
  `record_answer`/`is_attempt_complete`/`finalize_attempt` (Task 7) são usados com os mesmos
  nomes em Task 11. `save_uploaded_image` (Task 8) é usado com a mesma assinatura em Task 12.
  `verify_credentials`/`create_admin_session_token`/`verify_admin_session_token` (Task 9) são
  usados com os mesmos nomes em Task 12.
