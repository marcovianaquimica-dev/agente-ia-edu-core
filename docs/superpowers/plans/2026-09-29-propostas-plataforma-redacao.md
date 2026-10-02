# Propostas de Redação da Plataforma — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que o `PLATFORM_ADMIN` cadastre propostas de redação visíveis a professores de qualquer escola, materializadas como uma cópia real por escola na primeira atribuição a uma turma.

**Architecture:** Uma tabela nova `platform_essay_prompts` (global, sem `school_id`) guarda o tema+enunciado cadastrado pelo admin. `essay_prompts` (já escopada por escola, com a `UniqueConstraint("school_id","id")` que todo o resto do sistema referencia por FK composta) ganha só uma coluna de proveniência `materialized_from_platform_prompt_id`. Quando um professor atribui uma proposta da plataforma a uma turma, o backend busca-ou-cria a cópia da escola dele em `essay_prompts` e segue o fluxo de `PromptAssignment` exatamente como hoje — nenhuma FK composta existente muda.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy 2.x async / Alembic / Postgres (produção) + SQLite in-memory (testes de serviço) / frontend em JS puro (sem framework) testado com `node --test`.

**Spec:** `docs/superpowers/specs/2026-09-29-propostas-plataforma-redacao-design.md`

---

## Ambiente de execução (leia antes de rodar qualquer coisa)

Repo: `/Users/marcoviana/agente-ia-edu-core/.claude/worktrees/integrate-redacao-round2` (worktree git, branch `integrate-redacao-round2`). Rode todos os comandos de dentro desse diretório.

> **NUNCA use `/Users/marcoviana/agente-ia-edu-core/.venv` (checkout principal)** — o `.venv` deste worktree resolve pacotes Python diferente via pytest (`pyproject.toml` tem `pythonpath=["src","."]`), mas um script avulso fora do pytest pegaria código errado do checkout principal.

Na prática, em toda tarefa deste plano:

```bash
cd /Users/marcoviana/agente-ia-edu-core/.claude/worktrees/integrate-redacao-round2
.venv/bin/pytest tests/<arquivo> -v          # testes Python
node --test tests/<arquivo>.js               # testes de frontend
```

Nunca invoque `python tests/...` direto nem `/Users/marcoviana/agente-ia-edu-core/.venv/bin/...`.

---

## Global Constraints

Estas restrições valem para **todas** as tarefas (copiadas da §6 da spec, mais os invariantes já estabelecidos no código que as tarefas tocam):

- Nunca afrouxar a FK composta `(school_id, id)` de `essay_prompts` (`uq_essay_prompts_school_id_id`) nem as FKs compostas que dependem dela (`PromptAssignment`, `EssayBatchUpload`, `EssayBatchPage`) — a materialização existe exatamente pra evitar isso.
- `platform_essay_prompts` nunca é referenciada diretamente por `PromptAssignment`, `EssaySubmission`, `EssayBatchUpload` ou qualquer tabela escopada a uma escola — só por `essay_prompts.materialized_from_platform_prompt_id`, e só como proveniência/auditoria.
- Rotas de admin (`/api/v1/admin/platform-essay-prompts*`) exigem `PLATFORM_ADMIN`; nenhuma outra role tem acesso de escrita.
- A migration é **puramente aditiva**: uma tabela nova + uma coluna nullable. Nenhuma linha existente é tocada, nenhuma coluna existente muda de tipo ou de nullability.
- `school_id` sempre vem do contexto resolvido (`_authorize`), nunca do corpo da requisição — regra que `catalog.py`/`essay_prompts.py` já estabeleceram.
- Imutabilidade: nem a `PlatformEssayPrompt` (admin arquiva, nunca edita) nem a cópia materializada (professor nunca edita nem exclui) são alteradas depois de criadas — mesma filosofia "nunca edita, sempre substitui" já documentada em `db/models/essay_proposal.py:43-46`.
- **MissingGreenlet:** em toda rota, monte a resposta Pydantic (ou capture o `.id` num local) **antes** de `await session.commit()`. `expire_on_commit=True` em produção transforma qualquer leitura de atributo pós-commit num lazy-load síncrono que estoura em contexto async. As fixtures de teste usam `expire_on_commit=False` e mascaram isso.
- TDD obrigatório: o teste é escrito e roda **falhando** antes de qualquer linha de implementação.
- `MaterialStorage` não é usado em lugar nenhum desta leva — propostas da plataforma são só texto, sem upload de arquivo.

### Decisões de implementação tomadas onde a spec não desce ao detalhe

Estas três decisões foram tomadas ao escrever o plano, lendo o código real; estão aqui pra não serem reabertas tarefa a tarefa:

1. **`year` da cópia materializada.** `EssayPrompt.year` é `NOT NULL` e `platform_essay_prompts` não tem ano (spec §3). A cópia nasce com o ano UTC corrente, via um helper único (`materialization_year()`) que a listagem do professor também usa — assim o ano que o professor vê na lista é exatamente o ano que a cópia vai receber.
2. **A cópia materializada continua com `is_platform=true`.** A spec só exige que ela apareça uma vez na lista (nunca duplicada com a origem). Manter o selo depois da materialização é o que faz valer a decisão 3 do brainstorm ("professor nunca edita nem exclui uma proposta da plataforma") de forma permanente, e não só até a primeira atribuição.
3. **A rota de detalhe (`GET /catalog/essay-prompts/{id}`) também precisa ser ciente da plataforma.** Sem isso o fluxo não fecha: a tela do professor lista → clica "Abrir" → **só então** atribui a turmas. Com o id de uma proposta da plataforma ainda não materializada, o detalhe hoje devolveria 403 e o professor nunca chegaria ao formulário de atribuição. A rota passa a devolver uma pré-visualização somente-leitura (sem materiais, sem turmas) nesse caso, e o detalhe real da cópia quando a escola já materializou.

---

## Estrutura de arquivos

**Criados:**

| Arquivo | Responsabilidade |
|---|---|
| `src/agente_ia_edu/db/models/platform_essay_prompt.py` | Modelo ORM `PlatformEssayPrompt` (uma tabela, sem `school_id`) |
| `migrations/versions/058_platform_essay_prompts.py` | Tabela nova + coluna de proveniência + constraint única |
| `src/agente_ia_edu/services/platform_essay_prompt.py` | `PlatformEssayPromptService` (CRUD do admin + materialização) e `materialization_year()` |
| `src/agente_ia_edu/api/routes/admin_essay_prompts.py` | Três rotas `PLATFORM_ADMIN` (criar / listar / arquivar) |

**Modificados:**

| Arquivo | O que muda |
|---|---|
| `src/agente_ia_edu/db/models/essay_proposal.py` | `EssayPrompt` ganha `materialized_from_platform_prompt_id` + `UniqueConstraint` |
| `src/agente_ia_edu/db/models/__init__.py` | Importa e exporta `PlatformEssayPrompt` |
| `src/agente_ia_edu/api/app.py` | Registra `admin_essay_prompts_router` |
| `src/agente_ia_edu/api/routes/essay_prompts.py` | Listagem + detalhe cientes da plataforma; atribuição (individual e em lote) materializa |
| `src/agente_ia_edu/web/admin.html` / `admin.js` | Seção nova "Propostas de Redação da Plataforma" |
| `src/agente_ia_edu/web/essay-review.js` / `teacher.css` | Selo "Plataforma" na lista + esconder controles de edição |

**Migration mais recente confirmada no worktree (`ls migrations/versions/ | sort`): `057_essay_batch_upload`.** A migration desta leva (`058_platform_essay_prompts`) encadeia depois dela. Se ao executar a Tarefa 2 já existir uma `058_*` de outra sessão, renumere a desta leva para o próximo número livre e ajuste `down_revision` para a cabeça real da cadeia.

---

## Task 1: Modelo `PlatformEssayPrompt` + coluna de proveniência em `EssayPrompt`

> Ambiente: rode tudo de dentro do worktree, com o `.venv` **deste** worktree (ver "Ambiente de execução" no topo).

**Files:**
- Create: `src/agente_ia_edu/db/models/platform_essay_prompt.py`
- Modify: `src/agente_ia_edu/db/models/essay_proposal.py:49-84` (bloco `__table_args__` e as colunas de `EssayPrompt`)
- Modify: `src/agente_ia_edu/db/models/__init__.py:128-137` (imports) e o bloco `__all__` por volta da linha 226
- Test: `tests/test_r5_platform_essay_prompt_models.py`

**Interfaces:**
- Consumes: nada (primeira tarefa).
- Produces:
  - `agente_ia_edu.db.models.PlatformEssayPrompt` com os campos `id: uuid.UUID`, `title: str`, `statement: str`, `status: str` (default `"ACTIVE"`), `created_by_external_identity: str`, `created_at: datetime`.
  - `EssayPrompt.materialized_from_platform_prompt_id: uuid.UUID | None` (default `None`).
  - Constraints nomeadas: `ck_platform_essay_prompts_status`, `uq_essay_prompts_school_materialized_from`.

- [ ] **Step 1: Escrever o teste falhando**

Crie `tests/test_r5_platform_essay_prompt_models.py`:

```python
"""Modelo novo (platform_essay_prompts) + coluna de proveniencia em
essay_prompts.

SQLite in-memory, mesmo padrao de tests/test_r2_essay_proposal_service.py.
A unicidade de (school_id, materialized_from_platform_prompt_id) e testada
aqui no nivel de metadata; a validacao contra o banco real (Postgres) vem na
Task 2, porque so o upgrade de verdade prova que a constraint existe na
tabela ja existente.
"""

import unittest
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import EssayPrompt, PlatformEssayPrompt, School


class PlatformEssayPromptModelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _school(self, session, code):
        school = School(id=uuid.uuid4(), code=f"PEP-{code}", name=f"school-{code}")
        session.add(school)
        await session.flush()
        return school

    async def _origin(self, session, title="Tema da plataforma"):
        origin = PlatformEssayPrompt(
            id=uuid.uuid4(),
            title=title,
            statement="Disserte sobre o tema.",
            created_by_external_identity="user:ADMIN",
        )
        session.add(origin)
        await session.flush()
        return origin

    def _copy(self, *, school_id, origin_id, title="Tema da plataforma"):
        return EssayPrompt(
            id=uuid.uuid4(),
            school_id=school_id,
            title=title,
            statement="Disserte sobre o tema.",
            year=2026,
            status="ACTIVE",
            created_by_external_identity="teacher:p1",
            materialized_from_platform_prompt_id=origin_id,
        )

    async def test_platform_prompt_is_born_active_with_a_created_at(self):
        async with self.session_factory() as session:
            origin = await self._origin(session)
            self.assertEqual(origin.status, "ACTIVE")
            self.assertIsNotNone(origin.created_at)
            self.assertEqual(origin.created_by_external_identity, "user:ADMIN")

    async def test_a_normal_teacher_prompt_has_no_platform_origin(self):
        async with self.session_factory() as session:
            school = await self._school(session, "1")
            prompt = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="Proposta do professor",
                statement="Disserte.", year=2026, status="DRAFT",
                created_by_external_identity="teacher:p1",
            )
            session.add(prompt)
            await session.flush()
            self.assertIsNone(prompt.materialized_from_platform_prompt_id)

    async def test_the_same_school_cannot_materialize_the_same_origin_twice(self):
        async with self.session_factory() as session:
            school = await self._school(session, "2")
            origin = await self._origin(session)
            session.add(self._copy(school_id=school.id, origin_id=origin.id))
            await session.flush()
            session.add(self._copy(school_id=school.id, origin_id=origin.id))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_two_schools_materialize_the_same_origin_independently(self):
        async with self.session_factory() as session:
            school_a = await self._school(session, "3A")
            school_b = await self._school(session, "3B")
            origin = await self._origin(session)
            copy_a = self._copy(school_id=school_a.id, origin_id=origin.id)
            copy_b = self._copy(school_id=school_b.id, origin_id=origin.id)
            session.add_all([copy_a, copy_b])
            await session.flush()
            self.assertNotEqual(copy_a.id, copy_b.id)
            self.assertEqual(
                copy_a.materialized_from_platform_prompt_id,
                copy_b.materialized_from_platform_prompt_id,
            )

    async def test_two_normal_prompts_in_the_same_school_never_collide(self):
        # NULL nunca e igual a NULL para fins de unicidade - e por isso que a
        # UNIQUE comum basta, sem indice parcial (spec s2).
        async with self.session_factory() as session:
            school = await self._school(session, "4")
            for index in range(3):
                session.add(EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title=f"Proposta {index}",
                    statement="Disserte.", year=2026, status="DRAFT",
                    created_by_external_identity="teacher:p1",
                ))
            await session.flush()  # nao deve levantar nada


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `.venv/bin/pytest tests/test_r5_platform_essay_prompt_models.py -v`
Expected: FAIL já no import — `ImportError: cannot import name 'PlatformEssayPrompt' from 'agente_ia_edu.db.models'`.

- [ ] **Step 3: Criar o modelo novo**

Crie `src/agente_ia_edu/db/models/platform_essay_prompt.py`:

```python
"""Proposta de redacao "da plataforma": tema+enunciado cadastrado pelo
PLATFORM_ADMIN e visivel a professores de QUALQUER escola.

Uma tabela deliberadamente global - nao tem school_id, nao tem
UniqueConstraint("school_id","id") e NUNCA e referenciada por
PromptAssignment/EssaySubmission/EssayBatchUpload nem por qualquer outra
tabela escopada a uma escola. O unico ponteiro para ela vem de
essay_prompts.materialized_from_platform_prompt_id, e so como proveniencia:
quando um professor atribui uma proposta dessas a uma turma, o backend cria
uma copia real e escopada a escola dele em essay_prompts, e dali pra frente
tudo segue exatamente como uma proposta normal (spec 2026-09-29 s4).

Imutavel depois de criada, mesma filosofia de EssayPrompt (ver o docstring
dela em essay_proposal.py): o admin arquiva e cadastra outra, nunca edita.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PlatformEssayPrompt(Base):
    __tablename__ = "platform_essay_prompts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'ARCHIVED')", name="ck_platform_essay_prompts_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    # ARCHIVED so impede que escolas NOVAS passem a ver a proposta na lista
    # de disponiveis - nenhuma copia ja materializada e afetada.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_by_external_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


__all__ = ["PlatformEssayPrompt"]
```

- [ ] **Step 4: Adicionar a coluna e a constraint em `EssayPrompt`**

Em `src/agente_ia_edu/db/models/essay_proposal.py`, no `__table_args__` de `EssayPrompt` (hoje linhas 49-55), acrescente a `UniqueConstraint` nova logo depois da que já existe. Substitua:

```python
    __table_args__ = (
        UniqueConstraint("school_id", "id", name="uq_essay_prompts_school_id_id"),
        CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'SUPERSEDED')", name="ck_essay_prompts_status"
        ),
        Index("ix_essay_prompts_school_id", "school_id"),
    )
```

por:

```python
    __table_args__ = (
        UniqueConstraint("school_id", "id", name="uq_essay_prompts_school_id_id"),
        # Uma escola materializa a mesma proposta da plataforma no maximo uma
        # vez. UNIQUE comum basta: as propostas normais tem NULL nessa coluna
        # e NULL nunca e igual a NULL para fins de unicidade, entao elas nunca
        # colidem entre si (spec 2026-09-29 s2).
        UniqueConstraint(
            "school_id",
            "materialized_from_platform_prompt_id",
            name="uq_essay_prompts_school_materialized_from",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'SUPERSEDED')", name="ck_essay_prompts_status"
        ),
        Index("ix_essay_prompts_school_id", "school_id"),
    )
```

E, no fim das colunas de `EssayPrompt` (hoje logo depois de `deleted_at`, linha 83, antes de `materials: Mapped[list["PromptMaterial"]] = relationship(...)`), acrescente:

```python
    # Proveniencia: preenchida SO quando esta linha e a copia por escola de
    # uma proposta da plataforma (platform_essay_prompts), criada pelo
    # backend na primeira vez que um professor desta escola a atribuiu a uma
    # turma. NULL = proposta criada normalmente por um professor, o caso de
    # hoje, sem nenhuma mudanca de comportamento. RESTRICT: uma proposta da
    # plataforma nunca pode ser apagada enquanto alguma escola tiver copia
    # dela (defensivo - o admin arquiva, nunca apaga).
    materialized_from_platform_prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("platform_essay_prompts.id", ondelete="RESTRICT"), nullable=True
    )
```

(`ForeignKey` e `Uuid` já estão importados no topo do arquivo — nada a acrescentar nos imports.)

- [ ] **Step 5: Exportar o modelo novo**

Em `src/agente_ia_edu/db/models/__init__.py`, **antes** do bloco `from .essay_proposal import (...)` (hoje na linha 129), acrescente:

```python
from .platform_essay_prompt import PlatformEssayPrompt
```

O import vem antes porque `essay_proposal.py` agora tem uma `ForeignKey("platform_essay_prompts.id")` — a resolução é por nome de tabela no `MetaData`, então basta que os dois módulos estejam importados antes de qualquer `create_all`, mas manter a ordem explícita evita depender disso.

E, no bloco `__all__`, logo depois de `"EssayBatchPage",`:

```python
    "PlatformEssayPrompt",
```

- [ ] **Step 6: Rodar os testes e confirmar que passam**

Run: `.venv/bin/pytest tests/test_r5_platform_essay_prompt_models.py -v`
Expected: PASS (5 testes).

- [ ] **Step 7: Rodar a suíte de redação que já existe, pra provar que nada quebrou**

Run: `.venv/bin/pytest tests/test_r2_essay_proposal_service.py tests/test_r2_essay_prompts_routes.py tests/test_r4_essay_batch_models.py -v`
Expected: PASS, sem erro de mapeamento nem de `create_all`.

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/db/models/platform_essay_prompt.py \
        src/agente_ia_edu/db/models/essay_proposal.py \
        src/agente_ia_edu/db/models/__init__.py \
        tests/test_r5_platform_essay_prompt_models.py
git commit -m "feat(redacao): modelo PlatformEssayPrompt + proveniencia em EssayPrompt

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Migration `058_platform_essay_prompts` validada em Postgres real

> Ambiente: rode tudo de dentro do worktree, com o `.venv` **deste** worktree (ver "Ambiente de execução" no topo).

**Files:**
- Create: `migrations/versions/058_platform_essay_prompts.py`
- Test: `tests/test_r5_platform_essay_prompts_migration_postgresql.py`

**Interfaces:**
- Consumes: modelo e constraints da Task 1 (`platform_essay_prompts`, `essay_prompts.materialized_from_platform_prompt_id`, `uq_essay_prompts_school_materialized_from`).
- Produces: `revision = "058_platform_essay_prompts"` com `down_revision = "057_essay_batch_upload"` — a nova cabeça da cadeia de migrations.

- [ ] **Step 0: Confirmar a cabeça real da cadeia**

Run: `ls migrations/versions/ | sort | tail -5`
Expected: a última é `057_essay_batch_upload.py`. Se já existir uma `058_*` de outra sessão, use o próximo número livre no nome do arquivo, no `revision` e nos testes abaixo, e aponte `down_revision` para a cabeça real.

- [ ] **Step 1: Escrever o teste falhando**

Crie `tests/test_r5_platform_essay_prompts_migration_postgresql.py`:

```python
"""Validacao em PostgreSQL da migration 058 (propostas da plataforma).

Ler a migration nao prova nada: a coluna nova numa tabela ja existente
(essay_prompts.materialized_from_platform_prompt_id) e, principalmente, a
UNIQUE (school_id, materialized_from_platform_prompt_id) - de que o
buscar-ou-criar idempotente depende - so aparecem rodando o upgrade de
verdade. Banco descartavel na porta 5433, mesmo alvo dos outros testes de
migration.
"""

from __future__ import annotations

import os
import unittest
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests._postgres_test_db import create_database, drop_database


class TestPlatformEssayPromptsMigrationPostgreSQL(unittest.TestCase):
    database_name = "agente_ia_edu_platform_essay_prompts_test"
    admin_url = os.getenv(
        "PLATFORM_ESSAY_PROMPTS_TEST_ADMIN_DATABASE_URL",
        "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/postgres",
    )
    database_url = os.getenv(
        "PLATFORM_ESSAY_PROMPTS_TEST_DATABASE_URL",
        f"postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            engine = create_engine(
                cls.admin_url,
                connect_args={"autocommit": True},
                execution_options={"isolation_level": "AUTOCOMMIT"},
            )
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            engine.dispose()
        except Exception as exc:
            raise unittest.SkipTest(
                "PostgreSQL de teste indisponivel; migration 058 nao validada."
            ) from exc

    def setUp(self):
        drop_database(self.admin_url, self.database_name)
        create_database(self.admin_url, self.database_name)

    def tearDown(self):
        drop_database(self.admin_url, self.database_name)

    def _alembic_config(self) -> Config:
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", self.database_url)
        return config

    def _seed_school_and_origin(self, connection):
        school_id = uuid.uuid4()
        origin_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO schools (id, code, name, status, created_at, updated_at) "
                "VALUES (:id, :code, :name, 'ACTIVE', now(), now())"
            ),
            {"id": school_id, "code": f"PEPM-{str(school_id)[:8]}", "name": "Escola de teste"},
        )
        connection.execute(
            text(
                "INSERT INTO platform_essay_prompts "
                "(id, title, statement, status, created_by_external_identity, created_at) "
                "VALUES (:id, 'Tema', 'Disserte.', 'ACTIVE', 'user:ADMIN', now())"
            ),
            {"id": origin_id},
        )
        return school_id, origin_id

    @staticmethod
    def _insert_prompt_sql() -> str:
        return (
            "INSERT INTO essay_prompts "
            "(id, school_id, title, statement, year, status, is_free_theme, "
            " created_by_external_identity, created_at, updated_at, "
            " materialized_from_platform_prompt_id) "
            "VALUES (:id, :school_id, 'Tema', 'Disserte.', 2026, 'ACTIVE', false, "
            "        'teacher:p1', now(), now(), :origin_id)"
        )

    def test_upgrade_058_creates_table_column_and_unique_constraint(self):
        command.upgrade(self._alembic_config(), "058_platform_essay_prompts")

        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            self.assertIn("platform_essay_prompts", set(inspector.get_table_names()))

            prompt_columns = {c["name"] for c in inspector.get_columns("essay_prompts")}
            self.assertIn("materialized_from_platform_prompt_id", prompt_columns)

            status_checks = {
                item["name"]: item["sqltext"]
                for item in inspector.get_check_constraints("platform_essay_prompts")
            }
            self.assertIn("ACTIVE", status_checks["ck_platform_essay_prompts_status"])
            self.assertIn("ARCHIVED", status_checks["ck_platform_essay_prompts_status"])

            uniques = {
                item["name"]: tuple(item["column_names"])
                for item in inspector.get_unique_constraints("essay_prompts")
            }
            self.assertEqual(
                uniques["uq_essay_prompts_school_materialized_from"],
                ("school_id", "materialized_from_platform_prompt_id"),
            )
            # A trava composta que todo o resto do sistema referencia continua
            # existindo exatamente como estava (Global Constraints).
            self.assertEqual(uniques["uq_essay_prompts_school_id_id"], ("school_id", "id"))

            fks = {
                tuple(fk["constrained_columns"]): fk
                for fk in inspector.get_foreign_keys("essay_prompts")
            }
            origin_fk = fks[("materialized_from_platform_prompt_id",)]
            self.assertEqual(origin_fk["referred_table"], "platform_essay_prompts")
            self.assertEqual(origin_fk["options"]["ondelete"], "RESTRICT")
        finally:
            engine.dispose()

    def test_unique_constraint_rejects_a_second_materialization_of_the_same_origin(self):
        command.upgrade(self._alembic_config(), "058_platform_essay_prompts")

        engine = create_engine(self.database_url)
        try:
            with engine.begin() as connection:
                school_id, origin_id = self._seed_school_and_origin(connection)
                connection.execute(
                    text(self._insert_prompt_sql()),
                    {"id": uuid.uuid4(), "school_id": school_id, "origin_id": origin_id},
                )
            with engine.begin() as connection:
                with self.assertRaises(IntegrityError):
                    connection.execute(
                        text(self._insert_prompt_sql()),
                        {"id": uuid.uuid4(), "school_id": school_id, "origin_id": origin_id},
                    )
        finally:
            engine.dispose()

    def test_unique_constraint_never_blocks_normal_teacher_prompts(self):
        command.upgrade(self._alembic_config(), "058_platform_essay_prompts")

        engine = create_engine(self.database_url)
        try:
            with engine.begin() as connection:
                school_id, _ = self._seed_school_and_origin(connection)
                for _ in range(3):
                    connection.execute(
                        text(self._insert_prompt_sql()),
                        {"id": uuid.uuid4(), "school_id": school_id, "origin_id": None},
                    )
                total = connection.execute(
                    text(
                        "SELECT count(*) FROM essay_prompts "
                        "WHERE school_id = :school_id "
                        "AND materialized_from_platform_prompt_id IS NULL"
                    ),
                    {"school_id": school_id},
                ).scalar_one()
            self.assertEqual(total, 3)
        finally:
            engine.dispose()

    def test_downgrade_058_is_clean(self):
        config = self._alembic_config()
        command.upgrade(config, "058_platform_essay_prompts")
        command.downgrade(config, "057_essay_batch_upload")

        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            self.assertNotIn("platform_essay_prompts", set(inspector.get_table_names()))
            prompt_columns = {c["name"] for c in inspector.get_columns("essay_prompts")}
            self.assertNotIn("materialized_from_platform_prompt_id", prompt_columns)
            uniques = {item["name"] for item in inspector.get_unique_constraints("essay_prompts")}
            self.assertNotIn("uq_essay_prompts_school_materialized_from", uniques)
            self.assertIn("uq_essay_prompts_school_id_id", uniques)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `.venv/bin/pytest tests/test_r5_platform_essay_prompts_migration_postgresql.py -v`
Expected: FAIL com `KeyError: '058_platform_essay_prompts'` / `Can't locate revision identified by '058_platform_essay_prompts'`.
Se aparecer `SKIPPED ("PostgreSQL de teste indisponivel...")`, **suba o Postgres de teste na porta 5433 antes de seguir** — esta tarefa não pode ser dada como pronta em cima de um skip.

- [ ] **Step 3: Escrever a migration**

Crie `migrations/versions/058_platform_essay_prompts.py`:

```python
"""Propostas de redacao da plataforma (admin, cross-escola).

Revision ID: 058_platform_essay_prompts
Revises: 057_essay_batch_upload

Puramente aditiva: uma tabela que nao existia e uma coluna NULLABLE em
essay_prompts (default NULL = toda proposta ja cadastrada continua sendo uma
proposta normal de professor, sem nenhuma mudanca de comportamento). Nenhuma
linha existente e tocada.

A UNIQUE (school_id, materialized_from_platform_prompt_id) e o que impede no
banco duas materializacoes da mesma proposta da plataforma na mesma escola -
inclusive numa corrida de duas requisicoes simultaneas, que e exatamente o
caso que o buscar-ou-criar do servico trata capturando IntegrityError. UNIQUE
comum basta, sem indice parcial: linhas com NULL nessa coluna (as propostas
normais) nunca conflitam entre si, porque NULL nunca e igual a NULL para fins
de unicidade.

A FK ON DELETE RESTRICT e defensiva: o admin arquiva, nunca apaga, entao ela
nao deve ser exercitada no fluxo normal.
"""

from alembic import op
import sqlalchemy as sa

revision = "058_platform_essay_prompts"
down_revision = "057_essay_batch_upload"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_essay_prompts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by_external_identity", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'ARCHIVED')", name="ck_platform_essay_prompts_status"
        ),
    )

    op.add_column(
        "essay_prompts",
        sa.Column("materialized_from_platform_prompt_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_essay_prompts_materialized_from_platform_prompt",
        "essay_prompts",
        "platform_essay_prompts",
        ["materialized_from_platform_prompt_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_essay_prompts_school_materialized_from",
        "essay_prompts",
        ["school_id", "materialized_from_platform_prompt_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_essay_prompts_school_materialized_from", "essay_prompts", type_="unique"
    )
    op.drop_constraint(
        "fk_essay_prompts_materialized_from_platform_prompt",
        "essay_prompts",
        type_="foreignkey",
    )
    op.drop_column("essay_prompts", "materialized_from_platform_prompt_id")
    op.drop_table("platform_essay_prompts")
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `.venv/bin/pytest tests/test_r5_platform_essay_prompts_migration_postgresql.py -v`
Expected: PASS (4 testes), nenhum SKIPPED.

- [ ] **Step 5: Confirmar que a cadeia de migrations tem uma cabeça só**

Run: `.venv/bin/alembic heads`
Expected: uma única linha, `058_platform_essay_prompts (head)`.

- [ ] **Step 6: Commit**

```bash
git add migrations/versions/058_platform_essay_prompts.py \
        tests/test_r5_platform_essay_prompts_migration_postgresql.py
git commit -m "feat(redacao): migration 058 - platform_essay_prompts + proveniencia

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `PlatformEssayPromptService` — criar, listar com contagem, arquivar

> Ambiente: rode tudo de dentro do worktree, com o `.venv` **deste** worktree (ver "Ambiente de execução" no topo).

**Files:**
- Create: `src/agente_ia_edu/services/platform_essay_prompt.py`
- Test: `tests/test_r5_platform_essay_prompt_service.py`

**Interfaces:**
- Consumes: `PlatformEssayPrompt` e `EssayPrompt.materialized_from_platform_prompt_id` (Task 1).
- Produces (usados pelas Tasks 4, 5, 6 e 7):
  - `materialization_year() -> int`
  - `PlatformEssayPromptService(session: AsyncSession)`
  - `async create_prompt(*, title: str, statement: str, created_by_external_identity: str) -> PlatformEssayPrompt`
  - `async list_with_materialization_counts() -> list[tuple[PlatformEssayPrompt, int]]`
  - `async archive_prompt(*, platform_essay_prompt_id: uuid.UUID) -> PlatformEssayPrompt` (levanta `ValueError` se não existir)

- [ ] **Step 1: Escrever o teste falhando**

Crie `tests/test_r5_platform_essay_prompt_service.py`:

```python
"""PlatformEssayPromptService - lado do admin (criar / listar / arquivar).

SQLite in-memory, mesmo padrao de tests/test_r2_essay_proposal_service.py.
"""

import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import EssayPrompt, School
from agente_ia_edu.services.platform_essay_prompt import (
    PlatformEssayPromptService,
    materialization_year,
)


class PlatformEssayPromptServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _school(self, session, code):
        school = School(id=uuid.uuid4(), code=f"PSVC-{code}", name=f"school-{code}")
        session.add(school)
        await session.flush()
        return school

    async def test_create_prompt_is_born_active(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            prompt = await svc.create_prompt(
                title="Mobilidade urbana",
                statement="A partir dos textos, disserte.",
                created_by_external_identity="user:ADMIN",
            )
            self.assertEqual(prompt.status, "ACTIVE")
            self.assertEqual(prompt.title, "Mobilidade urbana")
            self.assertEqual(prompt.created_by_external_identity, "user:ADMIN")

    async def test_list_returns_archived_too_newest_first(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            older = await svc.create_prompt(
                title="Antiga", statement="s", created_by_external_identity="user:ADMIN"
            )
            older.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
            newer = await svc.create_prompt(
                title="Recente", statement="s", created_by_external_identity="user:ADMIN"
            )
            newer.created_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
            await svc.archive_prompt(platform_essay_prompt_id=older.id)

            listed = await svc.list_with_materialization_counts()
            self.assertEqual([p.title for p, _ in listed], ["Recente", "Antiga"])
            self.assertEqual([p.status for p, _ in listed], ["ACTIVE", "ARCHIVED"])

    async def test_list_counts_how_many_schools_materialized_each_prompt(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            used = await svc.create_prompt(
                title="Usada", statement="s", created_by_external_identity="user:ADMIN"
            )
            unused = await svc.create_prompt(
                title="Nunca usada", statement="s", created_by_external_identity="user:ADMIN"
            )
            school_a = await self._school(session, "A")
            school_b = await self._school(session, "B")
            for school in (school_a, school_b):
                session.add(EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Usada", statement="s",
                    year=2026, status="ACTIVE", created_by_external_identity="teacher:p1",
                    materialized_from_platform_prompt_id=used.id,
                ))
            # Uma proposta normal do professor nunca entra na contagem.
            session.add(EssayPrompt(
                id=uuid.uuid4(), school_id=school_a.id, title="Propria", statement="s",
                year=2026, status="DRAFT", created_by_external_identity="teacher:p1",
            ))
            await session.flush()

            counts = {p.id: count for p, count in await svc.list_with_materialization_counts()}
            self.assertEqual(counts[used.id], 2)
            self.assertEqual(counts[unused.id], 0)

    async def test_archive_prompt_flips_status_and_leaves_copies_untouched(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            origin = await svc.create_prompt(
                title="Tema", statement="s", created_by_external_identity="user:ADMIN"
            )
            school = await self._school(session, "C")
            copy = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="Tema", statement="s",
                year=2026, status="ACTIVE", created_by_external_identity="teacher:p1",
                materialized_from_platform_prompt_id=origin.id,
            )
            session.add(copy)
            await session.flush()

            archived = await svc.archive_prompt(platform_essay_prompt_id=origin.id)
            self.assertEqual(archived.status, "ARCHIVED")

            refreshed = await session.get(EssayPrompt, copy.id)
            self.assertEqual(refreshed.status, "ACTIVE")
            self.assertIsNone(refreshed.deleted_at)

    async def test_archive_unknown_prompt_raises_value_error(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            with self.assertRaises(ValueError):
                await svc.archive_prompt(platform_essay_prompt_id=uuid.uuid4())

    async def test_materialization_year_is_the_current_utc_year(self):
        self.assertEqual(materialization_year(), datetime.now(timezone.utc).year)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `.venv/bin/pytest tests/test_r5_platform_essay_prompt_service.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.services.platform_essay_prompt'`.

- [ ] **Step 3: Escrever o serviço**

Crie `src/agente_ia_edu/services/platform_essay_prompt.py`:

```python
"""Propostas de redacao da plataforma (spec 2026-09-29).

Dois lados na mesma classe, porque operam sobre o mesmo par de tabelas:
 - o lado do PLATFORM_ADMIN (criar / listar com contagem de uso / arquivar);
 - o lado da materializacao por escola, que a rota de atribuicao do professor
   usa (Task 5).

Autorizacao (papel, escopo de escola) vive na camada de rota, como em todo o
resto do sistema - este servico so trata as regras de entidade.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import EssayPrompt, PlatformEssayPrompt


def materialization_year() -> int:
    """O ano que uma copia materializada recebe em EssayPrompt.year (NOT NULL).

    platform_essay_prompts nao tem ano proprio (spec s3) - a proposta da
    plataforma e atemporal, quem a datou foi a escola que a adotou. O ano UTC
    corrente e o mesmo default que o formulario "Nova proposta" do professor
    usa. A listagem do professor chama este mesmo helper, para que o ano que
    ele ve na lista seja exatamente o ano que a copia vai receber.
    """
    return datetime.now(timezone.utc).year


class PlatformEssayPromptService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- lado do PLATFORM_ADMIN -------------------------------------------

    async def create_prompt(
        self,
        *,
        title: str,
        statement: str,
        created_by_external_identity: str,
    ) -> PlatformEssayPrompt:
        prompt = PlatformEssayPrompt(
            id=uuid.uuid4(),
            title=title,
            statement=statement,
            status="ACTIVE",
            created_by_external_identity=created_by_external_identity,
        )
        self.session.add(prompt)
        await self.session.flush()
        return prompt

    async def list_with_materialization_counts(
        self,
    ) -> list[tuple[PlatformEssayPrompt, int]]:
        """Todas as propostas (ACTIVE e ARCHIVED), mais recentes primeiro, com
        quantas escolas ja materializaram cada uma - a metrica de uso simples
        que a tela do admin mostra (spec s2)."""
        counts = (
            select(
                EssayPrompt.materialized_from_platform_prompt_id.label("origin_id"),
                func.count().label("school_count"),
            )
            .where(EssayPrompt.materialized_from_platform_prompt_id.is_not(None))
            .group_by(EssayPrompt.materialized_from_platform_prompt_id)
            .subquery()
        )
        result = await self.session.execute(
            select(PlatformEssayPrompt, func.coalesce(counts.c.school_count, 0))
            .outerjoin(counts, counts.c.origin_id == PlatformEssayPrompt.id)
            .order_by(PlatformEssayPrompt.created_at.desc())
        )
        return [(row[0], int(row[1])) for row in result.all()]

    async def archive_prompt(
        self, *, platform_essay_prompt_id: uuid.UUID
    ) -> PlatformEssayPrompt:
        """ARCHIVED so tira a proposta da lista de disponiveis para escolas que
        ainda NAO a materializaram. Nenhuma copia ja materializada e tocada -
        elas continuam existindo e atribuiveis normalmente (spec s2)."""
        prompt = await self.session.get(PlatformEssayPrompt, platform_essay_prompt_id)
        if prompt is None:
            raise ValueError(f"PlatformEssayPrompt not found: {platform_essay_prompt_id}")
        prompt.status = "ARCHIVED"
        await self.session.flush()
        return prompt


__all__ = ["PlatformEssayPromptService", "materialization_year"]
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `.venv/bin/pytest tests/test_r5_platform_essay_prompt_service.py -v`
Expected: PASS (6 testes).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/platform_essay_prompt.py \
        tests/test_r5_platform_essay_prompt_service.py
git commit -m "feat(redacao): PlatformEssayPromptService (criar/listar/arquivar)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Rotas de admin `/api/v1/admin/platform-essay-prompts`

> Ambiente: rode tudo de dentro do worktree, com o `.venv` **deste** worktree (ver "Ambiente de execução" no topo).

**Files:**
- Create: `src/agente_ia_edu/api/routes/admin_essay_prompts.py`
- Modify: `src/agente_ia_edu/api/app.py:30` (import) e `:77` (registro do router)
- Test: `tests/test_r5_platform_essay_prompts_admin_routes.py`

**Interfaces:**
- Consumes: `PlatformEssayPromptService.create_prompt / list_with_materialization_counts / archive_prompt` (Task 3); `require_platform_admin` de `api/routes/admin.py:37`.
- Produces (usado pelo frontend na Task 8):
  - `POST /api/v1/admin/platform-essay-prompts` → 201 `PlatformEssayPromptResponse`, corpo `{"title": str, "statement": str}`
  - `GET /api/v1/admin/platform-essay-prompts` → `list[PlatformEssayPromptResponse]`
  - `POST /api/v1/admin/platform-essay-prompts/{platform_essay_prompt_id}/archive` → `PlatformEssayPromptResponse`
  - `PlatformEssayPromptResponse = {id: UUID, title: str, statement: str, status: str, created_at: datetime, materialized_school_count: int}`
  - `admin_essay_prompts_router` exportado do módulo.

- [ ] **Step 1: Escrever o teste falhando**

Crie `tests/test_r5_platform_essay_prompts_admin_routes.py`:

```python
"""Rotas de admin das propostas da plataforma.

TestClient + dependency_overrides, mesmo padrao de tests/test_admin_http.py
(inclusive a forma de trocar a identidade por teste para exercitar o 403).
"""

import asyncio
import unittest
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.admin_essay_prompts import admin_essay_prompts_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import EssayPrompt, School
from agente_ia_edu.identity import ExternalIdentityContext

BASE = "/api/v1/admin/platform-essay-prompts"


class PlatformEssayPromptsAdminRoutesTests(unittest.TestCase):
    def setUp(self):
        async def _setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            return engine, factory

        self.engine, self.session_factory = asyncio.run(_setup())
        self.identity = {
            "value": ExternalIdentityContext(
                provider="test", external_user_id="admin", roles=("PLATFORM_ADMIN",)
            )
        }
        app = FastAPI()
        app.include_router(admin_essay_prompts_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_identity] = lambda: self.identity["value"]
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    def as_identity(self, **kwargs):
        defaults = {"provider": "test", "external_user_id": "someone", "roles": ()}
        defaults.update(kwargs)
        self.identity["value"] = ExternalIdentityContext(**defaults)

    def _create(self, title="Mobilidade urbana"):
        response = self.client.post(
            BASE, json={"title": title, "statement": "A partir dos textos, disserte."}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _materialize(self, origin_id, school_code):
        async def _seed():
            async with self.session_factory() as session:
                school = School(id=uuid.uuid4(), code=school_code, name=school_code)
                session.add(school)
                await session.flush()
                session.add(EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Mobilidade urbana",
                    statement="A partir dos textos, disserte.", year=2026, status="ACTIVE",
                    created_by_external_identity="teacher:p1",
                    materialized_from_platform_prompt_id=uuid.UUID(origin_id),
                ))
                await session.commit()

        asyncio.run(_seed())

    def test_create_returns_201_with_an_active_prompt(self):
        body = self._create()
        self.assertEqual(body["status"], "ACTIVE")
        self.assertEqual(body["title"], "Mobilidade urbana")
        self.assertEqual(body["materialized_school_count"], 0)
        self.assertTrue(body["created_at"])

    def test_create_rejects_an_empty_title(self):
        response = self.client.post(BASE, json={"title": "", "statement": "s"})
        self.assertEqual(response.status_code, 422, response.text)

    def test_list_returns_every_prompt_with_its_materialization_count(self):
        created = self._create()
        self._materialize(created["id"], "SCH-COUNT-A")
        self._materialize(created["id"], "SCH-COUNT-B")

        response = self.client.get(BASE)
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["materialized_school_count"], 2)

    def test_archive_flips_the_status_and_is_listed_as_archived(self):
        created = self._create()
        response = self.client.post(f"{BASE}/{created['id']}/archive")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ARCHIVED")

        listed = self.client.get(BASE).json()
        self.assertEqual(listed[0]["status"], "ARCHIVED")

    def test_archive_unknown_prompt_returns_404(self):
        response = self.client.post(f"{BASE}/{uuid.uuid4()}/archive")
        self.assertEqual(response.status_code, 404, response.text)

    def test_teacher_director_and_coordinator_are_all_denied(self):
        created = self._create()
        for role in ("TEACHER", "DIRECTOR", "COORDINATOR"):
            with self.subTest(role=role):
                self.as_identity(external_user_id=f"user-{role.lower()}", roles=(role,))
                self.assertEqual(self.client.get(BASE).status_code, 403)
                self.assertEqual(
                    self.client.post(BASE, json={"title": "x", "statement": "y"}).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.post(f"{BASE}/{created['id']}/archive").status_code, 403
                )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `.venv/bin/pytest tests/test_r5_platform_essay_prompts_admin_routes.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.api.routes.admin_essay_prompts'`.

- [ ] **Step 3: Escrever as rotas**

Crie `src/agente_ia_edu/api/routes/admin_essay_prompts.py`:

```python
"""Propostas de redacao da plataforma - lado do PLATFORM_ADMIN (spec
2026-09-29 s2).

Reusa a dependencia require_platform_admin de routes/admin.py (as tres vias
de autorizacao dela - role no token, atalho por external_user_id e vinculo
PLATFORM_ADMIN no banco - ja sao as mesmas que /api/v1/admin/schools usa),
em vez de inventar uma checagem propria: e o padrao exato de autorizacao de
admin que o sistema ja tem.

Prefixo proprio (/api/v1/admin/platform-essay-prompts) e router proprio, e
nao rotas soltas dentro de admin.py, porque este e um dominio diferente do
de multi-tenancy que aquele arquivo cobre.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..dependencies import get_session_factory
from ...identity import ExternalIdentityContext
from ...services.platform_essay_prompt import PlatformEssayPromptService
from .admin import require_platform_admin

admin_essay_prompts_router = APIRouter(
    prefix="/api/v1/admin/platform-essay-prompts",
    tags=["platform-essay-prompts"],
)


class PlatformEssayPromptCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    statement: str = Field(..., min_length=1)


class PlatformEssayPromptResponse(BaseModel):
    id: UUID
    title: str
    statement: str
    status: str
    created_at: datetime
    # Quantas escolas ja materializaram uma copia desta proposta - a metrica
    # de uso simples da tela do admin (spec s2). Uma proposta recem-criada e
    # sempre 0.
    materialized_school_count: int = 0


@admin_essay_prompts_router.post("", status_code=201, response_model=PlatformEssayPromptResponse)
async def create_platform_essay_prompt(
    request: PlatformEssayPromptCreateRequest,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> PlatformEssayPromptResponse:
    async with session_factory() as session:
        service = PlatformEssayPromptService(session)
        prompt = await service.create_prompt(
            title=request.title,
            statement=request.statement,
            created_by_external_identity=identity.external_user_id,
        )
        # Resposta montada ANTES do commit: commit() expira o objeto
        # (expire_on_commit=True em producao) e ler atributos depois disso
        # dispara um lazy-load sincrono que estoura com MissingGreenlet em
        # contexto async - mesmo cuidado de essay_prompts.py.
        response = PlatformEssayPromptResponse(
            id=prompt.id, title=prompt.title, statement=prompt.statement,
            status=prompt.status, created_at=prompt.created_at,
            materialized_school_count=0,
        )
        await session.commit()
        return response


@admin_essay_prompts_router.get("", response_model=list[PlatformEssayPromptResponse])
async def list_platform_essay_prompts(
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> list[PlatformEssayPromptResponse]:
    async with session_factory() as session:
        service = PlatformEssayPromptService(session)
        rows = await service.list_with_materialization_counts()
        return [
            PlatformEssayPromptResponse(
                id=prompt.id, title=prompt.title, statement=prompt.statement,
                status=prompt.status, created_at=prompt.created_at,
                materialized_school_count=count,
            )
            for prompt, count in rows
        ]


@admin_essay_prompts_router.post(
    "/{platform_essay_prompt_id}/archive", response_model=PlatformEssayPromptResponse
)
async def archive_platform_essay_prompt(
    platform_essay_prompt_id: UUID,
    identity: ExternalIdentityContext = Depends(require_platform_admin),
    session_factory=Depends(get_session_factory),
) -> PlatformEssayPromptResponse:
    async with session_factory() as session:
        service = PlatformEssayPromptService(session)
        try:
            prompt = await service.archive_prompt(
                platform_essay_prompt_id=platform_essay_prompt_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        # Recontagem depois do arquivamento: a tela mostra a mesma linha
        # atualizada, e arquivar NUNCA muda a contagem (nenhuma copia e
        # tocada) - pegar o valor real aqui e o que prova isso na resposta.
        counts = {p.id: count for p, count in await service.list_with_materialization_counts()}
        response = PlatformEssayPromptResponse(
            id=prompt.id, title=prompt.title, statement=prompt.statement,
            status=prompt.status, created_at=prompt.created_at,
            materialized_school_count=counts.get(prompt.id, 0),
        )
        await session.commit()
        return response


__all__ = ["admin_essay_prompts_router"]
```

- [ ] **Step 4: Registrar o router na app**

Em `src/agente_ia_edu/api/app.py`, logo depois da linha `from .routes.admin import admin_router` (linha 30):

```python
from .routes.admin_essay_prompts import admin_essay_prompts_router
```

E logo depois da linha `app.include_router(admin_router, dependencies=reception_only_guard)` (linha 77):

```python
    app.include_router(admin_essay_prompts_router, dependencies=reception_only_guard)
```

- [ ] **Step 5: Rodar os testes e confirmar que passam**

Run: `.venv/bin/pytest tests/test_r5_platform_essay_prompts_admin_routes.py tests/test_admin_http.py -v`
Expected: PASS nos dois arquivos (o segundo prova que nada em `/api/v1/admin/*` regrediu com o router novo).

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/api/routes/admin_essay_prompts.py \
        src/agente_ia_edu/api/app.py \
        tests/test_r5_platform_essay_prompts_admin_routes.py
git commit -m "feat(redacao): rotas PLATFORM_ADMIN de propostas da plataforma

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Materialização por escola (buscar-ou-criar idempotente)

> Ambiente: rode tudo de dentro do worktree, com o `.venv` **deste** worktree (ver "Ambiente de execução" no topo).

**Files:**
- Modify: `src/agente_ia_edu/services/platform_essay_prompt.py` (acrescenta 4 métodos à classe criada na Task 3)
- Test: `tests/test_r5_platform_essay_prompt_materialization.py`

**Interfaces:**
- Consumes: `PlatformEssayPromptService` e `materialization_year()` (Task 3).
- Produces (usados pelas Tasks 6 e 7):
  - `async get_platform_prompt(self, platform_essay_prompt_id: uuid.UUID) -> PlatformEssayPrompt | None`
  - `async find_materialized(self, *, platform_essay_prompt_id: uuid.UUID, school_id: uuid.UUID) -> EssayPrompt | None`
  - `async materialize_for_school(self, *, platform_essay_prompt_id: uuid.UUID, school_id: uuid.UUID, created_by_external_identity: str) -> EssayPrompt` (levanta `ValueError` se a origem não existir)
  - `async list_available_for_school(self, *, school_id: uuid.UUID) -> list[PlatformEssayPrompt]`

- [ ] **Step 1: Escrever o teste falhando**

Crie `tests/test_r5_platform_essay_prompt_materialization.py`:

```python
"""Materializacao de uma proposta da plataforma numa escola (spec s4).

O contrato inteiro do fluxo novo esta aqui: buscar-ou-criar idempotente,
isolamento entre escolas e o que entra/sai da lista de disponiveis.
"""

import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import EssayPrompt, School
from agente_ia_edu.services.platform_essay_prompt import (
    PlatformEssayPromptService,
    materialization_year,
)


class PlatformEssayPromptMaterializationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _school(self, session, code):
        school = School(id=uuid.uuid4(), code=f"PMAT-{code}", name=f"school-{code}")
        session.add(school)
        await session.flush()
        return school

    async def test_materialize_copies_title_and_statement_into_the_school(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            school = await self._school(session, "1")
            origin = await svc.create_prompt(
                title="Mobilidade urbana", statement="A partir dos textos, disserte.",
                created_by_external_identity="user:ADMIN",
            )
            copy = await svc.materialize_for_school(
                platform_essay_prompt_id=origin.id, school_id=school.id,
                created_by_external_identity="teacher:p1",
            )
            self.assertEqual(copy.school_id, school.id)
            self.assertEqual(copy.title, origin.title)
            self.assertEqual(copy.statement, origin.statement)
            self.assertEqual(copy.status, "ACTIVE")
            self.assertEqual(copy.year, materialization_year())
            self.assertEqual(copy.materialized_from_platform_prompt_id, origin.id)
            self.assertFalse(copy.is_free_theme)
            self.assertIsNone(copy.deleted_at)

    async def test_materializing_twice_in_the_same_school_reuses_the_same_row(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            school = await self._school(session, "2")
            origin = await svc.create_prompt(
                title="Tema", statement="s", created_by_external_identity="user:ADMIN"
            )
            first = await svc.materialize_for_school(
                platform_essay_prompt_id=origin.id, school_id=school.id,
                created_by_external_identity="teacher:p1",
            )
            second = await svc.materialize_for_school(
                platform_essay_prompt_id=origin.id, school_id=school.id,
                created_by_external_identity="teacher:p2",
            )
            self.assertEqual(first.id, second.id)

            rows = (await session.execute(
                select(EssayPrompt).where(
                    EssayPrompt.school_id == school.id,
                    EssayPrompt.materialized_from_platform_prompt_id == origin.id,
                )
            )).scalars().all()
            self.assertEqual(len(rows), 1)

    async def test_two_schools_get_independent_copies(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            school_a = await self._school(session, "3A")
            school_b = await self._school(session, "3B")
            origin = await svc.create_prompt(
                title="Tema", statement="s", created_by_external_identity="user:ADMIN"
            )
            copy_a = await svc.materialize_for_school(
                platform_essay_prompt_id=origin.id, school_id=school_a.id,
                created_by_external_identity="teacher:a",
            )
            copy_b = await svc.materialize_for_school(
                platform_essay_prompt_id=origin.id, school_id=school_b.id,
                created_by_external_identity="teacher:b",
            )
            self.assertNotEqual(copy_a.id, copy_b.id)
            self.assertEqual(copy_a.school_id, school_a.id)
            self.assertEqual(copy_b.school_id, school_b.id)

    async def test_materialize_unknown_origin_raises_value_error(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            school = await self._school(session, "4")
            with self.assertRaises(ValueError):
                await svc.materialize_for_school(
                    platform_essay_prompt_id=uuid.uuid4(), school_id=school.id,
                    created_by_external_identity="teacher:p1",
                )

    async def test_get_platform_prompt_returns_none_for_a_normal_essay_prompt_id(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            school = await self._school(session, "5")
            own = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="Propria", statement="s",
                year=2026, status="DRAFT", created_by_external_identity="teacher:p1",
            )
            session.add(own)
            await session.flush()
            self.assertIsNone(await svc.get_platform_prompt(own.id))

    async def test_available_list_hides_archived_and_already_materialized(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            school = await self._school(session, "6")
            other_school = await self._school(session, "6B")

            free = await svc.create_prompt(
                title="Disponivel", statement="s", created_by_external_identity="user:ADMIN"
            )
            free.created_at = datetime(2026, 9, 3, tzinfo=timezone.utc)
            archived = await svc.create_prompt(
                title="Arquivada", statement="s", created_by_external_identity="user:ADMIN"
            )
            archived.created_at = datetime(2026, 9, 2, tzinfo=timezone.utc)
            mine = await svc.create_prompt(
                title="Ja materializada", statement="s", created_by_external_identity="user:ADMIN"
            )
            mine.created_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
            await svc.archive_prompt(platform_essay_prompt_id=archived.id)
            await svc.materialize_for_school(
                platform_essay_prompt_id=mine.id, school_id=school.id,
                created_by_external_identity="teacher:p1",
            )
            # Materializada por OUTRA escola nao afeta esta.
            await svc.materialize_for_school(
                platform_essay_prompt_id=free.id, school_id=other_school.id,
                created_by_external_identity="teacher:o",
            )

            available = await svc.list_available_for_school(school_id=school.id)
            self.assertEqual([p.title for p in available], ["Disponivel"])

    async def test_find_materialized_is_scoped_to_the_school(self):
        async with self.session_factory() as session:
            svc = PlatformEssayPromptService(session)
            school_a = await self._school(session, "7A")
            school_b = await self._school(session, "7B")
            origin = await svc.create_prompt(
                title="Tema", statement="s", created_by_external_identity="user:ADMIN"
            )
            await svc.materialize_for_school(
                platform_essay_prompt_id=origin.id, school_id=school_a.id,
                created_by_external_identity="teacher:a",
            )
            self.assertIsNotNone(await svc.find_materialized(
                platform_essay_prompt_id=origin.id, school_id=school_a.id
            ))
            self.assertIsNone(await svc.find_materialized(
                platform_essay_prompt_id=origin.id, school_id=school_b.id
            ))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `.venv/bin/pytest tests/test_r5_platform_essay_prompt_materialization.py -v`
Expected: FAIL com `AttributeError: 'PlatformEssayPromptService' object has no attribute 'materialize_for_school'`.

- [ ] **Step 3: Acrescentar os métodos de materialização ao serviço**

Em `src/agente_ia_edu/services/platform_essay_prompt.py`, troque o import do SQLAlchemy no topo:

```python
from sqlalchemy import func, select
```

por:

```python
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
```

E acrescente, no fim da classe `PlatformEssayPromptService` (depois de `archive_prompt`, antes do `__all__` do módulo):

```python
    # ---- lado da materializacao por escola --------------------------------

    async def get_platform_prompt(
        self, platform_essay_prompt_id: uuid.UUID
    ) -> PlatformEssayPrompt | None:
        """Devolve None quando o id NAO e de uma proposta da plataforma - e
        assim que a rota de atribuicao decide se precisa materializar ou se
        esta lidando com um EssayPrompt normal, ja que o frontend manda o
        mesmo campo nos dois casos (spec s2)."""
        return await self.session.get(PlatformEssayPrompt, platform_essay_prompt_id)

    async def find_materialized(
        self, *, platform_essay_prompt_id: uuid.UUID, school_id: uuid.UUID
    ) -> EssayPrompt | None:
        """A copia desta escola para esta origem, se ja existir.

        Devolve tambem uma copia que o professor mandou para a Lixeira
        (deleted_at preenchido) - a constraint unica nao distingue, entao
        reaproveitar a linha existente e a unica saida correta; quem barra o
        uso de uma proposta na lixeira e _prompt_for_own_school_or_403 na
        camada de rota, com a mensagem certa ("esta proposta esta na
        lixeira"), e o caminho do professor e restaurar.
        """
        result = await self.session.execute(
            select(EssayPrompt).where(
                EssayPrompt.school_id == school_id,
                EssayPrompt.materialized_from_platform_prompt_id == platform_essay_prompt_id,
            )
        )
        return result.scalars().first()

    async def materialize_for_school(
        self,
        *,
        platform_essay_prompt_id: uuid.UUID,
        school_id: uuid.UUID,
        created_by_external_identity: str,
    ) -> EssayPrompt:
        """Buscar-ou-criar idempotente da copia desta escola (spec s4).

        Uma proposta da plataforma ARQUIVADA ainda materializa: arquivar so
        tira a proposta da lista de escolas que ainda nao a adotaram, e um
        professor que ja tinha a lista aberta quando o admin arquivou nao
        deve receber um erro no meio da atribuicao.

        O IntegrityError capturado e a corrida de duas requisicoes
        simultaneas da mesma escola: a constraint unica rejeita a segunda
        insercao e nos relemos a linha que a primeira criou, em vez de
        estourar. E seguro fazer rollback aqui porque a materializacao
        acontece ANTES de qualquer PromptAssignment ser criada na requisicao.
        """
        existing = await self.find_materialized(
            platform_essay_prompt_id=platform_essay_prompt_id, school_id=school_id
        )
        if existing is not None:
            return existing

        origin = await self.session.get(PlatformEssayPrompt, platform_essay_prompt_id)
        if origin is None:
            raise ValueError(f"PlatformEssayPrompt not found: {platform_essay_prompt_id}")

        copy = EssayPrompt(
            id=uuid.uuid4(),
            school_id=school_id,
            title=origin.title,
            statement=origin.statement,
            year=materialization_year(),
            status="ACTIVE",
            is_free_theme=False,
            created_by_external_identity=created_by_external_identity,
            materialized_from_platform_prompt_id=origin.id,
        )
        self.session.add(copy)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.find_materialized(
                platform_essay_prompt_id=platform_essay_prompt_id, school_id=school_id
            )
            if existing is None:
                raise
            return existing
        return copy

    async def list_available_for_school(
        self, *, school_id: uuid.UUID
    ) -> list[PlatformEssayPrompt]:
        """As propostas ACTIVE da plataforma que esta escola ainda NAO
        materializou - exatamente o que entra na lista do professor junto das
        propostas proprias da escola (spec s2).

        Uma origem cuja copia esta na Lixeira desta escola continua fora
        desta lista: a copia existe, a constraint unica impediria uma
        segunda, e o caminho certo e restaurar a copia pela Lixeira.
        """
        materialized = select(EssayPrompt.materialized_from_platform_prompt_id).where(
            EssayPrompt.school_id == school_id,
            EssayPrompt.materialized_from_platform_prompt_id.is_not(None),
        )
        result = await self.session.execute(
            select(PlatformEssayPrompt)
            .where(
                PlatformEssayPrompt.status == "ACTIVE",
                PlatformEssayPrompt.id.not_in(materialized),
            )
            .order_by(PlatformEssayPrompt.created_at.desc())
        )
        return list(result.scalars().all())
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `.venv/bin/pytest tests/test_r5_platform_essay_prompt_materialization.py tests/test_r5_platform_essay_prompt_service.py -v`
Expected: PASS nos dois arquivos.

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/platform_essay_prompt.py \
        tests/test_r5_platform_essay_prompt_materialization.py
git commit -m "feat(redacao): materializacao idempotente de proposta da plataforma por escola

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Listagem e detalhe do professor cientes da plataforma

> Ambiente: rode tudo de dentro do worktree, com o `.venv` **deste** worktree (ver "Ambiente de execução" no topo).

**Files:**
- Modify: `src/agente_ia_edu/api/routes/essay_prompts.py` — imports (linhas 20-31), `EssayPromptResponse` (57-63), `EssayPromptDetailResponse` (409-411), `list_essay_prompts` (454-472), `get_essay_prompt_detail` (538-578)
- Test: `tests/test_r5_platform_essay_prompts_teacher_listing.py`

**Interfaces:**
- Consumes: `PlatformEssayPromptService.list_available_for_school / get_platform_prompt / find_materialized` e `materialization_year()` (Tasks 3 e 5).
- Produces (consumidos pela Task 9):
  - `EssayPromptResponse` ganha `is_platform: bool = False` e `platform_prompt_id: Optional[UUID] = None`
  - `EssayPromptDetailResponse` ganha `materialized: bool = True`
  - `GET /api/v1/catalog/essay-prompts` devolve as próprias da escola seguidas das da plataforma disponíveis
  - `GET /api/v1/catalog/essay-prompts/{id}` aceita também o id de uma `PlatformEssayPrompt`

- [ ] **Step 1: Escrever o teste falhando**

Crie `tests/test_r5_platform_essay_prompts_teacher_listing.py`:

```python
"""Listagem e detalhe do professor com propostas da plataforma misturadas.

TestClient + dependency_overrides, mesmo padrao de
tests/test_r2_essay_prompts_routes.py.
"""

import asyncio
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import PlatformEssayPrompt, School, UserSchoolLink
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.platform_essay_prompt import materialization_year

PROMPTS = "/api/v1/catalog/essay-prompts"


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class PlatformEssayPromptsTeacherListingTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async def _prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        self.loop.run_until_complete(_prep())
        self.app = create_app()
        self.app.dependency_overrides[get_session_factory] = lambda: self.factory
        self.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_r5")
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def _seed_school_and_teacher(self, code: str, user: str = "prof_r5"):
        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"PTL-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id=user, school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                await session.commit()
                return school.id

        return self.loop.run_until_complete(_seed())

    def _seed_platform_prompt(self, title: str, status: str = "ACTIVE"):
        async def _seed():
            async with self.factory() as session:
                prompt = PlatformEssayPrompt(
                    id=uuid.uuid4(), title=title, statement=f"Enunciado de {title}.",
                    status=status, created_by_external_identity="user:ADMIN",
                )
                session.add(prompt)
                await session.commit()
                return prompt.id

        return self.loop.run_until_complete(_seed())

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def test_active_platform_prompts_appear_flagged_next_to_the_schools_own(self):
        self._seed_school_and_teacher("1")
        origin_id = self._seed_platform_prompt("Mobilidade urbana")
        own = self.client.post(
            PROMPTS, json={"title": "Minha proposta", "statement": "Disserte.", "year": 2026}
        )
        self.assertEqual(own.status_code, 201, own.text)

        rows = self.client.get(PROMPTS).json()
        by_title = {row["title"]: row for row in rows}
        self.assertEqual(set(by_title), {"Minha proposta", "Mobilidade urbana"})

        self.assertFalse(by_title["Minha proposta"]["is_platform"])
        self.assertIsNone(by_title["Minha proposta"]["platform_prompt_id"])

        platform_row = by_title["Mobilidade urbana"]
        self.assertTrue(platform_row["is_platform"])
        self.assertEqual(platform_row["platform_prompt_id"], str(origin_id))
        self.assertEqual(platform_row["id"], str(origin_id))
        self.assertEqual(platform_row["status"], "ACTIVE")
        self.assertEqual(platform_row["year"], materialization_year())

    def test_archived_platform_prompt_never_appears(self):
        self._seed_school_and_teacher("2")
        self._seed_platform_prompt("Arquivada", status="ARCHIVED")
        rows = self.client.get(PROMPTS).json()
        self.assertEqual(rows, [])

    def test_a_materialized_prompt_appears_once_still_flagged_as_platform(self):
        self._seed_school_and_teacher("3")
        origin_id = self._seed_platform_prompt("Adotada")

        detail = self.client.get(f"{PROMPTS}/{origin_id}").json()
        self.assertFalse(detail["materialized"])
        self.assertTrue(detail["is_platform"])
        self.assertEqual(detail["materials"], [])
        self.assertEqual(detail["assignments"], [])

        # Esta tarefa cobre so listagem/detalhe (a materializacao pela rota de
        # atribuicao e a Task 7), entao usamos o servico diretamente.
        async def _materialize():
            from agente_ia_edu.services.platform_essay_prompt import PlatformEssayPromptService
            async with self.factory() as session:
                school_id = (await session.execute(
                    select(School.id).where(School.code == "PTL-3")
                )).scalar_one()
                copy = await PlatformEssayPromptService(session).materialize_for_school(
                    platform_essay_prompt_id=origin_id, school_id=school_id,
                    created_by_external_identity="teacher:prof_r5",
                )
                copy_id = copy.id
                await session.commit()
                return copy_id

        copy_id = self.loop.run_until_complete(_materialize())

        rows = self.client.get(PROMPTS).json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], str(copy_id))
        self.assertTrue(rows[0]["is_platform"])
        self.assertEqual(rows[0]["platform_prompt_id"], str(origin_id))

    def test_detail_by_origin_id_returns_the_real_copy_once_materialized(self):
        self._seed_school_and_teacher("4")
        origin_id = self._seed_platform_prompt("Adotada 2")

        async def _materialize():
            from agente_ia_edu.services.platform_essay_prompt import PlatformEssayPromptService
            async with self.factory() as session:
                school_id = (await session.execute(
                    select(School.id).where(School.code == "PTL-4")
                )).scalar_one()
                copy = await PlatformEssayPromptService(session).materialize_for_school(
                    platform_essay_prompt_id=origin_id, school_id=school_id,
                    created_by_external_identity="teacher:prof_r5",
                )
                copy_id = copy.id
                await session.commit()
                return copy_id

        copy_id = self.loop.run_until_complete(_materialize())

        detail = self.client.get(f"{PROMPTS}/{origin_id}").json()
        self.assertEqual(detail["id"], str(copy_id))
        self.assertTrue(detail["materialized"])
        self.assertTrue(detail["is_platform"])

    def test_one_schools_adoption_does_not_hide_the_prompt_from_another_school(self):
        school_a = self._seed_school_and_teacher("5A", user="prof_a")
        self._seed_school_and_teacher("5B", user="prof_b")
        origin_id = self._seed_platform_prompt("Compartilhada")

        async def _materialize():
            from agente_ia_edu.services.platform_essay_prompt import PlatformEssayPromptService
            async with self.factory() as session:
                await PlatformEssayPromptService(session).materialize_for_school(
                    platform_essay_prompt_id=origin_id, school_id=school_a,
                    created_by_external_identity="teacher:prof_a",
                )
                await session.commit()

        self.loop.run_until_complete(_materialize())

        self._as("prof_b")
        rows = self.client.get(PROMPTS).json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], str(origin_id))
        self.assertTrue(rows[0]["is_platform"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `.venv/bin/pytest tests/test_r5_platform_essay_prompts_teacher_listing.py -v`
Expected: FAIL com `KeyError: 'is_platform'` no primeiro teste.

- [ ] **Step 3: Acrescentar os campos novos aos response models**

Em `src/agente_ia_edu/api/routes/essay_prompts.py`, troque `EssayPromptResponse` (hoje linhas 57-63):

```python
class EssayPromptResponse(BaseModel):
    id: UUID
    school_id: UUID
    title: str
    statement: str
    year: int
    status: str
```

por:

```python
class EssayPromptResponse(BaseModel):
    id: UUID
    # Para uma proposta da plataforma ainda NAO materializada, este e o
    # school_id da escola do professor que esta lendo - a escola que vai
    # receber a copia se ele atribuir. Nunca vem do corpo da requisicao.
    school_id: UUID
    title: str
    statement: str
    year: int
    status: str
    # True tanto para uma proposta da plataforma ainda nao materializada
    # quanto para a copia dela ja materializada nesta escola: em ambos os
    # casos ela e somente-leitura para o professor (spec, decisao 3).
    is_platform: bool = False
    # A origem em platform_essay_prompts, quando houver.
    platform_prompt_id: Optional[UUID] = None
```

E troque `EssayPromptDetailResponse` (hoje linhas 409-411):

```python
class EssayPromptDetailResponse(EssayPromptResponse):
    materials: list[PromptMaterialResponse]
    assignments: list[PromptAssignmentResponse]
```

por:

```python
class EssayPromptDetailResponse(EssayPromptResponse):
    materials: list[PromptMaterialResponse]
    assignments: list[PromptAssignmentResponse]
    # False so na pre-visualizacao de uma proposta da plataforma que esta
    # escola ainda nao adotou: nao existe linha em essay_prompts ainda, entao
    # nada que dependa de um EssayPrompt real (folha de resposta, materiais,
    # dashboard) funciona nela - so o formulario de atribuicao, que e o que
    # dispara a materializacao.
    materialized: bool = True
```

- [ ] **Step 4: Importar o serviço no módulo de rotas**

Ainda em `essay_prompts.py`, logo depois de `from ...services.material_storage import MaterialStorage` (linha 31):

```python
from ...services.platform_essay_prompt import PlatformEssayPromptService, materialization_year
```

- [ ] **Step 5: Reescrever a listagem**

Troque o corpo de `list_essay_prompts` (hoje linhas 454-472) por:

```python
@essay_prompts_router.get("", response_model=list[EssayPromptResponse])
async def list_essay_prompts(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[EssayPromptResponse]:
    """As propostas da escola do professor, seguidas das propostas da
    plataforma ACTIVE que esta escola ainda nao materializou - na MESMA
    lista, com o campo is_platform como unica diferenca (spec, decisao 2).
    Uma proposta da plataforma ja adotada por esta escola aparece so uma
    vez, como a copia dela (que tambem carrega is_platform=True)."""
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        result = await session.execute(
            select(EssayPrompt)
            .where(EssayPrompt.school_id == school_id, EssayPrompt.deleted_at.is_(None))
            .order_by(EssayPrompt.created_at.desc())
        )
        items = [
            EssayPromptResponse(
                id=p.id, school_id=p.school_id, title=p.title,
                statement=p.statement, year=p.year, status=p.status,
                is_platform=p.materialized_from_platform_prompt_id is not None,
                platform_prompt_id=p.materialized_from_platform_prompt_id,
            )
            for p in result.scalars().all()
        ]
        available = await PlatformEssayPromptService(session).list_available_for_school(
            school_id=school_id
        )
        # O mesmo ano que materialize_for_school vai gravar em
        # EssayPrompt.year, para o professor nao ver um ano na lista e outro
        # depois de atribuir.
        year = materialization_year()
        items.extend(
            EssayPromptResponse(
                id=origin.id, school_id=school_id, title=origin.title,
                statement=origin.statement, year=year,
                # A copia nasce ACTIVE - e o status que a proposta tera nesta
                # escola. list_available_for_school ja so devolve origens
                # ACTIVE, entao nao ha ARCHIVED para propagar aqui.
                status="ACTIVE",
                is_platform=True, platform_prompt_id=origin.id,
            )
            for origin in available
        )
        return items
```

- [ ] **Step 6: Tornar a rota de detalhe ciente da plataforma**

Troque o corpo de `get_essay_prompt_detail` (hoje linhas 538-578) por:

```python
@essay_prompts_router.get("/{essay_prompt_id}", response_model=EssayPromptDetailResponse)
async def get_essay_prompt_detail(
    essay_prompt_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayPromptDetailResponse:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)

        # A tela do professor lista proposta propria e proposta da plataforma
        # sob o mesmo campo de id, e so abre o detalhe antes de atribuir - sem
        # este trecho, abrir uma proposta da plataforma daria 403 e o fluxo
        # nunca chegaria ao formulario de atribuicao.
        platform_service = PlatformEssayPromptService(session)
        origin = await platform_service.get_platform_prompt(essay_prompt_id)
        if origin is not None:
            copy = await platform_service.find_materialized(
                platform_essay_prompt_id=origin.id, school_id=school_id
            )
            if copy is None:
                return EssayPromptDetailResponse(
                    id=origin.id, school_id=school_id, title=origin.title,
                    statement=origin.statement, year=materialization_year(),
                    status="ACTIVE", is_platform=True, platform_prompt_id=origin.id,
                    materialized=False, materials=[], assignments=[],
                )
            essay_prompt_id = copy.id

        prompt = await _prompt_for_own_school_or_403(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id
        )
        materials = (
            await session.execute(
                select(PromptMaterial)
                .where(PromptMaterial.essay_prompt_id == prompt.id)
                .order_by(PromptMaterial.position)
            )
        ).scalars().all()
        assignments = (
            await session.execute(
                select(PromptAssignment).where(PromptAssignment.essay_prompt_id == prompt.id)
            )
        ).scalars().all()
        return EssayPromptDetailResponse(
            id=prompt.id, school_id=prompt.school_id, title=prompt.title,
            statement=prompt.statement, year=prompt.year, status=prompt.status,
            is_platform=prompt.materialized_from_platform_prompt_id is not None,
            platform_prompt_id=prompt.materialized_from_platform_prompt_id,
            materialized=True,
            materials=[
                PromptMaterialResponse(
                    id=m.id, essay_prompt_id=m.essay_prompt_id, material_type=m.material_type,
                    content=m.content, storage_uri=m.storage_uri, position=m.position,
                )
                for m in materials
            ],
            assignments=[
                PromptAssignmentResponse(
                    id=a.id, school_id=a.school_id, essay_prompt_id=a.essay_prompt_id,
                    class_id=a.class_id, status=a.status, validation_enabled=a.validation_enabled,
                )
                for a in assignments
            ],
        )
```

- [ ] **Step 7: Rodar os testes e confirmar que passam**

Run: `.venv/bin/pytest tests/test_r5_platform_essay_prompts_teacher_listing.py tests/test_r2_essay_prompts_routes.py tests/test_frontend_r_teacher_essay_prompts_list_route.py -v`
Expected: PASS nos três (os dois últimos provam que a listagem e o detalhe existentes não regrediram).

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_prompts.py \
        tests/test_r5_platform_essay_prompts_teacher_listing.py
git commit -m "feat(redacao): listagem e detalhe do professor cientes das propostas da plataforma

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Atribuir uma proposta da plataforma materializa a cópia da escola

> Ambiente: rode tudo de dentro do worktree, com o `.venv` **deste** worktree (ver "Ambiente de execução" no topo).

**Files:**
- Modify: `src/agente_ia_edu/api/routes/essay_prompts.py` — helper novo perto de `_prompt_for_own_school_or_403` (linha 128), `create_prompt_assignment` (273-308), `create_prompt_assignments_bulk` (311-342)
- Test: `tests/test_r5_platform_essay_prompts_assignment_materialization.py`

**Interfaces:**
- Consumes: `PlatformEssayPromptService.get_platform_prompt / materialize_for_school` (Task 5); `EssayPromptResponse.is_platform` (Task 6).
- Produces: `async _materialize_if_platform_prompt(session, *, essay_prompt_id: uuid.UUID, school_id: uuid.UUID, created_by_external_identity: str) -> uuid.UUID` — devolve sempre o id de um `EssayPrompt` real e escopado à escola.

> A linha `from ...services.platform_essay_prompt import PlatformEssayPromptService, materialization_year` já foi acrescentada a `essay_prompts.py` na Task 6, Step 4 — não repita o import.

- [ ] **Step 1: Escrever o teste falhando**

Crie `tests/test_r5_platform_essay_prompts_assignment_materialization.py`:

```python
"""Atribuir uma proposta da plataforma a uma turma materializa a copia da
escola (spec s4) - individual e em lote, idempotente e isolada por escola.
"""

import asyncio
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EssayPrompt,
    GradeLevel,
    PlatformEssayPrompt,
    School,
    Segment,
    UserSchoolLink,
)
from agente_ia_edu.identity import ExternalIdentityContext

PROMPTS = "/api/v1/catalog/essay-prompts"


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class PlatformPromptAssignmentMaterializationTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async def _prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        self.loop.run_until_complete(_prep())
        self.app = create_app()
        self.app.dependency_overrides[get_session_factory] = lambda: self.factory
        self.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_r5")
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _seed(self, code: str, user: str = "prof_r5", class_count: int = 2):
        """Escola + professor vinculado + N turmas reais."""

        async def _do():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"PAM-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id=user, school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                segment = Segment(
                    id=uuid.uuid4(), school_id=school.id, name="seg", external_id=f"SEG-{code}"
                )
                session.add(segment)
                await session.flush()
                grade = GradeLevel(
                    id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
                    name="grade", external_id=f"GRADE-{code}",
                )
                year = AcademicYear(
                    id=uuid.uuid4(), school_id=school.id, year=2026, external_id=f"YEAR-{code}"
                )
                session.add_all([grade, year])
                await session.flush()
                class_ids = []
                for index in range(class_count):
                    klass = Class(
                        id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
                        grade_level_id=grade.id, name=f"turma-{index}",
                        external_id=f"TURMA-{code}-{index}",
                    )
                    session.add(klass)
                    class_ids.append(klass.id)
                await session.commit()
                return school.id, class_ids

        return self.loop.run_until_complete(_do())

    def _seed_platform_prompt(self, title="Mobilidade urbana"):
        async def _do():
            async with self.factory() as session:
                prompt = PlatformEssayPrompt(
                    id=uuid.uuid4(), title=title, statement=f"Enunciado de {title}.",
                    status="ACTIVE", created_by_external_identity="user:ADMIN",
                )
                session.add(prompt)
                await session.commit()
                return prompt.id

        return self.loop.run_until_complete(_do())

    def _copies(self, school_id, origin_id):
        async def _do():
            async with self.factory() as session:
                rows = (await session.execute(
                    select(EssayPrompt).where(
                        EssayPrompt.school_id == school_id,
                        EssayPrompt.materialized_from_platform_prompt_id == origin_id,
                    )
                )).scalars().all()
                return [row.id for row in rows]

        return self.loop.run_until_complete(_do())

    def test_single_assignment_materializes_and_points_at_the_copy(self):
        school_id, class_ids = self._seed("1")
        origin_id = self._seed_platform_prompt()

        response = self.client.post(
            f"{PROMPTS}/{origin_id}/assignments", json={"class_id": str(class_ids[0])}
        )
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()

        copies = self._copies(school_id, origin_id)
        self.assertEqual(len(copies), 1)
        self.assertEqual(body["essay_prompt_id"], str(copies[0]))
        self.assertNotEqual(body["essay_prompt_id"], str(origin_id))
        self.assertEqual(body["school_id"], str(school_id))

    def test_two_assignments_to_different_classes_reuse_the_same_copy(self):
        school_id, class_ids = self._seed("2")
        origin_id = self._seed_platform_prompt()

        first = self.client.post(
            f"{PROMPTS}/{origin_id}/assignments", json={"class_id": str(class_ids[0])}
        )
        second = self.client.post(
            f"{PROMPTS}/{origin_id}/assignments", json={"class_id": str(class_ids[1])}
        )
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(
            first.json()["essay_prompt_id"], second.json()["essay_prompt_id"]
        )
        self.assertEqual(len(self._copies(school_id, origin_id)), 1)

    def test_bulk_assignment_materializes_once_for_every_class(self):
        school_id, class_ids = self._seed("3")
        origin_id = self._seed_platform_prompt()

        response = self.client.post(
            f"{PROMPTS}/{origin_id}/assignments/bulk",
            json={"class_ids": [str(c) for c in class_ids]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["failures"], {})
        self.assertEqual(len(body["assigned"]), 2)

        copies = self._copies(school_id, origin_id)
        self.assertEqual(len(copies), 1)
        self.assertEqual({a["essay_prompt_id"] for a in body["assigned"]}, {str(copies[0])})

    def test_bulk_then_single_still_reuses_the_same_copy(self):
        school_id, class_ids = self._seed("4", class_count=2)
        origin_id = self._seed_platform_prompt()

        bulk = self.client.post(
            f"{PROMPTS}/{origin_id}/assignments/bulk",
            json={"class_ids": [str(class_ids[0])]},
        )
        single = self.client.post(
            f"{PROMPTS}/{origin_id}/assignments", json={"class_id": str(class_ids[1])}
        )
        self.assertEqual(bulk.status_code, 200, bulk.text)
        self.assertEqual(single.status_code, 201, single.text)
        self.assertEqual(
            bulk.json()["assigned"][0]["essay_prompt_id"], single.json()["essay_prompt_id"]
        )
        self.assertEqual(len(self._copies(school_id, origin_id)), 1)

    def test_two_schools_get_independent_copies_through_the_routes(self):
        school_a, classes_a = self._seed("5A", user="prof_a", class_count=1)
        school_b, classes_b = self._seed("5B", user="prof_b", class_count=1)
        origin_id = self._seed_platform_prompt()

        self._as("prof_a")
        resp_a = self.client.post(
            f"{PROMPTS}/{origin_id}/assignments", json={"class_id": str(classes_a[0])}
        )
        self._as("prof_b")
        resp_b = self.client.post(
            f"{PROMPTS}/{origin_id}/assignments", json={"class_id": str(classes_b[0])}
        )
        self.assertEqual(resp_a.status_code, 201, resp_a.text)
        self.assertEqual(resp_b.status_code, 201, resp_b.text)
        self.assertNotEqual(
            resp_a.json()["essay_prompt_id"], resp_b.json()["essay_prompt_id"]
        )
        self.assertEqual(resp_a.json()["school_id"], str(school_a))
        self.assertEqual(resp_b.json()["school_id"], str(school_b))
        self.assertEqual(len(self._copies(school_a, origin_id)), 1)
        self.assertEqual(len(self._copies(school_b, origin_id)), 1)

    def test_a_normal_prompt_assignment_is_completely_unchanged(self):
        school_id, class_ids = self._seed("6", class_count=1)
        created = self.client.post(
            PROMPTS, json={"title": "Minha", "statement": "Disserte.", "year": 2026}
        )
        self.assertEqual(created.status_code, 201, created.text)
        prompt_id = created.json()["id"]

        response = self.client.post(
            f"{PROMPTS}/{prompt_id}/assignments", json={"class_id": str(class_ids[0])}
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["essay_prompt_id"], prompt_id)

    def test_a_platform_prompt_is_never_assignable_to_another_schools_class(self):
        # A copia de prof_b chega a ser criada na escola B (a materializacao
        # acontece antes da validacao da turma), mas a atribuicao e recusada:
        # o que nao pode existir em hipotese nenhuma e uma PromptAssignment
        # ligando uma proposta da escola B a uma turma da escola A. A copia
        # orfa e inofensiva - e uma proposta comum da escola B, sem turma.
        _school_a, classes_a = self._seed("7A", user="prof_a", class_count=1)
        self._seed("7B", user="prof_b", class_count=1)
        origin_id = self._seed_platform_prompt()

        self._as("prof_b")
        response = self.client.post(
            f"{PROMPTS}/{origin_id}/assignments", json={"class_id": str(classes_a[0])}
        )
        self.assertEqual(response.status_code, 422, response.text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `.venv/bin/pytest tests/test_r5_platform_essay_prompts_assignment_materialization.py -v`
Expected: FAIL — o primeiro teste devolve 403 ("This proposal is not yours."), porque hoje `_prompt_for_own_school_or_403` recebe o id de uma `PlatformEssayPrompt` e não acha `EssayPrompt` nenhum.

- [ ] **Step 3: Escrever o helper de materialização**

Em `src/agente_ia_edu/api/routes/essay_prompts.py`, logo **depois** de `_prompt_for_own_school_or_403` (que termina na linha 143), acrescente:

```python
async def _materialize_if_platform_prompt(
    session: AsyncSession,
    *,
    essay_prompt_id: uuid.UUID,
    school_id: uuid.UUID,
    created_by_external_identity: str,
) -> uuid.UUID:
    """Devolve sempre o id de um EssayPrompt REAL e escopado a esta escola.

    A lista do professor mistura propostas da escola e propostas da
    plataforma ainda nao materializadas sob o mesmo campo de id (spec s2) -
    e aqui que o backend decide qual e qual. Se o id for de uma
    PlatformEssayPrompt, busca-ou-cria a copia desta escola (spec s4) e
    devolve o id dela; caso contrario devolve o id recebido, intacto.

    Do ponto de vista de PromptAssignment pra frente nada muda: ele sempre
    aponta para um EssayPrompt escopado a escola, como sempre apontou.

    A materializacao acontece ANTES da validacao da turma, entao uma
    atribuicao que depois falhar (turma de outra escola, turma ja atribuida)
    deixa a copia criada na escola do professor. E inofensivo: a copia e uma
    proposta comum daquela escola, sem turma nenhuma, e a proxima tentativa a
    reaproveita em vez de criar outra.
    """
    service = PlatformEssayPromptService(session)
    origin = await service.get_platform_prompt(essay_prompt_id)
    if origin is None:
        return essay_prompt_id

    copy = await service.materialize_for_school(
        platform_essay_prompt_id=origin.id,
        school_id=school_id,
        created_by_external_identity=created_by_external_identity,
    )
    # Ler copy.id ANTES do commit: commit() expira o objeto
    # (expire_on_commit=True em producao) e a leitura seguinte viraria um
    # lazy-load sincrono com MissingGreenlet.
    copy_id = copy.id
    # Commit imediato, antes de qualquer atribuicao: create_assignments_bulk
    # faz rollback por turma que falha (ver o docstring dele em
    # services/essay_proposal.py) e um rollback depois deste ponto
    # descartaria a copia recem-criada enquanto as turmas seguintes do mesmo
    # lote continuariam apontando para o id dela.
    await session.commit()
    return copy_id
```

- [ ] **Step 4: Usar o helper nas duas rotas de atribuição**

Em `create_prompt_assignment`, troque o começo do corpo:

```python
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        await _prompt_for_own_school_or_403(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id
        )
        service = EssayProposalService(session)
```

por:

```python
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        essay_prompt_id = await _materialize_if_platform_prompt(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id,
            created_by_external_identity=identity.external_user_id,
        )
        await _prompt_for_own_school_or_403(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id
        )
        service = EssayProposalService(session)
```

E em `create_prompt_assignments_bulk`, troque:

```python
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        await _prompt_for_own_school_or_403(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id
        )
        service = EssayProposalService(session)
```

por:

```python
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        essay_prompt_id = await _materialize_if_platform_prompt(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id,
            created_by_external_identity=identity.external_user_id,
        )
        await _prompt_for_own_school_or_403(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id
        )
        service = EssayProposalService(session)
```

(Nos dois casos `essay_prompt_id` chega como `uuid.UUID` do path param, então a reatribuição mantém o mesmo tipo.)

- [ ] **Step 5: Rodar os testes e confirmar que passam**

Run: `.venv/bin/pytest tests/test_r5_platform_essay_prompts_assignment_materialization.py -v`
Expected: PASS (7 testes).

- [ ] **Step 6: Rodar a suíte de redação inteira, pra provar que nada regrediu**

Run: `.venv/bin/pytest tests/ -k "essay" -q`
Expected: PASS em tudo (o único SKIP aceitável é de teste Postgres se o banco 5433 estiver fora — e o desta leva, da Task 2, não pode estar entre eles).

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_prompts.py \
        tests/test_r5_platform_essay_prompts_assignment_materialization.py
git commit -m "feat(redacao): atribuicao de proposta da plataforma materializa a copia da escola

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Tela do admin — seção "Propostas de Redação da Plataforma"

> Ambiente: rode tudo de dentro do worktree, com o `.venv` **deste** worktree (ver "Ambiente de execução" no topo).

**Files:**
- Modify: `src/agente_ia_edu/web/admin.html:131` (seção nova antes de `</main>`)
- Modify: `src/agente_ia_edu/web/admin.js:18-25` (state), bloco novo antes de `// ---------- Identity ----------` (linha 508), e a inicialização no fim do arquivo (510-519)
- Test: `tests/test_r5_platform_essay_prompts_admin_frontend.js`

**Interfaces:**
- Consumes: `POST/GET /api/v1/admin/platform-essay-prompts`, `POST .../{id}/archive` e o shape `PlatformEssayPromptResponse` (Task 4).
- Produces: nada consumido por outra tarefa.

- [ ] **Step 1: Escrever o teste falhando**

Crie `tests/test_r5_platform_essay_prompts_admin_frontend.js`:

```javascript
// Contrato de frontend da secao "Propostas de Redacao da Plataforma"
// (src/agente_ia_edu/web/admin.js + admin.html) contra
// src/agente_ia_edu/api/routes/admin_essay_prompts.py.
//
// admin.js nao tem module.exports (roda inteiro dentro de um listener de
// DOMContentLoaded), entao - como os outros *_frontend.js desta suite - as
// assercoes rodam contra o texto-fonte, nao contra o codigo executado.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const js = fs.readFileSync('src/agente_ia_edu/web/admin.js', 'utf8');
const html = fs.readFileSync('src/agente_ia_edu/web/admin.html', 'utf8');

test('admin.html tem os elementos que admin.js procura por id', () => {
  for (const id of [
    'btn-new-platform-prompt',
    'platform-prompt-form',
    'platform-prompt-title',
    'platform-prompt-statement',
    'platform-prompt-cancel-btn',
    'platform-prompt-form-msg',
    'platform-prompts-list',
  ]) {
    assert.ok(html.includes(`id="${id}"`), `admin.html nao tem id="${id}"`);
  }
});

test('as chamadas usam os caminhos e os nomes de campo de admin_essay_prompts.py', () => {
  assert.match(js, /fetch\(`\$\{API\}\/platform-essay-prompts`, \{ headers: authHeaders\(\) \}\)/);
  assert.match(js, /fetch\(`\$\{API\}\/platform-essay-prompts`, \{\s*method: 'POST'/);
  assert.match(js, /fetch\(`\$\{API\}\/platform-essay-prompts\/\$\{promptId\}\/archive`, \{\s*method: 'POST'/);
  // PlatformEssayPromptCreateRequest: exatamente title + statement.
  assert.match(js, /title: \$\('platform-prompt-title'\)\.value\.trim\(\)/);
  assert.match(js, /statement: \$\('platform-prompt-statement'\)\.value\.trim\(\)/);
});

test('a listagem mostra status e a contagem de escolas que ja materializaram', () => {
  assert.match(js, /materialized_school_count/);
  assert.match(js, /PLATFORM_PROMPT_STATUS_LABELS/);
  assert.match(js, /ARCHIVED: 'Arquivada'/);
});

test('o botao arquivar so aparece em proposta ACTIVE', () => {
  // Uma proposta ja arquivada nao pode ser arquivada de novo - a linha dela
  // sai sem botao em vez de oferecer uma acao que nao faz nada.
  assert.match(js, /p\.status === 'ACTIVE'\s*\?[\s\S]{0,200}data-archive-platform-prompt/);
});

test('403 na listagem vira a mesma mensagem de acesso negado das escolas', () => {
  const platformSection = js.slice(js.indexOf('async function loadPlatformPrompts'));
  assert.match(platformSection, /res\.status === 403/);
  assert.match(platformSection, /Administrador da Plataforma/);
});

test('a secao carrega no boot e recarrega quando a identidade muda', () => {
  const boot = js.slice(js.indexOf("// ---------- Identity ----------"));
  assert.match(boot, /loadPlatformPrompts\(\);/);
  // Quatro ocorrencias no arquivo: apos arquivar, apos criar, no handler de
  // troca de identidade e no boot.
  assert.equal((js.match(/loadPlatformPrompts\(\);/g) || []).length, 4);
});

test('todo texto vindo da API passa por esc() antes de virar HTML', () => {
  const render = js.slice(
    js.indexOf('function renderPlatformPrompts'),
    js.indexOf('async function archivePlatformPrompt'),
  );
  assert.match(render, /esc\(p\.title\)/);
  assert.doesNotMatch(render, /\$\{p\.title\}/);
});
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `node --test tests/test_r5_platform_essay_prompts_admin_frontend.js`
Expected: FAIL — o primeiro teste já quebra (`admin.html nao tem id="btn-new-platform-prompt"`).

- [ ] **Step 3: Acrescentar a seção ao `admin.html`**

Em `src/agente_ia_edu/web/admin.html`, logo **depois** do `</section>` que fecha `#school-detail` (linha 131) e **antes** de `</main>` (linha 132), insira:

```html
      <!-- ================= PLATFORM ESSAY PROMPTS ================= -->
      <section class="card">
        <div class="card-header">
          <h3>✍️ Propostas de Redação da Plataforma</h3>
          <button class="btn btn-primary" id="btn-new-platform-prompt" type="button">+ Nova proposta</button>
        </div>
        <p class="empty-text">Visíveis para professores de todas as escolas. Cada escola recebe uma cópia própria na primeira vez que atribui a proposta a uma turma — arquivar não afeta nenhuma cópia já criada.</p>

        <form id="platform-prompt-form" class="admin-form" hidden>
          <div class="admin-form-row">
            <div class="form-group admin-form-row-grow"><label for="platform-prompt-title">Título *</label><input id="platform-prompt-title" class="text-input" placeholder="Os desafios da mobilidade urbana no Brasil" maxlength="255" required></div>
          </div>
          <div class="admin-form-row">
            <div class="form-group admin-form-row-grow"><label for="platform-prompt-statement">Enunciado *</label><textarea id="platform-prompt-statement" class="textarea-input" rows="6" placeholder="A partir da leitura dos textos motivadores, redija um texto dissertativo-argumentativo…" required></textarea></div>
          </div>
          <div class="admin-form-actions">
            <button type="submit" class="btn btn-primary">Criar proposta</button>
            <button type="button" class="btn btn-secondary" id="platform-prompt-cancel-btn">Cancelar</button>
          </div>
          <p id="platform-prompt-form-msg" class="admin-msg" hidden></p>
        </form>

        <div id="platform-prompts-list" class="admin-table-wrap"><p class="empty-text">Carregando propostas…</p></div>
      </section>
```

- [ ] **Step 4: Acrescentar o bloco ao `admin.js`**

Em `src/agente_ia_edu/web/admin.js`, no objeto `state` (linhas 18-25), acrescente uma chave depois de `schoolUniverseScopes: []`:

```js
    platformPrompts: [],        // propostas de redacao da plataforma (cross-escola)
```

Depois, **antes** do comentário `// ---------- Identity ----------` (linha 508), insira o bloco inteiro:

```js
  // ---------- Platform essay prompts (propostas de redação cross-escola) ----------
  // Backed by /api/v1/admin/platform-essay-prompts (PLATFORM_ADMIN only, ver
  // api/routes/admin_essay_prompts.py). Uma proposta criada aqui fica visível
  // a professores de TODAS as escolas; a cópia por escola
  // (essay_prompts.materialized_from_platform_prompt_id) é criada pelo backend
  // na primeira atribuição daquela escola, e é isso que
  // materialized_school_count conta. Nunca há edição: arquivar e cadastrar
  // outra é o caminho.

  const PLATFORM_PROMPT_STATUS_LABELS = { ACTIVE: 'Ativa', ARCHIVED: 'Arquivada' };

  async function loadPlatformPrompts() {
    const container = $('platform-prompts-list');
    try {
      const res = await fetch(`${API}/platform-essay-prompts`, { headers: authHeaders() });
      if (res.status === 403) {
        container.innerHTML = '<p class="empty-text">Acesso negado — este usuário não tem papel de Administrador da Plataforma.</p>';
        return;
      }
      if (!res.ok) throw new Error(await errorDetail(res));
      state.platformPrompts = await res.json();
      renderPlatformPrompts();
    } catch (err) {
      container.innerHTML = '<p class="empty-text">Não foi possível carregar as propostas da plataforma.</p>';
    }
  }

  function renderPlatformPrompts() {
    const container = $('platform-prompts-list');
    if (!state.platformPrompts.length) {
      container.innerHTML = '<p class="empty-text">Nenhuma proposta da plataforma cadastrada ainda.</p>';
      return;
    }
    container.innerHTML = `
      <table class="data-table">
        <thead><tr><th>Título</th><th>Enunciado</th><th>Status</th><th>Escolas que já usaram</th><th>Criada em</th><th></th></tr></thead>
        <tbody>
          ${state.platformPrompts.map((p) => `
            <tr>
              <td><strong>${esc(p.title)}</strong></td>
              <td>${esc(String(p.statement || '').slice(0, 120))}${String(p.statement || '').length > 120 ? '…' : ''}</td>
              <td>${esc(PLATFORM_PROMPT_STATUS_LABELS[p.status] || p.status)}</td>
              <td>${p.materialized_school_count}</td>
              <td>${formatDate(p.created_at)}</td>
              <td>${p.status === 'ACTIVE'
                ? `<button class="btn btn-secondary" type="button" data-archive-platform-prompt="${esc(p.id)}">Arquivar</button>`
                : ''}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>`;
    container.querySelectorAll('[data-archive-platform-prompt]').forEach((btn) => {
      btn.addEventListener('click', () => archivePlatformPrompt(btn.dataset.archivePlatformPrompt));
    });
  }

  async function archivePlatformPrompt(promptId) {
    const prompt = state.platformPrompts.find((p) => p.id === promptId);
    const title = prompt ? prompt.title : 'esta proposta';
    if (!confirm(`Arquivar "${title}"? Ela deixa de aparecer para escolas que ainda não a usaram. As cópias já criadas continuam funcionando normalmente.`)) return;
    try {
      const res = await fetch(`${API}/platform-essay-prompts/${promptId}/archive`, {
        method: 'POST', headers: authHeaders(),
      });
      if (!res.ok) throw new Error(await errorDetail(res));
      showAlert('✅ Proposta arquivada.', 'success');
      loadPlatformPrompts();
    } catch (err) {
      showAlert(`Não foi possível arquivar a proposta: ${err.message}`, 'error');
    }
  }

  $('btn-new-platform-prompt').addEventListener('click', () => {
    $('platform-prompt-form').hidden = false;
    formMsg('platform-prompt-form-msg', '');
  });
  $('platform-prompt-cancel-btn').addEventListener('click', () => {
    $('platform-prompt-form').hidden = true;
    $('platform-prompt-form').reset();
  });
  $('platform-prompt-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    formMsg('platform-prompt-form-msg', '');
    const body = {
      title: $('platform-prompt-title').value.trim(),
      statement: $('platform-prompt-statement').value.trim(),
    };
    try {
      const res = await fetch(`${API}/platform-essay-prompts`, {
        method: 'POST', headers: authHeaders(), body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await errorDetail(res));
      $('platform-prompt-form').hidden = true;
      $('platform-prompt-form').reset();
      showAlert(`✅ Proposta "${body.title}" publicada para todas as escolas.`, 'success');
      loadPlatformPrompts();
    } catch (err) {
      formMsg('platform-prompt-form-msg', `Não foi possível criar a proposta: ${err.message}`, false);
    }
  });
```

Por fim, no bloco de identidade (fim do arquivo), acrescente a recarga: troque

```js
  $('admin-identity-id').addEventListener('change', (e) => {
    state.identity = e.target.value.trim() || 'user:ADMIN';
    $('school-detail').hidden = true;
    state.selectedSchool = null;
    state.schoolUniverse = null;
    state.schoolUniverseScopes = [];
    loadSchools();
  });

  loadSchools();
});
```

por

```js
  $('admin-identity-id').addEventListener('change', (e) => {
    state.identity = e.target.value.trim() || 'user:ADMIN';
    $('school-detail').hidden = true;
    state.selectedSchool = null;
    state.schoolUniverse = null;
    state.schoolUniverseScopes = [];
    loadSchools();
    loadPlatformPrompts();
  });

  loadSchools();
  loadPlatformPrompts();
});
```

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `node --test tests/test_r5_platform_essay_prompts_admin_frontend.js`
Expected: PASS (7 testes).

- [ ] **Step 6: Rodar o teste de frontend do admin que já existia**

Run: `node --test tests/test_admin_frontend.js`
Expected: PASS — a seção nova não pode ter mexido em nenhum contrato antigo.

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/web/admin.html src/agente_ia_edu/web/admin.js \
        tests/test_r5_platform_essay_prompts_admin_frontend.js
git commit -m "feat(redacao): tela do admin para propostas de redacao da plataforma

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Tela do professor — selo "Plataforma" e controles somente-leitura

> Ambiente: rode tudo de dentro do worktree, com o `.venv` **deste** worktree (ver "Ambiente de execução" no topo).

**Files:**
- Modify: `src/agente_ia_edu/web/essay-review.js` — `renderPromptsList` (linhas 115-166) e `renderPromptDetail` (441-520)
- Modify: `src/agente_ia_edu/web/teacher.css:326` (regra nova do selo, ao lado de `.essay-review-tabs`)
- Test: `tests/test_r5_platform_essay_prompts_teacher_frontend.js`

**Interfaces:**
- Consumes: `is_platform`, `platform_prompt_id` e `materialized` de `EssayPromptResponse`/`EssayPromptDetailResponse` (Task 6); a rota de atribuição que materializa (Task 7).
- Produces: nada consumido por outra tarefa.

- [ ] **Step 1: Escrever o teste falhando**

Crie `tests/test_r5_platform_essay_prompts_teacher_frontend.js`:

```javascript
// Contrato de frontend do selo "Plataforma" na tela de propostas do
// professor (src/agente_ia_edu/web/essay-review.js) contra os campos
// is_platform / platform_prompt_id / materialized que
// api/routes/essay_prompts.py passou a devolver.
//
// essay-review.js roda dentro de um IIFE sem module.exports, entao - como os
// outros *_frontend.js desta suite - as assercoes rodam contra o texto-fonte.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const js = fs.readFileSync('src/agente_ia_edu/web/essay-review.js', 'utf8');
const css = fs.readFileSync('src/agente_ia_edu/web/teacher.css', 'utf8');

test('a lista estampa o selo Plataforma nos itens com is_platform', () => {
  const list = js.slice(
    js.indexOf('async function renderPromptsList'),
    js.indexOf('async function renderTrashTab'),
  );
  assert.match(list, /p\.is_platform/);
  assert.match(list, /er-platform-badge/);
  assert.match(list, /Plataforma/);
});

test('o botao de lixeira nunca aparece numa proposta da plataforma', () => {
  // Decisao 3 da spec: o professor nunca edita nem exclui uma proposta da
  // plataforma - so usa.
  const list = js.slice(
    js.indexOf('async function renderPromptsList'),
    js.indexOf('async function renderTrashTab'),
  );
  assert.match(list, /p\.is_platform\s*\?\s*''\s*:\s*`<button[^`]*data-delete-prompt/);
});

test('o detalhe esconde o formulario de material quando a proposta e da plataforma', () => {
  const detail = js.slice(js.indexOf('async function renderPromptDetail'));
  assert.match(detail, /const isPlatform = !!detail\.is_platform;/);
  assert.match(detail, /#er-material-form'\)\.hidden = true/);
});

test('o detalhe esconde a folha de resposta enquanto a copia nao existe', () => {
  // answer-sheet.pdf exige um EssayPrompt real na escola; numa
  // pre-visualizacao (materialized === false) ele daria 403.
  const detail = js.slice(js.indexOf('async function renderPromptDetail'));
  assert.match(detail, /detail\.materialized === false/);
  assert.match(detail, /#er-sheet-actions/);
  assert.match(js, /id="er-sheet-actions"/);
});

test('o formulario de atribuicao continua postando com o id recebido na lista', () => {
  // E esse id (que pode ser o de uma platform_essay_prompts) que o backend
  // resolve e materializa - o frontend nao trata isso.
  assert.match(js, /\/api\/v1\/catalog\/essay-prompts\/\$\{promptId\}\/assignments\/bulk/);
});

test('teacher.css tem a regra do selo', () => {
  assert.match(css, /\.er-platform-badge\s*\{/);
});
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `node --test tests/test_r5_platform_essay_prompts_teacher_frontend.js`
Expected: FAIL nos seis testes (nada de `er-platform-badge` / `isPlatform` / `er-sheet-actions` existe ainda).

- [ ] **Step 3: Estampar o selo e esconder a lixeira na lista**

Em `src/agente_ia_edu/web/essay-review.js`, dentro de `renderPromptsList`, troque o bloco de `<tbody>`:

```js
          <tbody id="er-prompts-body">
            ${prompts.map((p) => `
              <tr>
                <td>${tmEsc(p.title)}</td><td>${p.year}</td><td>${tmEsc(statusLabel(p.status))}</td>
                <td>
                  <button class="btn btn-secondary" type="button" data-open-prompt="${tmEsc(p.id)}">Abrir</button>
                  <button class="btn btn-secondary" type="button" data-delete-prompt="${tmEsc(p.id)}" title="Mover para a lixeira">🗑️</button>
                </td>
              </tr>`).join('') || '<tr><td colspan="4" class="empty-text">Nenhuma proposta criada ainda.</td></tr>'}
          </tbody>
```

por:

```js
          <tbody id="er-prompts-body">
            ${prompts.map((p) => `
              <tr>
                <td>${tmEsc(p.title)}${p.is_platform ? ' <span class="er-platform-badge">Plataforma</span>' : ''}</td>
                <td>${p.year}</td><td>${tmEsc(statusLabel(p.status))}</td>
                <td>
                  <button class="btn btn-secondary" type="button" data-open-prompt="${tmEsc(p.id)}">Abrir</button>
                  ${p.is_platform ? '' : `<button class="btn btn-secondary" type="button" data-delete-prompt="${tmEsc(p.id)}" title="Mover para a lixeira">🗑️</button>`}
                </td>
              </tr>`).join('') || '<tr><td colspan="4" class="empty-text">Nenhuma proposta criada ainda.</td></tr>'}
          </tbody>
```

- [ ] **Step 4: Dar um id ao bloco da folha de resposta**

Ainda em `essay-review.js`, dentro do template de `renderPromptDetail`, troque:

```js
        <div class="tm-form-actions" style="margin: 8px 0;">
          <label for="er-sheet-copies" style="margin-right:6px;">Cópias</label>
```

por:

```js
        <div class="tm-form-actions" id="er-sheet-actions" style="margin: 8px 0;">
          <label for="er-sheet-copies" style="margin-right:6px;">Cópias</label>
```

- [ ] **Step 5: Esconder os controles de edição no detalhe**

Ainda em `renderPromptDetail`, logo **depois** da linha que insere o detalhe no DOM:

```js
    tabsEl.insertAdjacentElement('afterend', detailHtml.firstElementChild);
```

acrescente:

```js
    // Proposta da plataforma é somente-leitura pro professor (spec, decisão
    // 3): ele só atribui a turmas, nunca edita nem adiciona material. Os
    // blocos continuam no DOM (os listeners abaixo os procuram) e só são
    // escondidos - o backend recusaria essas chamadas de qualquer forma.
    const isPlatform = !!detail.is_platform;
    if (isPlatform) {
      container.querySelector('#er-material-form').hidden = true;
    }
    if (detail.materialized === false) {
      // Ainda não existe EssayPrompt nenhum nesta escola - a folha de
      // resposta (answer-sheet.pdf) só passa a fazer sentido depois da
      // primeira atribuição, que é o que materializa a cópia.
      container.querySelector('#er-sheet-actions').hidden = true;
    }
```

- [ ] **Step 6: Acrescentar a regra do selo ao `teacher.css`**

Em `src/agente_ia_edu/web/teacher.css`, logo depois da linha 326 (`.essay-review-tabs { ... }`), acrescente:

```css
/* Selo das propostas de redação da plataforma (cadastradas pelo admin e
   visíveis a todas as escolas) na lista de propostas do professor. */
.er-platform-badge {
  display: inline-block;
  margin-left: 6px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: #1a4fa0;
  background: #e6efff;
  vertical-align: middle;
}
```

- [ ] **Step 7: Rodar o teste e confirmar que passa**

Run: `node --test tests/test_r5_platform_essay_prompts_teacher_frontend.js`
Expected: PASS (6 testes).

- [ ] **Step 8: Rodar a suíte inteira antes de fechar a leva**

Run: `.venv/bin/pytest tests/ -q` e depois `for f in tests/*_frontend.js; do node --test "$f"; done`
Expected: PASS em tudo. O único SKIP aceitável é de teste Postgres com o banco 5433 fora — e, se o banco estiver no ar, `tests/test_r5_platform_essay_prompts_migration_postgresql.py` **tem** que rodar de verdade.

- [ ] **Step 9: Commit**

```bash
git add src/agente_ia_edu/web/essay-review.js src/agente_ia_edu/web/teacher.css \
        tests/test_r5_platform_essay_prompts_teacher_frontend.js
git commit -m "feat(redacao): selo Plataforma e controles somente-leitura na lista do professor

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Cobertura da spec (auto-revisão)

| Requisito da spec | Onde é implementado |
|---|---|
| §2 tabela `platform_essay_prompts` | Task 1 (modelo), Task 2 (migration) |
| §2 coluna `materialized_from_platform_prompt_id` + UNIQUE | Task 1, Task 2 |
| §2 `POST /admin/platform-essay-prompts` | Task 4 |
| §2 `GET /admin/platform-essay-prompts` | Task 4 |
| §2 `POST /admin/platform-essay-prompts/{id}/archive` | Task 3 (serviço) + Task 4 (rota) |
| §2 listagem do professor com `is_platform` + `platform_prompt_id` | Task 6 |
| §2 atribuição materializa-ou-reaproveita | Task 5 (serviço) + Task 7 (rotas individual e em lote) |
| §2 tela do admin (lista, formulário, contagem, arquivar) | Task 8 |
| §2 selo "Plataforma" na lista do professor | Task 9 |
| §3 `ON DELETE RESTRICT` na FK de proveniência | Task 2 (migration + asserção no teste Postgres) |
| §4 fluxo de materialização (buscar → reaproveitar → criar) | Task 5 |
| §5 admin: criar/listar/arquivar; arquivar não afeta cópias | Task 3 e Task 4 |
| §5 admin: 403 para TEACHER/DIRECTOR/COORDINATOR | Task 4 |
| §5 listagem: ACTIVE aparece, ARCHIVED não, materializada aparece uma vez | Task 6 |
| §5 materialização: duas turmas reaproveitam a mesma linha | Task 7 |
| §5 isolamento entre escolas: cópias independentes | Task 5 (serviço) e Task 7 (rotas) |
| §5 constraint única em banco real (corrida) | Task 2 |
| §6 nenhuma FK composta existente muda | Task 2 (o teste assere que `uq_essay_prompts_school_id_id` continua igual) |
| §6 `platform_essay_prompts` não é referenciada por tabela escopada | Garantido pelo desenho: a única FK para ela é `essay_prompts.materialized_from_platform_prompt_id` (Task 1/2) |
| §6 rotas de admin exigem `PLATFORM_ADMIN` | Task 4 |
| Não-entrega: edição da proposta pelo admin | Nenhuma rota `PATCH`/`PUT` em nenhuma tarefa |
| Não-entrega: edição da cópia pelo professor | Task 9 esconde os controles; o backend já recusaria |
| Não-entrega: notificação às escolas | Nenhuma tarefa |
| Não-entrega: filtro/tag temática | Nenhuma tarefa |
| Não-entrega: métricas além da contagem | Só `materialized_school_count` (Tasks 3/4/8) |

**Ponto que a spec não cobria e o plano fecha:** a rota de detalhe do professor (Task 6, Step 6) — sem ela o professor nunca chegaria ao formulário de atribuição de uma proposta da plataforma, porque a tela lista → abre o detalhe → atribui, e o detalhe daria 403.
