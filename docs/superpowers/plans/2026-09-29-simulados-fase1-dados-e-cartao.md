# Simulados — Fase 1: modelo de dados + gerador de cartão-resposta — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar o modelo de dados completo do subsistema de correção de simulados (migrations 057–059) e um gerador de cartão-resposta em ReportLab que emite, no mesmo ato, o PDF nominal por aluno e o template geométrico normalizado das bolhas.

**Architecture:** Três módulos de modelo SQLAlchemy separados por responsabilidade (prova/gabarito, cartão/digitalização, TRI), cada um com a sua própria migration aditiva. O gerador de cartão vive em um pacote novo `agente_ia_edu.simulado_card`, dividido em geometria pura (stdlib apenas) e renderização (ReportLab, importado tardiamente), de modo que o template — o artefato de que o leitor óptico da Fase 4 vai depender — possa ser construído e testado sem nenhuma dependência gráfica. Os quatro marcadores ArUco são PNGs pré-gerados e versionados no repositório; nada os gera em tempo de execução.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x (async), Alembic, PostgreSQL, PyYAML, ReportLab (extra opcional), pypdf (já dependência), pytest/unittest.

**Spec:** `docs/superpowers/specs/2026-09-29-correcao-simulados-tri-design.md`

## Global Constraints

Cada uma destas foi conferida no repositório; o caminho citado é a evidência.

- **Python `>=3.13,<3.14`** — `pyproject.toml:8`.
- **SQLAlchemy `>=2.0,<3.0`, estilo async** — `pyproject.toml:10`; sessões via `sqlalchemy.ext.asyncio` (`AsyncSession`, `async_sessionmaker`), como em `tests/test_material_assignment_model.py:35`.
- **Alembic** é dependência de `dev`, não do runtime — `pyproject.toml:26`. `alembic.ini` usa `script_location = migrations`, `prepend_sys_path = .`.
- **pytest com `pythonpath = ["src", "."]`** e `testpaths = ["tests"]` — `pyproject.toml:46-47`. Os testes são `unittest.TestCase` executados pelo pytest (convenção do repositório inteiro).
- **O core NUNCA importa `cv2`, OpenCV ou Pillow.** `pyproject.toml:33-35` proíbe explicitamente Pillow: *"Do NOT add pdfplumber here: it pulls in Pillow, which changes pypdf's page.images behaviour and breaks the parser."* `tests/test_authorial_material_parser_coverage.py:160-174` documenta que, sem Pillow, `page.images` do pypdf levanta `ImportError` e o parser autoral engole isso de propósito — instalar Pillow muda esse comportamento.
- **ReportLab nunca entra no `site-packages` do core** (spec §2.1 e §2.3). `reportlab` 5.0.1 declara `requires_dist: ['pillow>=9.0.0', 'charset-normalizer']` (conferido em 2026-09-29 contra `https://pypi.org/pypi/reportlab/json`), e `src/agente_ia_edu/services/ingestion_parser.py:371` chama `list(page.images)`, cujo comportamento o pypdf muda conforme Pillow estar **instalado no ambiente** — não conforme quem o importou. Import tardio e extra opcional **não protegem**: bastaria alguém instalar o extra. Por isso o renderizador vive no **ambiente isolado compartilhado com o `omr_worker`** (`workers/simulado/`), com declaração de dependência própria. Task 7 escreve o teste-sentinela que prova que o core não consegue importar nem `reportlab` nem `PIL`.
- **A geometria é do core; só a renderização é do ambiente isolado** (spec §2.1). `template.py`, `layout.py` e `markers.py` são stdlib + PyYAML e ficam em `src/agente_ia_edu/simulado_card/`, para que o template seja legível dos dois lados da fronteira. `renderer.py` é o único arquivo com ReportLab, e vive fora do core.
- **Nenhuma biblioteca de QR.** O QR sai do `QrCodeWidget` vetorial que o próprio ReportLab já traz (spec §2.1). Zero dependência adicional no ambiente isolado.
- **Isolamento multi-tenant (spec §3.0):** *toda* tabela deste subsistema carrega `school_id`, e *toda* FK entre elas é **composta** — `(school_id, parent_id)` referenciando `parent(school_id, id)`, viabilizada por `UNIQUE(school_id, id)` em cada pai. É exatamente a regra que `src/agente_ia_edu/db/models/academic.py` documenta (docstring do módulo, linhas 20-33) e aplica; `Class` (`academic.py:290-317`) é o modelo a copiar, incluindo os nomes de constraint `fk_<tabela>_school_<pai>` e `uq_<tabela>_school_id_id`. Uma `school_id` acrescentada por essa regra **não** ganha FK própria para `schools`: ela já está presa a um pai cuja `school_id` aponta para lá. Só as raízes do subsistema (`mock_exams`, `tri_scales`) têm FK direta para `schools.id`.
- **Os marcadores ArUco são PNGs pré-gerados e versionados** em `src/agente_ia_edu/simulado_card/assets/aruco/`. `opencv-contrib-python` é usado **uma única vez**, num virtualenv descartável, pelo script gerador — e nunca entra no `pyproject.toml`.
- **Convenção de modelo:** `uuid.UUID` com `mapped_column(Uuid, primary_key=True, default=uuid.uuid4)`; `DateTime(timezone=True)` com `default=_utcnow`; `CheckConstraint`/`Index` nomeados em `__table_args__`; JSON via `agente_ia_edu.db.types.JSONBCompatible`. Referências: `src/agente_ia_edu/db/models/assessments.py:44-80` e `src/agente_ia_edu/db/models/academic.py:345-378`.
- **Convenção de migration:** `revision = "NNN_nome"`, `down_revision = "<anterior>"`, docstring explicando *por que* a tabela existe, `upgrade()`/`downgrade()` simétricos. Referência: `migrations/versions/055_essay_prompt_soft_delete.py` e `migrations/versions/056_material_assignments.py`.
- **A migration 056 (`056_material_assignments`) já existe como arquivo não commitado. NÃO a modifique.** A cadeia nova começa em `057`, com `down_revision = "056_material_assignments"`.
- **Convenção de teste de modelo:** banco PostgreSQL real e descartável na porta 5433, via `tests/_postgres_test_db.py` (`create_database`/`drop_database`), `Base.metadata.create_all`, e `raise unittest.SkipTest("PostgreSQL de teste indisponivel")` quando o servidor não responde. Referência integral: `tests/test_material_assignment_model.py:56-100`.
- **Storage:** reusar `MaterialStorage` (`src/agente_ia_edu/services/material_storage.py:31-48`), endereçado por conteúdo. Nenhum storage novo.
- **Fora do escopo desta fase:** leitura óptica (`cv2`), motor de TRI, relatórios e rotas HTTP de portal. As tabelas de TRI e de leitura são criadas porque o modelo de dados é entregue inteiro na Fase 1 (spec §9), mas nenhuma lógica que as consome é escrita aqui.

---

## File Structure

**Modelos (um módulo por responsabilidade — 12 tabelas em um arquivo só seria ilegível):**

| Arquivo | Responsabilidade | Tabelas |
|---|---|---|
| `src/agente_ia_edu/db/models/mock_exam.py` (criar) | A prova, o gabarito e a matriz de respostas | `mock_exams`, `mock_exam_areas`, `mock_exam_items`, `mock_exam_responses` |
| `src/agente_ia_edu/db/models/answer_card.py` (criar) | O papel: cartão emitido, imagem capturada, marcas lidas, fila do worker | `answer_cards`, `answer_card_scans`, `answer_card_marks`, `omr_jobs` |
| `src/agente_ia_edu/db/models/tri.py` (criar) | A régua e os resultados estatísticos | `tri_scales`, `tri_calibrations`, `tri_item_parameters`, `tri_student_scores` |
| `src/agente_ia_edu/db/models/__init__.py` (modificar) | Re-export, como todos os outros módulos de modelo | — |

**Migrations:** `migrations/versions/057_mock_exam_core.py`, `058_answer_card_capture.py`, `059_tri_scales_and_scores.py` (criar; cadeia `056 → 057 → 058 → 059`).

**Geometria do cartão — fica NO CORE (pacote novo `src/agente_ia_edu/simulado_card/`), stdlib + PyYAML:**

| Arquivo | Responsabilidade |
|---|---|
| `__init__.py` | API pública do pacote |
| `markers.py` | Carregar e validar o manifesto dos ArUco; leitor de cabeçalho PNG em stdlib. **Sem ReportLab, sem Pillow, sem cv2.** |
| `template.py` | Dataclasses do template geométrico + serialização JSON + `LAYOUT_VERSION`. **Declaração única do contrato**: o leitor óptico da Fase 4 importa daqui, não redeclara (spec §2.1). **Idem: sem ReportLab, sem Pillow, sem cv2** — é o que torna esse import seguro dos dois lados. |
| `layout.py` | `CardLayoutSpec` + `build_template(spec)` — a geometria pura, em milímetros, normalizada pelos centros dos marcadores. **Idem.** |
| `assets/aruco/*.png` + `assets/aruco/markers.yaml` | Os quatro marcadores pré-gerados e o seu manifesto. |

**Renderização — fica FORA do core, no ambiente isolado compartilhado com o `omr_worker` (spec §2.1 e §2.3):**

| Arquivo | Responsabilidade |
|---|---|
| `workers/simulado/pyproject.toml` (criar) | Declaração de dependência própria: `agente-ia-edu-core` (pela geometria) + `reportlab`. |
| `workers/simulado/src/simulado_card_renderer/__init__.py` (criar) | API pública do renderizador |
| `workers/simulado/src/simulado_card_renderer/renderer.py` (criar) | O único arquivo do repositório que importa ReportLab |
| `workers/simulado/src/simulado_card_renderer/cli.py` (criar) | `python -m simulado_card_renderer` — lê o manifesto de impressão e o template, escreve o PDF |
| `workers/simulado/tests/test_renderer.py` (criar) | Testes do PDF, rodados dentro do container/venv isolado |
| `docker/simulado-worker/Dockerfile` (criar) | Imagem do ambiente isolado, ao lado de `docker/postgres/` |
| `docker-compose.yml` (modificar) | Novo serviço `simulado-worker`, ao lado de `postgres` e `n8n` |

**Régua de referência:** `src/agente_ia_edu/data/__init__.py`, `src/agente_ia_edu/data/enem_reference_scales.yaml`, `src/agente_ia_edu/data/reference_scales.py` (padrão de `agente_ia_edu.rubrics`: YAML como package-data + loader que recusa arquivo estruturalmente inválido — `src/agente_ia_edu/rubrics/loader.py:80-86`).

**Serviço (core):** `src/agente_ia_edu/services/answer_card_issuing.py` — emite os tokens opacos, grava as linhas `answer_cards`, e guarda **no mesmo ato** o template geométrico e o manifesto de impressão via `MaterialStorage`. Não renderiza PDF: isso é do ambiente isolado, que lê exatamente o template gravado aqui.

**Scripts (ferramentas de uma vez só, sem teste próprio):** `scripts/generate_aruco_markers.py`, `scripts/compute_enem_reference_medians.py`.

**Testes do core:** `tests/test_mock_exam_models.py`, `tests/test_answer_card_models.py`, `tests/test_tri_models.py`, `tests/test_enem_reference_scales.py`, `tests/test_simulado_card_markers.py`, `tests/test_simulado_card_layout.py`, `tests/test_core_graphics_boundary.py`, `tests/test_answer_card_issuing.py`.

---

### Task 1: Prova, gabarito, auditoria de status e matriz de respostas (migration 057)

**Files:**
- Create: `src/agente_ia_edu/db/models/mock_exam.py`
- Create: `migrations/versions/057_mock_exam_core.py`
- Modify: `src/agente_ia_edu/db/models/__init__.py` (bloco de imports no topo e lista `__all__` no fim)
- Test: `tests/test_mock_exam_models.py`

**Interfaces:**
- Consumes: `agente_ia_edu.db.base.Base`, `agente_ia_edu.db.types.JSONBCompatible`, tabelas já existentes `schools.id`, `academic_years.id`, `students.id`.
- Produces: `MockExam`, `MockExamWorkflowAudit`, `MockExamArea`, `MockExamItem`, `MockExamResponse` — todos importáveis de `agente_ia_edu.db.models`. Constantes `MOCK_EXAM_STATUSES: tuple[str, ...]`, `OPTION_CODES: tuple[str, ...] = ("A", "B", "C", "D", "E")` e `RESPONSE_SOURCES: tuple[str, ...] = ("MANUAL", "OMR")`. Os cinco carregam `school_id: uuid.UUID` obrigatório (spec §3.0); `mock_exams` e `mock_exam_items` ganham `UNIQUE(school_id, id)` e são os pais das FKs compostas das tasks seguintes. `MockExam.academic_year_id` é **obrigatório**, e `MockExam` tem `created_at` e `updated_at`. `MockExamResponse` exige `source` e amarra `entered_by_external_id: str | None` (String(255)) a ele por CHECK. O ator da auditoria é `MockExamWorkflowAudit.actor_external_id: str | None` (String(255)) — identificador **externo**, como `AssessmentWorkflowAudit.performed_by_external_id` (`db/models/assessments.py:200`), nunca FK para `users`. `occurred_at`, `created_at` e `updated_at` têm `default`/`onupdate` no modelo: as Fases 2 e 4 nunca escrevem essas colunas à mão. Migration head passa a ser `057_mock_exam_core`.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_mock_exam_models.py`:

```python
"""Simulados Fase 1, Task 1 - modelo da prova e do gabarito (spec s3,
"Prova e gabarito" de docs/superpowers/specs/2026-09-29-correcao-simulados-tri-design.md).

Roda contra um PostgreSQL real e descartavel na porta 5433 (mesma convencao de
tests/test_material_assignment_model.py): os CheckConstraints de status, de
alternativa correta e de exam_day so sao exercidos de verdade por um banco que
os aplica - SQLite aceitaria linha invalida em varios deles. As chaves
estrangeiras COMPOSTAS do isolamento multi-tenant (spec s3.0), entao, so sao
verificaveis num banco que as aplique - e e o teste
test_refuses_a_response_that_crosses_schools que prova que elas funcionam.
"""

from __future__ import annotations

import asyncio
import os
import unittest

from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    MockExam,
    MockExamArea,
    MockExamItem,
    MockExamResponse,
    MockExamWorkflowAudit,
    Person,
    School,
    Student,
)
from tests._postgres_test_db import create_database, drop_database


class MockExamModelPostgreSQLTests(unittest.TestCase):
    database_name = "agente_ia_edu_mock_exam_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = os.getenv(
        "MOCK_EXAM_TEST_ADMIN_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres",
    )
    async_database_url = os.getenv(
        "MOCK_EXAM_TEST_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            cls._admin_execute("SELECT 1")
        except Exception as exc:
            raise unittest.SkipTest("PostgreSQL de teste indisponivel") from exc
        cls._drop_database()
        create_database(cls.admin_url, cls.database_name)
        cls.engine = create_async_engine(cls.async_database_url)
        cls.session_factory = async_sessionmaker(
            cls.engine, class_=AsyncSession, expire_on_commit=False
        )

        async def _prep():
            async with cls.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            return await cls._seed_school()

        cls.school_id, cls.academic_year_id, cls.student_id = asyncio.run(_prep())

    # Identificador externo do operador, como a camada HTTP o entrega - nunca
    # um UUID resolvido contra `users` (ver o docstring do modelo).
    operator_external_id = "secretaria:operador-digitacao"


    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "engine"):
            asyncio.run(cls.engine.dispose())
        cls._drop_database()

    @classmethod
    def _admin_execute(cls, statement):
        engine = create_engine(
            cls.admin_url,
            connect_args={"autocommit": True},
            execution_options={"isolation_level": "AUTOCOMMIT"},
        )
        try:
            with engine.connect() as connection:
                return connection.execute(text(statement))
        finally:
            engine.dispose()

    @classmethod
    def _drop_database(cls):
        drop_database(cls.admin_url, cls.database_name)

    @classmethod
    async def _seed_school(cls):
        async with cls.session_factory() as session:
            school = School(code="SIM-MOCK-EXAM", name="Escola Simulado")
            session.add(school)
            await session.flush()
            year = AcademicYear(school_id=school.id, year=2026, status="ACTIVE")
            person = Person(school_id=school.id, full_name="Aluna de Teste")
            session.add_all([year, person])
            await session.flush()
            student = Student(school_id=school.id, person_id=person.id)
            session.add(student)
            await session.commit()
            return school.id, year.id, student.id

    def _run(self, coro):
        return asyncio.run(coro)

    async def _new_exam(self, session, *, name: str, exam_day: int = 1) -> MockExam:
        exam = MockExam(
            school_id=self.school_id,
            academic_year_id=self.academic_year_id,
            name=name,
            exam_day=exam_day,
        )
        session.add(exam)
        await session.flush()
        return exam

    def test_exam_with_area_item_and_response_round_trips(self):
        async def _scenario():
            async with self.session_factory() as session:
                exam = await self._new_exam(session, name="Simulado ENEM 1o dia")
                area = MockExamArea(
                    school_id=self.school_id, mock_exam_id=exam.id,
                    code="LC", label="Linguagens", display_order=1
                )
                session.add(area)
                item = MockExamItem(
                    school_id=self.school_id,
                    mock_exam_id=exam.id,
                    position=1,
                    area_code="LC",
                    correct_option="C",
                )
                session.add(item)
                await session.flush()
                session.add(
                    MockExamResponse(
                        school_id=self.school_id,
                        mock_exam_id=exam.id,
                        student_id=self.student_id,
                        item_id=item.id,
                        chosen_option="C",
                        is_correct=True,
                        source="MANUAL",
                        entered_by_external_id=self.operator_external_id,
                    )
                )
                await session.commit()

                stored = await session.get(MockExam, exam.id)
                self.assertEqual(stored.status, "DRAFT")
                self.assertEqual(stored.exam_day, 1)
                # created_at/updated_at saem do default do modelo: nenhuma fase
                # seguinte escreve essas colunas a mao.
                self.assertIsNotNone(stored.created_at)
                self.assertIsNotNone(stored.updated_at)

                stored_response = (await session.execute(
                    select(MockExamResponse).where(MockExamResponse.item_id == item.id)
                )).scalar_one()
                self.assertEqual(stored_response.source, "MANUAL")
                self.assertEqual(
                    stored_response.entered_by_external_id, self.operator_external_id
                )
                self.assertIsNotNone(stored_response.created_at)

                stored_item = await session.get(MockExamItem, item.id)
                self.assertEqual(stored_item.correct_option, "C")
                self.assertFalse(stored_item.is_anchor)
                self.assertIsNone(stored_item.anchor_key)
                self.assertIsNone(stored_item.source_question_version_id)

        self._run(_scenario())

    def test_blank_response_is_allowed(self):
        async def _scenario():
            async with self.session_factory() as session:
                exam = await self._new_exam(session, name="Simulado com branco")
                item = MockExamItem(
                    school_id=self.school_id, mock_exam_id=exam.id,
                    position=1, area_code="MT", correct_option="A"
                )
                session.add(item)
                await session.flush()
                response = MockExamResponse(
                    school_id=self.school_id,
                    mock_exam_id=exam.id,
                    student_id=self.student_id,
                    item_id=item.id,
                    chosen_option=None,  # questao em branco
                    is_correct=False,
                    source="OMR",  # lido pela maquina: sem autor humano
                )
                session.add(response)
                await session.commit()
                self.assertIsNone((await session.get(MockExamResponse, response.id)).chosen_option)

        self._run(_scenario())

    def test_rejects_unknown_status(self):
        async def _scenario():
            async with self.session_factory() as session:
                exam = await self._new_exam(session, name="Simulado status invalido")
                exam.status = "CORRIGIDO"
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())

    def test_rejects_option_outside_a_to_e(self):
        async def _scenario():
            async with self.session_factory() as session:
                exam = await self._new_exam(session, name="Simulado alternativa invalida")
                session.add(
                    MockExamItem(
                        school_id=self.school_id, mock_exam_id=exam.id,
                        position=1, area_code="CN", correct_option="F"
                    )
                )
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())

    def test_rejects_exam_day_outside_one_or_two(self):
        async def _scenario():
            async with self.session_factory() as session:
                # Montado a mao, sem _new_exam: o flush tem que acontecer
                # DENTRO do assertRaises, e _new_exam ja faz flush.
                session.add(MockExam(
                    school_id=self.school_id,
                    academic_year_id=self.academic_year_id,
                    name="Simulado dia 3",
                    exam_day=3,
                ))
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())

    def test_duplicate_item_position_in_same_exam_is_rejected(self):
        async def _scenario():
            async with self.session_factory() as session:
                exam = await self._new_exam(session, name="Simulado posicao duplicada")
                session.add_all([
                    MockExamItem(school_id=self.school_id, mock_exam_id=exam.id,
                                 position=7, area_code="CH", correct_option="A"),
                    MockExamItem(school_id=self.school_id, mock_exam_id=exam.id,
                                 position=7, area_code="CH", correct_option="B"),
                ])
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())

    def test_one_response_per_student_and_item(self):
        async def _scenario():
            async with self.session_factory() as session:
                exam = await self._new_exam(session, name="Simulado resposta duplicada")
                item = MockExamItem(
                    school_id=self.school_id, mock_exam_id=exam.id,
                    position=1, area_code="LC", correct_option="B"
                )
                session.add(item)
                await session.flush()
                session.add_all([
                    MockExamResponse(school_id=self.school_id, mock_exam_id=exam.id,
                                     student_id=self.student_id, item_id=item.id,
                                     chosen_option="B", is_correct=True, source="OMR"),
                    MockExamResponse(school_id=self.school_id, mock_exam_id=exam.id,
                                     student_id=self.student_id, item_id=item.id,
                                     chosen_option="A", is_correct=False, source="OMR"),
                ])
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())

    def test_refuses_a_response_that_crosses_schools(self):
        """O teste do isolamento multi-tenant (spec s3.0).

        Uma resposta cujo aluno e da escola B e cujo item e da escola A nao
        pode existir. Sem a FK composta, nenhuma das duas FKs simples teria o
        que reclamar - e o join errado viraria nota de aluno em silencio.
        """
        async def _scenario():
            # Segunda escola, com o seu proprio aluno.
            async with self.session_factory() as session:
                other_school = School(code="SIM-MOCK-EXAM-B", name="Escola Vizinha")
                session.add(other_school)
                await session.flush()
                other_person = Person(school_id=other_school.id, full_name="Aluno Vizinho")
                session.add(other_person)
                await session.flush()
                other_student = Student(
                    school_id=other_school.id, person_id=other_person.id
                )
                session.add(other_student)
                await session.commit()
                other_student_id = other_student.id

            async with self.session_factory() as session:
                exam = await self._new_exam(session, name="Simulado vazamento")
                item = MockExamItem(
                    school_id=self.school_id, mock_exam_id=exam.id,
                    position=1, area_code="LC", correct_option="A"
                )
                session.add(item)
                await session.flush()
                session.add(MockExamResponse(
                    school_id=self.school_id,      # escola A
                    mock_exam_id=exam.id,          # escola A
                    item_id=item.id,               # escola A
                    student_id=other_student_id,   # escola B - nao existe em (A, id)
                    chosen_option="A", is_correct=True, source="OMR",
                ))
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())

    def test_requires_an_academic_year(self):
        """Spec s3: ano letivo obrigatorio.

        Toda resolucao de aluno e turma passa por
        StudentEnrollment -> Class.academic_year_id. Um simulado sem ano nao
        levanta erro nenhum na leitura - so devolve roster vazio e boletim
        vazio, que e o erro mais caro de achar.
        """
        async def _scenario():
            async with self.session_factory() as session:
                session.add(MockExam(
                    school_id=self.school_id,
                    academic_year_id=None,
                    name="Simulado sem ano letivo",
                    exam_day=1,
                ))
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())

    def test_manual_response_requires_an_author_and_omr_refuses_one(self):
        """Spec s3: a procedencia nao e opcional.

        Sem isso, uma resposta digitada a mao fica indistinguivel de uma lida
        pelo scanner, e nao ha como auditar quem digitou o cartao de um aluno
        quando a nota for contestada.
        """
        async def _scenario():
            async with self.session_factory() as session:
                exam = await self._new_exam(session, name="Simulado procedencia")
                item = MockExamItem(
                    school_id=self.school_id, mock_exam_id=exam.id,
                    position=1, area_code="MT", correct_option="A"
                )
                session.add(item)
                await session.flush()
                session.add(MockExamResponse(
                    school_id=self.school_id, mock_exam_id=exam.id,
                    student_id=self.student_id, item_id=item.id,
                    chosen_option="A", is_correct=True,
                    source="MANUAL", entered_by_external_id=None,  # sem autor
                ))
                with self.assertRaises(IntegrityError):
                    await session.flush()

            async with self.session_factory() as session:
                exam = await self._new_exam(session, name="Simulado OMR com autor")
                item = MockExamItem(
                    school_id=self.school_id, mock_exam_id=exam.id,
                    position=1, area_code="MT", correct_option="A"
                )
                session.add(item)
                await session.flush()
                session.add(MockExamResponse(
                    school_id=self.school_id, mock_exam_id=exam.id,
                    student_id=self.student_id, item_id=item.id,
                    chosen_option="A", is_correct=True,
                    source="OMR",
                    entered_by_external_id=self.operator_external_id,  # maquina nao digita
                ))
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())

    def test_workflow_audit_records_a_status_transition(self):
        async def _scenario():
            async with self.session_factory() as session:
                exam = await self._new_exam(session, name="Simulado auditado")
                audit = MockExamWorkflowAudit(
                    school_id=self.school_id,
                    mock_exam_id=exam.id,
                    from_status="DRAFT",
                    to_status="PRINTED",
                    actor_external_id=self.operator_external_id,
                    note="Cartoes emitidos",
                )
                session.add(audit)
                await session.commit()
                stored = await session.get(MockExamWorkflowAudit, audit.id)
                self.assertEqual(stored.from_status, "DRAFT")
                self.assertEqual(stored.to_status, "PRINTED")
                self.assertEqual(stored.actor_external_id, self.operator_external_id)
                # occurred_at nunca e escrito a mao pelas fases seguintes: o
                # default do modelo e que o preenche.
                self.assertIsNotNone(stored.occurred_at)

        self._run(_scenario())

    def test_workflow_audit_allows_a_null_origin_but_not_a_bogus_destination(self):
        async def _scenario():
            async with self.session_factory() as session:
                exam = await self._new_exam(session, name="Simulado auditoria inicial")
                # Criacao do simulado: nao havia status anterior.
                session.add(MockExamWorkflowAudit(
                    school_id=self.school_id, mock_exam_id=exam.id,
                    from_status=None, to_status="DRAFT",
                ))
                await session.commit()

            async with self.session_factory() as session:
                exam = await self._new_exam(session, name="Simulado auditoria invalida")
                session.add(MockExamWorkflowAudit(
                    school_id=self.school_id, mock_exam_id=exam.id,
                    from_status="DRAFT", to_status="ARQUIVADO",
                ))
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_mock_exam_models.py -v`
Expected: FAIL na coleta — `ImportError: cannot import name 'MockExam' from 'agente_ia_edu.db.models'`.

- [ ] **Step 3: Escrever o modelo**

Crie `src/agente_ia_edu/db/models/mock_exam.py`:

```python
"""Simulados Fase 1 - a prova, o gabarito e a matriz de respostas
(spec s3 "Prova e gabarito" e "Respostas consolidadas" de
docs/superpowers/specs/2026-09-29-correcao-simulados-tri-design.md).

A prova vem de fora: o sistema conhece apenas o gabarito, nao os enunciados.
Por isso ``mock_exam_items`` guarda posicao, area e alternativa correta, e
``source_question_version_id`` fica NULO em todo o v1 - ele existe como gancho
para o dia em que a prova for montada a partir do banco interno de questoes
(spec "Fora de escopo" e s3).

``anchor_key`` e a identidade estavel de um item atraves de simulados
diferentes: dois itens em simulados distintos com a mesma chave sao a mesma
questao, e e isso que torna a equalizacao da Fase 5 possivel. Nenhuma logica de
equalizacao e escrita aqui.

Os codigos de area NAO sao um enum no codigo: sao dados do simulado
(``mock_exam_areas``), porque a escola pode aplicar um simulado so de Quimica
ou o 1o dia completo do ENEM (spec s3).

ISOLAMENTO MULTI-TENANT (spec s3.0), a mesma regra de db/models/academic.py:
toda tabela carrega ``school_id`` e toda chave estrangeira entre elas e
COMPOSTA - ``(school_id, parent_id)`` referenciando ``parent(school_id, id)``,
viabilizada por ``UNIQUE(school_id, id)`` em cada pai. A coluna e redundante
com a cadeia de chaves, e e esse o ponto: sem ela o banco nao tem como recusar
uma resposta cujo aluno e da escola A e cujo item e da escola B. Num subsistema
cujo produto final e a nota do aluno, esse erro seria caro e silencioso.

Apenas ``mock_exams`` tem FK direta para ``schools``, por ser raiz do
subsistema; as filhas herdam a garantia pelo pai, exatamente como
``Class`` faz em academic.py.

``UNIQUE(school_id, id)`` so aparece em tabela que e PAI de alguma outra
(``mock_exams``, ``mock_exam_items``). Pondo em folha - ``mock_exam_areas``,
``mock_exam_responses`` - seria um indice unico a mais para manter em ~135 mil
linhas por simulado sem nenhuma FK que o use.

SEM ``relationship()``: com FKs compostas, varias delas escrevem a MESMA coluna
``school_id``, o que obriga a marcar ``overlaps=`` em cada relacao (ver o
comentario em ``Class.academic_year``, academic.py:331-338). Esse custo so se
paga onde alguem navega pelo grafo; neste subsistema toda leitura e ``select``
explicito, entao as relacoes sao deliberadamente omitidas.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base

MOCK_EXAM_STATUSES = (
    "DRAFT",
    "PRINTED",
    "APPLIED",
    "SCANNED",
    "CALIBRATED",
    "PUBLISHED",
)
OPTION_CODES = ("A", "B", "C", "D", "E")
RESPONSE_SOURCES = ("MANUAL", "OMR")

_STATUS_SQL = ", ".join(f"'{value}'" for value in MOCK_EXAM_STATUSES)
_OPTION_SQL = ", ".join(f"'{value}'" for value in OPTION_CODES)
_RESPONSE_SOURCE_SQL = ", ".join(f"'{value}'" for value in RESPONSE_SOURCES)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MockExam(Base):
    """Uma aplicacao de simulado numa escola, num ano letivo.

    Raiz do subsistema: e a unica tabela de prova com FK direta para
    ``schools``. O ano letivo entra por chave composta, para que um simulado
    nunca possa apontar para o ano letivo de outra escola.

    ``academic_year_id`` e OBRIGATORIO (spec s3). Aluno, turma e todos os
    relatorios sao resolvidos por ``StudentEnrollment -> Class.academic_year_id``;
    um simulado sem ano letivo produziria roster vazio e boletim vazio EM
    SILENCIO, que e a pior forma de errar - ninguem ve excecao, so uma lista
    curta que parece plausivel.
    """

    __tablename__ = "mock_exams"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "academic_year_id"],
            ["academic_years.school_id", "academic_years.id"],
            ondelete="RESTRICT",
            name="fk_mock_exams_school_academic_year",
        ),
        UniqueConstraint("school_id", "id", name="uq_mock_exams_school_id_id"),
        CheckConstraint(f"status IN ({_STATUS_SQL})", name="ck_mock_exams_status"),
        CheckConstraint("exam_day IN (1, 2)", name="ck_mock_exams_exam_day"),
        Index("ix_mock_exams_school_id", "school_id"),
        Index("ix_mock_exams_academic_year_id", "academic_year_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    academic_year_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    application_date: Mapped[date | None] = mapped_column(Date)
    exam_day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class MockExamWorkflowAudit(Base):
    """Quem mudou o status deste simulado, quando e por que (spec s3).

    Segue o padrao de ``AssessmentWorkflowAudit`` (db/models/assessments.py):
    linha por transicao, nunca sobrescrita. Quem publicou a nota de um simulado,
    e quando, precisa estar registrado - e uma nota contestada dois anos depois
    nao se audita a partir de uma coluna ``status`` que so guarda o presente.

    O ator e guardado como IDENTIFICADOR EXTERNO, nao como FK para ``users`` -
    exatamente como ``AssessmentWorkflowAudit.performed_by_external_id``
    (db/models/assessments.py:200), que e a precedente real deste padrao no
    repositorio. A camada HTTP autentica por identificador externo e sempre o
    tem; um UUID exigiria uma resolucao que pode devolver nulo, e a coluna
    ficaria vazia justamente quando alguem pergunta quem publicou a nota.
    """

    __tablename__ = "mock_exam_workflow_audit"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "mock_exam_id"],
            ["mock_exams.school_id", "mock_exams.id"],
            ondelete="CASCADE",
            name="fk_mock_exam_workflow_audit_school_mock_exam",
        ),
        CheckConstraint(
            f"from_status IS NULL OR from_status IN ({_STATUS_SQL})",
            name="ck_mock_exam_workflow_audit_from_status",
        ),
        CheckConstraint(
            f"to_status IN ({_STATUS_SQL})",
            name="ck_mock_exam_workflow_audit_to_status",
        ),
        Index("ix_mock_exam_workflow_audit_mock_exam_id", "mock_exam_id"),
        Index("ix_mock_exam_workflow_audit_school_id", "school_id"),
        Index("ix_mock_exam_workflow_audit_occurred_at", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    mock_exam_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # NULO na criacao do simulado: nao havia status anterior.
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_external_id: Mapped[str | None] = mapped_column(String(255))
    # default OBRIGATORIO: as fases seguintes nunca escrevem esta coluna a mao.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    note: Mapped[str | None] = mapped_column(Text)


class MockExamArea(Base):
    """Uma area de conhecimento DESTE simulado, com rotulo e ordem de exibicao."""

    __tablename__ = "mock_exam_areas"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "mock_exam_id"],
            ["mock_exams.school_id", "mock_exams.id"],
            ondelete="CASCADE",
            name="fk_mock_exam_areas_school_mock_exam",
        ),
        UniqueConstraint("mock_exam_id", "code", name="uq_mock_exam_areas_exam_code"),
        Index("ix_mock_exam_areas_mock_exam_id", "mock_exam_id"),
        Index("ix_mock_exam_areas_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    mock_exam_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class MockExamItem(Base):
    """Uma questao da prova, conhecida apenas pelo gabarito."""

    __tablename__ = "mock_exam_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "mock_exam_id"],
            ["mock_exams.school_id", "mock_exams.id"],
            ondelete="CASCADE",
            name="fk_mock_exam_items_school_mock_exam",
        ),
        UniqueConstraint("school_id", "id", name="uq_mock_exam_items_school_id_id"),
        UniqueConstraint("mock_exam_id", "position", name="uq_mock_exam_items_exam_position"),
        CheckConstraint("position >= 1", name="ck_mock_exam_items_position"),
        CheckConstraint(
            f"correct_option IN ({_OPTION_SQL})", name="ck_mock_exam_items_correct_option"
        ),
        CheckConstraint(
            "(is_anchor = false) OR (anchor_key IS NOT NULL)",
            name="ck_mock_exam_items_anchor_key_required",
        ),
        Index("ix_mock_exam_items_mock_exam_id", "mock_exam_id"),
        Index("ix_mock_exam_items_school_id", "school_id"),
        Index("ix_mock_exam_items_anchor_key", "anchor_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    mock_exam_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    area_code: Mapped[str] = mapped_column(String(20), nullable=False)
    correct_option: Mapped[str] = mapped_column(String(1), nullable=False)
    is_anchor: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    anchor_key: Mapped[str | None] = mapped_column(String(120))
    # Banco de questoes OFICIAL, que nao e por escola - FK simples de proposito.
    source_question_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("question_versions.id", ondelete="RESTRICT")
    )


class MockExamResponse(Base):
    """A resposta de um aluno a um item. E a matriz que alimenta a TRI.

    ``chosen_option`` NULO significa questao em branco - nunca "nao sabemos".

    A PROCEDENCIA NAO E OPCIONAL (spec s3). Sem ``source`` e sem autor, uma
    resposta digitada a mao fica indistinguivel de uma lida pelo scanner, e nao
    ha como auditar quem digitou o cartao de um aluno quando a nota for
    contestada. ``entered_by_external_id`` e nulo exatamente quando ``source`` e
    ``OMR`` - nesse caso quem "digitou" foi a maquina, e o CHECK impede que
    alguem preencha um autor humano para uma leitura automatica (ou omita o
    autor de uma digitacao manual). Como na auditoria, o autor e o identificador
    EXTERNO que a camada HTTP ja tem em maos, nao uma FK para ``users`` cuja
    resolucao poderia devolver nulo.

    Tres pais (prova, item, aluno) e o pior caso de vazamento entre escolas,
    porque cada um poderia legitimamente vir de uma escola diferente se nada os
    amarrasse. ``school_id`` e essa amarra: as tres FKs a carregam, entao os
    tres pais sao obrigados a concordar - mesmo desenho de ``Class`` em
    academic.py.
    """

    __tablename__ = "mock_exam_responses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "mock_exam_id"],
            ["mock_exams.school_id", "mock_exams.id"],
            ondelete="CASCADE",
            name="fk_mock_exam_responses_school_mock_exam",
        ),
        ForeignKeyConstraint(
            ["school_id", "item_id"],
            ["mock_exam_items.school_id", "mock_exam_items.id"],
            ondelete="CASCADE",
            name="fk_mock_exam_responses_school_item",
        ),
        ForeignKeyConstraint(
            ["school_id", "student_id"],
            ["students.school_id", "students.id"],
            ondelete="RESTRICT",
            name="fk_mock_exam_responses_school_student",
        ),
        UniqueConstraint(
            "student_id", "item_id", name="uq_mock_exam_responses_student_item"
        ),
        CheckConstraint(
            f"chosen_option IS NULL OR chosen_option IN ({_OPTION_SQL})",
            name="ck_mock_exam_responses_chosen_option",
        ),
        CheckConstraint(
            f"source IN ({_RESPONSE_SOURCE_SQL})", name="ck_mock_exam_responses_source"
        ),
        CheckConstraint(
            "(source = 'OMR' AND entered_by_external_id IS NULL) OR "
            "(source = 'MANUAL' AND entered_by_external_id IS NOT NULL)",
            name="ck_mock_exam_responses_author_matches_source",
        ),
        Index("ix_mock_exam_responses_mock_exam_id", "mock_exam_id"),
        Index("ix_mock_exam_responses_item_id", "item_id"),
        Index("ix_mock_exam_responses_student_id", "student_id"),
        Index("ix_mock_exam_responses_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    mock_exam_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    chosen_option: Mapped[str | None] = mapped_column(String(1))
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    entered_by_external_id: Mapped[str | None] = mapped_column(String(255))
    # default OBRIGATORIO: a digitacao manual da Fase 2 e o worker da Fase 4
    # nunca escrevem esta coluna a mao.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


__all__ = [
    "MOCK_EXAM_STATUSES",
    "OPTION_CODES",
    "RESPONSE_SOURCES",
    "MockExam",
    "MockExamArea",
    "MockExamItem",
    "MockExamResponse",
    "MockExamWorkflowAudit",
]
```

- [ ] **Step 4: Registrar os modelos no pacote**

Em `src/agente_ia_edu/db/models/__init__.py`, acrescente o import junto aos demais (logo depois do bloco `from .catalog import (...)`):

```python
from .mock_exam import (
    MOCK_EXAM_STATUSES,
    OPTION_CODES,
    RESPONSE_SOURCES,
    MockExam,
    MockExamArea,
    MockExamItem,
    MockExamResponse,
    MockExamWorkflowAudit,
)
```

e acrescente as entradas ao fim da lista `__all__`, antes do `]` final:

```python
    "MOCK_EXAM_STATUSES",
    "OPTION_CODES",
    "RESPONSE_SOURCES",
    "MockExam",
    "MockExamArea",
    "MockExamItem",
    "MockExamResponse",
    "MockExamWorkflowAudit",
```

- [ ] **Step 5: Rodar o teste e ver passar**

Run: `.venv/bin/python -m pytest tests/test_mock_exam_models.py -v`
Expected: PASS (12 testes). Se sair `SKIP`, suba o Postgres de desenvolvimento (`docker compose up -d`, porta 5433) e rode de novo — um skip não é evidência.

- [ ] **Step 6: Escrever a migration 057**

Crie `migrations/versions/057_mock_exam_core.py`:

```python
"""Simulados Fase 1 - prova, areas, gabarito e matriz de respostas.

Revision ID: 057_mock_exam_core
Revises: 056_material_assignments

Primeira das tres migrations do subsistema de correcao de simulados
(spec s3 de docs/superpowers/specs/2026-09-29-correcao-simulados-tri-design.md).
Puramente aditiva: quatro tabelas novas, nenhuma tabela existente e tocada.

Toda tabela carrega ``school_id`` e toda FK entre elas e COMPOSTA -
``(school_id, parent_id)`` referenciando ``parent(school_id, id)`` - que e a
regra de isolamento multi-tenant do spec s3.0, ja aplicada em todo o modelo
academico (db/models/academic.py). O ``UNIQUE(school_id, id)`` em ``mock_exams``
e ``mock_exam_items`` e o que torna essas FKs possiveis; ele nao aparece nas
folhas porque nenhuma FK o usaria.

``academic_year_id`` e NOT NULL porque toda resolucao de aluno, turma e
relatorio passa por ``StudentEnrollment -> Class.academic_year_id``: com ano
nulo, o simulado devolveria roster vazio e boletim vazio sem levantar erro.

``mock_exam_workflow_audit`` guarda quem moveu o status e quando, no padrao de
``assessment_workflow_audit`` - inclusive no tipo do ator: identificador
EXTERNO (String(255)), como ``performed_by_external_id`` la, e nao FK para
``users``. A camada HTTP autentica por identificador externo e sempre o tem; um
UUID exigiria resolucao que pode devolver nulo, esvaziando a coluna justamente
quando alguem pergunta quem digitou o cartao.
``mock_exam_responses.source`` + ``entered_by_external_id`` guardam a
PROCEDENCIA de cada resposta, com um CHECK que amarra os dois: OMR nunca tem
autor humano, digitacao manual nunca fica sem um.

``mock_exam_areas`` existe porque codigo de area e DADO do simulado, nao enum de
codigo - a escola pode aplicar um simulado so de Quimica. ``anchor_key`` e a
identidade estavel de um item entre simulados (equalizacao, Fase 5) e o CHECK
garante que um item marcado como ancora nunca fique sem chave.
``source_question_version_id`` fica nulo em todo o v1, e aponta para o banco de
questoes OFICIAL, que nao e por escola - por isso, e so essa, e FK simples.
"""

from alembic import op
import sqlalchemy as sa

revision = "057_mock_exam_core"
down_revision = "056_material_assignments"
branch_labels = None
depends_on = None

OPTIONS = "'A', 'B', 'C', 'D', 'E'"
STATUSES = "'DRAFT', 'PRINTED', 'APPLIED', 'SCANNED', 'CALIBRATED', 'PUBLISHED'"


def upgrade() -> None:
    op.create_table(
        "mock_exams",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        # NOT NULL: aluno, turma e relatorios sao resolvidos por
        # StudentEnrollment -> Class.academic_year_id, e um simulado sem ano
        # letivo devolveria roster e boletim VAZIOS, sem erro nenhum.
        sa.Column("academic_year_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("application_date", sa.Date(), nullable=True),
        sa.Column("exam_day", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="DRAFT"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT",
                                name="fk_mock_exams_school"),
        sa.ForeignKeyConstraint(["school_id", "academic_year_id"],
                                ["academic_years.school_id", "academic_years.id"],
                                ondelete="RESTRICT",
                                name="fk_mock_exams_school_academic_year"),
        sa.UniqueConstraint("school_id", "id", name="uq_mock_exams_school_id_id"),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="ck_mock_exams_status"),
        sa.CheckConstraint("exam_day IN (1, 2)", name="ck_mock_exams_exam_day"),
    )
    op.create_index("ix_mock_exams_school_id", "mock_exams", ["school_id"])
    op.create_index("ix_mock_exams_academic_year_id", "mock_exams", ["academic_year_id"])

    op.create_table(
        "mock_exam_workflow_audit",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("mock_exam_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("actor_external_id", sa.String(length=255), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["school_id", "mock_exam_id"],
                                ["mock_exams.school_id", "mock_exams.id"],
                                ondelete="CASCADE",
                                name="fk_mock_exam_workflow_audit_school_mock_exam"),
        sa.CheckConstraint(f"from_status IS NULL OR from_status IN ({STATUSES})",
                           name="ck_mock_exam_workflow_audit_from_status"),
        sa.CheckConstraint(f"to_status IN ({STATUSES})",
                           name="ck_mock_exam_workflow_audit_to_status"),
    )
    op.create_index("ix_mock_exam_workflow_audit_mock_exam_id",
                    "mock_exam_workflow_audit", ["mock_exam_id"])
    op.create_index("ix_mock_exam_workflow_audit_school_id",
                    "mock_exam_workflow_audit", ["school_id"])
    op.create_index("ix_mock_exam_workflow_audit_occurred_at",
                    "mock_exam_workflow_audit", ["occurred_at"])

    op.create_table(
        "mock_exam_areas",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("mock_exam_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["school_id", "mock_exam_id"],
                                ["mock_exams.school_id", "mock_exams.id"],
                                ondelete="CASCADE",
                                name="fk_mock_exam_areas_school_mock_exam"),
        sa.UniqueConstraint("mock_exam_id", "code", name="uq_mock_exam_areas_exam_code"),
    )
    op.create_index("ix_mock_exam_areas_mock_exam_id", "mock_exam_areas", ["mock_exam_id"])
    op.create_index("ix_mock_exam_areas_school_id", "mock_exam_areas", ["school_id"])

    op.create_table(
        "mock_exam_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("mock_exam_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("area_code", sa.String(length=20), nullable=False),
        sa.Column("correct_option", sa.String(length=1), nullable=False),
        sa.Column("is_anchor", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("anchor_key", sa.String(length=120), nullable=True),
        sa.Column("source_question_version_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["school_id", "mock_exam_id"],
                                ["mock_exams.school_id", "mock_exams.id"],
                                ondelete="CASCADE",
                                name="fk_mock_exam_items_school_mock_exam"),
        sa.ForeignKeyConstraint(["source_question_version_id"], ["question_versions.id"],
                                ondelete="RESTRICT",
                                name="fk_mock_exam_items_source_question_version"),
        sa.UniqueConstraint("school_id", "id", name="uq_mock_exam_items_school_id_id"),
        sa.UniqueConstraint("mock_exam_id", "position", name="uq_mock_exam_items_exam_position"),
        sa.CheckConstraint("position >= 1", name="ck_mock_exam_items_position"),
        sa.CheckConstraint(f"correct_option IN ({OPTIONS})",
                           name="ck_mock_exam_items_correct_option"),
        sa.CheckConstraint("(is_anchor = false) OR (anchor_key IS NOT NULL)",
                           name="ck_mock_exam_items_anchor_key_required"),
    )
    op.create_index("ix_mock_exam_items_mock_exam_id", "mock_exam_items", ["mock_exam_id"])
    op.create_index("ix_mock_exam_items_school_id", "mock_exam_items", ["school_id"])
    op.create_index("ix_mock_exam_items_anchor_key", "mock_exam_items", ["anchor_key"])

    op.create_table(
        "mock_exam_responses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("mock_exam_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("chosen_option", sa.String(length=1), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("entered_by_external_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id", "mock_exam_id"],
                                ["mock_exams.school_id", "mock_exams.id"],
                                ondelete="CASCADE",
                                name="fk_mock_exam_responses_school_mock_exam"),
        sa.ForeignKeyConstraint(["school_id", "item_id"],
                                ["mock_exam_items.school_id", "mock_exam_items.id"],
                                ondelete="CASCADE",
                                name="fk_mock_exam_responses_school_item"),
        sa.ForeignKeyConstraint(["school_id", "student_id"],
                                ["students.school_id", "students.id"],
                                ondelete="RESTRICT",
                                name="fk_mock_exam_responses_school_student"),
        sa.UniqueConstraint("student_id", "item_id", name="uq_mock_exam_responses_student_item"),
        sa.CheckConstraint(f"chosen_option IS NULL OR chosen_option IN ({OPTIONS})",
                           name="ck_mock_exam_responses_chosen_option"),
        sa.CheckConstraint("source IN ('MANUAL', 'OMR')",
                           name="ck_mock_exam_responses_source"),
        # Procedencia e autoria andam juntas: OMR nunca tem autor humano, e
        # digitacao manual nunca pode ficar sem quem a fez (spec s3).
        sa.CheckConstraint(
            "(source = 'OMR' AND entered_by_external_id IS NULL) OR "
            "(source = 'MANUAL' AND entered_by_external_id IS NOT NULL)",
            name="ck_mock_exam_responses_author_matches_source"),
    )
    op.create_index("ix_mock_exam_responses_mock_exam_id", "mock_exam_responses", ["mock_exam_id"])
    op.create_index("ix_mock_exam_responses_item_id", "mock_exam_responses", ["item_id"])
    op.create_index("ix_mock_exam_responses_student_id", "mock_exam_responses", ["student_id"])
    op.create_index("ix_mock_exam_responses_school_id", "mock_exam_responses", ["school_id"])


def downgrade() -> None:
    for ix in ("ix_mock_exam_responses_school_id", "ix_mock_exam_responses_student_id",
               "ix_mock_exam_responses_item_id", "ix_mock_exam_responses_mock_exam_id"):
        op.drop_index(ix, table_name="mock_exam_responses")
    op.drop_table("mock_exam_responses")
    for ix in ("ix_mock_exam_items_anchor_key", "ix_mock_exam_items_school_id",
               "ix_mock_exam_items_mock_exam_id"):
        op.drop_index(ix, table_name="mock_exam_items")
    op.drop_table("mock_exam_items")
    for ix in ("ix_mock_exam_areas_school_id", "ix_mock_exam_areas_mock_exam_id"):
        op.drop_index(ix, table_name="mock_exam_areas")
    op.drop_table("mock_exam_areas")
    for ix in ("ix_mock_exam_workflow_audit_occurred_at",
               "ix_mock_exam_workflow_audit_school_id",
               "ix_mock_exam_workflow_audit_mock_exam_id"):
        op.drop_index(ix, table_name="mock_exam_workflow_audit")
    op.drop_table("mock_exam_workflow_audit")
    for ix in ("ix_mock_exams_academic_year_id", "ix_mock_exams_school_id"):
        op.drop_index(ix, table_name="mock_exams")
    op.drop_table("mock_exams")
```

- [ ] **Step 7: Verificar que a cadeia de migrations resolve**

Run: `.venv/bin/python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; s=ScriptDirectory.from_config(Config('alembic.ini')); print(s.get_current_head())"`
Expected: imprime `057_mock_exam_core` e nenhuma exceção (`Multiple head revisions` ou `revision not found` reprovam o passo).

- [ ] **Step 8: Rodar a suíte inteira**

Run: `.venv/bin/python -m pytest -q`
Expected: nenhuma falha nova em relação ao baseline. Uma fase aditiva quebra testes antigos com mais frequência do que parece (ver `.claude/.../memory` do projeto), então a suíte completa é obrigatória aqui, não opcional.

- [ ] **Step 9: Commit**

```bash
git add src/agente_ia_edu/db/models/mock_exam.py \
        src/agente_ia_edu/db/models/__init__.py \
        migrations/versions/057_mock_exam_core.py \
        tests/test_mock_exam_models.py
git commit -m "feat(simulados): modelo da prova, gabarito e respostas (migration 057)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Cartão emitido, digitalização, marcas e fila do worker (migration 058)

**Files:**
- Create: `src/agente_ia_edu/db/models/answer_card.py`
- Create: `migrations/versions/058_answer_card_capture.py`
- Modify: `src/agente_ia_edu/db/models/__init__.py`
- Test: `tests/test_answer_card_models.py`

**Interfaces:**
- Consumes: `MockExam` (Task 1), `Base`, `JSONBCompatible`, `students.id`, `users.id`.
- Produces: `AnswerCard`, `AnswerCardScan`, `AnswerCardMark`, `OmrJob`, e as constantes `SCAN_STATUSES`, `SCAN_SOURCES`, `MARK_RESOLUTIONS = ("PENDING", "AUTO", "HUMAN")`, `OMR_JOB_STATUSES`, `DEFAULT_BOOKLET_CODE: str = "UNICO"`. `AnswerCardMark.resolution` nasce em `PENDING`, o operador é `AnswerCardMark.resolved_by_external_id: str | None` (`String(255)`, sem FK — mesma regra de ator das demais tabelas), e `OmrJob` tem `created_at` (FIFO da fila). Todos carregam `school_id: uuid.UUID` obrigatório com FKs compostas (spec §3.0); `answer_cards` e `answer_card_scans` ganham `UNIQUE(school_id, id)`. `AnswerCard.qr_token` é `str` único **globalmente** — a única exceção deliberada ao escopo por escola. Migration head passa a ser `058_answer_card_capture`.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_answer_card_models.py` com exatamente este conteúdo (o arranjo de banco descartável é o mesmo de todo teste de modelo deste repositório, repetido aqui por inteiro para que esta task possa ser lida sozinha):

```python
"""Simulados Fase 1, Task 2 - cartao emitido, digitalizacao, marcas lidas e
fila do worker (spec s3 "Cartao e digitalizacao").

Postgres real e descartavel na porta 5433. A unicidade GLOBAL do qr_token e um
requisito de seguranca, nao de conveniencia: o token e a unica coisa impressa
no papel que identifica o aluno, e duas escolas com o mesmo token resolveriam
para o cartao errado. So um banco de verdade prova esse indice - e o mesmo vale
para as FKs compostas do isolamento multi-tenant (spec s3.0), exercidas em
test_refuses_a_card_for_a_student_of_another_school.
"""

from __future__ import annotations

import asyncio
import os
import unittest

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    AnswerCard,
    AnswerCardMark,
    AnswerCardScan,
    MockExam,
    OmrJob,
    Person,
    School,
    Student,
)
from tests._postgres_test_db import create_database, drop_database


class AnswerCardModelPostgreSQLTests(unittest.TestCase):
    database_name = "agente_ia_edu_answer_card_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = os.getenv(
        "ANSWER_CARD_TEST_ADMIN_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres",
    )
    async_database_url = os.getenv(
        "ANSWER_CARD_TEST_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            cls._admin_execute("SELECT 1")
        except Exception as exc:
            raise unittest.SkipTest("PostgreSQL de teste indisponivel") from exc
        cls._drop_database()
        create_database(cls.admin_url, cls.database_name)
        cls.engine = create_async_engine(cls.async_database_url)
        cls.session_factory = async_sessionmaker(
            cls.engine, class_=AsyncSession, expire_on_commit=False
        )

        async def _prep():
            async with cls.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with cls.session_factory() as session:
                school = School(code="SIM-ANSWER-CARD", name="Escola Cartao")
                session.add(school)
                await session.flush()
                year = AcademicYear(school_id=school.id, year=2026, status="ACTIVE")
                session.add(year)
                await session.flush()
                # QUATRO alunos, nao um: uq_answer_cards_exam_student permite um
                # cartao por aluno por prova, entao cada teste que grava um
                # cartao precisa do seu proprio aluno, senao os testes colidem
                # entre si em vez de exercitar o que dizem exercitar.
                student_ids = []
                for index in range(4):
                    person = Person(school_id=school.id, full_name=f"Aluno {index + 1}")
                    session.add(person)
                    await session.flush()
                    student = Student(school_id=school.id, person_id=person.id)
                    session.add(student)
                    await session.flush()
                    student_ids.append(student.id)
                exam = MockExam(
                    school_id=school.id,
                    academic_year_id=year.id,
                    name="Simulado para cartoes",
                    exam_day=1,
                )
                session.add(exam)
                await session.commit()
                return school.id, exam.id, student_ids

        cls.school_id, cls.mock_exam_id, cls.student_ids = asyncio.run(_prep())

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "engine"):
            asyncio.run(cls.engine.dispose())
        cls._drop_database()

    @classmethod
    def _admin_execute(cls, statement):
        engine = create_engine(
            cls.admin_url,
            connect_args={"autocommit": True},
            execution_options={"isolation_level": "AUTOCOMMIT"},
        )
        try:
            with engine.connect() as connection:
                return connection.execute(text(statement))
        finally:
            engine.dispose()

    @classmethod
    def _drop_database(cls):
        drop_database(cls.admin_url, cls.database_name)

    def _run(self, coro):
        return asyncio.run(coro)

    def _card(self, token: str, student_index: int = 0) -> AnswerCard:
        return AnswerCard(
            school_id=self.school_id,
            mock_exam_id=self.mock_exam_id,
            student_id=self.student_ids[student_index],
            qr_token=token,
            template_version="enem-90x5-v1",
        )

    def test_card_defaults_to_single_booklet(self):
        async def _scenario():
            async with self.session_factory() as session:
                card = self._card("tok-default-booklet", student_index=0)
                session.add(card)
                await session.commit()
                stored = await session.get(AnswerCard, card.id)
                self.assertEqual(stored.booklet_code, "UNICO")
                self.assertIsNone(stored.printed_at)

        self._run(_scenario())

    def test_qr_token_is_globally_unique(self):
        async def _scenario():
            async with self.session_factory() as session:
                session.add(self._card("tok-colisao", student_index=1))
                await session.commit()
            async with self.session_factory() as session:
                # Aluno DIFERENTE, token IGUAL: o que tem que falhar aqui e o
                # indice unico global do token, nao o de (prova, aluno).
                session.add(self._card("tok-colisao", student_index=2))
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())

    def test_one_card_per_student_per_exam(self):
        async def _scenario():
            async with self.session_factory() as session:
                session.add(self._card("tok-primeiro-do-aluno", student_index=3))
                await session.commit()
            async with self.session_factory() as session:
                # Mesmo aluno, token diferente: agora o que falha e
                # uq_answer_cards_exam_student.
                session.add(self._card("tok-segundo-do-aluno", student_index=3))
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())

    def test_scan_starts_pending_and_unresolved(self):
        async def _scenario():
            async with self.session_factory() as session:
                scan = AnswerCardScan(
                    school_id=self.school_id,
                    mock_exam_id=self.mock_exam_id,
                    image_hash="a" * 64,
                    storage_path="var/material_storage/aa/" + "a" * 64 + "/pagina.png",
                    source="MOBILE",
                )
                session.add(scan)
                await session.commit()
                stored = await session.get(AnswerCardScan, scan.id)
                # answer_card_id fica nulo ate o QR ser resolvido (spec s3).
                self.assertIsNone(stored.answer_card_id)
                self.assertEqual(stored.status, "PENDING")
                self.assertIsNone(stored.failure_reason)

        self._run(_scenario())

    def test_rejects_unknown_scan_source(self):
        async def _scenario():
            async with self.session_factory() as session:
                session.add(AnswerCardScan(
                    school_id=self.school_id,
                    mock_exam_id=self.mock_exam_id,
                    image_hash="b" * 64,
                    storage_path="/tmp/x.png",
                    source="FAX",
                ))
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())

    def test_mark_keeps_all_five_measured_intensities(self):
        async def _scenario():
            async with self.session_factory() as session:
                scan = AnswerCardScan(
                    school_id=self.school_id,
                    mock_exam_id=self.mock_exam_id,
                    image_hash="c" * 64,
                    storage_path="/tmp/c.png",
                    source="SCANNER",
                )
                session.add(scan)
                await session.flush()
                mark = AnswerCardMark(
                    school_id=self.school_id,
                    scan_id=scan.id,
                    item_position=12,
                    detected_option="D",
                    fill_intensities=[0.04, 0.06, 0.03, 0.81, 0.05],
                    confidence=0.93,
                )
                session.add(mark)
                await session.commit()
                stored = await session.get(AnswerCardMark, mark.id)
                # As cinco intensidades medidas, e nao so a conclusao: e o que
                # permite auditar uma leitura contestada sem reprocessar a
                # imagem (spec s3).
                self.assertEqual(len(stored.fill_intensities), 5)
                # Nasce PENDENTE: quem decide que virou AUTO e o leitor optico
                # da Fase 4, nao o default da coluna (spec s3).
                self.assertEqual(stored.resolution, "PENDING")
                self.assertIsNone(stored.resolved_option)

        self._run(_scenario())

    def test_one_mark_per_scan_and_position(self):
        async def _scenario():
            async with self.session_factory() as session:
                scan = AnswerCardScan(
                    school_id=self.school_id,
                    mock_exam_id=self.mock_exam_id,
                    image_hash="d" * 64,
                    storage_path="/tmp/d.png",
                    source="SCANNER",
                )
                session.add(scan)
                await session.flush()
                session.add_all([
                    AnswerCardMark(school_id=self.school_id, scan_id=scan.id,
                                   item_position=3,
                                   fill_intensities=[0.1, 0.1, 0.1, 0.1, 0.1]),
                    AnswerCardMark(school_id=self.school_id, scan_id=scan.id,
                                   item_position=3,
                                   fill_intensities=[0.2, 0.2, 0.2, 0.2, 0.2]),
                ])
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())

    def test_omr_job_is_one_per_scan_and_starts_queued(self):
        async def _scenario():
            async with self.session_factory() as session:
                scan = AnswerCardScan(
                    school_id=self.school_id,
                    mock_exam_id=self.mock_exam_id,
                    image_hash="e" * 64,
                    storage_path="/tmp/e.png",
                    source="SCANNER",
                )
                session.add(scan)
                await session.flush()
                job = OmrJob(school_id=self.school_id, scan_id=scan.id)
                session.add(job)
                await session.commit()
                stored = await session.get(OmrJob, job.id)
                self.assertEqual(stored.status, "QUEUED")
                self.assertEqual(stored.attempts, 0)
                self.assertIsNone(stored.locked_at)

                session.add(OmrJob(school_id=self.school_id, scan_id=scan.id))
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())

    def test_mark_resolution_accepts_the_three_states_and_nothing_else(self):
        async def _scenario():
            async with self.session_factory() as session:
                scan = AnswerCardScan(
                    school_id=self.school_id,
                    mock_exam_id=self.mock_exam_id,
                    image_hash="f" * 64,
                    storage_path="/tmp/f.png",
                    source="SCANNER",
                )
                session.add(scan)
                await session.flush()
                for position, resolution in enumerate(("PENDING", "AUTO", "HUMAN"), start=1):
                    session.add(AnswerCardMark(
                        school_id=self.school_id, scan_id=scan.id,
                        item_position=position, resolution=resolution,
                        fill_intensities=[0.1, 0.1, 0.1, 0.1, 0.1],
                    ))
                await session.commit()

            async with self.session_factory() as session:
                scan = AnswerCardScan(
                    school_id=self.school_id,
                    mock_exam_id=self.mock_exam_id,
                    image_hash="g" * 64,
                    storage_path="/tmp/g.png",
                    source="SCANNER",
                )
                session.add(scan)
                await session.flush()
                session.add(AnswerCardMark(
                    school_id=self.school_id, scan_id=scan.id, item_position=1,
                    resolution="TALVEZ",
                    fill_intensities=[0.1, 0.1, 0.1, 0.1, 0.1],
                ))
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())

    def test_refuses_a_card_for_a_student_of_another_school(self):
        """Isolamento multi-tenant (spec s3.0).

        Nao existe FK simples que recuse isto: o simulado existe, o aluno
        existe. So a chave composta (school_id, student_id) percebe que o aluno
        nao e desta escola - e um cartao nominal emitido para o aluno errado e
        exatamente o tipo de erro que so aparece depois de impresso.
        """
        async def _scenario():
            async with self.session_factory() as session:
                other = School(code="SIM-ANSWER-CARD-B", name="Escola Vizinha")
                session.add(other)
                await session.flush()
                person = Person(school_id=other.id, full_name="Aluno Vizinho")
                session.add(person)
                await session.flush()
                foreign_student = Student(school_id=other.id, person_id=person.id)
                session.add(foreign_student)
                await session.commit()
                foreign_student_id = foreign_student.id

            async with self.session_factory() as session:
                session.add(AnswerCard(
                    school_id=self.school_id,
                    mock_exam_id=self.mock_exam_id,
                    student_id=foreign_student_id,
                    qr_token="tok-aluno-de-outra-escola",
                    template_version="enem-90x5-v1",
                ))
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_answer_card_models.py -v`
Expected: FAIL na coleta — `ImportError: cannot import name 'AnswerCard' from 'agente_ia_edu.db.models'`.

- [ ] **Step 3: Escrever o modelo**

Crie `src/agente_ia_edu/db/models/answer_card.py`:

```python
"""Simulados Fase 1 - o papel: cartao emitido, imagem capturada, marcas lidas
e fila do worker (spec s3 "Cartao e digitalizacao", s2.5 e s5).

``qr_token`` e OPACO por decisao de privacidade: o QR impresso no papel nao
carrega nome, matricula nem qualquer dado pessoal - apenas uma chave que so o
banco resolve (spec s3). A unicidade e global, nao por escola: o leitor optico
ve o token antes de saber de que prova - e portanto de que escola - a folha e.
E a unica coluna do subsistema deliberadamente FORA do escopo por escola.

``answer_card_scans.answer_card_id`` e NULO ate o QR ser lido. Isso nao e
frouxidao de modelagem: a imagem chega antes de qualquer identificacao, e um
scan cujo QR ficou ilegivel precisa existir como linha para poder ir para a
fila de conferencia humana em vez de sumir.

``answer_card_marks.fill_intensities`` guarda as CINCO intensidades medidas, e
nao apenas a conclusao, porque e isso que permite auditar uma leitura
contestada sem reprocessar a imagem (spec s3).

O operador que resolveu uma marca ambigua e guardado como IDENTIFICADOR
EXTERNO (``resolved_by_external_id``), nunca como FK para ``users`` - a mesma
regra dos demais atores do subsistema (``actor_external_id``,
``entered_by_external_id`` em mock_exam.py) e de
``AssessmentWorkflowAudit.performed_by_external_id``. A camada HTTP autentica
por identificador externo e sempre o tem; um UUID exigiria resolucao que pode
devolver nulo, esvaziando a coluna justamente quando alguem contesta a leitura.

``resolution`` tem TRES estados - ``PENDING | AUTO | HUMAN`` - porque
``detected_option`` nulo e ambiguo sozinho: pode ser item em branco (decidido,
o aluno nao marcou nada) ou item indeciso (esperando conferencia humana). Sem
``PENDING`` a fila de conferencia da s5 nao tem como ser montada, e o leitor
acabaria tendo que chutar - exatamente o que o principio "o leitor nunca chuta"
proibe. O padrao e ``PENDING``: uma marca nasce indecisa e so vira ``AUTO``
quando o leitor decidiu.

``omr_jobs`` e a fila: uma tabela Postgres consumida com
``SELECT ... FOR UPDATE SKIP LOCKED`` (spec s2.5). Nenhum consumidor e escrito
nesta fase - so a tabela. ``locked_at``/``locked_by`` existem para que um
worker morto possa ter o seu trabalho retomado por outro.

ISOLAMENTO MULTI-TENANT (spec s3.0): toda tabela carrega ``school_id`` e toda
FK entre elas e COMPOSTA, como em db/models/academic.py. Nenhuma destas tem FK
direta para ``schools``: a garantia chega pelo pai. ``UNIQUE(school_id, id)``
so em ``answer_cards`` e ``answer_card_scans``, que sao pais de outras tabelas.

SEM ``relationship()``, pela mesma razao registrada em mock_exam.py: com FKs
compostas varias relacoes escreveriam a mesma coluna ``school_id`` e exigiriam
``overlaps=``, e neste subsistema toda leitura e ``select`` explicito.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..types import JSONBCompatible

DEFAULT_BOOKLET_CODE = "UNICO"
SCAN_STATUSES = ("PENDING", "PROCESSED", "NEEDS_REVIEW", "FAILED")
SCAN_SOURCES = ("SCANNER", "MOBILE")
MARK_RESOLUTIONS = ("PENDING", "AUTO", "HUMAN")
OMR_JOB_STATUSES = ("QUEUED", "RUNNING", "DONE", "FAILED")
OPTION_CODES = ("A", "B", "C", "D", "E")

_SCAN_STATUS_SQL = ", ".join(f"'{v}'" for v in SCAN_STATUSES)
_SCAN_SOURCE_SQL = ", ".join(f"'{v}'" for v in SCAN_SOURCES)
_RESOLUTION_SQL = ", ".join(f"'{v}'" for v in MARK_RESOLUTIONS)
_JOB_STATUS_SQL = ", ".join(f"'{v}'" for v in OMR_JOB_STATUSES)
_OPTION_SQL = ", ".join(f"'{v}'" for v in OPTION_CODES)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AnswerCard(Base):
    """Um cartao-resposta nominal, emitido para um aluno num simulado."""

    __tablename__ = "answer_cards"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "mock_exam_id"],
            ["mock_exams.school_id", "mock_exams.id"],
            ondelete="CASCADE",
            name="fk_answer_cards_school_mock_exam",
        ),
        ForeignKeyConstraint(
            ["school_id", "student_id"],
            ["students.school_id", "students.id"],
            ondelete="RESTRICT",
            name="fk_answer_cards_school_student",
        ),
        UniqueConstraint("school_id", "id", name="uq_answer_cards_school_id_id"),
        UniqueConstraint("qr_token", name="uq_answer_cards_qr_token"),
        UniqueConstraint("mock_exam_id", "student_id", name="uq_answer_cards_exam_student"),
        Index("ix_answer_cards_mock_exam_id", "mock_exam_id"),
        Index("ix_answer_cards_student_id", "student_id"),
        Index("ix_answer_cards_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    mock_exam_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    booklet_code: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DEFAULT_BOOKLET_CODE
    )
    qr_token: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version: Mapped[str] = mapped_column(String(40), nullable=False)
    printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class AnswerCardScan(Base):
    """Uma imagem enviada. Pode ainda nao ter dono - o QR resolve isso depois."""

    __tablename__ = "answer_card_scans"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "mock_exam_id"],
            ["mock_exams.school_id", "mock_exams.id"],
            ondelete="CASCADE",
            name="fk_answer_card_scans_school_mock_exam",
        ),
        # answer_card_id e nulavel: com NULL, a FK composta (MATCH SIMPLE) nao
        # e checada, que e o comportamento desejado enquanto o QR nao foi lido.
        ForeignKeyConstraint(
            ["school_id", "answer_card_id"],
            ["answer_cards.school_id", "answer_cards.id"],
            ondelete="RESTRICT",
            name="fk_answer_card_scans_school_answer_card",
        ),
        UniqueConstraint("school_id", "id", name="uq_answer_card_scans_school_id_id"),
        CheckConstraint(f"status IN ({_SCAN_STATUS_SQL})", name="ck_answer_card_scans_status"),
        CheckConstraint(f"source IN ({_SCAN_SOURCE_SQL})", name="ck_answer_card_scans_source"),
        Index("ix_answer_card_scans_mock_exam_id", "mock_exam_id"),
        Index("ix_answer_card_scans_answer_card_id", "answer_card_id"),
        Index("ix_answer_card_scans_school_id", "school_id"),
        Index("ix_answer_card_scans_status", "status"),
        Index("ix_answer_card_scans_image_hash", "image_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    mock_exam_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    answer_card_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    image_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    reader_version: Mapped[str | None] = mapped_column(String(40))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class AnswerCardMark(Base):
    """A leitura de UMA questao num scan, com as cinco intensidades medidas."""

    __tablename__ = "answer_card_marks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "scan_id"],
            ["answer_card_scans.school_id", "answer_card_scans.id"],
            ondelete="CASCADE",
            name="fk_answer_card_marks_school_scan",
        ),
        UniqueConstraint("scan_id", "item_position", name="uq_answer_card_marks_scan_position"),
        CheckConstraint("item_position >= 1", name="ck_answer_card_marks_item_position"),
        CheckConstraint(
            f"detected_option IS NULL OR detected_option IN ({_OPTION_SQL})",
            name="ck_answer_card_marks_detected_option",
        ),
        CheckConstraint(
            f"resolved_option IS NULL OR resolved_option IN ({_OPTION_SQL})",
            name="ck_answer_card_marks_resolved_option",
        ),
        CheckConstraint(f"resolution IN ({_RESOLUTION_SQL})", name="ck_answer_card_marks_resolution"),
        Index("ix_answer_card_marks_scan_id", "scan_id"),
        Index("ix_answer_card_marks_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    scan_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    item_position: Mapped[int] = mapped_column(Integer, nullable=False)
    detected_option: Mapped[str | None] = mapped_column(String(1))
    fill_intensities: Mapped[list[Any] | None] = mapped_column(JSONBCompatible)
    confidence: Mapped[float | None] = mapped_column(Float)
    resolution: Mapped[str] = mapped_column(String(10), nullable=False, default="PENDING")
    resolved_option: Mapped[str | None] = mapped_column(String(1))
    resolved_by_external_id: Mapped[str | None] = mapped_column(String(255))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OmrJob(Base):
    """A fila do worker de leitura optica. Uma tabela Postgres, nao Celery."""

    __tablename__ = "omr_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "scan_id"],
            ["answer_card_scans.school_id", "answer_card_scans.id"],
            ondelete="CASCADE",
            name="fk_omr_jobs_school_scan",
        ),
        UniqueConstraint("scan_id", name="uq_omr_jobs_scan_id"),
        CheckConstraint(f"status IN ({_JOB_STATUS_SQL})", name="ck_omr_jobs_status"),
        CheckConstraint("attempts >= 0", name="ck_omr_jobs_attempts"),
        Index("ix_omr_jobs_status", "status"),
        Index("ix_omr_jobs_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    scan_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="QUEUED")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


__all__ = [
    "DEFAULT_BOOKLET_CODE",
    "MARK_RESOLUTIONS",
    "OMR_JOB_STATUSES",
    "SCAN_SOURCES",
    "SCAN_STATUSES",
    "AnswerCard",
    "AnswerCardMark",
    "AnswerCardScan",
    "OmrJob",
]
```

- [ ] **Step 4: Registrar no pacote**

Em `src/agente_ia_edu/db/models/__init__.py`, logo depois do bloco `from .mock_exam import (...)` criado na Task 1:

```python
from .answer_card import (
    DEFAULT_BOOKLET_CODE,
    MARK_RESOLUTIONS,
    OMR_JOB_STATUSES,
    SCAN_SOURCES,
    SCAN_STATUSES,
    AnswerCard,
    AnswerCardMark,
    AnswerCardScan,
    OmrJob,
)
```

e ao fim de `__all__`:

```python
    "DEFAULT_BOOKLET_CODE",
    "MARK_RESOLUTIONS",
    "OMR_JOB_STATUSES",
    "SCAN_SOURCES",
    "SCAN_STATUSES",
    "AnswerCard",
    "AnswerCardMark",
    "AnswerCardScan",
    "OmrJob",
```

Atenção: `answer_card.py` também define `OPTION_CODES`, igual ao de `mock_exam.py`. **Não** reexporte o de `answer_card`; o `OPTION_CODES` público do pacote é o da Task 1 (`mock_exam.py`). Os dois têm o mesmo valor `("A", "B", "C", "D", "E")` e o duplicado local existe só para o módulo não depender do outro.

- [ ] **Step 5: Rodar o teste e ver passar**

Run: `.venv/bin/python -m pytest tests/test_answer_card_models.py -v`
Expected: PASS (10 testes).

- [ ] **Step 6: Escrever a migration 058**

Crie `migrations/versions/058_answer_card_capture.py`:

```python
"""Simulados Fase 1 - cartao emitido, digitalizacao, marcas e fila do worker.

Revision ID: 058_answer_card_capture
Revises: 057_mock_exam_core

Segunda das tres migrations do subsistema (spec s3 "Cartao e digitalizacao").
Puramente aditiva.

Toda tabela carrega ``school_id`` e toda FK entre elas e COMPOSTA (spec s3.0).
A unica excecao deliberada e ``qr_token``, cujo indice unico e GLOBAL e nao por
escola: o leitor optico ve o token antes de saber de que escola a folha e.

``answer_card_scans.answer_card_id`` e nulavel porque a imagem existe antes de
o QR ser resolvido - e como a FK composta e MATCH SIMPLE, ela nao e checada
enquanto a coluna for NULL, que e exatamente o que se quer.
``answer_card_marks.resolution`` tem tres estados (PENDING | AUTO | HUMAN)
porque detected_option nulo sozinho nao distingue item em branco de item
indeciso, e sem essa distincao a fila de conferencia da s5 nao existe.
``omr_jobs.created_at`` existe para a fila ordenar por chegada: sem ele a
ordenacao cairia no id, que e UUID - estavel e arbitrario, mas nao FIFO.
``answer_card_marks.resolved_by_external_id`` e identificador EXTERNO
(String(255)), como todos os atores deste subsistema - quem transiciona status,
quem digita resposta e quem resolve marca ambigua seguem a mesma regra, a de
``AssessmentWorkflowAudit.performed_by_external_id``.
``omr_jobs`` e a fila consumida com FOR UPDATE SKIP LOCKED - nenhum consumidor
existe ainda nesta fase.
"""

from alembic import op
import sqlalchemy as sa

revision = "058_answer_card_capture"
down_revision = "057_mock_exam_core"
branch_labels = None
depends_on = None

OPTIONS = "'A', 'B', 'C', 'D', 'E'"
JSONB = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "answer_cards",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("mock_exam_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("booklet_code", sa.String(length=20), nullable=False, server_default="UNICO"),
        sa.Column("qr_token", sa.String(length=64), nullable=False),
        sa.Column("template_version", sa.String(length=40), nullable=False),
        sa.Column("printed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id", "mock_exam_id"],
                                ["mock_exams.school_id", "mock_exams.id"],
                                ondelete="CASCADE",
                                name="fk_answer_cards_school_mock_exam"),
        sa.ForeignKeyConstraint(["school_id", "student_id"],
                                ["students.school_id", "students.id"],
                                ondelete="RESTRICT",
                                name="fk_answer_cards_school_student"),
        sa.UniqueConstraint("school_id", "id", name="uq_answer_cards_school_id_id"),
        sa.UniqueConstraint("qr_token", name="uq_answer_cards_qr_token"),
        sa.UniqueConstraint("mock_exam_id", "student_id", name="uq_answer_cards_exam_student"),
    )
    op.create_index("ix_answer_cards_mock_exam_id", "answer_cards", ["mock_exam_id"])
    op.create_index("ix_answer_cards_student_id", "answer_cards", ["student_id"])
    op.create_index("ix_answer_cards_school_id", "answer_cards", ["school_id"])

    op.create_table(
        "answer_card_scans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("mock_exam_id", sa.Uuid(), nullable=False),
        sa.Column("answer_card_id", sa.Uuid(), nullable=True),
        sa.Column("image_hash", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("reader_version", sa.String(length=40), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id", "mock_exam_id"],
                                ["mock_exams.school_id", "mock_exams.id"],
                                ondelete="CASCADE",
                                name="fk_answer_card_scans_school_mock_exam"),
        sa.ForeignKeyConstraint(["school_id", "answer_card_id"],
                                ["answer_cards.school_id", "answer_cards.id"],
                                ondelete="RESTRICT",
                                name="fk_answer_card_scans_school_answer_card"),
        sa.UniqueConstraint("school_id", "id", name="uq_answer_card_scans_school_id_id"),
        sa.CheckConstraint("status IN ('PENDING', 'PROCESSED', 'NEEDS_REVIEW', 'FAILED')",
                           name="ck_answer_card_scans_status"),
        sa.CheckConstraint("source IN ('SCANNER', 'MOBILE')", name="ck_answer_card_scans_source"),
    )
    op.create_index("ix_answer_card_scans_mock_exam_id", "answer_card_scans", ["mock_exam_id"])
    op.create_index("ix_answer_card_scans_answer_card_id", "answer_card_scans", ["answer_card_id"])
    op.create_index("ix_answer_card_scans_school_id", "answer_card_scans", ["school_id"])
    op.create_index("ix_answer_card_scans_status", "answer_card_scans", ["status"])
    op.create_index("ix_answer_card_scans_image_hash", "answer_card_scans", ["image_hash"])

    op.create_table(
        "answer_card_marks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("scan_id", sa.Uuid(), nullable=False),
        sa.Column("item_position", sa.Integer(), nullable=False),
        sa.Column("detected_option", sa.String(length=1), nullable=True),
        sa.Column("fill_intensities", JSONB, nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("resolution", sa.String(length=10), nullable=False, server_default="PENDING"),
        sa.Column("resolved_option", sa.String(length=1), nullable=True),
        sa.Column("resolved_by_external_id", sa.String(length=255), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["school_id", "scan_id"],
                                ["answer_card_scans.school_id", "answer_card_scans.id"],
                                ondelete="CASCADE",
                                name="fk_answer_card_marks_school_scan"),
        sa.UniqueConstraint("scan_id", "item_position", name="uq_answer_card_marks_scan_position"),
        sa.CheckConstraint("item_position >= 1", name="ck_answer_card_marks_item_position"),
        sa.CheckConstraint(f"detected_option IS NULL OR detected_option IN ({OPTIONS})",
                           name="ck_answer_card_marks_detected_option"),
        sa.CheckConstraint(f"resolved_option IS NULL OR resolved_option IN ({OPTIONS})",
                           name="ck_answer_card_marks_resolved_option"),
        sa.CheckConstraint("resolution IN ('PENDING', 'AUTO', 'HUMAN')",
                           name="ck_answer_card_marks_resolution"),
    )
    op.create_index("ix_answer_card_marks_scan_id", "answer_card_marks", ["scan_id"])
    op.create_index("ix_answer_card_marks_school_id", "answer_card_marks", ["school_id"])

    op.create_table(
        "omr_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("scan_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="QUEUED"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id", "scan_id"],
                                ["answer_card_scans.school_id", "answer_card_scans.id"],
                                ondelete="CASCADE", name="fk_omr_jobs_school_scan"),
        sa.UniqueConstraint("scan_id", name="uq_omr_jobs_scan_id"),
        sa.CheckConstraint("status IN ('QUEUED', 'RUNNING', 'DONE', 'FAILED')",
                           name="ck_omr_jobs_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_omr_jobs_attempts"),
    )
    op.create_index("ix_omr_jobs_status", "omr_jobs", ["status"])
    op.create_index("ix_omr_jobs_school_id", "omr_jobs", ["school_id"])


def downgrade() -> None:
    for ix in ("ix_omr_jobs_school_id", "ix_omr_jobs_status"):
        op.drop_index(ix, table_name="omr_jobs")
    op.drop_table("omr_jobs")
    for ix in ("ix_answer_card_marks_school_id", "ix_answer_card_marks_scan_id"):
        op.drop_index(ix, table_name="answer_card_marks")
    op.drop_table("answer_card_marks")
    for ix in ("ix_answer_card_scans_image_hash", "ix_answer_card_scans_status",
               "ix_answer_card_scans_school_id", "ix_answer_card_scans_answer_card_id",
               "ix_answer_card_scans_mock_exam_id"):
        op.drop_index(ix, table_name="answer_card_scans")
    op.drop_table("answer_card_scans")
    for ix in ("ix_answer_cards_school_id", "ix_answer_cards_student_id",
               "ix_answer_cards_mock_exam_id"):
        op.drop_index(ix, table_name="answer_cards")
    op.drop_table("answer_cards")
```

- [ ] **Step 7: Verificar a cadeia de migrations**

Run: `.venv/bin/python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; s=ScriptDirectory.from_config(Config('alembic.ini')); print(s.get_current_head())"`
Expected: imprime `058_answer_card_capture`.

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/db/models/answer_card.py \
        src/agente_ia_edu/db/models/__init__.py \
        migrations/versions/058_answer_card_capture.py \
        tests/test_answer_card_models.py
git commit -m "feat(simulados): modelo de cartao, digitalizacao e fila de OMR (migration 058)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Régua, calibragem, parâmetros de item e notas (migration 059)

**Files:**
- Create: `src/agente_ia_edu/db/models/tri.py`
- Create: `migrations/versions/059_tri_scales_and_scores.py`
- Modify: `src/agente_ia_edu/db/models/__init__.py`
- Test: `tests/test_tri_models.py`

**Interfaces:**
- Consumes: `MockExam`, `MockExamItem` (Task 1), `Base`, `JSONBCompatible`, `schools.id`, `students.id`.
- Produces: `TriScale`, `TriCalibration`, `TriItemParameter`, `TriStudentScore`, e as constantes `TRI_MODELS = ("RASCH", "2PL", "3PL")`, `CALIBRATION_STATUSES`, `DEFAULT_THETA_MIN = -3.0`, `DEFAULT_THETA_MAX = 3.0`. Todos carregam `school_id: uuid.UUID` obrigatório com FKs compostas (spec §3.0); `tri_scales` e `tri_calibrations` ganham `UNIQUE(school_id, id)`. Migration head final da fase: `059_tri_scales_and_scores`.

**Nota de tipo (decisão desta task):** `theta`, `theta_se`, `a`, `b`, `c`, `se_a`, `se_b`, `p_value`, `point_biserial` e `log_likelihood` são `Float` (dupla precisão), não `Numeric`. São saídas de máxima verossimilhança: arredondá-las para escala decimal fixa destrói a reprodutibilidade de uma calibragem. Já `reference_min_score` / `reference_median_score` / `reference_max_score` / `scaled_score` / `percentile_class` / `percentile_school` são `Numeric`, porque são números *exibidos* — a mesma escolha já feita em `db/models/learning_path.py:151` para `mastery_score`.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_tri_models.py`:

```python
"""Simulados Fase 1, Task 3 - regua de reporte, calibragem, parametros de item
e notas (spec s3 "TRI", s6.3 e s6.5).

Postgres real e descartavel na porta 5433.

O teste mais importante aqui e o da regua NAO verificada: spec s6.3.1 diz que
"uma regua com reference_verified_at nulo NAO publica nota". Esta fase nao
implementa a publicacao, mas o modelo tem que conseguir REPRESENTAR esse
estado - regua criada, minimo e maximo preenchidos, mediana ainda nula e
verificacao pendente - senao a Fase 3 nasce sem onde guardar a diferenca.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    MockExam,
    MockExamItem,
    Person,
    School,
    Student,
    TriCalibration,
    TriItemParameter,
    TriScale,
    TriStudentScore,
)
from tests._postgres_test_db import create_database, drop_database


class TriModelPostgreSQLTests(unittest.TestCase):
    database_name = "agente_ia_edu_tri_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = os.getenv(
        "TRI_TEST_ADMIN_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres",
    )
    async_database_url = os.getenv(
        "TRI_TEST_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            cls._admin_execute("SELECT 1")
        except Exception as exc:
            raise unittest.SkipTest("PostgreSQL de teste indisponivel") from exc
        cls._drop_database()
        create_database(cls.admin_url, cls.database_name)
        cls.engine = create_async_engine(cls.async_database_url)
        cls.session_factory = async_sessionmaker(
            cls.engine, class_=AsyncSession, expire_on_commit=False
        )

        async def _prep():
            async with cls.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with cls.session_factory() as session:
                school = School(code="SIM-TRI", name="Escola TRI")
                session.add(school)
                await session.flush()
                year = AcademicYear(school_id=school.id, year=2026, status="ACTIVE")
                person = Person(school_id=school.id, full_name="Aluno TRI")
                session.add_all([year, person])
                await session.flush()
                student = Student(school_id=school.id, person_id=person.id)
                exam = MockExam(school_id=school.id, academic_year_id=year.id,
                                name="Simulado TRI", exam_day=1)
                session.add_all([student, exam])
                await session.flush()
                item = MockExamItem(school_id=school.id, mock_exam_id=exam.id,
                                    position=1, area_code="MT", correct_option="E")
                session.add(item)
                await session.commit()
                return school.id, exam.id, item.id, student.id

        (cls.school_id, cls.mock_exam_id, cls.item_id, cls.student_id) = asyncio.run(_prep())

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "engine"):
            asyncio.run(cls.engine.dispose())
        cls._drop_database()

    @classmethod
    def _admin_execute(cls, statement):
        engine = create_engine(
            cls.admin_url,
            connect_args={"autocommit": True},
            execution_options={"isolation_level": "AUTOCOMMIT"},
        )
        try:
            with engine.connect() as connection:
                return connection.execute(text(statement))
        finally:
            engine.dispose()

    @classmethod
    def _drop_database(cls):
        drop_database(cls.admin_url, cls.database_name)

    def _run(self, coro):
        return asyncio.run(coro)

    def _scale(self, area_code: str, **overrides) -> TriScale:
        values = dict(
            school_id=self.school_id,
            area_code=area_code,
            name=f"Regua {area_code} ENEM 2025",
            reference_label="ENEM 2025",
            reference_min_score=Decimal("312.6"),
            reference_max_score=Decimal("980.3"),
            reference_source="Portal educacional secundario (nao INEP)",
        )
        values.update(overrides)
        return TriScale(**values)

    def test_unverified_scale_without_median_is_representable(self):
        async def _scenario():
            async with self.session_factory() as session:
                scale = self._scale("MT")
                session.add(scale)
                await session.commit()
                stored = await session.get(TriScale, scale.id)
                self.assertIsNone(stored.reference_median_score)
                self.assertIsNone(stored.reference_verified_at)
                self.assertEqual(float(stored.reference_theta_min), -3.0)
                self.assertEqual(float(stored.reference_theta_max), 3.0)

        self._run(_scenario())

    def test_one_scale_per_school_and_area(self):
        async def _scenario():
            async with self.session_factory() as session:
                session.add(self._scale("CN"))
                await session.commit()
            async with self.session_factory() as session:
                session.add(self._scale("CN", name="Regua CN duplicada"))
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())

    def test_rejects_min_score_above_max_score(self):
        async def _scenario():
            async with self.session_factory() as session:
                session.add(self._scale(
                    "LC",
                    reference_min_score=Decimal("900.0"),
                    reference_max_score=Decimal("300.0"),
                ))
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())

    def test_rejects_theta_min_above_theta_max(self):
        async def _scenario():
            async with self.session_factory() as session:
                session.add(self._scale(
                    "CH", reference_theta_min=2.0, reference_theta_max=-2.0
                ))
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())

    def test_calibration_item_parameters_and_score_round_trip(self):
        async def _scenario():
            async with self.session_factory() as session:
                scale = self._scale("MT-E2E")
                session.add(scale)
                await session.flush()
                calibration = TriCalibration(
                    school_id=self.school_id,
                    mock_exam_id=self.mock_exam_id,
                    area_code="MT-E2E",
                    scale_id=scale.id,
                    model="2PL",
                    status="DONE",
                    n_examinees=1200,
                    n_items=45,
                    converged=True,
                    iterations=87,
                    log_likelihood=-31544.2071,
                    engine_version="tri-engine/0",
                )
                session.add(calibration)
                await session.flush()
                session.add(TriItemParameter(
                    school_id=self.school_id,
                    calibration_id=calibration.id,
                    item_id=self.item_id,
                    a=1.32, b=0.44, c=None,
                    se_a=0.09, se_b=0.07,
                    n_responses=1200, p_value=0.41, point_biserial=0.38,
                    is_fixed=False,
                    flags=["LOW_DISCRIMINATION"],
                ))
                session.add(TriStudentScore(
                    school_id=self.school_id,
                    calibration_id=calibration.id,
                    student_id=self.student_id,
                    area_code="MT-E2E",
                    theta=0.8123, theta_se=0.2011,
                    scaled_score=Decimal("623.40"),
                    raw_correct=27,
                    percentile_class=Decimal("72.00"),
                    percentile_school=Decimal("68.50"),
                ))
                await session.commit()

                stored = await session.get(TriCalibration, calibration.id)
                self.assertTrue(stored.converged)
                self.assertEqual(stored.engine_version, "tri-engine/0")

        self._run(_scenario())

    def test_rejects_unknown_tri_model(self):
        async def _scenario():
            async with self.session_factory() as session:
                scale = self._scale("MT-BAD-MODEL")
                session.add(scale)
                await session.flush()
                session.add(TriCalibration(
                    school_id=self.school_id, mock_exam_id=self.mock_exam_id,
                    area_code="MT-BAD-MODEL", scale_id=scale.id, model="4PL",
                    engine_version="tri-engine/0",
                ))
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())

    def test_one_parameter_row_per_calibration_and_item(self):
        async def _scenario():
            async with self.session_factory() as session:
                scale = self._scale("MT-DUP-PARAM")
                session.add(scale)
                await session.flush()
                calibration = TriCalibration(
                    school_id=self.school_id, mock_exam_id=self.mock_exam_id,
                    area_code="MT-DUP-PARAM", scale_id=scale.id, model="RASCH",
                    engine_version="tri-engine/0",
                )
                session.add(calibration)
                await session.flush()
                session.add_all([
                    TriItemParameter(school_id=self.school_id,
                                     calibration_id=calibration.id,
                                     item_id=self.item_id, a=1.0, b=0.0),
                    TriItemParameter(school_id=self.school_id,
                                     calibration_id=calibration.id,
                                     item_id=self.item_id, a=1.0, b=0.5),
                ])
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())

    def test_refuses_a_calibration_against_another_schools_scale(self):
        """Isolamento multi-tenant (spec s3.0).

        Uma calibragem de um simulado da escola A convertida pela regua da
        escola B sairia com numeros plausiveis e faixa errada - o erro mais
        caro possivel aqui, porque vira nota e nao levanta excecao nenhuma.
        Quem o recusa e a chave composta (school_id, scale_id).
        """
        async def _scenario():
            async with self.session_factory() as session:
                other = School(code="SIM-TRI-B", name="Escola Vizinha")
                session.add(other)
                await session.flush()
                foreign_scale = TriScale(
                    school_id=other.id, area_code="MT",
                    name="Regua MT da escola vizinha",
                    reference_min_score=Decimal("312.6"),
                    reference_max_score=Decimal("980.3"),
                    reference_source="Portal educacional secundario (nao INEP)",
                )
                session.add(foreign_scale)
                await session.commit()
                foreign_scale_id = foreign_scale.id

            async with self.session_factory() as session:
                session.add(TriCalibration(
                    school_id=self.school_id,        # escola A
                    mock_exam_id=self.mock_exam_id,  # escola A
                    area_code="MT-CROSS",
                    scale_id=foreign_scale_id,       # escola B
                    model="2PL", engine_version="tri-engine/0",
                ))
                with self.assertRaises(IntegrityError):
                    await session.flush()

        self._run(_scenario())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_tri_models.py -v`
Expected: FAIL na coleta — `ImportError: cannot import name 'TriScale' from 'agente_ia_edu.db.models'`.

- [ ] **Step 3: Escrever o modelo**

Crie `src/agente_ia_edu/db/models/tri.py`:

```python
"""Simulados Fase 1 - a regua de reporte e os resultados da TRI (spec s3 "TRI",
s6.3, s6.4 e s6.5). Nenhuma matematica e escrita aqui: esta fase entrega o
lugar onde ela vai gravar.

``tri_scales`` e a REGUA: uma por escola e por area. E contra ela que os itens
ancora sao travados (spec s6.4) e e ela que converte theta em nota exibida
(s6.3). Os tres valores de referencia sao cadastrados manualmente pela
coordenacao; ``reference_source`` e ``reference_verified_at`` registram de onde
vieram e quando foram conferidos, porque sem isso ninguem audita de onde saiu a
nota de um aluno dois anos depois. Regua com ``reference_verified_at`` nulo nao
publica nota - a regra e da Fase 3, a coluna que a torna possivel e desta.

``reference_median_score`` e NULAVEL de proposito: o spec s6.3.1 entrega minimo
e maximo do ENEM 2025 e deixa a mediana "a computar" a partir dos microdados.
Um numero inventado no lugar dela inflaria a nota de todo aluno mediano em mais
de 100 pontos (s6.3). Nulo e o estado honesto ate o calculo existir.

``tri_calibrations.engine_version`` nao e burocracia: quando o algoritmo for
melhorado, e preciso saber quais notas ja publicadas vieram de qual versao, sem
recalcular a historia inteira (spec s3).

ISOLAMENTO MULTI-TENANT (spec s3.0): toda tabela carrega ``school_id`` e toda
FK entre elas e COMPOSTA, como em db/models/academic.py. ``tri_scales`` e a
segunda raiz do subsistema (existe por escola, sem depender de simulado
nenhum), entao e a unica daqui com FK direta para ``schools``.
``UNIQUE(school_id, id)`` so em ``tri_scales`` e ``tri_calibrations``, que sao
pais de outras tabelas.

Tipos: os parametros estimados (a, b, c, erros-padrao, theta, log-verossimi-
lhanca) sao Float de dupla precisao, nao Numeric - sao saida de maxima
verossimilhanca, e arredonda-los para escala decimal fixa destruiria a
reprodutibilidade da calibragem. Os numeros EXIBIDOS (notas de referencia,
scaled_score, percentis) sao Numeric, como mastery_score em
db/models/learning_path.py.

SEM ``relationship()``, pela mesma razao registrada em mock_exam.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..types import JSONBCompatible

TRI_MODELS = ("RASCH", "2PL", "3PL")
CALIBRATION_STATUSES = ("PENDING", "RUNNING", "DONE", "FAILED")
DEFAULT_THETA_MIN = -3.0
DEFAULT_THETA_MAX = 3.0

_MODEL_SQL = ", ".join(f"'{v}'" for v in TRI_MODELS)
_CALIBRATION_STATUS_SQL = ", ".join(f"'{v}'" for v in CALIBRATION_STATUSES)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TriScale(Base):
    """A regua de reporte de uma area, numa escola. Segunda raiz do subsistema."""

    __tablename__ = "tri_scales"
    __table_args__ = (
        # base_mock_exam_id e nulavel; com NULL a FK composta (MATCH SIMPLE)
        # nao e checada, que e o desejado para uma regua ainda sem simulado base.
        ForeignKeyConstraint(
            ["school_id", "base_mock_exam_id"],
            ["mock_exams.school_id", "mock_exams.id"],
            ondelete="RESTRICT",
            name="fk_tri_scales_school_base_mock_exam",
        ),
        UniqueConstraint("school_id", "id", name="uq_tri_scales_school_id_id"),
        UniqueConstraint("school_id", "area_code", name="uq_tri_scales_school_area"),
        CheckConstraint(
            "reference_min_score < reference_max_score", name="ck_tri_scales_score_range"
        ),
        CheckConstraint(
            "reference_theta_min < reference_theta_max", name="ck_tri_scales_theta_range"
        ),
        CheckConstraint(
            "reference_median_score IS NULL OR ("
            "reference_median_score > reference_min_score AND "
            "reference_median_score < reference_max_score)",
            name="ck_tri_scales_median_between",
        ),
        Index("ix_tri_scales_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    area_code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_mock_exam_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reference_label: Mapped[str | None] = mapped_column(String(120))
    reference_min_score: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    reference_median_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    reference_max_score: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    reference_theta_min: Mapped[float] = mapped_column(
        Float, nullable=False, default=DEFAULT_THETA_MIN
    )
    reference_theta_max: Mapped[float] = mapped_column(
        Float, nullable=False, default=DEFAULT_THETA_MAX
    )
    reference_source: Mapped[str | None] = mapped_column(Text)
    reference_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class TriCalibration(Base):
    """Uma rodada de calibragem de uma area de um simulado contra uma regua."""

    __tablename__ = "tri_calibrations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "mock_exam_id"],
            ["mock_exams.school_id", "mock_exams.id"],
            ondelete="CASCADE",
            name="fk_tri_calibrations_school_mock_exam",
        ),
        # A regua tem que ser da MESMA escola do simulado, senao a nota do
        # aluno sai convertida por uma faixa que nao e a da escola dele.
        ForeignKeyConstraint(
            ["school_id", "scale_id"],
            ["tri_scales.school_id", "tri_scales.id"],
            ondelete="RESTRICT",
            name="fk_tri_calibrations_school_scale",
        ),
        UniqueConstraint("school_id", "id", name="uq_tri_calibrations_school_id_id"),
        UniqueConstraint(
            "mock_exam_id", "area_code", "engine_version",
            name="uq_tri_calibrations_exam_area_engine",
        ),
        CheckConstraint(f"model IN ({_MODEL_SQL})", name="ck_tri_calibrations_model"),
        CheckConstraint(
            f"status IN ({_CALIBRATION_STATUS_SQL})", name="ck_tri_calibrations_status"
        ),
        Index("ix_tri_calibrations_mock_exam_id", "mock_exam_id"),
        Index("ix_tri_calibrations_scale_id", "scale_id"),
        Index("ix_tri_calibrations_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    mock_exam_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    area_code: Mapped[str] = mapped_column(String(20), nullable=False)
    scale_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    model: Mapped[str] = mapped_column(String(10), nullable=False, default="2PL")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    n_examinees: Mapped[int | None] = mapped_column(Integer)
    n_items: Mapped[int | None] = mapped_column(Integer)
    converged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    iterations: Mapped[int | None] = mapped_column(Integer)
    log_likelihood: Mapped[float | None] = mapped_column(Float)
    engine_version: Mapped[str] = mapped_column(String(40), nullable=False)
    calibrated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class TriItemParameter(Base):
    """Os parametros estimados de um item nesta calibragem.

    ``flags`` carrega os sinais do spec s6.5 que avisam sem bloquear
    (discriminacao negativa, discriminacao baixa, item degenerado). Esta fase
    nao os calcula; a coluna existe para que a Fase 3 nao precise de migration.
    """

    __tablename__ = "tri_item_parameters"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "calibration_id"],
            ["tri_calibrations.school_id", "tri_calibrations.id"],
            ondelete="CASCADE",
            name="fk_tri_item_parameters_school_calibration",
        ),
        ForeignKeyConstraint(
            ["school_id", "item_id"],
            ["mock_exam_items.school_id", "mock_exam_items.id"],
            ondelete="CASCADE",
            name="fk_tri_item_parameters_school_item",
        ),
        UniqueConstraint(
            "calibration_id", "item_id", name="uq_tri_item_parameters_calibration_item"
        ),
        Index("ix_tri_item_parameters_calibration_id", "calibration_id"),
        Index("ix_tri_item_parameters_item_id", "item_id"),
        Index("ix_tri_item_parameters_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    calibration_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    a: Mapped[float | None] = mapped_column(Float)
    b: Mapped[float | None] = mapped_column(Float)
    c: Mapped[float | None] = mapped_column(Float)
    se_a: Mapped[float | None] = mapped_column(Float)
    se_b: Mapped[float | None] = mapped_column(Float)
    n_responses: Mapped[int | None] = mapped_column(Integer)
    p_value: Mapped[float | None] = mapped_column(Float)
    point_biserial: Mapped[float | None] = mapped_column(Float)
    is_fixed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    flags: Mapped[list[Any] | None] = mapped_column(JSONBCompatible)


class TriStudentScore(Base):
    """A nota de um aluno numa area, saida de uma calibragem."""

    __tablename__ = "tri_student_scores"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "calibration_id"],
            ["tri_calibrations.school_id", "tri_calibrations.id"],
            ondelete="CASCADE",
            name="fk_tri_student_scores_school_calibration",
        ),
        ForeignKeyConstraint(
            ["school_id", "student_id"],
            ["students.school_id", "students.id"],
            ondelete="RESTRICT",
            name="fk_tri_student_scores_school_student",
        ),
        UniqueConstraint(
            "calibration_id", "student_id", name="uq_tri_student_scores_calibration_student"
        ),
        Index("ix_tri_student_scores_calibration_id", "calibration_id"),
        Index("ix_tri_student_scores_student_id", "student_id"),
        Index("ix_tri_student_scores_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    calibration_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    area_code: Mapped[str] = mapped_column(String(20), nullable=False)
    theta: Mapped[float | None] = mapped_column(Float)
    theta_se: Mapped[float | None] = mapped_column(Float)
    scaled_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    raw_correct: Mapped[int | None] = mapped_column(Integer)
    percentile_class: Mapped[float | None] = mapped_column(Numeric(5, 2))
    percentile_school: Mapped[float | None] = mapped_column(Numeric(5, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


__all__ = [
    "CALIBRATION_STATUSES",
    "DEFAULT_THETA_MAX",
    "DEFAULT_THETA_MIN",
    "TRI_MODELS",
    "TriCalibration",
    "TriItemParameter",
    "TriScale",
    "TriStudentScore",
]
```

- [ ] **Step 4: Registrar no pacote**

Em `src/agente_ia_edu/db/models/__init__.py`, depois do bloco `from .answer_card import (...)`:

```python
from .tri import (
    CALIBRATION_STATUSES,
    DEFAULT_THETA_MAX,
    DEFAULT_THETA_MIN,
    TRI_MODELS,
    TriCalibration,
    TriItemParameter,
    TriScale,
    TriStudentScore,
)
```

e ao fim de `__all__`:

```python
    "CALIBRATION_STATUSES",
    "DEFAULT_THETA_MAX",
    "DEFAULT_THETA_MIN",
    "TRI_MODELS",
    "TriCalibration",
    "TriItemParameter",
    "TriScale",
    "TriStudentScore",
```

- [ ] **Step 5: Rodar o teste e ver passar**

Run: `.venv/bin/python -m pytest tests/test_tri_models.py -v`
Expected: PASS (8 testes).

- [ ] **Step 6: Escrever a migration 059**

Crie `migrations/versions/059_tri_scales_and_scores.py`:

```python
"""Simulados Fase 1 - regua de reporte, calibragem, parametros e notas.

Revision ID: 059_tri_scales_and_scores
Revises: 058_answer_card_capture

Terceira e ultima migration do modelo de dados do subsistema (spec s3 "TRI").
Puramente aditiva. Nenhuma matematica e escrita nesta fase - so o lugar onde
ela vai gravar.

Toda tabela carrega ``school_id`` e toda FK entre elas e COMPOSTA (spec s3.0).
``tri_scales`` e a segunda raiz do subsistema, e por isso a unica daqui com FK
direta para ``schools``. A chave composta ``(school_id, scale_id)`` em
``tri_calibrations`` e o que impede a nota de um aluno de sair convertida pela
regua de outra escola.

``reference_median_score`` e NULAVEL: o spec s6.3.1 entrega minimo e maximo do
ENEM 2025 e deixa a mediana a computar a partir dos microdados. Nulo e o estado
honesto ate o calculo existir; um valor inventado inflaria a nota de todo aluno
mediano. O CHECK garante que, quando ela existir, esteja ENTRE minimo e maximo.
"""

from alembic import op
import sqlalchemy as sa

revision = "059_tri_scales_and_scores"
down_revision = "058_answer_card_capture"
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "tri_scales",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("area_code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("base_mock_exam_id", sa.Uuid(), nullable=True),
        sa.Column("reference_label", sa.String(length=120), nullable=True),
        sa.Column("reference_min_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("reference_median_score", sa.Numeric(6, 2), nullable=True),
        sa.Column("reference_max_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("reference_theta_min", sa.Float(), nullable=False, server_default="-3.0"),
        sa.Column("reference_theta_max", sa.Float(), nullable=False, server_default="3.0"),
        sa.Column("reference_source", sa.Text(), nullable=True),
        sa.Column("reference_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT",
                                name="fk_tri_scales_school"),
        sa.ForeignKeyConstraint(["school_id", "base_mock_exam_id"],
                                ["mock_exams.school_id", "mock_exams.id"],
                                ondelete="RESTRICT",
                                name="fk_tri_scales_school_base_mock_exam"),
        sa.UniqueConstraint("school_id", "id", name="uq_tri_scales_school_id_id"),
        sa.UniqueConstraint("school_id", "area_code", name="uq_tri_scales_school_area"),
        sa.CheckConstraint("reference_min_score < reference_max_score",
                           name="ck_tri_scales_score_range"),
        sa.CheckConstraint("reference_theta_min < reference_theta_max",
                           name="ck_tri_scales_theta_range"),
        sa.CheckConstraint(
            "reference_median_score IS NULL OR ("
            "reference_median_score > reference_min_score AND "
            "reference_median_score < reference_max_score)",
            name="ck_tri_scales_median_between",
        ),
    )
    op.create_index("ix_tri_scales_school_id", "tri_scales", ["school_id"])

    op.create_table(
        "tri_calibrations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("mock_exam_id", sa.Uuid(), nullable=False),
        sa.Column("area_code", sa.String(length=20), nullable=False),
        sa.Column("scale_id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.String(length=10), nullable=False, server_default="2PL"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("n_examinees", sa.Integer(), nullable=True),
        sa.Column("n_items", sa.Integer(), nullable=True),
        sa.Column("converged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("iterations", sa.Integer(), nullable=True),
        sa.Column("log_likelihood", sa.Float(), nullable=True),
        sa.Column("engine_version", sa.String(length=40), nullable=False),
        sa.Column("calibrated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id", "mock_exam_id"],
                                ["mock_exams.school_id", "mock_exams.id"],
                                ondelete="CASCADE",
                                name="fk_tri_calibrations_school_mock_exam"),
        sa.ForeignKeyConstraint(["school_id", "scale_id"],
                                ["tri_scales.school_id", "tri_scales.id"],
                                ondelete="RESTRICT",
                                name="fk_tri_calibrations_school_scale"),
        sa.UniqueConstraint("school_id", "id", name="uq_tri_calibrations_school_id_id"),
        sa.UniqueConstraint("mock_exam_id", "area_code", "engine_version",
                            name="uq_tri_calibrations_exam_area_engine"),
        sa.CheckConstraint("model IN ('RASCH', '2PL', '3PL')", name="ck_tri_calibrations_model"),
        sa.CheckConstraint("status IN ('PENDING', 'RUNNING', 'DONE', 'FAILED')",
                           name="ck_tri_calibrations_status"),
    )
    op.create_index("ix_tri_calibrations_mock_exam_id", "tri_calibrations", ["mock_exam_id"])
    op.create_index("ix_tri_calibrations_scale_id", "tri_calibrations", ["scale_id"])
    op.create_index("ix_tri_calibrations_school_id", "tri_calibrations", ["school_id"])

    op.create_table(
        "tri_item_parameters",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("calibration_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("a", sa.Float(), nullable=True),
        sa.Column("b", sa.Float(), nullable=True),
        sa.Column("c", sa.Float(), nullable=True),
        sa.Column("se_a", sa.Float(), nullable=True),
        sa.Column("se_b", sa.Float(), nullable=True),
        sa.Column("n_responses", sa.Integer(), nullable=True),
        sa.Column("p_value", sa.Float(), nullable=True),
        sa.Column("point_biserial", sa.Float(), nullable=True),
        sa.Column("is_fixed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("flags", JSONB, nullable=True),
        sa.ForeignKeyConstraint(["school_id", "calibration_id"],
                                ["tri_calibrations.school_id", "tri_calibrations.id"],
                                ondelete="CASCADE",
                                name="fk_tri_item_parameters_school_calibration"),
        sa.ForeignKeyConstraint(["school_id", "item_id"],
                                ["mock_exam_items.school_id", "mock_exam_items.id"],
                                ondelete="CASCADE",
                                name="fk_tri_item_parameters_school_item"),
        sa.UniqueConstraint("calibration_id", "item_id",
                            name="uq_tri_item_parameters_calibration_item"),
    )
    op.create_index("ix_tri_item_parameters_calibration_id", "tri_item_parameters",
                    ["calibration_id"])
    op.create_index("ix_tri_item_parameters_item_id", "tri_item_parameters", ["item_id"])
    op.create_index("ix_tri_item_parameters_school_id", "tri_item_parameters", ["school_id"])

    op.create_table(
        "tri_student_scores",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("calibration_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("area_code", sa.String(length=20), nullable=False),
        sa.Column("theta", sa.Float(), nullable=True),
        sa.Column("theta_se", sa.Float(), nullable=True),
        sa.Column("scaled_score", sa.Numeric(6, 2), nullable=True),
        sa.Column("raw_correct", sa.Integer(), nullable=True),
        sa.Column("percentile_class", sa.Numeric(5, 2), nullable=True),
        sa.Column("percentile_school", sa.Numeric(5, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id", "calibration_id"],
                                ["tri_calibrations.school_id", "tri_calibrations.id"],
                                ondelete="CASCADE",
                                name="fk_tri_student_scores_school_calibration"),
        sa.ForeignKeyConstraint(["school_id", "student_id"],
                                ["students.school_id", "students.id"],
                                ondelete="RESTRICT",
                                name="fk_tri_student_scores_school_student"),
        sa.UniqueConstraint("calibration_id", "student_id",
                            name="uq_tri_student_scores_calibration_student"),
    )
    op.create_index("ix_tri_student_scores_calibration_id", "tri_student_scores",
                    ["calibration_id"])
    op.create_index("ix_tri_student_scores_student_id", "tri_student_scores", ["student_id"])
    op.create_index("ix_tri_student_scores_school_id", "tri_student_scores", ["school_id"])


def downgrade() -> None:
    for ix in ("ix_tri_student_scores_school_id", "ix_tri_student_scores_student_id",
               "ix_tri_student_scores_calibration_id"):
        op.drop_index(ix, table_name="tri_student_scores")
    op.drop_table("tri_student_scores")
    for ix in ("ix_tri_item_parameters_school_id", "ix_tri_item_parameters_item_id",
               "ix_tri_item_parameters_calibration_id"):
        op.drop_index(ix, table_name="tri_item_parameters")
    op.drop_table("tri_item_parameters")
    for ix in ("ix_tri_calibrations_school_id", "ix_tri_calibrations_scale_id",
               "ix_tri_calibrations_mock_exam_id"):
        op.drop_index(ix, table_name="tri_calibrations")
    op.drop_table("tri_calibrations")
    op.drop_index("ix_tri_scales_school_id", table_name="tri_scales")
    op.drop_table("tri_scales")
```

- [ ] **Step 7: Aplicar a cadeia 057→059 num banco descartável de verdade**

O `Base.metadata.create_all` dos testes NÃO exercita as migrations. Este passo exercita.

Run:
```bash
.venv/bin/python - <<'PY'
import os, uuid
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

user = os.getenv("POSTGRES_USER", "agenteedu")
password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
admin = f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres"
db = "agente_ia_edu_migr_" + uuid.uuid4().hex[:8]
eng = create_engine(admin, execution_options={"isolation_level": "AUTOCOMMIT"})
with eng.connect() as c:
    c.execute(text(f"CREATE DATABASE {db}"))
url = f"postgresql+psycopg://{user}:{password}@localhost:5433/{db}"
cfg = Config("alembic.ini"); cfg.set_main_option("sqlalchemy.url", url)
try:
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "056_material_assignments")
    command.upgrade(cfg, "head")
    print("MIGRATION CHAIN OK")
finally:
    with eng.connect() as c:
        c.execute(text(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{db}' AND pid<>pg_backend_pid()"))
        c.execute(text(f"DROP DATABASE IF EXISTS {db}"))
PY
```
Expected: imprime `MIGRATION CHAIN OK`. Qualquer traceback aqui é um `downgrade()` assimétrico e precisa ser corrigido antes de seguir.

- [ ] **Step 8: Rodar a suíte inteira**

Run: `.venv/bin/python -m pytest -q`
Expected: nenhuma falha nova em relação ao baseline.

- [ ] **Step 9: Commit**

```bash
git add src/agente_ia_edu/db/models/tri.py \
        src/agente_ia_edu/db/models/__init__.py \
        migrations/versions/059_tri_scales_and_scores.py \
        tests/test_tri_models.py
git commit -m "feat(simulados): modelo de regua, calibragem e notas de TRI (migration 059)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Seed da régua de referência do ENEM (YAML + loader + script das medianas)

**Files:**
- Create: `src/agente_ia_edu/data/__init__.py`
- Create: `src/agente_ia_edu/data/enem_reference_scales.yaml`
- Create: `src/agente_ia_edu/data/reference_scales.py`
- Create: `scripts/compute_enem_reference_medians.py`
- Modify: `pyproject.toml` (bloco `[tool.setuptools.package-data]`, hoje só com `agente_ia_edu.rubrics`)
- Test: `tests/test_enem_reference_scales.py`

**Interfaces:**
- Consumes: `PyYAML` (já dependência — `pyproject.toml:18`); o padrão de package-data + loader validador de `src/agente_ia_edu/rubrics/loader.py`.
- Produces:
  - `ReferenceScale` — dataclass congelada com `area_code: str`, `label: str`, `display_order: int`, `min_score: float`, `median_score: float | None`, `max_score: float`, `theta_min: float`, `theta_max: float`, `source: str`, `verified: bool`, `median_status: str`.
  - `ReferenceScaleFile` — dataclass congelada com `edition: str`, `scales: tuple[ReferenceScale, ...]`, e o método `by_area(area_code: str) -> ReferenceScale`.
  - `load_reference_scales(name: str = "enem_reference_scales") -> ReferenceScaleFile`.
  - `median_from_microdata_rows(rows: Iterable[Mapping[str, str]], column: str) -> float`.
  - `ReferenceScaleFileError(ValueError)`.
  - `MEDIAN_STATUSES = ("PENDING_MICRODATA", "COMPUTED")`, `MICRODATA_COLUMNS: dict[str, str]` mapeando `area_code → coluna NU_NOTA_*`.
- Consumida depois por: a Fase 3, ao pré-preencher uma `TriScale` (Task 3) para a coordenação confirmar. Esta fase **não** grava nenhuma linha em `tri_scales` — cadastrar a régua é ato da coordenação (spec §6.3.1) e a rota é Fase 3.

**Decisão registrada:** a mediana fica `null` no seed. O spec §6.3.1 dá mínimo e máximo do ENEM 2025 e diz que as medianas são "a computar" a partir das colunas `NU_NOTA_*` dos microdados. Inventar um número aqui inflaria a nota de todo aluno mediano (§6.3 estima >100 pontos em Matemática). O loader recusa um arquivo que diga `median_status: COMPUTED` sem mediana, e recusa um que traga mediana com `median_status: PENDING_MICRODATA` — os dois estados inconsistentes.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_enem_reference_scales.py`:

```python
"""Simulados Fase 1, Task 4 - seed da regua de referencia do ENEM
(spec s6.3.1 de docs/superpowers/specs/2026-09-29-correcao-simulados-tri-design.md).

Teste puro, sem banco: o arquivo YAML e o artefato revisavel, e o loader existe
para recusar um arquivo estruturalmente invalido antes que ele vire nota de
aluno - mesmo papel de rubrics/loader.py.

Os minimos e maximos aqui NAO sao numeros de enfeite: sao os valores do spec
s6.3.1, e este teste e o que impede que alguem os "ajuste" sem passar por
revisao. As medianas estao nulas de proposito.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.data.reference_scales import (
    MICRODATA_COLUMNS,
    ReferenceScaleFileError,
    load_reference_scales,
    median_from_microdata_rows,
    parse_reference_scale_mapping,
)


class ReferenceScaleFileTests(unittest.TestCase):
    def setUp(self):
        self.file = load_reference_scales()

    def test_declares_the_four_enem_areas_in_order(self):
        self.assertEqual(
            [scale.area_code for scale in self.file.scales],
            ["LC", "CH", "CN", "MT"],
        )
        self.assertEqual([s.display_order for s in self.file.scales], [1, 2, 3, 4])

    def test_min_and_max_match_the_spec_table(self):
        expected = {
            "LC": (309.2, 794.5),
            "CH": (320.8, 856.4),
            "CN": (308.6, 858.7),
            "MT": (312.6, 980.3),
        }
        for area_code, (minimum, maximum) in expected.items():
            scale = self.file.by_area(area_code)
            self.assertAlmostEqual(scale.min_score, minimum, places=4, msg=area_code)
            self.assertAlmostEqual(scale.max_score, maximum, places=4, msg=area_code)

    def test_every_median_is_pending_and_unverified(self):
        for scale in self.file.scales:
            self.assertIsNone(scale.median_score, scale.area_code)
            self.assertEqual(scale.median_status, "PENDING_MICRODATA", scale.area_code)
            # spec s6.3.1: numeros de terceira mao nao viram nota em silencio.
            self.assertFalse(scale.verified, scale.area_code)
            self.assertIn("INEP", scale.source)

    def test_theta_extremes_default_to_minus_three_and_three(self):
        for scale in self.file.scales:
            self.assertEqual(scale.theta_min, -3.0)
            self.assertEqual(scale.theta_max, 3.0)

    def test_by_area_rejects_unknown_code(self):
        with self.assertRaises(ReferenceScaleFileError):
            self.file.by_area("RED")

    def test_microdata_column_per_area(self):
        self.assertEqual(
            MICRODATA_COLUMNS,
            {"LC": "NU_NOTA_LC", "CH": "NU_NOTA_CH",
             "CN": "NU_NOTA_CN", "MT": "NU_NOTA_MT"},
        )


class ReferenceScaleValidationTests(unittest.TestCase):
    def _raw(self, **overrides):
        scale = {
            "area_code": "MT", "label": "Matematica", "display_order": 1,
            "min_score": 312.6, "median_score": None, "max_score": 980.3,
            "median_status": "PENDING_MICRODATA",
        }
        scale.update(overrides)
        return {
            "edition": "ENEM 2025",
            "source": "Portal educacional secundario, nao INEP",
            "verified": False,
            "scales": [scale],
        }

    def test_rejects_median_declared_computed_without_a_value(self):
        with self.assertRaises(ReferenceScaleFileError):
            parse_reference_scale_mapping(self._raw(median_status="COMPUTED"))

    def test_rejects_value_still_marked_pending(self):
        with self.assertRaises(ReferenceScaleFileError):
            parse_reference_scale_mapping(self._raw(median_score=520.0))

    def test_rejects_median_outside_min_max(self):
        with self.assertRaises(ReferenceScaleFileError):
            parse_reference_scale_mapping(
                self._raw(median_score=1200.0, median_status="COMPUTED")
            )

    def test_rejects_min_above_max(self):
        with self.assertRaises(ReferenceScaleFileError):
            parse_reference_scale_mapping(self._raw(min_score=990.0, max_score=300.0))

    def test_accepts_a_computed_median_between_min_and_max(self):
        parsed = parse_reference_scale_mapping(
            self._raw(median_score=521.4, median_status="COMPUTED")
        )
        self.assertEqual(parsed.by_area("MT").median_score, 521.4)


class MicrodataMedianTests(unittest.TestCase):
    def test_median_ignores_blank_scores(self):
        rows = [
            {"NU_NOTA_MT": "400.0"},
            {"NU_NOTA_MT": ""},       # ausente: sem nota, nao entra
            {"NU_NOTA_MT": "600.0"},
            {"NU_NOTA_MT": "   "},
            {"NU_NOTA_MT": "500.0"},
        ]
        self.assertEqual(median_from_microdata_rows(rows, "NU_NOTA_MT"), 500.0)

    def test_median_accepts_comma_decimal_separator(self):
        rows = [{"NU_NOTA_LC": "310,5"}, {"NU_NOTA_LC": "509,5"}]
        self.assertEqual(median_from_microdata_rows(rows, "NU_NOTA_LC"), 410.0)

    def test_rejects_column_absent_from_the_rows(self):
        with self.assertRaises(ReferenceScaleFileError):
            median_from_microdata_rows([{"NU_NOTA_CN": "500.0"}], "NU_NOTA_MT")

    def test_rejects_an_empty_sample(self):
        with self.assertRaises(ReferenceScaleFileError):
            median_from_microdata_rows([{"NU_NOTA_MT": ""}], "NU_NOTA_MT")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_enem_reference_scales.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'agente_ia_edu.data'`.

- [ ] **Step 3: Escrever o seed YAML**

Crie `src/agente_ia_edu/data/__init__.py`:

```python
"""Dados de referencia versionados (YAML como package-data).

Mesmo padrao de ``agente_ia_edu.rubrics``: o arquivo e o artefato revisavel em
pull request, e o codigo so o le e valida.
"""
```

Crie `src/agente_ia_edu/data/enem_reference_scales.yaml`:

```yaml
# src/agente_ia_edu/data/enem_reference_scales.yaml
#
# Faixa de nota por area do ENEM 2025, usada para PRE-PREENCHER a regua de
# reporte de uma escola (spec s6.3.1 de
# docs/superpowers/specs/2026-09-29-correcao-simulados-tri-design.md).
#
# ---------------------------------------------------------------------------
# PROCEDENCIA - LEIA ANTES DE USAR ESTES NUMEROS
# ---------------------------------------------------------------------------
# Os minimos e maximos abaixo vieram de PORTAL EDUCACIONAL SECUNDARIO, NAO do
# INEP direto. Por isso `verified: false`. Uma regua nao verificada NAO publica
# nota: o sistema calcula, exibe marcado como provisorio e exige confirmacao da
# coordenacao. Numeros de terceira mao nao viram nota de aluno em silencio.
#
# As MEDIANAS estao nulas de proposito. O spec manda calcula-las a partir das
# colunas NU_NOTA_* dos microdados publicos do ENEM 2025, que trazem a nota de
# cada participante. Rode scripts/compute_enem_reference_medians.py sobre o
# arquivo de microdados baixado e traga o resultado para ca numa revisao, junto
# com `median_status: COMPUTED`.
#
# NAO estime uma mediana. Um mapeamento linear simples entre minimo e maximo
# infla a nota do miolo da distribuicao: em Matematica o ponto medio entre
# minimo e maximo e 646, contra media nacional real na casa dos 520 - mais de
# 100 pontos de inflacao para todo aluno mediano (spec s6.3).
#
# A nota resultante e uma ESCALA DE REFERENCIA DA ESCOLA, legivel na faixa do
# ENEM. NAO e previsao de nota do ENEM.
# ---------------------------------------------------------------------------

edition: "ENEM 2025"
source: >-
  Portal educacional secundario (nao INEP direto); minimos e maximos por area
  da edicao 2025, pendentes de conferencia contra a fonte oficial do INEP.
verified: false
theta_min: -3.0
theta_max: 3.0

scales:
  - area_code: "LC"
    label: "Linguagens, Codigos e suas Tecnologias"
    display_order: 1
    min_score: 309.2
    median_score: null
    max_score: 794.5
    median_status: "PENDING_MICRODATA"

  - area_code: "CH"
    label: "Ciencias Humanas e suas Tecnologias"
    display_order: 2
    min_score: 320.8
    median_score: null
    max_score: 856.4
    median_status: "PENDING_MICRODATA"

  - area_code: "CN"
    label: "Ciencias da Natureza e suas Tecnologias"
    display_order: 3
    min_score: 308.6
    median_score: null
    max_score: 858.7
    median_status: "PENDING_MICRODATA"

  - area_code: "MT"
    label: "Matematica e suas Tecnologias"
    display_order: 4
    min_score: 312.6
    median_score: null
    max_score: 980.3
    median_status: "PENDING_MICRODATA"
```

- [ ] **Step 4: Escrever o loader**

Crie `src/agente_ia_edu/data/reference_scales.py`:

```python
"""Le e valida estruturalmente o seed da regua de referencia do ENEM.

O YAML e o artefato revisavel; este loader recusa um arquivo incoerente antes
que ele pre-preencha uma regua e vire nota de aluno. Mesmo papel, e mesmo
formato, de ``agente_ia_edu.rubrics.loader``.

A regra central: mediana e ``median_status`` tem que concordar. COMPUTED sem
valor, ou valor ainda marcado PENDING_MICRODATA, sao os dois jeitos de um
arquivo mentir sobre a procedencia de um numero - e os dois sao recusados.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

# Dono unico: quem declara a coluna declara o default dela. Importar de
# ``db.models.tri`` (e nao redeclarar aqui) garante que o fallback deste
# loader e o mesmo valor que o banco grava quando o YAML omite a faixa.
from ..db.models.tri import DEFAULT_THETA_MAX, DEFAULT_THETA_MIN

_DATA_DIR = Path(__file__).parent

MEDIAN_STATUSES = ("PENDING_MICRODATA", "COMPUTED")
MICRODATA_COLUMNS = {
    "LC": "NU_NOTA_LC",
    "CH": "NU_NOTA_CH",
    "CN": "NU_NOTA_CN",
    "MT": "NU_NOTA_MT",
}


class ReferenceScaleFileError(ValueError):
    """O arquivo de referencia e invalido e nao pode pre-preencher uma regua."""


@dataclass(frozen=True)
class ReferenceScale:
    area_code: str
    label: str
    display_order: int
    min_score: float
    median_score: float | None
    max_score: float
    theta_min: float
    theta_max: float
    source: str
    verified: bool
    median_status: str


@dataclass(frozen=True)
class ReferenceScaleFile:
    edition: str
    scales: tuple[ReferenceScale, ...]

    def by_area(self, area_code: str) -> ReferenceScale:
        for scale in self.scales:
            if scale.area_code == area_code:
                return scale
        known = ", ".join(scale.area_code for scale in self.scales)
        raise ReferenceScaleFileError(
            f"Area desconhecida {area_code!r}; conhecidas: {known}"
        )


def load_reference_scales(name: str = "enem_reference_scales") -> ReferenceScaleFile:
    """Carrega ``<name>.yaml`` deste pacote."""
    path = _DATA_DIR / f"{name}.yaml"
    if not path.exists():
        raise ReferenceScaleFileError(f"Arquivo de referencia desconhecido {name!r} ({path})")
    return parse_reference_scale_mapping(yaml.safe_load(path.read_text(encoding="utf-8")))


def parse_reference_scale_mapping(raw: Any) -> ReferenceScaleFile:
    if not isinstance(raw, dict):
        raise ReferenceScaleFileError("O arquivo de referencia deve ser um mapeamento")

    edition = _required(raw, "edition")
    source = _required(raw, "source")
    verified = bool(raw.get("verified", False))
    theta_min = float(raw.get("theta_min", DEFAULT_THETA_MIN))
    theta_max = float(raw.get("theta_max", DEFAULT_THETA_MAX))
    if theta_min >= theta_max:
        raise ReferenceScaleFileError(
            f"theta_min ({theta_min}) deve ser menor que theta_max ({theta_max})"
        )

    entries = raw.get("scales")
    if not isinstance(entries, list) or not entries:
        raise ReferenceScaleFileError("O arquivo deve declarar ao menos uma area em 'scales'")

    scales = tuple(
        _parse_scale(entry, source=source, verified=verified,
                     theta_min=theta_min, theta_max=theta_max)
        for entry in entries
    )
    codes = [scale.area_code for scale in scales]
    if len(set(codes)) != len(codes):
        raise ReferenceScaleFileError(f"Codigos de area repetidos: {codes}")

    return ReferenceScaleFile(edition=edition, scales=scales)


def _parse_scale(
    raw: Any, *, source: str, verified: bool, theta_min: float, theta_max: float
) -> ReferenceScale:
    if not isinstance(raw, dict):
        raise ReferenceScaleFileError("Cada area deve ser um mapeamento")

    area_code = _required(raw, "area_code")
    min_score = float(_required(raw, "min_score"))
    max_score = float(_required(raw, "max_score"))
    if min_score >= max_score:
        raise ReferenceScaleFileError(
            f"Area {area_code!r}: min_score ({min_score}) deve ser menor que "
            f"max_score ({max_score})"
        )

    median_status = raw.get("median_status", "PENDING_MICRODATA")
    if median_status not in MEDIAN_STATUSES:
        raise ReferenceScaleFileError(
            f"Area {area_code!r} declara median_status desconhecido {median_status!r}"
        )

    median_raw = raw.get("median_score")
    median_score = None if median_raw is None else float(median_raw)
    if median_status == "COMPUTED" and median_score is None:
        raise ReferenceScaleFileError(
            f"Area {area_code!r} diz median_status COMPUTED mas nao traz median_score"
        )
    if median_status == "PENDING_MICRODATA" and median_score is not None:
        raise ReferenceScaleFileError(
            f"Area {area_code!r} traz median_score mas ainda diz PENDING_MICRODATA; "
            "marque COMPUTED e registre de onde o numero veio"
        )
    if median_score is not None and not (min_score < median_score < max_score):
        raise ReferenceScaleFileError(
            f"Area {area_code!r}: median_score ({median_score}) deve ficar entre "
            f"min_score ({min_score}) e max_score ({max_score})"
        )

    return ReferenceScale(
        area_code=area_code,
        label=_required(raw, "label"),
        display_order=int(_required(raw, "display_order")),
        min_score=min_score,
        median_score=median_score,
        max_score=max_score,
        theta_min=theta_min,
        theta_max=theta_max,
        source=source,
        verified=verified,
        median_status=median_status,
    )


def median_from_microdata_rows(rows: Iterable[Mapping[str, str]], column: str) -> float:
    """Mediana da coluna ``column`` (ex. ``NU_NOTA_MT``) dos microdados do ENEM.

    Celula vazia significa participante sem nota naquela area (ausente) e e
    descartada - nunca convertida para 0.0, o que puxaria a mediana para baixo
    e inventaria desempenho que ninguem teve. O separador decimal dos microdados
    do INEP varia entre ponto e virgula conforme o corte; os dois sao aceitos.
    """
    values: list[float] = []
    saw_column = False
    for row in rows:
        if column not in row:
            continue
        saw_column = True
        cell = (row[column] or "").strip()
        if not cell:
            continue
        values.append(float(cell.replace(",", ".")))
    if not saw_column:
        raise ReferenceScaleFileError(f"Coluna {column!r} ausente nos microdados")
    if not values:
        raise ReferenceScaleFileError(f"Nenhuma nota valida na coluna {column!r}")
    return float(statistics.median(values))


def _required(raw: Mapping[str, Any], field: str) -> Any:
    if field not in raw or raw[field] in (None, ""):
        raise ReferenceScaleFileError(f"Campo obrigatorio ausente: {field!r}")
    return raw[field]


__all__ = [
    "MEDIAN_STATUSES",
    "MICRODATA_COLUMNS",
    "ReferenceScale",
    "ReferenceScaleFile",
    "ReferenceScaleFileError",
    "load_reference_scales",
    "median_from_microdata_rows",
    "parse_reference_scale_mapping",
]
```

- [ ] **Step 5: Declarar o YAML como package-data**

Em `pyproject.toml`, no bloco `[tool.setuptools.package-data]` (que hoje tem só a linha do `rubrics`), acrescente a segunda linha:

```toml
[tool.setuptools.package-data]
"agente_ia_edu.rubrics" = ["*.yaml"]
"agente_ia_edu.data" = ["*.yaml"]
```

- [ ] **Step 6: Rodar o teste e ver passar**

Run: `.venv/bin/python -m pytest tests/test_enem_reference_scales.py -v`
Expected: PASS (15 testes).

- [ ] **Step 7: Escrever o script das medianas**

Crie `scripts/compute_enem_reference_medians.py`:

```python
#!/usr/bin/env python
"""Calcula as medianas de nota por area a partir dos microdados do ENEM.

O arquivo de microdados do INEP e publico, tem alguns gigabytes e NAO e
versionado neste repositorio. Baixe-o, aponte este script para o CSV de
participantes (``MICRODADOS_ENEM_<ano>.csv``) e leve o resultado para
``src/agente_ia_edu/data/enem_reference_scales.yaml`` numa revisao, trocando
tambem ``median_status`` para ``COMPUTED``.

Toda a logica de fato vive em
``agente_ia_edu.data.reference_scales.median_from_microdata_rows``, que tem
teste. Este arquivo e so a casca de linha de comando: ele abre o CSV e imprime.

Uso:
    python scripts/compute_enem_reference_medians.py MICRODADOS_ENEM_2025.csv
    python scripts/compute_enem_reference_medians.py arquivo.csv --encoding utf-8 --delimiter ,
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agente_ia_edu.data.reference_scales import (  # noqa: E402
    MICRODATA_COLUMNS,
    ReferenceScaleFileError,
    median_from_microdata_rows,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, help="CSV de participantes do INEP")
    parser.add_argument("--encoding", default="latin-1",
                        help="Codificacao do CSV (o INEP costuma publicar em latin-1)")
    parser.add_argument("--delimiter", default=";",
                        help="Delimitador do CSV (o INEP costuma usar ';')")
    args = parser.parse_args(argv)

    if not args.csv_path.is_file():
        print(f"Arquivo nao encontrado: {args.csv_path}", file=sys.stderr)
        return 2

    # O CSV e lido UMA vez por area, de propósito: o arquivo do INEP nao cabe
    # em memoria como lista, e reler e mais barato que guardar tudo.
    for area_code, column in MICRODATA_COLUMNS.items():
        with open(args.csv_path, newline="", encoding=args.encoding) as handle:
            reader = csv.DictReader(handle, delimiter=args.delimiter)
            try:
                median = median_from_microdata_rows(reader, column)
            except ReferenceScaleFileError as exc:
                print(f"{area_code}: ERRO - {exc}", file=sys.stderr)
                return 1
        print(f"{area_code}: median_score: {median:.1f}   # coluna {column}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 8: Verificar o script contra um CSV minúsculo de mentira**

Run:
```bash
printf 'NU_NOTA_LC;NU_NOTA_CH;NU_NOTA_CN;NU_NOTA_MT\n300,0;300,0;300,0;300,0\n700,0;700,0;700,0;700,0\n' > /tmp/microdados_fake.csv
.venv/bin/python scripts/compute_enem_reference_medians.py /tmp/microdados_fake.csv --encoding utf-8
```
Expected: quatro linhas, cada uma `XX: median_score: 500.0   # coluna NU_NOTA_XX`. Depois: `rm /tmp/microdados_fake.csv`.

- [ ] **Step 9: Commit**

```bash
git add src/agente_ia_edu/data/__init__.py \
        src/agente_ia_edu/data/enem_reference_scales.yaml \
        src/agente_ia_edu/data/reference_scales.py \
        scripts/compute_enem_reference_medians.py \
        pyproject.toml \
        tests/test_enem_reference_scales.py
git commit -m "feat(simulados): seed da regua de referencia do ENEM 2025 com mediana pendente

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Marcadores ArUco pré-gerados e versionados

**Files:**
- Create: `scripts/generate_aruco_markers.py`
- Create (saída do script, commitada): `src/agente_ia_edu/simulado_card/assets/aruco/markers.yaml`, `aruco_4x4_50_id0.png`, `aruco_4x4_50_id1.png`, `aruco_4x4_50_id2.png`, `aruco_4x4_50_id3.png`
- Create: `src/agente_ia_edu/simulado_card/__init__.py`
- Create: `src/agente_ia_edu/simulado_card/markers.py`
- Modify: `pyproject.toml` (`[tool.setuptools.package-data]`)
- Test: `tests/test_simulado_card_markers.py`

**Interfaces:**
- Consumes: `PyYAML` (já dependência). **Nada mais** — `markers.py` é stdlib + yaml, sem ReportLab e sem cv2.
- Produces:
  - `CORNERS: tuple[str, ...] = ("TOP_LEFT", "TOP_RIGHT", "BOTTOM_RIGHT", "BOTTOM_LEFT")`
  - `ArucoMarker` — dataclass congelada: `corner: str`, `marker_id: int`, `filename: str`, `side_px: int`, `sha256: str`, e a propriedade `path: Path`.
  - `MarkerSet` — dataclass congelada: `dictionary: str`, `grid_cells: int`, `markers: tuple[ArucoMarker, ...]`, método `by_corner(corner: str) -> ArucoMarker`.
  - `load_marker_set() -> MarkerSet` (valida existência, dimensões e sha256 de cada PNG).
  - `png_dimensions(path: Path) -> tuple[int, int]` — lê o IHDR em stdlib, sem Pillow.
  - `MarkerAssetError(ValueError)`, `ASSETS_DIR: Path`.
- Consumida por: Task 6 (posição geométrica dos marcadores) e Task 7 (o renderer desenha o PNG).

**Por que assim:** um dicionário ArUco é um conjunto fixo e pequeno de padrões conhecidos. Gerá-los em tempo de execução exigiria OpenCV no core, que é exatamente o que a fronteira do §2.3 do spec existe para impedir. Os quatro são gerados **uma vez**, num virtualenv descartável, e versionados como imagem estática que o ReportLab apenas posiciona.

- [ ] **Step 1: Escrever o script gerador**

Crie `scripts/generate_aruco_markers.py`:

```python
#!/usr/bin/env python
"""Gera UMA VEZ os quatro marcadores ArUco de canto do cartao-resposta.

LEIA ISTO ANTES DE RODAR
------------------------
Este script depende de ``opencv-contrib-python``, que NAO e - e nunca deve ser -
dependencia deste projeto: OpenCV convive com Pillow, e o pyproject.toml proibe
Pillow porque ele muda o comportamento de ``page.images`` do pypdf e quebra o
parser de ingestao de questoes. Rode-o num virtualenv DESCARTAVEL, fora do
.venv do projeto:

    python3 -m venv /tmp/aruco-venv
    /tmp/aruco-venv/bin/pip install "opencv-contrib-python>=4.8,<5" pyyaml
    /tmp/aruco-venv/bin/python scripts/generate_aruco_markers.py
    rm -rf /tmp/aruco-venv

A saida - quatro PNGs e um markers.yaml - vai para
src/agente_ia_edu/simulado_card/assets/aruco/ e e COMMITADA. Depois disso,
nada no runtime gera marcador nenhum: o ReportLab so posiciona a imagem.

DICT_4X4_50, ids 0 a 3: dicionario pequeno (4x4 bits de dado + borda preta de
uma celula = grade de 6x6), que e o que se quer aqui. Quanto menos bits, mais
robusta a deteccao numa foto de celular tremida - e quatro padroes e tudo o que
este cartao precisa distinguir.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import yaml

DICTIONARY_NAME = "DICT_4X4_50"
SIDE_PX = 600          # 100 px por celula da grade 6x6: borda nitida na impressao
GRID_CELLS = 6         # 4x4 de dado + 1 celula de borda preta de cada lado
CORNERS = ("TOP_LEFT", "TOP_RIGHT", "BOTTOM_RIGHT", "BOTTOM_LEFT")

ASSETS_DIR = (
    Path(__file__).resolve().parents[1]
    / "src" / "agente_ia_edu" / "simulado_card" / "assets" / "aruco"
)


def main() -> int:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    dictionary = cv2.aruco.getPredefinedDictionary(
        getattr(cv2.aruco, DICTIONARY_NAME)
    )

    entries = []
    for marker_id, corner in enumerate(CORNERS):
        filename = f"aruco_4x4_50_id{marker_id}.png"
        path = ASSETS_DIR / filename
        image = cv2.aruco.generateImageMarker(dictionary, marker_id, SIDE_PX)
        if not cv2.imwrite(str(path), image):
            raise RuntimeError(f"cv2.imwrite falhou para {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append({
            "corner": corner,
            "marker_id": marker_id,
            "filename": filename,
            "side_px": SIDE_PX,
            "sha256": digest,
        })
        print(f"{corner}: {filename} ({digest[:12]}...)")

    manifest = {
        "dictionary": DICTIONARY_NAME,
        "grid_cells": GRID_CELLS,
        "generated_by": "scripts/generate_aruco_markers.py",
        "markers": entries,
    }
    (ASSETS_DIR / "markers.yaml").write_text(
        "# Gerado por scripts/generate_aruco_markers.py num virtualenv descartavel\n"
        "# com opencv-contrib-python. NAO edite a mao: os sha256 abaixo sao\n"
        "# verificados por tests/test_simulado_card_markers.py.\n"
        + yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"manifesto escrito em {ASSETS_DIR / 'markers.yaml'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Rodar o gerador no virtualenv descartável e commitar a saída**

Run:
```bash
python3 -m venv /tmp/aruco-venv
/tmp/aruco-venv/bin/pip install "opencv-contrib-python>=4.8,<5" pyyaml
/tmp/aruco-venv/bin/python scripts/generate_aruco_markers.py
rm -rf /tmp/aruco-venv
ls -la src/agente_ia_edu/simulado_card/assets/aruco/
```
Expected: quatro `.png` e um `markers.yaml` listados. Confirme que **nem opencv nem Pillow entraram no .venv do projeto**:
```bash
.venv/bin/python -c "import importlib.util as u; print('cv2', u.find_spec('cv2')); print('PIL', u.find_spec('PIL'))"
```
Expected: `cv2 None` e `PIL None`. Se qualquer um dos dois não for `None`, pare — o virtualenv errado foi usado.

- [ ] **Step 3: Escrever o teste que falha**

Crie `tests/test_simulado_card_markers.py`:

```python
"""Simulados Fase 1, Task 5 - os quatro ArUco pre-gerados (spec s2.1).

Teste puro, sem banco e sem ReportLab.

O ponto deste teste nao e "o arquivo existe". E que o manifesto e os PNGs
CONCORDAM: os assets sao gerados fora do projeto, num virtualenv descartavel
com OpenCV, e uma vez commitados ninguem mais os regenera. Se alguem
reotimizar, recortar ou reescalar um PNG a mao, o sha256 do manifesto denuncia
- e sem essa trava a leitura optica da Fase 4 passaria a procurar um padrao que
nao esta mais impresso.

O leitor de PNG aqui e o IHDR cru em stdlib, DE PROPOSITO: Pillow e proibido
neste projeto (pyproject.toml), e ler largura e altura de um PNG sao 8 bytes.
"""

from __future__ import annotations

import hashlib
import unittest

from agente_ia_edu.simulado_card.markers import (
    ASSETS_DIR,
    CORNERS,
    MarkerAssetError,
    load_marker_set,
    png_dimensions,
)


class ArucoMarkerAssetTests(unittest.TestCase):
    def setUp(self):
        self.marker_set = load_marker_set()

    def test_declares_the_four_corners_in_reading_order(self):
        self.assertEqual(
            [marker.corner for marker in self.marker_set.markers], list(CORNERS)
        )
        self.assertEqual(CORNERS, ("TOP_LEFT", "TOP_RIGHT", "BOTTOM_RIGHT", "BOTTOM_LEFT"))

    def test_uses_the_small_4x4_dictionary_with_distinct_ids(self):
        self.assertEqual(self.marker_set.dictionary, "DICT_4X4_50")
        ids = [marker.marker_id for marker in self.marker_set.markers]
        self.assertEqual(sorted(ids), [0, 1, 2, 3])

    def test_every_png_exists_and_is_square(self):
        for marker in self.marker_set.markers:
            self.assertTrue(marker.path.is_file(), marker.filename)
            width, height = png_dimensions(marker.path)
            self.assertEqual(width, height, marker.filename)
            self.assertEqual(width, marker.side_px, marker.filename)

    def test_committed_bytes_match_the_manifest_digest(self):
        for marker in self.marker_set.markers:
            digest = hashlib.sha256(marker.path.read_bytes()).hexdigest()
            self.assertEqual(digest, marker.sha256, marker.filename)

    def test_by_corner_resolves_and_rejects_nonsense(self):
        self.assertEqual(self.marker_set.by_corner("TOP_LEFT").marker_id, 0)
        with self.assertRaises(MarkerAssetError):
            self.marker_set.by_corner("MIDDLE")

    def test_png_dimensions_refuses_a_file_that_is_not_a_png(self):
        intruder = ASSETS_DIR / "markers.yaml"
        with self.assertRaises(MarkerAssetError):
            png_dimensions(intruder)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: Rodar o teste e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_simulado_card_markers.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'agente_ia_edu.simulado_card'`.

- [ ] **Step 5: Escrever o pacote e o loader**

Crie `src/agente_ia_edu/simulado_card/__init__.py`:

```python
"""Geracao do cartao-resposta do simulado (spec s2.1).

Este pacote NAO importa OpenCV, nao importa Pillow e nao fala com banco de
dados. ``markers``, ``template`` e ``layout`` sao stdlib puro; so ``renderer``
toca em ReportLab, e mesmo assim por import tardio dentro da funcao.
"""
```

Crie `src/agente_ia_edu/simulado_card/markers.py`:

```python
"""Carrega e valida os quatro ArUco pre-gerados que ancoram o cartao.

Os marcadores NAO sao gerados em tempo de execucao. Um dicionario ArUco e um
conjunto fixo e pequeno de padroes conhecidos; os quatro usados aqui foram
gerados uma vez por scripts/generate_aruco_markers.py, num virtualenv
descartavel com OpenCV, e versionados como PNG. E isso que permite ao gerador
de cartao nao depender de OpenCV, mantendo intacta a fronteira do spec s2.3.

O manifesto carrega o sha256 de cada PNG e este modulo o confere na carga: uma
imagem reotimizada ou reescalada a mao deixaria a leitura optica procurando um
padrao que nao esta mais impresso, e falhar alto na carga e melhor do que
descobrir isso com 1.500 folhas ja aplicadas.

Leitura de PNG em stdlib (cabecalho IHDR), nunca Pillow - proibido pelo
pyproject.toml deste projeto.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ASSETS_DIR = Path(__file__).parent / "assets" / "aruco"
MANIFEST_PATH = ASSETS_DIR / "markers.yaml"
CORNERS = ("TOP_LEFT", "TOP_RIGHT", "BOTTOM_RIGHT", "BOTTOM_LEFT")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class MarkerAssetError(ValueError):
    """Os assets de marcador estao ausentes ou nao batem com o manifesto."""


@dataclass(frozen=True)
class ArucoMarker:
    corner: str
    marker_id: int
    filename: str
    side_px: int
    sha256: str

    @property
    def path(self) -> Path:
        return ASSETS_DIR / self.filename


@dataclass(frozen=True)
class MarkerSet:
    dictionary: str
    grid_cells: int
    markers: tuple[ArucoMarker, ...]

    def by_corner(self, corner: str) -> ArucoMarker:
        for marker in self.markers:
            if marker.corner == corner:
                return marker
        raise MarkerAssetError(
            f"Canto desconhecido {corner!r}; conhecidos: {', '.join(CORNERS)}"
        )


def png_dimensions(path: Path) -> tuple[int, int]:
    """(largura, altura) de um PNG, lendo apenas o IHDR. Sem Pillow."""
    data = path.read_bytes()[:24]
    if len(data) < 24 or data[:8] != _PNG_SIGNATURE or data[12:16] != b"IHDR":
        raise MarkerAssetError(f"{path} nao e um PNG valido")
    return (
        int.from_bytes(data[16:20], "big"),
        int.from_bytes(data[20:24], "big"),
    )


def load_marker_set() -> MarkerSet:
    """Le markers.yaml e confere cada PNG (existencia, lado e sha256)."""
    if not MANIFEST_PATH.is_file():
        raise MarkerAssetError(
            f"Manifesto de marcadores ausente ({MANIFEST_PATH}). "
            "Rode scripts/generate_aruco_markers.py num virtualenv descartavel."
        )
    raw: Any = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise MarkerAssetError("markers.yaml deve ser um mapeamento")

    entries = raw.get("markers")
    if not isinstance(entries, list) or len(entries) != len(CORNERS):
        raise MarkerAssetError(f"markers.yaml deve declarar {len(CORNERS)} marcadores")

    markers: list[ArucoMarker] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise MarkerAssetError("Cada marcador deve ser um mapeamento")
        corner = entry.get("corner")
        if corner not in CORNERS:
            raise MarkerAssetError(f"Canto desconhecido no manifesto: {corner!r}")
        marker = ArucoMarker(
            corner=corner,
            marker_id=int(entry["marker_id"]),
            filename=str(entry["filename"]),
            side_px=int(entry["side_px"]),
            sha256=str(entry["sha256"]),
        )
        if not marker.path.is_file():
            raise MarkerAssetError(f"PNG de marcador ausente: {marker.path}")
        width, height = png_dimensions(marker.path)
        if (width, height) != (marker.side_px, marker.side_px):
            raise MarkerAssetError(
                f"{marker.filename}: manifesto diz {marker.side_px}px, "
                f"arquivo tem {width}x{height}"
            )
        digest = hashlib.sha256(marker.path.read_bytes()).hexdigest()
        if digest != marker.sha256:
            raise MarkerAssetError(
                f"{marker.filename}: sha256 do arquivo ({digest[:12]}...) nao bate "
                f"com o manifesto ({marker.sha256[:12]}...). O PNG foi alterado a mao."
            )
        markers.append(marker)

    ordered = tuple(
        next(m for m in markers if m.corner == corner) for corner in CORNERS
    )
    return MarkerSet(
        dictionary=str(raw.get("dictionary", "")),
        grid_cells=int(raw.get("grid_cells", 6)),
        markers=ordered,
    )


__all__ = [
    "ASSETS_DIR",
    "CORNERS",
    "MANIFEST_PATH",
    "ArucoMarker",
    "MarkerAssetError",
    "MarkerSet",
    "load_marker_set",
    "png_dimensions",
]
```

- [ ] **Step 6: Declarar os assets como package-data**

Em `pyproject.toml`, acrescente a terceira linha ao bloco:

```toml
[tool.setuptools.package-data]
"agente_ia_edu.rubrics" = ["*.yaml"]
"agente_ia_edu.data" = ["*.yaml"]
"agente_ia_edu.simulado_card" = ["assets/aruco/*.png", "assets/aruco/*.yaml"]
```

- [ ] **Step 7: Rodar o teste e ver passar**

Run: `.venv/bin/python -m pytest tests/test_simulado_card_markers.py -v`
Expected: PASS (6 testes).

- [ ] **Step 8: Commit**

```bash
git add scripts/generate_aruco_markers.py \
        src/agente_ia_edu/simulado_card/__init__.py \
        src/agente_ia_edu/simulado_card/markers.py \
        src/agente_ia_edu/simulado_card/assets/aruco/ \
        pyproject.toml \
        tests/test_simulado_card_markers.py
git commit -m "feat(simulados): marcadores ArUco pre-gerados e versionados, com manifesto conferido

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Template geométrico do cartão (geometria pura, sem ReportLab)

**Files:**
- Create: `src/agente_ia_edu/simulado_card/template.py`
- Create: `src/agente_ia_edu/simulado_card/layout.py`
- Test: `tests/test_simulado_card_layout.py`

**Interfaces:**
- Consumes: `CORNERS`, `MarkerSet`, `load_marker_set` (Task 5). Stdlib apenas.
- Produces (`template.py`):
  - `LAYOUT_VERSION: str = "enem-90x5-v1"`
  - `BubbleBox` — dataclass congelada: `item_position: int`, `option: str`, `cx: float`, `cy: float`, `radius: float` (tudo normalizado, adimensional).
  - `MarkerPlacement` — dataclass congelada: `corner: str`, `marker_id: int`, `cx: float`, `cy: float`, `size: float`.
  - `QrPlacement` — dataclass congelada: `cx: float`, `cy: float`, `size: float`.
  - `CardTemplate` — dataclass congelada: `template_version: str`, `item_count: int`, `option_codes: tuple[str, ...]`, `marker_rect_aspect: float`, `markers: tuple[MarkerPlacement, ...]`, `qr: QrPlacement`, `bubbles: tuple[BubbleBox, ...]`; métodos `bubbles_for(item_position: int) -> tuple[BubbleBox, ...]`, `to_dict() -> dict`, e o classmethod `from_dict(raw: dict) -> CardTemplate`.
  - `TemplateError(ValueError)`.
- Produces (`layout.py`):
  - `CardLayoutSpec` — dataclass congelada com todas as medidas em milímetros e valores-padrão (listados no código abaixo), mais `to_dict() -> dict[str, float | int]` e o classmethod `from_dict(raw: dict) -> CardLayoutSpec`.
  - `build_template(spec: CardLayoutSpec | None = None, *, marker_set: MarkerSet | None = None) -> CardTemplate`
  - `DEFAULT_LAYOUT: CardLayoutSpec`

**Por que `CardLayoutSpec` serializa.** O renderizador roda no ambiente isolado (Task 8) e precisa converter as coordenadas normalizadas de volta para milímetros na folha. Sem as medidas no manifesto, ele teria que *assumir* o layout padrão — e um cartão emitido com layout customizado sairia impresso errado, em silêncio.
- Consumida por: Task 7 (o renderer desenha a partir deste template) e Task 8 (grava o JSON).

**CONTRATO PÚBLICO — leia antes de mexer em qualquer nome deste módulo.** `CardTemplate` e as dataclasses que ela contém são **declaradas uma única vez, aqui**, e o leitor óptico da Fase 4 as **importa** — ele não redeclara uma cópia própria para ler o mesmo JSON (spec §2.1). Duas definições do mesmo contrato divergem em silêncio, e o sintoma de divergência aqui não é teste vermelho: é o leitor medindo a bolha errada e devolvendo a resposta errada. Gerador e leitor rodam no mesmo ambiente isolado justamente por serem as duas pontas deste contrato, e o módulo é stdlib puro (sem ReportLab no topo nem dentro de função) exatamente para que esse import seja seguro dos dois lados.

**As chaves exatas do JSON**, produzidas por `CardTemplate.to_dict()` e consumidas por `CardTemplate.from_dict()` — quem for escrever o leitor não precisa inferir nada:

| Nível | Chaves |
|---|---|
| raiz | `template_version`, `item_count`, `option_codes`, `marker_rect_aspect`, `markers`, `qr`, `bubbles` |
| cada item de `markers` | `corner`, `marker_id`, `cx`, `cy`, `size` |
| `qr` | `cx`, `cy`, `size` |
| cada item de `bubbles` | `item_position`, `option`, `cx`, `cy`, `radius` |

`corner` assume `TOP_LEFT`, `TOP_RIGHT`, `BOTTOM_RIGHT`, `BOTTOM_LEFT` (`markers.CORNERS`, nessa ordem); `option` assume `A`–`E` (`template.OPTION_CODES`); `item_position` é 1-based. Renomear qualquer uma dessas chaves é mudar `LAYOUT_VERSION` junto, senão folhas já impressas passam a ser lidas por um template que diz outra coisa.

**`marker_rect_aspect` é altura/largura do retângulo entre os centros dos marcadores** — `frame_height_mm / frame_width_mm`, que no layout padrão dá `257 / 180 = 1.4278`. Ela **não é derivável** das demais chaves: `MarkerPlacement.size` é um valor só, normalizado pela largura. O leitor da Fase 4 mapeia os quatro centros detectados nos cantos de um retângulo canônico em pixels; se a proporção em pixels não for a do impresso, o cartão retificado sai esticado, a bolha redonda vira elipse e a máscara circular mede fora do disco nas 450. Por isso `from_dict` a exige como campo obrigatório: o leitor **recusa** um template sem ela em vez de supor um valor.

**Sistema de coordenadas — a decisão central desta task.** As coordenadas normalizadas **não** são relativas à página A4. São relativas ao retângulo formado pelos **centros dos quatro marcadores ArUco**: `(0,0)` é o centro do marcador superior-esquerdo, `(1,1)` o do inferior-direito, `y` cresce para baixo. Isso é exatamente o que o leitor óptico da Fase 4 obtém depois de detectar os quatro cantos e calcular a homografia — ou seja, o template já vem no sistema de coordenadas em que ele vai ser consumido, sem nenhuma conversão intermediária que possa errar. O `radius` e os `size` são normalizados pela **largura** desse retângulo, para não deformar círculos.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_simulado_card_layout.py`:

```python
"""Simulados Fase 1, Task 6 - a geometria do cartao (spec s2.1).

Teste puro: nem banco, nem ReportLab, nem imagem.

O acoplamento entre gerador e template e a principal alavanca de robustez do
subsistema: o leitor nunca precisa DESCOBRIR onde as bolhas estao, porque quem
imprimiu ja disse. Este teste e o que garante que o que foi dito e coerente -
bolhas dentro do quadro dos marcadores, sem sobreposicao, uma linha por
questao, cinco colunas por linha.
"""

from __future__ import annotations

import json
import unittest

from agente_ia_edu.simulado_card.layout import DEFAULT_LAYOUT, CardLayoutSpec, build_template
from agente_ia_edu.simulado_card.template import LAYOUT_VERSION, CardTemplate, TemplateError


class CardTemplateGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = build_template()

    def test_default_layout_is_ninety_questions_of_five_options(self):
        self.assertEqual(DEFAULT_LAYOUT.item_count, 90)
        self.assertEqual(self.template.item_count, 90)
        self.assertEqual(self.template.option_codes, ("A", "B", "C", "D", "E"))
        self.assertEqual(len(self.template.bubbles), 450)
        self.assertEqual(self.template.template_version, LAYOUT_VERSION)

    def test_every_question_has_exactly_five_bubbles_in_option_order(self):
        for position in range(1, 91):
            row = self.template.bubbles_for(position)
            self.assertEqual(len(row), 5, position)
            self.assertEqual(
                tuple(bubble.option for bubble in row), ("A", "B", "C", "D", "E")
            )

    def test_bubbles_of_one_question_share_a_baseline_and_advance_to_the_right(self):
        row = self.template.bubbles_for(42)
        self.assertEqual(len({round(bubble.cy, 9) for bubble in row}), 1)
        xs = [bubble.cx for bubble in row]
        self.assertEqual(xs, sorted(xs))

    def test_questions_are_laid_out_in_three_columns_of_thirty(self):
        first_of_column = [
            self.template.bubbles_for(1)[0],
            self.template.bubbles_for(31)[0],
            self.template.bubbles_for(61)[0],
        ]
        # Cada coluna comeca mais a direita e na MESMA altura da anterior.
        xs = [bubble.cx for bubble in first_of_column]
        self.assertEqual(xs, sorted(xs))
        self.assertEqual(len(set(round(b.cy, 9) for b in first_of_column)), 1)
        # E a ultima questao de cada coluna esta abaixo da primeira.
        self.assertGreater(
            self.template.bubbles_for(30)[0].cy, self.template.bubbles_for(1)[0].cy
        )

    def test_everything_sits_strictly_inside_the_marker_frame(self):
        for bubble in self.template.bubbles:
            self.assertGreater(bubble.cx - bubble.radius, 0.0)
            self.assertLess(bubble.cx + bubble.radius, 1.0)
            self.assertGreater(bubble.cy - bubble.radius, 0.0)
            self.assertLess(bubble.cy + bubble.radius, 1.0)

    def test_no_two_bubbles_overlap(self):
        bubbles = self.template.bubbles
        by_row: dict[int, list] = {}
        for bubble in bubbles:
            by_row.setdefault(bubble.item_position, []).append(bubble)
        # Dentro da questao: distancia horizontal maior que a soma dos raios.
        for row in by_row.values():
            ordered = sorted(row, key=lambda b: b.cx)
            for left, right in zip(ordered, ordered[1:]):
                self.assertGreater(right.cx - left.cx, left.radius + right.radius)
        # Entre questoes consecutivas da mesma coluna: idem na vertical.
        for position in list(range(1, 30)) + list(range(31, 60)) + list(range(61, 90)):
            upper = self.template.bubbles_for(position)[0]
            lower = self.template.bubbles_for(position + 1)[0]
            self.assertGreater(lower.cy - upper.cy, upper.radius + lower.radius)

    def test_the_four_markers_sit_at_the_corners_of_the_unit_square(self):
        placements = {marker.corner: marker for marker in self.template.markers}
        self.assertEqual(
            set(placements), {"TOP_LEFT", "TOP_RIGHT", "BOTTOM_RIGHT", "BOTTOM_LEFT"}
        )
        self.assertAlmostEqual(placements["TOP_LEFT"].cx, 0.0, places=9)
        self.assertAlmostEqual(placements["TOP_LEFT"].cy, 0.0, places=9)
        self.assertAlmostEqual(placements["BOTTOM_RIGHT"].cx, 1.0, places=9)
        self.assertAlmostEqual(placements["BOTTOM_RIGHT"].cy, 1.0, places=9)
        self.assertAlmostEqual(placements["TOP_RIGHT"].cx, 1.0, places=9)
        self.assertAlmostEqual(placements["BOTTOM_LEFT"].cy, 1.0, places=9)
        self.assertEqual([m.marker_id for m in self.template.markers], [0, 1, 2, 3])

    def test_qr_does_not_collide_with_any_marker_or_bubble(self):
        qr = self.template.qr
        half = qr.size / 2
        for marker in self.template.markers:
            marker_half = marker.size / 2
            separated = (
                abs(qr.cx - marker.cx) > half + marker_half
                or abs(qr.cy - marker.cy) > half + marker_half
            )
            self.assertTrue(separated, marker.corner)
        for bubble in self.template.bubbles:
            separated = (
                abs(qr.cx - bubble.cx) > half + bubble.radius
                or abs(qr.cy - bubble.cy) > half + bubble.radius
            )
            self.assertTrue(separated, (bubble.item_position, bubble.option))


class CardTemplateSerializationTests(unittest.TestCase):
    def test_round_trips_through_dict(self):
        original = build_template()
        restored = CardTemplate.from_dict(original.to_dict())
        self.assertEqual(restored, original)

    def test_from_dict_rejects_an_unknown_template_version(self):
        raw = build_template().to_dict()
        raw["template_version"] = "enem-90x5-v9999"
        with self.assertRaises(TemplateError):
            CardTemplate.from_dict(raw)

    def test_from_dict_rejects_a_truncated_payload(self):
        raw = build_template().to_dict()
        del raw["bubbles"]
        with self.assertRaises(TemplateError):
            CardTemplate.from_dict(raw)

    def test_the_json_keys_are_exactly_the_published_contract(self):
        """Spec s2.1: o tipo do template e declarado UMA vez e o leitor o
        importa. Estas chaves sao contrato publico - o leitor da Fase 4 le por
        elas. Renomear qualquer uma sem mudar LAYOUT_VERSION faria folha ja
        impressa ser lida por um template que diz outra coisa, e o sintoma nao
        seria teste vermelho: seria resposta errada."""
        raw = build_template().to_dict()
        self.assertEqual(
            set(raw),
            {"template_version", "item_count", "option_codes", "marker_rect_aspect",
             "markers", "qr", "bubbles"},
        )
        self.assertEqual(
            set(raw["markers"][0]), {"corner", "marker_id", "cx", "cy", "size"}
        )
        self.assertEqual(set(raw["qr"]), {"cx", "cy", "size"})
        self.assertEqual(
            set(raw["bubbles"][0]),
            {"item_position", "option", "cx", "cy", "radius"},
        )
        self.assertEqual(raw["bubbles"][0]["item_position"], 1)  # 1-based

    def test_the_contract_survives_a_json_round_trip(self):
        # O leitor recebe o template como JSON, nao como objeto Python.
        original = build_template()
        restored = CardTemplate.from_dict(json.loads(json.dumps(original.to_dict())))
        self.assertEqual(restored, original)

    def test_marker_rect_aspect_is_the_printed_height_over_width(self):
        """Spec s2.1: sem esta razao o leitor retifica esticado.

        Ela nao e derivavel das outras chaves - MarkerPlacement.size e um valor
        so, normalizado pela largura - entao tem que vir explicita. No layout
        padrao o quadro dos marcadores tem 180 x 257 mm.
        """
        template = build_template()
        self.assertAlmostEqual(
            template.marker_rect_aspect,
            DEFAULT_LAYOUT.frame_height_mm / DEFAULT_LAYOUT.frame_width_mm,
            places=12,
        )
        self.assertAlmostEqual(template.marker_rect_aspect, 257.0 / 180.0, places=12)
        # Nao e 1.0: um cartao quadrado seria coincidencia, e supor 1.0 e
        # exatamente o erro que este campo existe para impedir.
        self.assertGreater(template.marker_rect_aspect, 1.0)

    def test_from_dict_refuses_a_template_without_the_aspect_ratio(self):
        raw = build_template().to_dict()
        del raw["marker_rect_aspect"]
        with self.assertRaises(TemplateError):
            CardTemplate.from_dict(raw)


class CardLayoutSpecTests(unittest.TestCase):
    def test_a_smaller_exam_produces_fewer_bubbles(self):
        spec = CardLayoutSpec(item_count=45, rows_per_column=15)
        template = build_template(spec)
        self.assertEqual(template.item_count, 45)
        self.assertEqual(len(template.bubbles), 225)
        self.assertEqual(len(template.bubbles_for(45)), 5)

    def test_rejects_a_layout_whose_columns_cannot_hold_every_question(self):
        # 3 colunas x 10 linhas = 30 lugares, mas 90 questoes foram pedidas.
        with self.assertRaises(ValueError):
            build_template(CardLayoutSpec(item_count=90, rows_per_column=10))

    def test_rejects_a_bubble_grid_that_would_spill_past_the_right_marker(self):
        with self.assertRaises(ValueError):
            build_template(CardLayoutSpec(bubble_pitch_mm=40.0))

    def test_round_trips_through_dict(self):
        # O ambiente isolado (renderizador) recebe estas medidas pelo manifesto
        # de impressao; se elas nao sobreviverem ao JSON, o cartao sai impresso
        # com a geometria errada e nada avisa.
        spec = CardLayoutSpec(item_count=45, rows_per_column=15)
        self.assertEqual(CardLayoutSpec.from_dict(spec.to_dict()), spec)
        self.assertEqual(CardLayoutSpec.from_dict(DEFAULT_LAYOUT.to_dict()), DEFAULT_LAYOUT)
        # JSON nao distingue 90 de 90.0; item_count tem que voltar int, senao
        # range(1, item_count + 1) quebra no renderizador.
        restored = CardLayoutSpec.from_dict(json.loads(json.dumps(DEFAULT_LAYOUT.to_dict())))
        self.assertIsInstance(restored.item_count, int)
        self.assertEqual(restored, DEFAULT_LAYOUT)

    def test_from_dict_rejects_an_unknown_field(self):
        raw = DEFAULT_LAYOUT.to_dict()
        raw["margem_secreta_mm"] = 3.0
        with self.assertRaises(ValueError):
            CardLayoutSpec.from_dict(raw)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_simulado_card_layout.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'agente_ia_edu.simulado_card.layout'`.

- [ ] **Step 3: Escrever `template.py`**

Crie `src/agente_ia_edu/simulado_card/template.py`:

```python
"""O template geometrico do cartao: onde cada bolha esta, em coordenadas
normalizadas (spec s2.1).

CONTRATO PUBLICO. ``CardTemplate`` e as dataclasses que ela contem sao
declaradas UMA VEZ, aqui, e o leitor optico da Fase 4 as IMPORTA - ele nao
redeclara uma copia propria para ler o mesmo JSON (spec s2.1). Duas definicoes
do mesmo contrato divergem em silencio, e o sintoma de divergencia aqui nao e
teste vermelho: e o leitor medindo a bolha errada e devolvendo a resposta
errada. Este modulo e stdlib puro justamente para que esse import seja seguro
dos dois lados da fronteira - nada de ReportLab aqui, nem no topo nem dentro de
funcao.

As chaves do JSON (``to_dict``/``from_dict``) sao parte do contrato:
  raiz      -> template_version, item_count, option_codes, marker_rect_aspect,
               markers, qr, bubbles
  markers[] -> corner, marker_id, cx, cy, size
  qr        -> cx, cy, size
  bubbles[] -> item_position (1-based), option, cx, cy, radius

``marker_rect_aspect`` e altura/largura do retangulo entre os centros dos
marcadores. Ela NAO e derivavel das outras chaves, e sem ela o leitor nao tem
como escolher um retangulo canonico em pixels com a mesma proporcao do
impresso: a homografia entrega um cartao esticado, a bolha redonda vira elipse
e a mascara circular mede fora do disco nas 450. ``from_dict`` a exige - o
leitor recusa um template sem ela em vez de supor um valor.
Renomear qualquer uma delas exige mudar ``LAYOUT_VERSION`` junto, senao folha
ja impressa passa a ser lida por um template que diz outra coisa.

O SISTEMA DE COORDENADAS IMPORTA E NAO E O DA PAGINA A4.
Tudo aqui e relativo ao retangulo formado pelos CENTROS dos quatro marcadores
ArUco: (0,0) e o centro do marcador superior-esquerdo, (1,1) o do
inferior-direito, e y cresce para baixo. Esse e exatamente o sistema que o
leitor optico obtem depois de detectar os quatro cantos e desfazer a
perspectiva - o template ja chega nas coordenadas em que vai ser consumido,
sem nenhuma conversao intermediaria que possa errar.

``radius`` e ``size`` sao normalizados pela LARGURA do retangulo (nao pela
altura), para que um circulo continue circular.

O acoplamento entre gerador e template e a principal alavanca de robustez do
subsistema: o leitor nunca precisa DESCOBRIR onde as bolhas estao, porque quem
imprimiu ja disse. Isso elimina a etapa mais fragil de qualquer OMR.

``LAYOUT_VERSION`` e gravado em ``answer_cards.template_version``: uma folha
impressa com uma versao nao pode ser lida com outra, e a coluna e o que torna
essa incompatibilidade detectavel em vez de silenciosa.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Muda sempre que a geometria ou as chaves do JSON mudarem: e por ele que uma
# folha impressa e o template que a le se reconhecem.
LAYOUT_VERSION = "enem-90x5-v1"
OPTION_CODES = ("A", "B", "C", "D", "E")


class TemplateError(ValueError):
    """O template serializado esta incompleto ou e de outra versao."""


@dataclass(frozen=True)
class BubbleBox:
    item_position: int
    option: str
    cx: float
    cy: float
    radius: float


@dataclass(frozen=True)
class MarkerPlacement:
    corner: str
    marker_id: int
    cx: float
    cy: float
    size: float


@dataclass(frozen=True)
class QrPlacement:
    cx: float
    cy: float
    size: float


@dataclass(frozen=True)
class CardTemplate:
    template_version: str
    item_count: int
    option_codes: tuple[str, ...]
    # Altura/largura do retangulo entre os CENTROS dos marcadores. Nao e
    # derivavel das outras chaves (``MarkerPlacement.size`` e um valor so,
    # normalizado pela largura) e o leitor precisa dela para escolher um
    # retangulo canonico em pixels com a MESMA proporcao do impresso. Sem isso
    # a homografia entrega um cartao esticado: a bolha redonda vira elipse e a
    # mascara circular mede fora do disco nas 450.
    marker_rect_aspect: float
    markers: tuple[MarkerPlacement, ...]
    qr: QrPlacement
    bubbles: tuple[BubbleBox, ...]

    def bubbles_for(self, item_position: int) -> tuple[BubbleBox, ...]:
        row = tuple(b for b in self.bubbles if b.item_position == item_position)
        if not row:
            raise TemplateError(f"Questao {item_position} nao existe neste template")
        return row

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_version": self.template_version,
            "item_count": self.item_count,
            "option_codes": list(self.option_codes),
            "marker_rect_aspect": self.marker_rect_aspect,
            "markers": [
                {"corner": m.corner, "marker_id": m.marker_id,
                 "cx": m.cx, "cy": m.cy, "size": m.size}
                for m in self.markers
            ],
            "qr": {"cx": self.qr.cx, "cy": self.qr.cy, "size": self.qr.size},
            "bubbles": [
                {"item_position": b.item_position, "option": b.option,
                 "cx": b.cx, "cy": b.cy, "radius": b.radius}
                for b in self.bubbles
            ],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> CardTemplate:
        if not isinstance(raw, dict):
            raise TemplateError("O template serializado deve ser um mapeamento")
        for field in ("template_version", "item_count", "option_codes",
                      "marker_rect_aspect", "markers", "qr", "bubbles"):
            if field not in raw:
                # ``marker_rect_aspect`` entra nesta lista de proposito: o
                # leitor optico RECUSA um template sem ela em vez de supor um
                # valor, e este ``from_dict`` e o mesmo codigo que ele usa.
                raise TemplateError(f"Template sem o campo obrigatorio {field!r}")
        version = raw["template_version"]
        if version != LAYOUT_VERSION:
            raise TemplateError(
                f"Template versao {version!r}; este codigo so le {LAYOUT_VERSION!r}"
            )
        qr = raw["qr"]
        return cls(
            template_version=version,
            item_count=int(raw["item_count"]),
            option_codes=tuple(raw["option_codes"]),
            marker_rect_aspect=float(raw["marker_rect_aspect"]),
            markers=tuple(
                MarkerPlacement(
                    corner=m["corner"], marker_id=int(m["marker_id"]),
                    cx=float(m["cx"]), cy=float(m["cy"]), size=float(m["size"]),
                )
                for m in raw["markers"]
            ),
            qr=QrPlacement(cx=float(qr["cx"]), cy=float(qr["cy"]), size=float(qr["size"])),
            bubbles=tuple(
                BubbleBox(
                    item_position=int(b["item_position"]), option=b["option"],
                    cx=float(b["cx"]), cy=float(b["cy"]), radius=float(b["radius"]),
                )
                for b in raw["bubbles"]
            ),
        )


__all__ = [
    "LAYOUT_VERSION",
    "OPTION_CODES",
    "BubbleBox",
    "CardTemplate",
    "MarkerPlacement",
    "QrPlacement",
    "TemplateError",
]
```

- [ ] **Step 4: Escrever `layout.py`**

Crie `src/agente_ia_edu/simulado_card/layout.py`:

```python
"""A geometria do cartao, em milimetros, convertida para o template normalizado.

Medidas do cartao padrao (``DEFAULT_LAYOUT``), em A4 retrato de 210x297 mm,
90 questoes de 5 alternativas em 3 colunas de 30 - o modelo ENEM 1o dia que o
cartao de referencia do projeto usa (spec s1).

Os quatro marcadores ficam com os centros em x = 15 e 195 mm e y = 20 e 277 mm,
o que da um quadro de 180 x 257 mm. Toda a normalizacao e feita contra ESSE
quadro, nao contra a folha: ver o docstring de template.py.

O cabecalho (nome do aluno, prova, turma) e o QR ocupam os 46 mm de cima do
quadro; a grade de bolhas comeca em y = 66 mm.

Todas as verificacoes de que a grade cabe sao feitas na construcao, nao na
impressao: descobrir que a ultima coluna vazou por cima do marcador direito
depois de 1.500 folhas impressas e caro demais.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping

from .markers import CORNERS, MarkerSet, load_marker_set
from .template import (
    LAYOUT_VERSION,
    OPTION_CODES,
    BubbleBox,
    CardTemplate,
    MarkerPlacement,
    QrPlacement,
)


# Os tres campos de contagem sao inteiros; todo o resto e medida em
# milimetros. JSON nao distingue 90 de 90.0, entao from_dict reconverte.
_INT_FIELDS = frozenset({"item_count", "columns", "rows_per_column"})


@dataclass(frozen=True)
class CardLayoutSpec:
    """Todas as medidas do cartao, em milimetros na folha A4."""

    page_width_mm: float = 210.0
    page_height_mm: float = 297.0

    marker_size_mm: float = 12.0
    marker_center_left_mm: float = 15.0
    marker_center_right_mm: float = 195.0
    marker_center_top_mm: float = 20.0
    marker_center_bottom_mm: float = 277.0

    qr_size_mm: float = 24.0
    qr_center_x_mm: float = 172.0
    qr_center_y_mm: float = 40.0

    item_count: int = 90
    columns: int = 3
    rows_per_column: int = 30
    column_left_mm: float = 18.0
    column_pitch_mm: float = 60.0
    label_width_mm: float = 10.0
    grid_top_mm: float = 66.0
    row_pitch_mm: float = 6.8
    bubble_pitch_mm: float = 7.0
    bubble_diameter_mm: float = 4.0

    @property
    def frame_width_mm(self) -> float:
        return self.marker_center_right_mm - self.marker_center_left_mm

    @property
    def frame_height_mm(self) -> float:
        return self.marker_center_bottom_mm - self.marker_center_top_mm

    def to_dict(self) -> dict[str, float | int]:
        """As medidas, prontas para viajar no manifesto de impressao."""
        return {field.name: getattr(self, field.name) for field in fields(self)}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CardLayoutSpec:
        known = {field.name for field in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            # Recusar em vez de ignorar: um campo desconhecido significa que o
            # manifesto veio de outra versao do layout, e imprimir "o que deu
            # para entender" produziria um cartao que o leitor nao reconhece.
            raise ValueError(f"Campos desconhecidos no layout: {sorted(unknown)}")
        missing = known - set(raw)
        if missing:
            raise ValueError(f"Campos ausentes no layout: {sorted(missing)}")
        return cls(**{
            name: int(raw[name]) if name in _INT_FIELDS else float(raw[name])
            for name in known
        })


DEFAULT_LAYOUT = CardLayoutSpec()


def build_template(
    spec: CardLayoutSpec | None = None, *, marker_set: MarkerSet | None = None
) -> CardTemplate:
    """Constroi o template normalizado a partir das medidas em milimetros."""
    spec = spec or DEFAULT_LAYOUT
    marker_set = marker_set or load_marker_set()
    _validate(spec)

    def nx(x_mm: float) -> float:
        return (x_mm - spec.marker_center_left_mm) / spec.frame_width_mm

    def ny(y_mm: float) -> float:
        return (y_mm - spec.marker_center_top_mm) / spec.frame_height_mm

    markers = tuple(
        MarkerPlacement(
            corner=corner,
            marker_id=marker_set.by_corner(corner).marker_id,
            cx=nx(_corner_x_mm(spec, corner)),
            cy=ny(_corner_y_mm(spec, corner)),
            size=spec.marker_size_mm / spec.frame_width_mm,
        )
        for corner in CORNERS
    )

    qr = QrPlacement(
        cx=nx(spec.qr_center_x_mm),
        cy=ny(spec.qr_center_y_mm),
        size=spec.qr_size_mm / spec.frame_width_mm,
    )

    radius = (spec.bubble_diameter_mm / 2.0) / spec.frame_width_mm
    bubbles: list[BubbleBox] = []
    for position in range(1, spec.item_count + 1):
        column, row = divmod(position - 1, spec.rows_per_column)
        first_x_mm = (
            spec.column_left_mm + column * spec.column_pitch_mm + spec.label_width_mm
        )
        y_mm = spec.grid_top_mm + row * spec.row_pitch_mm
        for index, option in enumerate(OPTION_CODES):
            bubbles.append(BubbleBox(
                item_position=position,
                option=option,
                cx=nx(first_x_mm + index * spec.bubble_pitch_mm),
                cy=ny(y_mm),
                radius=radius,
            ))

    return CardTemplate(
        template_version=LAYOUT_VERSION,
        item_count=spec.item_count,
        option_codes=OPTION_CODES,
        # Altura/largura do quadro dos marcadores: no layout padrao,
        # 257 / 180 = 1.4278. E o que permite ao leitor retificar sem esticar.
        marker_rect_aspect=spec.frame_height_mm / spec.frame_width_mm,
        markers=markers,
        qr=qr,
        bubbles=tuple(bubbles),
    )


def _corner_x_mm(spec: CardLayoutSpec, corner: str) -> float:
    return (
        spec.marker_center_left_mm
        if corner in ("TOP_LEFT", "BOTTOM_LEFT")
        else spec.marker_center_right_mm
    )


def _corner_y_mm(spec: CardLayoutSpec, corner: str) -> float:
    return (
        spec.marker_center_top_mm
        if corner in ("TOP_LEFT", "TOP_RIGHT")
        else spec.marker_center_bottom_mm
    )


def _validate(spec: CardLayoutSpec) -> None:
    if spec.item_count < 1:
        raise ValueError("item_count deve ser ao menos 1")
    capacity = spec.columns * spec.rows_per_column
    if spec.item_count > capacity:
        raise ValueError(
            f"{spec.item_count} questoes nao cabem em {spec.columns} colunas de "
            f"{spec.rows_per_column} linhas ({capacity} lugares)"
        )
    if spec.frame_width_mm <= 0 or spec.frame_height_mm <= 0:
        raise ValueError("Os marcadores nao formam um quadro com area positiva")

    half_bubble = spec.bubble_diameter_mm / 2.0
    if spec.bubble_pitch_mm <= spec.bubble_diameter_mm:
        raise ValueError(
            f"bubble_pitch_mm ({spec.bubble_pitch_mm}) precisa ser maior que "
            f"bubble_diameter_mm ({spec.bubble_diameter_mm}), senao as bolhas se tocam"
        )
    if spec.row_pitch_mm <= spec.bubble_diameter_mm:
        raise ValueError(
            f"row_pitch_mm ({spec.row_pitch_mm}) precisa ser maior que "
            f"bubble_diameter_mm ({spec.bubble_diameter_mm})"
        )

    used_columns = min(
        spec.columns,
        -(-spec.item_count // spec.rows_per_column),  # teto da divisao
    )
    last_column = used_columns - 1
    rightmost_mm = (
        spec.column_left_mm
        + last_column * spec.column_pitch_mm
        + spec.label_width_mm
        + (len(OPTION_CODES) - 1) * spec.bubble_pitch_mm
        + half_bubble
    )
    right_limit_mm = spec.marker_center_right_mm - spec.marker_size_mm / 2.0
    if rightmost_mm >= right_limit_mm:
        raise ValueError(
            f"A grade termina em x={rightmost_mm:.1f} mm e invadiria o marcador "
            f"direito, que comeca em x={right_limit_mm:.1f} mm"
        )

    rows_used = min(spec.rows_per_column, spec.item_count)
    bottom_mm = spec.grid_top_mm + (rows_used - 1) * spec.row_pitch_mm + half_bubble
    bottom_limit_mm = spec.marker_center_bottom_mm - spec.marker_size_mm / 2.0
    if bottom_mm >= bottom_limit_mm:
        raise ValueError(
            f"A grade termina em y={bottom_mm:.1f} mm e invadiria os marcadores "
            f"inferiores, que comecam em y={bottom_limit_mm:.1f} mm"
        )

    top_limit_mm = spec.marker_center_top_mm + spec.marker_size_mm / 2.0
    if spec.grid_top_mm - half_bubble <= top_limit_mm:
        raise ValueError(
            f"A grade comeca em y={spec.grid_top_mm} mm e colidiria com o cabecalho "
            f"e os marcadores superiores (limite y={top_limit_mm:.1f} mm)"
        )


__all__ = ["DEFAULT_LAYOUT", "CardLayoutSpec", "build_template"]
```

- [ ] **Step 5: Rodar o teste e ver passar**

Run: `.venv/bin/python -m pytest tests/test_simulado_card_layout.py -v`
Expected: PASS (20 testes).

- [ ] **Step 6: Conferir na mão que a geometria fecha**

Run:
```bash
.venv/bin/python -c "
from agente_ia_edu.simulado_card.layout import build_template
t = build_template()
xs = [b.cx for b in t.bubbles]; ys = [b.cy for b in t.bubbles]
print('bolhas', len(t.bubbles), 'x', round(min(xs),4), round(max(xs),4), 'y', round(min(ys),4), round(max(ys),4))
print('qr', round(t.qr.cx,4), round(t.qr.cy,4), round(t.qr.size,4))
print('aspect', round(t.marker_rect_aspect,6))
"
```
Expected: `bolhas 450`, com `x` e `y` estritamente dentro de `0.0`–`1.0`, e o QR igualmente dentro. Qualquer valor negativo ou acima de 1 é geometria que vazou do quadro dos marcadores. `aspect` deve sair `1.427778` (257/180) — se sair `1.0`, a razão está sendo calculada sobre a folha e não sobre o quadro dos marcadores.

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/simulado_card/template.py \
        src/agente_ia_edu/simulado_card/layout.py \
        tests/test_simulado_card_layout.py
git commit -m "feat(simulados): template geometrico do cartao normalizado pelos marcadores

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Sentinela da fronteira — o core não pode importar ReportLab nem Pillow

**Files:**
- Test: `tests/test_core_graphics_boundary.py`

**Interfaces:**
- Consumes: `pyproject.toml`, a árvore `src/agente_ia_edu/`, e `agente_ia_edu.simulado_card.layout` / `.template` / `.markers` (Tasks 5 e 6).
- Produces: nenhuma API. O deliverable é a garantia executável de que a fronteira do spec §2.1/§2.3 existe no ambiente, e não só na intenção.

**Por que esta task existe separada.** A mitigação anterior (extra opcional + import tardio) não protege: `src/agente_ia_edu/services/ingestion_parser.py:371` chama `list(page.images)`, e o pypdf muda de comportamento conforme Pillow estar **instalado**, não conforme quem o importou. A única garantia real é ReportLab nunca entrar no `site-packages` do core — e uma garantia que ninguém mede é uma intenção. Este teste mede.

Ele vem **antes** da task que cria o ambiente isolado de propósito: assim, se alguém tentar o atalho de `pip install reportlab` no `.venv` do core durante a Task 8, a suíte reprova na hora.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_core_graphics_boundary.py`:

```python
"""Simulados Fase 1, Task 7 - a fronteira grafica do core (spec s2.1 e s2.3).

ESTE TESTE E A FRONTEIRA. Nao e documentacao dela.

reportlab 5.0.1 declara `pillow>=9.0.0` como dependencia OBRIGATORIA
(requires_dist, conferido em pypi.org/pypi/reportlab/json). E
services/ingestion_parser.py chama `list(page.images)`, cujo comportamento o
pypdf muda conforme Pillow estar INSTALADO no ambiente - nao conforme quem o
importou. Import tardio, portanto, nao protege nada: bastaria alguem instalar
reportlab por qualquer razao para o parser de ingestao mudar de comportamento
em silencio.

Por isso o renderizador de cartao vive fora deste ambiente, em
workers/simulado/ (Task 8), e este teste existe para que a tentacao de
`pip install reportlab` aqui reprove a suite em vez de passar despercebida.
"""

from __future__ import annotations

import importlib.util
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_SRC = ROOT / "src" / "agente_ia_edu"
FORBIDDEN_MODULES = ("reportlab", "PIL")
FORBIDDEN_DISTRIBUTIONS = ("reportlab", "pillow")


class CoreGraphicsBoundaryTests(unittest.TestCase):
    def test_the_core_environment_cannot_import_reportlab_or_pillow(self):
        for module_name in FORBIDDEN_MODULES:
            with self.subTest(module=module_name):
                self.assertIsNone(
                    importlib.util.find_spec(module_name),
                    f"{module_name} esta instalado no ambiente do core. reportlab "
                    "arrasta pillow, e pillow muda o comportamento de page.images "
                    "do pypdf, consumido em services/ingestion_parser.py. O "
                    "renderizador de cartao pertence a workers/simulado/ - "
                    "desinstale-o daqui.",
                )

    def test_pyproject_declares_neither_of_them_anywhere(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
        declared = list(pyproject["project"]["dependencies"])
        for extra_requirements in pyproject["project"].get(
            "optional-dependencies", {}
        ).values():
            declared.extend(extra_requirements)
        joined = " ".join(declared).lower()
        for distribution in FORBIDDEN_DISTRIBUTIONS:
            with self.subTest(distribution=distribution):
                # Nem nas dependencias, nem em NENHUM extra: um extra opcional
                # nao protege, porque basta alguem instala-lo.
                self.assertNotIn(distribution, joined)

    def test_no_core_module_imports_them(self):
        offenders = []
        for path in CORE_SRC.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for module_name in FORBIDDEN_MODULES:
                if (
                    f"import {module_name}" in source
                    or f"from {module_name}" in source
                ):
                    offenders.append(f"{path.relative_to(ROOT)} -> {module_name}")
        self.assertEqual(offenders, [], f"Imports proibidos no core: {offenders}")

    def test_the_card_geometry_lives_in_the_core_and_needs_nothing_graphical(self):
        # A contrapartida da fronteira: a GEOMETRIA fica aqui, para que o
        # template seja legivel dos dois lados (spec s2.1). Se estes imports
        # falharem, o leitor optico da Fase 4 perde o gabarito geometrico.
        from agente_ia_edu.simulado_card.layout import build_template
        from agente_ia_edu.simulado_card.markers import load_marker_set
        from agente_ia_edu.simulado_card.template import LAYOUT_VERSION

        template = build_template()
        self.assertEqual(template.template_version, LAYOUT_VERSION)
        self.assertEqual(len(load_marker_set().markers), 4)

    def test_the_ingestion_parser_still_depends_on_this_boundary(self):
        # Teste de regressao da PREMISSA, nao do parser: se um dia o parser
        # deixar de usar page.images, esta fronteira pode ser reavaliada - e
        # este teste e o lembrete de onde procurar.
        parser = (CORE_SRC / "services" / "ingestion_parser.py").read_text(encoding="utf-8")
        self.assertIn("page.images", parser)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Se a Task 6 foi executada num ambiente onde alguém já instalou reportlab, `test_the_core_environment_cannot_import_reportlab_or_pillow` falha — que é exatamente o que ele deve fazer. Para ver os outros falharem antes de existir o arquivo:

Run: `.venv/bin/python -m pytest tests/test_core_graphics_boundary.py -v`
Expected: antes de criar o arquivo, `no tests ran` / arquivo inexistente.

- [ ] **Step 3: Garantir que o ambiente do core está limpo**

Run:
```bash
.venv/bin/pip uninstall -y reportlab pillow 2>/dev/null || true
.venv/bin/python -c "import importlib.util as u; print('reportlab', u.find_spec('reportlab')); print('PIL', u.find_spec('PIL'))"
```
Expected: `reportlab None` e `PIL None`.

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `.venv/bin/python -m pytest tests/test_core_graphics_boundary.py -v`
Expected: PASS (5 testes).

- [ ] **Step 5: Confirmar que o parser de ingestão continua íntegro**

Run: `.venv/bin/python -m pytest tests/test_authorial_material_parser_coverage.py -q`
Expected: PASS. É o comportamento que a fronteira protege; guarde este resultado como linha de base.

- [ ] **Step 6: Commit**

```bash
git add tests/test_core_graphics_boundary.py
git commit -m "test(simulados): sentinela que prova que o core nao importa ReportLab nem Pillow

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Ambiente isolado `workers/simulado/` — renderizador ReportLab, CLI e container

**Files:**
- Create: `workers/simulado/pyproject.toml`
- Create: `workers/simulado/README.md`
- Create: `workers/simulado/src/simulado_card_renderer/__init__.py`
- Create: `workers/simulado/src/simulado_card_renderer/renderer.py`
- Create: `workers/simulado/src/simulado_card_renderer/cli.py`
- Create: `workers/simulado/tests/test_renderer.py`
- Create: `docker/simulado-worker/Dockerfile`
- Modify: `docker-compose.yml` (novo serviço `simulado-worker`, ao lado de `postgres` e `n8n`)

**Interfaces:**
- Consumes (do core, instalado como dependência **neste** ambiente): `CardTemplate`, `BubbleBox`, `MarkerPlacement`, `QrPlacement`, `LAYOUT_VERSION` (`agente_ia_edu.simulado_card.template`); `CardLayoutSpec`, `DEFAULT_LAYOUT` (`agente_ia_edu.simulado_card.layout`); `MarkerSet`, `load_marker_set` (`agente_ia_edu.simulado_card.markers`).
- Produces:
  - `CardPrintData` — dataclass congelada: `student_name: str`, `qr_token: str`, `exam_name: str`, `class_label: str | None = None`, `student_code: str | None = None`, `booklet_code: str = "UNICO"`.
  - `render_answer_cards(cards: Sequence[CardPrintData], *, template: CardTemplate, spec: CardLayoutSpec, marker_set: MarkerSet | None = None) -> bytes` — uma página por cartão, na ordem recebida. **`template` e `spec` são obrigatórios:** o renderizador nunca recalcula geometria, ele imprime exatamente o template que o core gravou (Task 9).
  - `render_print_job(manifest: Mapping[str, Any]) -> bytes` — lê um manifesto de impressão (formato definido na Task 9) e devolve o PDF.
  - `RendererError(RuntimeError)`
  - CLI: `python -m simulado_card_renderer <manifesto.json> --out <cartoes.pdf>`

**Por que o core é dependência do worker, e não o contrário.** A geometria é do core (Task 6) e precisa ser legível dos dois lados da fronteira. O ambiente isolado pode instalar o core sem problema — Pillow aqui é permitido. O caminho inverso é que é proibido.

- [ ] **Step 1: Declarar o ambiente isolado**

Crie `workers/simulado/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "simulado-card-renderer"
version = "0.1.0"
description = "Renderizacao do cartao-resposta de simulado. Ambiente ISOLADO do core."
requires-python = ">=3.13,<3.14"
# reportlab declara pillow>=9.0.0 como dependencia obrigatoria, e pillow muda o
# comportamento de page.images do pypdf, consumido pelo parser de ingestao do
# core (services/ingestion_parser.py). Por isso este pacote NAO vive no
# ambiente do core: ele compartilha o ambiente isolado do omr_worker
# (spec s2.1 e s2.3). tests/test_core_graphics_boundary.py reprova a suite do
# core se reportlab ou pillow aparecerem la.
dependencies = [
	"reportlab>=4.2,<6.0",
	"agente-ia-edu-core",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

Crie `workers/simulado/README.md`:

````markdown
# Ambiente isolado do simulado

Renderizacao do cartao-resposta (ReportLab) e, a partir da Fase 4, a leitura
optica (OpenCV). **Nunca instale nada daqui no ambiente do core.**

## Por que existe

`reportlab` declara `pillow>=9.0.0` como dependencia obrigatoria, e `pillow`
muda o comportamento de `page.images` do `pypdf`, consumido em
`src/agente_ia_edu/services/ingestion_parser.py`. Import tardio nao resolve: o
pypdf reage a pillow estar INSTALADO, nao a quem o importou. Ver spec
`docs/superpowers/specs/2026-09-29-correcao-simulados-tri-design.md` §2.1 e §2.3.

`tests/test_core_graphics_boundary.py`, na suite do core, reprova se reportlab
ou pillow aparecerem la.

## O que fica de cada lado

- **Core:** a GEOMETRIA (`agente_ia_edu.simulado_card.template`, `.layout`,
  `.markers` e os assets ArUco). Stdlib + PyYAML, sem nada grafico, para que o
  template seja legivel dos dois lados da fronteira.
- **Aqui:** so a renderizacao. Este pacote instala o core como dependencia e
  imprime exatamente o template que o core gravou - nunca recalcula geometria.

## Uso local

```bash
python3 -m venv .venv-simulado
./.venv-simulado/bin/pip install -e ../..        # o core
./.venv-simulado/bin/pip install -e .            # este pacote
./.venv-simulado/bin/python -m pytest
./.venv-simulado/bin/python -m simulado_card_renderer manifesto.json --out cartoes.pdf
```

## Uso via container

```bash
docker compose run --rm simulado-worker python -m simulado_card_renderer \
    /work/manifesto.json --out /work/cartoes.pdf
```
````

- [ ] **Step 2: Escrever o teste que falha**

Crie `workers/simulado/tests/test_renderer.py`:

```python
"""Ambiente isolado do simulado - o PDF do cartao nominal (spec s2.1).

Roda DENTRO do ambiente isolado (venv proprio ou container), nunca na suite do
core: aqui reportlab e pillow sao permitidos, e la sao proibidos.

O renderizador nao calcula geometria. Ele recebe o template que o core gravou e
imprime exatamente aquilo - por isso os testes montam o template com
build_template() do core e o passam adiante, em vez de deixar o renderizador
descobrir sozinho.
"""

from __future__ import annotations

import io
import json
import unittest

from pypdf import PdfReader

from agente_ia_edu.simulado_card.layout import DEFAULT_LAYOUT, build_template
from agente_ia_edu.simulado_card.template import LAYOUT_VERSION
from simulado_card_renderer.renderer import (
    CardPrintData,
    RendererError,
    render_answer_cards,
    render_print_job,
)

_CARDS = (
    CardPrintData(
        student_name="Ana Beatriz de Souza",
        qr_token="tok-ana-0001",
        exam_name="Simulado ENEM - 1o dia",
        class_label="3a serie A",
        student_code="2026-0001",
    ),
    CardPrintData(
        student_name="Bruno Carvalho Lima",
        qr_token="tok-bruno-0002",
        exam_name="Simulado ENEM - 1o dia",
        class_label="3a serie A",
        student_code="2026-0002",
    ),
)


class AnswerCardPdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = build_template()
        cls.pdf_bytes = render_answer_cards(
            _CARDS, template=cls.template, spec=DEFAULT_LAYOUT
        )
        cls.reader = PdfReader(io.BytesIO(cls.pdf_bytes))

    def test_produces_one_a4_page_per_student(self):
        self.assertEqual(len(self.reader.pages), 2)
        page = self.reader.pages[0]
        width_mm = float(page.mediabox.width) / 72.0 * 25.4
        height_mm = float(page.mediabox.height) / 72.0 * 25.4
        self.assertAlmostEqual(width_mm, DEFAULT_LAYOUT.page_width_mm, delta=0.5)
        self.assertAlmostEqual(height_mm, DEFAULT_LAYOUT.page_height_mm, delta=0.5)

    def test_each_page_names_its_own_student(self):
        first = self.reader.pages[0].extract_text()
        second = self.reader.pages[1].extract_text()
        self.assertIn("Ana Beatriz de Souza", first)
        self.assertNotIn("Bruno Carvalho Lima", first)
        self.assertIn("Bruno Carvalho Lima", second)

    def test_page_shows_exam_class_and_template_version(self):
        text = self.reader.pages[0].extract_text()
        self.assertIn("Simulado ENEM - 1o dia", text)
        self.assertIn("3a serie A", text)
        self.assertIn(LAYOUT_VERSION, text)

    def test_the_opaque_token_is_never_printed_as_text(self):
        # spec s3: o token opaco nao deve aparecer legivel na folha - quem le o
        # cartao com os olhos nao tem por que conseguir copiar a chave.
        for page in self.reader.pages:
            self.assertNotIn("tok-ana-0001", page.extract_text())
            self.assertNotIn("tok-bruno-0002", page.extract_text())

    def test_every_question_number_is_printed(self):
        text = "".join(page.extract_text() for page in self.reader.pages)
        for position in (1, 30, 31, 60, 61, 90):
            self.assertIn(f"{position:02d}", text)

    def test_rejects_an_empty_batch(self):
        with self.assertRaises(RendererError):
            render_answer_cards((), template=self.template, spec=DEFAULT_LAYOUT)

    def test_rejects_a_card_without_a_token(self):
        with self.assertRaises(RendererError):
            render_answer_cards(
                (CardPrintData(student_name="Sem Token", qr_token="", exam_name="X"),),
                template=self.template, spec=DEFAULT_LAYOUT,
            )


class PrintJobManifestTests(unittest.TestCase):
    def _manifest(self, **overrides):
        manifest = {
            "manifest_version": 1,
            "exam_name": "Simulado ENEM - 1o dia",
            "template_version": LAYOUT_VERSION,
            "layout": DEFAULT_LAYOUT.to_dict(),
            "template": build_template().to_dict(),
            "cards": [
                {
                    "student_name": "Ana Beatriz de Souza",
                    "qr_token": "tok-ana-0001",
                    "class_label": "3a serie A",
                    "student_code": "2026-0001",
                    "booklet_code": "UNICO",
                }
            ],
        }
        manifest.update(overrides)
        return manifest

    def test_renders_straight_from_a_json_round_tripped_manifest(self):
        manifest = json.loads(json.dumps(self._manifest()))
        reader = PdfReader(io.BytesIO(render_print_job(manifest)))
        self.assertEqual(len(reader.pages), 1)
        self.assertIn("Ana Beatriz de Souza", reader.pages[0].extract_text())

    def test_rejects_a_manifest_of_another_version(self):
        with self.assertRaises(RendererError):
            render_print_job(self._manifest(manifest_version=99))

    def test_rejects_a_manifest_whose_template_version_disagrees(self):
        # O PDF tem que sair do template que o core gravou. Se as duas versoes
        # discordam, imprimir seria produzir folha que o leitor nao reconhece.
        manifest = self._manifest()
        manifest["template_version"] = "enem-90x5-v0"
        with self.assertRaises(RendererError):
            render_print_job(manifest)

    def test_rejects_a_manifest_with_no_cards(self):
        with self.assertRaises(RendererError):
            render_print_job(self._manifest(cards=[]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Montar o ambiente isolado e ver o teste falhar**

Run:
```bash
python3 -m venv workers/simulado/.venv-simulado
workers/simulado/.venv-simulado/bin/pip install -e .
workers/simulado/.venv-simulado/bin/pip install -e workers/simulado
workers/simulado/.venv-simulado/bin/python -m pytest workers/simulado/tests -v
```
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'simulado_card_renderer.renderer'`.

Confirme também, antes de seguir, que o `.venv` do core continua limpo:
```bash
.venv/bin/python -m pytest tests/test_core_graphics_boundary.py -q
```
Expected: PASS. Se falhar, o `pip install` foi feito no venv errado.

- [ ] **Step 4: Escrever o renderizador**

Crie `workers/simulado/src/simulado_card_renderer/__init__.py`:

```python
"""Renderizacao do cartao-resposta de simulado. AMBIENTE ISOLADO.

Este pacote vive fora do ambiente do core (spec s2.1 e s2.3) porque reportlab
declara pillow>=9.0.0 como dependencia obrigatoria, e pillow muda o
comportamento de page.images do pypdf, consumido pelo parser de ingestao do
core. A geometria fica no core; aqui so a tinta.
"""

from .renderer import (
    CardPrintData,
    RendererError,
    render_answer_cards,
    render_print_job,
)

__all__ = [
    "CardPrintData",
    "RendererError",
    "render_answer_cards",
    "render_print_job",
]
```

Crie `workers/simulado/src/simulado_card_renderer/renderer.py`:

```python
"""Desenha o PDF do cartao-resposta nominal (spec s2.1).

O UNICO arquivo do repositorio que importa ReportLab, e ele roda fora do
ambiente do core.

NAO CALCULA GEOMETRIA. Recebe o ``CardTemplate`` e o ``CardLayoutSpec`` que o
core ja gravou e imprime exatamente aquilo. Recalcular aqui abriria a
possibilidade de o papel impresso e o template guardado discordarem - e e
justamente a coincidencia entre os dois que faz a leitura optica funcionar sem
precisar descobrir onde as bolhas estao.

O QR sai de ``reportlab.graphics.barcode.qr``, que ja vem no ReportLab, e
vetorial e nao passa pelo leitor de imagens. Nenhuma biblioteca de QR e
adicionada.

Os marcadores ArUco sao PNGs pre-gerados e versionados no core; aqui eles sao
apenas POSICIONADOS. Nada os gera em tempo de execucao.

O conteudo do QR e exclusivamente o ``qr_token`` opaco. Nome, matricula e turma
sao impressos em texto legivel para o aluno se reconhecer, mas NUNCA entram no
QR: quem tiver a foto do codigo nao fica com dado pessoal na mao.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from agente_ia_edu.simulado_card.layout import CardLayoutSpec
from agente_ia_edu.simulado_card.markers import MarkerSet, load_marker_set
from agente_ia_edu.simulado_card.template import LAYOUT_VERSION, CardTemplate

MANIFEST_VERSION = 1
HEADER_FONT = "Helvetica"
HEADER_BOLD = "Helvetica-Bold"


class RendererError(RuntimeError):
    """Nao da para renderizar: lote invalido ou manifesto incoerente."""


@dataclass(frozen=True)
class CardPrintData:
    student_name: str
    qr_token: str
    exam_name: str
    class_label: str | None = None
    student_code: str | None = None
    booklet_code: str = "UNICO"


def _reportlab() -> dict[str, Any]:
    try:
        from reportlab.graphics import renderPDF
        from reportlab.graphics.barcode.qr import QrCodeWidget
        from reportlab.graphics.shapes import Drawing
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover - ambiente isolado mal montado
        raise RendererError(
            "ReportLab nao esta instalado NESTE ambiente. Ele pertence a "
            "workers/simulado/ e nunca ao ambiente do core - veja o README."
        ) from exc
    return {
        "renderPDF": renderPDF, "QrCodeWidget": QrCodeWidget,
        "Drawing": Drawing, "mm": mm, "canvas": canvas,
    }


def render_print_job(manifest: Mapping[str, Any]) -> bytes:
    """Renderiza o PDF a partir do manifesto de impressao gravado pelo core."""
    version = manifest.get("manifest_version")
    if version != MANIFEST_VERSION:
        raise RendererError(
            f"Manifesto versao {version!r}; este renderizador so le {MANIFEST_VERSION}"
        )
    for field in ("exam_name", "template_version", "layout", "template", "cards"):
        if field not in manifest:
            raise RendererError(f"Manifesto sem o campo obrigatorio {field!r}")

    template = CardTemplate.from_dict(manifest["template"])
    if manifest["template_version"] != template.template_version:
        raise RendererError(
            f"Manifesto diz template_version {manifest['template_version']!r} mas o "
            f"template embutido e {template.template_version!r}. Imprimir assim "
            "produziria folha que o leitor optico nao reconhece."
        )
    if template.template_version != LAYOUT_VERSION:
        raise RendererError(
            f"Template {template.template_version!r}; este renderizador so imprime "
            f"{LAYOUT_VERSION!r}"
        )

    spec = CardLayoutSpec.from_dict(manifest["layout"])
    exam_name = manifest["exam_name"]
    cards = tuple(
        CardPrintData(
            student_name=entry["student_name"],
            qr_token=entry["qr_token"],
            exam_name=exam_name,
            class_label=entry.get("class_label"),
            student_code=entry.get("student_code"),
            booklet_code=entry.get("booklet_code", "UNICO"),
        )
        for entry in manifest["cards"]
    )
    return render_answer_cards(cards, template=template, spec=spec)


def render_answer_cards(
    cards: Sequence[CardPrintData],
    *,
    template: CardTemplate,
    spec: CardLayoutSpec,
    marker_set: MarkerSet | None = None,
) -> bytes:
    """Devolve o PDF com uma pagina por cartao, na ordem recebida."""
    if not cards:
        raise RendererError("Nenhum cartao para renderizar")
    for card in cards:
        if not card.qr_token:
            raise RendererError(
                f"Cartao de {card.student_name!r} sem qr_token; o cartao e inutil "
                "sem a chave que o identifica"
            )
        if not card.student_name:
            raise RendererError("Cartao sem nome de aluno")

    marker_set = marker_set or load_marker_set()
    rl = _reportlab()
    mm = rl["mm"]
    buffer = io.BytesIO()
    pdf = rl["canvas"].Canvas(
        buffer, pagesize=(spec.page_width_mm * mm, spec.page_height_mm * mm)
    )

    for card in cards:
        _draw_card(pdf, rl, spec, template, marker_set, card)
        pdf.showPage()

    pdf.save()
    return buffer.getvalue()


def _draw_card(pdf, rl, spec: CardLayoutSpec, template: CardTemplate,
               marker_set: MarkerSet, card: CardPrintData) -> None:
    mm = rl["mm"]

    def x_pt(cx: float) -> float:
        return (spec.marker_center_left_mm + cx * spec.frame_width_mm) * mm

    def y_pt(cy: float) -> float:
        y_mm = spec.marker_center_top_mm + cy * spec.frame_height_mm
        return (spec.page_height_mm - y_mm) * mm  # ReportLab conta de baixo

    # 1. Marcadores ArUco de canto (PNG pre-gerado, apenas posicionado).
    for placement in template.markers:
        side_pt = placement.size * spec.frame_width_mm * mm
        pdf.drawImage(
            str(marker_set.by_corner(placement.corner).path),
            x_pt(placement.cx) - side_pt / 2,
            y_pt(placement.cy) - side_pt / 2,
            width=side_pt, height=side_pt,
            preserveAspectRatio=True, anchor="c", mask=None,
        )

    # 2. QR do token opaco - so o token, nunca nome ou matricula.
    qr_side_pt = template.qr.size * spec.frame_width_mm * mm
    widget = rl["QrCodeWidget"](card.qr_token, barLevel="M")
    bounds = widget.getBounds()
    widget_width = bounds[2] - bounds[0]
    widget_height = bounds[3] - bounds[1]
    drawing = rl["Drawing"](qr_side_pt, qr_side_pt)
    widget.transform = [
        qr_side_pt / widget_width, 0, 0, qr_side_pt / widget_height,
        -bounds[0] * qr_side_pt / widget_width,
        -bounds[1] * qr_side_pt / widget_height,
    ]
    drawing.add(widget)
    rl["renderPDF"].draw(
        drawing, pdf,
        x_pt(template.qr.cx) - qr_side_pt / 2,
        y_pt(template.qr.cy) - qr_side_pt / 2,
    )

    # 3. Cabecalho legivel para o aluno se reconhecer.
    header_x = x_pt(0.0)
    pdf.setFont(HEADER_BOLD, 13)
    pdf.drawString(header_x, y_pt(0.0) - 14 * mm, card.exam_name)
    pdf.setFont(HEADER_FONT, 11)
    pdf.drawString(header_x, y_pt(0.0) - 21 * mm, card.student_name)
    subtitle_parts = [part for part in (card.class_label, card.student_code) if part]
    pdf.setFont(HEADER_FONT, 9)
    if subtitle_parts:
        pdf.drawString(header_x, y_pt(0.0) - 27 * mm, "   ".join(subtitle_parts))
    pdf.drawString(
        header_x, y_pt(0.0) - 33 * mm,
        f"Caderno {card.booklet_code}   Gabarito {template.template_version}",
    )
    pdf.setFont(HEADER_FONT, 7)
    pdf.drawString(
        header_x, y_pt(0.0) - 38 * mm,
        "Preencha a bolha inteira com caneta preta ou azul. Nao rasure.",
    )

    # 4. Grade de bolhas, com o numero da questao a esquerda de cada linha.
    pdf.setLineWidth(0.6)
    for position in range(1, template.item_count + 1):
        row = template.bubbles_for(position)
        radius_pt = row[0].radius * spec.frame_width_mm * mm
        label_x = x_pt(row[0].cx) - radius_pt - 6 * mm
        pdf.setFont(HEADER_FONT, 7.5)
        pdf.drawString(label_x, y_pt(row[0].cy) - 2.2, f"{position:02d}")
        for bubble in row:
            cx_pt, cy_pt = x_pt(bubble.cx), y_pt(bubble.cy)
            pdf.circle(cx_pt, cy_pt, radius_pt, stroke=1, fill=0)
            pdf.setFont(HEADER_FONT, 4.5)
            pdf.drawCentredString(cx_pt, cy_pt - 1.6, bubble.option)


__all__ = [
    "MANIFEST_VERSION",
    "CardPrintData",
    "RendererError",
    "render_answer_cards",
    "render_print_job",
]
```

- [ ] **Step 5: Rodar o teste e ver passar**

Run: `workers/simulado/.venv-simulado/bin/python -m pytest workers/simulado/tests -v`
Expected: PASS (11 testes: 7 de PDF + 4 de manifesto).

- [ ] **Step 6: Escrever a CLI**

Crie `workers/simulado/src/simulado_card_renderer/cli.py`:

```python
"""Linha de comando do renderizador: manifesto de impressao -> PDF.

E assim que o core e o ambiente isolado conversam nesta fase: o core grava o
manifesto e o template (services/answer_card_issuing.py), e este comando le os
dois e produz o papel. Nenhum processo importa o outro.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .renderer import RendererError, render_print_job


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="Manifesto de impressao (JSON)")
    parser.add_argument("--out", type=Path, required=True, help="PDF de saida")
    args = parser.parse_args(argv)

    if not args.manifest.is_file():
        print(f"Manifesto nao encontrado: {args.manifest}", file=sys.stderr)
        return 2

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    try:
        pdf_bytes = render_print_job(manifest)
    except RendererError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(pdf_bytes)
    print(f"{len(manifest['cards'])} cartao(oes) em {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Crie também `workers/simulado/src/simulado_card_renderer/__main__.py`:

```python
from .cli import main

raise SystemExit(main())
```

- [ ] **Step 7: Escrever o Dockerfile**

Crie `docker/simulado-worker/Dockerfile`:

```dockerfile
# Ambiente ISOLADO do simulado: renderizacao do cartao-resposta (ReportLab) e,
# a partir da Fase 4, leitura optica (OpenCV). Nada daqui entra na imagem ou no
# virtualenv do core - ver workers/simulado/README.md e o spec s2.1/s2.3.
FROM python:3.13-slim

WORKDIR /app

# Fontes do core (pela geometria do cartao) e do renderizador.
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY workers/simulado/pyproject.toml ./workers/simulado/pyproject.toml
COPY workers/simulado/src/ ./workers/simulado/src/

RUN pip install --no-cache-dir -e . \
 && pip install --no-cache-dir -e ./workers/simulado

WORKDIR /work

# Sem comando padrao: esta imagem e usada com `docker compose run`, e a Fase 4
# vai acrescentar o loop do worker de OMR como entrypoint proprio.
CMD ["python", "-c", "import reportlab; print('simulado-worker pronto', reportlab.Version)"]
```

> Se `README.md` não existir na raiz do repositório, remova-o do `COPY` — ele está ali só porque `pyproject.toml` costuma referenciá-lo. Confira com `ls README.md` antes de construir.

- [ ] **Step 8: Declarar o serviço no compose**

Em `docker-compose.yml`, acrescente o serviço **depois** do bloco `n8n:` e **antes** da linha `volumes:` final:

```yaml
  simulado-worker:
    build:
      context: .
      dockerfile: docker/simulado-worker/Dockerfile
    container_name: agente-ia-edu-simulado-worker
    # Ambiente ISOLADO (spec §2.1 e §2.3): ReportLab e, na Fase 4, OpenCV.
    # Nenhuma dessas bibliotecas pode existir no ambiente do core, porque
    # Pillow (dependencia obrigatoria do ReportLab) muda o comportamento de
    # page.images do pypdf e quebra o parser de ingestao de questoes.
    # Sem `restart`: nesta fase o container e de uso pontual
    # (`docker compose run --rm simulado-worker ...`), nao um daemon.
    environment:
      DATABASE_URL: postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - ./var:/work
```

- [ ] **Step 9: Construir a imagem e provar a fronteira nos dois lados**

Run:
```bash
docker compose build simulado-worker
docker compose run --rm simulado-worker python -c "import importlib.util as u; print('reportlab', u.find_spec('reportlab') is not None); print('PIL', u.find_spec('PIL') is not None)"
```
Expected: `reportlab True` e `PIL True` **dentro do container**.

E, imediatamente depois, no host:
```bash
.venv/bin/python -m pytest tests/test_core_graphics_boundary.py -q
```
Expected: PASS. Os dois resultados juntos são a evidência de que a fronteira existe: gráfico de um lado, limpo do outro.

- [ ] **Step 10: Olhar o PDF com os próprios olhos**

Nenhum teste automático prova que um cartão é *imprimível*. Este é o portão visual.

Run:
```bash
workers/simulado/.venv-simulado/bin/python -c "
import json
from agente_ia_edu.simulado_card.layout import DEFAULT_LAYOUT, build_template
from simulado_card_renderer.renderer import render_print_job
from agente_ia_edu.simulado_card.template import LAYOUT_VERSION
m = {'manifest_version': 1, 'exam_name': 'Simulado ENEM - 1o dia',
     'template_version': LAYOUT_VERSION, 'layout': DEFAULT_LAYOUT.to_dict(),
     'template': build_template().to_dict(),
     'cards': [{'student_name': 'Ana Beatriz de Souza', 'qr_token': 'amostra-visual-0001',
                'class_label': '3a serie A', 'student_code': '2026-0001',
                'booklet_code': 'UNICO'}]}
open('/tmp/cartao_amostra.pdf','wb').write(render_print_job(json.loads(json.dumps(m))))
print('/tmp/cartao_amostra.pdf')
"
open /tmp/cartao_amostra.pdf
```
Confira, a olho: quatro marcadores pretos nos cantos, QR no alto à direita sem encostar em nada, 90 linhas numeradas de 01 a 90 em três colunas, cinco bolhas por linha com as letras A–E legíveis, nada cortado na borda. Só então siga.

- [ ] **Step 11: Ignorar o virtualenv do worker no git**

Em `.gitignore`, logo abaixo da linha `.venv/`, acrescente:

```gitignore
workers/simulado/.venv-simulado/
```

- [ ] **Step 12: Commit**

```bash
git add workers/simulado/ docker/simulado-worker/Dockerfile docker-compose.yml .gitignore
git commit -m "feat(simulados): renderizador do cartao em ambiente isolado, fora do core

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Emissão dos cartões no core — tokens, linhas `answer_cards`, template e manifesto

**Files:**
- Create: `src/agente_ia_edu/services/answer_card_issuing.py`
- Test: `tests/test_answer_card_issuing.py`

**Interfaces:**
- Consumes: `AnswerCard`, `DEFAULT_BOOKLET_CODE` (Task 2); `MockExam` (Task 1); `build_template`, `DEFAULT_LAYOUT`, `CardLayoutSpec` (Task 6); `MaterialStorage` (`src/agente_ia_edu/services/material_storage.py:31-48`). **Não** consome nada do ambiente isolado.
- Produces:
  - `AnswerCardRecipient` — dataclass congelada: `student_id: uuid.UUID`, `full_name: str`, `class_label: str | None = None`, `student_code: str | None = None`.
  - `IssuedCard` — dataclass congelada: `answer_card_id: uuid.UUID`, `student_id: uuid.UUID`, `qr_token: str`.
  - `AnswerCardBatch` — dataclass congelada: `mock_exam_id: uuid.UUID`, `school_id: uuid.UUID`, `template_version: str`, `cards: tuple[IssuedCard, ...]`, `template_path: Path`, `template_sha256: str`, `manifest_path: Path`, `manifest_sha256: str`.
  - `async def issue_answer_cards(session: AsyncSession, *, mock_exam_id: uuid.UUID, recipients: Sequence[AnswerCardRecipient], storage: MaterialStorage | None = None, spec: CardLayoutSpec | None = None, token_bytes: int = QR_TOKEN_BYTES) -> AnswerCardBatch`
  - `AnswerCardIssuingError(RuntimeError)`
  - `QR_TOKEN_BYTES: int = 16`, `MANIFEST_VERSION: int = 1`
- Consumida por: a CLI do ambiente isolado (Task 8), que lê o manifesto gravado aqui. O formato do manifesto é o contrato entre os dois: `manifest_version`, `exam_name`, `template_version`, `layout`, `template`, `cards[]`.

**Duas decisões registradas:**

1. **O core não renderiza PDF.** Ele emite os tokens, grava as linhas e produz o **template** e o **manifesto de impressão** — no mesmo ato, que é o que o spec §2.1 exige. O PDF sai do ambiente isolado (Task 8), a partir *exatamente* desse template. O acoplamento entre o que foi impresso e o que foi guardado fica mais forte assim, não mais fraco: o renderizador não tem como recalcular geometria diferente.
2. **O simulado passa a `PRINTED`.** Spec §4 passo 2. A função recusa um simulado que já não esteja em `DRAFT` ou `PRINTED` — reemitir para prova já aplicada trocaria os tokens de folhas que já estão com os alunos.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_answer_card_issuing.py`:

```python
"""Simulados Fase 1, Task 9 - emissao dos cartoes nominais (spec s2.1 e s4).

Postgres real e descartavel na porta 5433, mais um MaterialStorage apontado
para um diretorio temporario (nunca var/ do repositorio).

NAO renderiza PDF: isso e do ambiente isolado (workers/simulado/), e este
modulo nao importa nada de la. O que sai daqui e o template geometrico e o
manifesto de impressao, gravados no mesmo ato em que os tokens sao emitidos.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    AnswerCard,
    MockExam,
    Person,
    School,
    Student,
)
from agente_ia_edu.services.answer_card_issuing import (
    MANIFEST_VERSION,
    AnswerCardIssuingError,
    AnswerCardRecipient,
    issue_answer_cards,
)
from agente_ia_edu.services.material_storage import MaterialStorage
from agente_ia_edu.simulado_card.template import LAYOUT_VERSION
from tests._postgres_test_db import create_database, drop_database


class AnswerCardIssuingPostgreSQLTests(unittest.TestCase):
    database_name = "agente_ia_edu_answer_card_issuing_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = os.getenv(
        "ANSWER_CARD_ISSUING_TEST_ADMIN_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres",
    )
    async_database_url = os.getenv(
        "ANSWER_CARD_ISSUING_TEST_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            cls._admin_execute("SELECT 1")
        except Exception as exc:
            raise unittest.SkipTest("PostgreSQL de teste indisponivel") from exc
        cls._drop_database()
        create_database(cls.admin_url, cls.database_name)
        cls.engine = create_async_engine(cls.async_database_url)
        cls.session_factory = async_sessionmaker(
            cls.engine, class_=AsyncSession, expire_on_commit=False
        )

        async def _prep():
            async with cls.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

        asyncio.run(_prep())
        cls._tmp = tempfile.TemporaryDirectory()
        cls.storage = MaterialStorage(root=Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "engine"):
            asyncio.run(cls.engine.dispose())
        if hasattr(cls, "_tmp"):
            cls._tmp.cleanup()
        cls._drop_database()

    @classmethod
    def _admin_execute(cls, statement):
        engine = create_engine(
            cls.admin_url,
            connect_args={"autocommit": True},
            execution_options={"isolation_level": "AUTOCOMMIT"},
        )
        try:
            with engine.connect() as connection:
                return connection.execute(text(statement))
        finally:
            engine.dispose()

    @classmethod
    def _drop_database(cls):
        drop_database(cls.admin_url, cls.database_name)

    def _run(self, coro):
        return asyncio.run(coro)

    async def _seed_exam(self, session, *, slug: str, students: int, status: str = "DRAFT"):
        school = School(code=f"SIM-ISSUE-{slug}", name=f"Escola {slug}")
        session.add(school)
        await session.flush()
        year = AcademicYear(school_id=school.id, year=2026, status="ACTIVE")
        session.add(year)
        await session.flush()
        exam = MockExam(school_id=school.id, academic_year_id=year.id,
                        name=f"Simulado {slug}", exam_day=1, status=status)
        session.add(exam)
        recipients = []
        for index in range(students):
            person = Person(school_id=school.id, full_name=f"Aluno {slug} {index + 1}")
            session.add(person)
            await session.flush()
            student = Student(school_id=school.id, person_id=person.id)
            session.add(student)
            await session.flush()
            recipients.append(AnswerCardRecipient(
                student_id=student.id,
                full_name=person.full_name,
                class_label="3a serie A",
                student_code=f"{slug}-{index + 1:04d}",
            ))
        await session.commit()
        return school.id, exam.id, recipients

    def test_issues_one_card_per_student_with_distinct_opaque_tokens(self):
        async def _scenario():
            async with self.session_factory() as session:
                school_id, exam_id, recipients = await self._seed_exam(
                    session, slug="TOKENS", students=3
                )
                batch = await issue_answer_cards(
                    session, mock_exam_id=exam_id, recipients=recipients,
                    storage=self.storage,
                )
                self.assertEqual(len(batch.cards), 3)
                self.assertEqual(batch.school_id, school_id)
                tokens = [card.qr_token for card in batch.cards]
                self.assertEqual(len(set(tokens)), 3)
                for token in tokens:
                    # Opaco: nada do aluno pode ser lido no token.
                    self.assertNotIn("Aluno", token)
                    self.assertNotIn("TOKENS-", token)
                    self.assertGreaterEqual(len(token), 20)

                rows = (await session.execute(
                    select(AnswerCard).where(AnswerCard.mock_exam_id == exam_id)
                )).scalars().all()
                self.assertEqual(len(rows), 3)
                for row in rows:
                    self.assertEqual(row.school_id, school_id)
                    self.assertEqual(row.booklet_code, "UNICO")
                    self.assertEqual(row.template_version, LAYOUT_VERSION)
                    self.assertIsNotNone(row.printed_at)

        self._run(_scenario())

    def test_moves_the_exam_to_printed(self):
        async def _scenario():
            async with self.session_factory() as session:
                _, exam_id, recipients = await self._seed_exam(
                    session, slug="PRINTED", students=1
                )
                await issue_answer_cards(
                    session, mock_exam_id=exam_id, recipients=recipients,
                    storage=self.storage,
                )
                self.assertEqual((await session.get(MockExam, exam_id)).status, "PRINTED")

        self._run(_scenario())

    def test_refuses_to_reissue_for_an_applied_exam(self):
        async def _scenario():
            async with self.session_factory() as session:
                _, exam_id, recipients = await self._seed_exam(
                    session, slug="APPLIED", students=1, status="APPLIED"
                )
                # Reemitir trocaria os tokens de folhas que ja estao com os
                # alunos - o cartao impresso deixaria de resolver para ninguem.
                with self.assertRaises(AnswerCardIssuingError):
                    await issue_answer_cards(
                        session, mock_exam_id=exam_id, recipients=recipients,
                        storage=self.storage,
                    )

        self._run(_scenario())

    def test_refuses_an_empty_recipient_list(self):
        async def _scenario():
            async with self.session_factory() as session:
                _, exam_id, _recipients = await self._seed_exam(
                    session, slug="EMPTY", students=1
                )
                with self.assertRaises(AnswerCardIssuingError):
                    await issue_answer_cards(
                        session, mock_exam_id=exam_id, recipients=(),
                        storage=self.storage,
                    )

        self._run(_scenario())

    def test_refuses_an_unknown_exam(self):
        async def _scenario():
            async with self.session_factory() as session:
                with self.assertRaises(AnswerCardIssuingError):
                    await issue_answer_cards(
                        session, mock_exam_id=uuid.uuid4(),
                        recipients=(AnswerCardRecipient(
                            student_id=uuid.uuid4(), full_name="Fantasma"
                        ),),
                        storage=self.storage,
                    )

        self._run(_scenario())

    def test_refuses_a_recipient_from_another_school(self):
        """Isolamento multi-tenant (spec s3.0) na borda do servico.

        A FK composta do banco ja recusaria, mas a mensagem sairia como
        IntegrityError cru. O servico recusa antes, com motivo legivel - e o
        teste garante que a recusa acontece de um jeito ou de outro.
        """
        async def _scenario():
            async with self.session_factory() as session:
                _, exam_id, _recipients = await self._seed_exam(
                    session, slug="CROSS-A", students=1
                )
                _, _other_exam_id, other_recipients = await self._seed_exam(
                    session, slug="CROSS-B", students=1
                )
                with self.assertRaises(AnswerCardIssuingError):
                    await issue_answer_cards(
                        session, mock_exam_id=exam_id, recipients=other_recipients,
                        storage=self.storage,
                    )

        self._run(_scenario())

    def test_stores_the_template_and_the_print_manifest_in_the_same_act(self):
        async def _scenario():
            async with self.session_factory() as session:
                _, exam_id, recipients = await self._seed_exam(
                    session, slug="STORAGE", students=2
                )
                batch = await issue_answer_cards(
                    session, mock_exam_id=exam_id, recipients=recipients,
                    storage=self.storage,
                )

                self.assertTrue(batch.template_path.is_file())
                self.assertTrue(batch.manifest_path.is_file())
                # Enderecado por conteudo: o hash esta no caminho (MaterialStorage).
                self.assertIn(batch.template_sha256, str(batch.template_path))
                self.assertIn(batch.manifest_sha256, str(batch.manifest_path))

                template = json.loads(batch.template_path.read_text(encoding="utf-8"))
                self.assertEqual(template["template_version"], LAYOUT_VERSION)
                self.assertEqual(template["item_count"], 90)
                self.assertEqual(len(template["bubbles"]), 450)
                self.assertEqual(len(template["markers"]), 4)
                self.assertEqual(batch.template_version, LAYOUT_VERSION)

                manifest = json.loads(batch.manifest_path.read_text(encoding="utf-8"))
                self.assertEqual(manifest["manifest_version"], MANIFEST_VERSION)
                self.assertEqual(manifest["template_version"], LAYOUT_VERSION)
                self.assertEqual(manifest["exam_name"], "Simulado STORAGE")
                self.assertEqual(len(manifest["cards"]), 2)
                # O manifesto carrega o MESMO template gravado ao lado: o
                # renderizador imprime exatamente o que ficou guardado, nunca
                # uma geometria recalculada.
                self.assertEqual(manifest["template"], template)
                self.assertEqual(manifest["layout"]["item_count"], 90)
                emitted = {card.qr_token for card in batch.cards}
                self.assertEqual({c["qr_token"] for c in manifest["cards"]}, emitted)
                self.assertEqual(
                    {c["student_name"] for c in manifest["cards"]},
                    {"Aluno STORAGE 1", "Aluno STORAGE 2"},
                )

        self._run(_scenario())

    def test_the_service_never_imports_the_isolated_renderer(self):
        # A fronteira do spec s2.1/s2.3 vista do lado do core: se este modulo
        # um dia importar o renderizador, ReportLab volta a ser necessario aqui.
        source = (
            Path(__file__).resolve().parents[1]
            / "src" / "agente_ia_edu" / "services" / "answer_card_issuing.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("simulado_card_renderer", source)
        self.assertNotIn("reportlab", source.lower())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_answer_card_issuing.py -v`
Expected: FAIL na coleta — `ModuleNotFoundError: No module named 'agente_ia_edu.services.answer_card_issuing'`.

- [ ] **Step 3: Escrever o serviço**

Crie `src/agente_ia_edu/services/answer_card_issuing.py`:

```python
"""Emissao dos cartoes-resposta nominais de um simulado (spec s2.1 e s4 passo 2).

O TEMPLATE e o MANIFESTO DE IMPRESSAO saem no MESMO ATO em que os tokens sao
emitidos. Esse acoplamento e deliberado e e a principal alavanca de robustez do
subsistema: o leitor optico da Fase 4 nunca precisa descobrir onde as bolhas
estao, porque quem emitiu ja gravou.

ESTE MODULO NAO RENDERIZA PDF, e nao importa nada do ambiente isolado. O
ReportLab vive em workers/simulado/ porque arrasta Pillow, que muda o
comportamento de page.images do pypdf e quebra o parser de ingestao de questoes
(spec s2.1 e s2.3; tests/test_core_graphics_boundary.py reprova a suite se ele
aparecer aqui). O manifesto e o contrato entre os dois lados: o renderizador le
o template que ESTE modulo gravou e imprime exatamente aquilo, sem recalcular
geometria nenhuma.

``qr_token`` e gerado com ``secrets.token_urlsafe``: opaco por decisao de
privacidade (spec s3). O papel nao carrega nome, matricula nem nada derivado do
aluno - so uma chave que o banco resolve. Usar o UUID do aluno, ou qualquer
hash dele, permitiria correlacionar folhas fora do sistema.

O template e o manifesto sao gravados via ``MaterialStorage``, o storage local
enderecado por conteudo que ja existe no projeto. Nenhum storage novo e criado.
"""

from __future__ import annotations

import json
import secrets
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AnswerCard, MockExam, Student
from ..db.models.answer_card import DEFAULT_BOOKLET_CODE
from ..simulado_card.layout import DEFAULT_LAYOUT, CardLayoutSpec, build_template
from .material_storage import MaterialStorage

QR_TOKEN_BYTES = 16
MANIFEST_VERSION = 1
_ISSUABLE_STATUSES = ("DRAFT", "PRINTED")


class AnswerCardIssuingError(RuntimeError):
    """O lote de cartoes nao pode ser emitido."""


@dataclass(frozen=True)
class AnswerCardRecipient:
    student_id: uuid.UUID
    full_name: str
    class_label: str | None = None
    student_code: str | None = None


@dataclass(frozen=True)
class IssuedCard:
    answer_card_id: uuid.UUID
    student_id: uuid.UUID
    qr_token: str


@dataclass(frozen=True)
class AnswerCardBatch:
    mock_exam_id: uuid.UUID
    school_id: uuid.UUID
    template_version: str
    cards: tuple[IssuedCard, ...]
    template_path: Path
    template_sha256: str
    manifest_path: Path
    manifest_sha256: str


async def issue_answer_cards(
    session: AsyncSession,
    *,
    mock_exam_id: uuid.UUID,
    recipients: Sequence[AnswerCardRecipient],
    storage: MaterialStorage | None = None,
    spec: CardLayoutSpec | None = None,
    token_bytes: int = QR_TOKEN_BYTES,
) -> AnswerCardBatch:
    """Emite um cartao por destinatario e grava template + manifesto."""
    if not recipients:
        raise AnswerCardIssuingError("Nenhum destinatario para emitir cartao")

    student_ids = [recipient.student_id for recipient in recipients]
    if len(set(student_ids)) != len(student_ids):
        raise AnswerCardIssuingError("Destinatario repetido no lote")

    exam = await session.get(MockExam, mock_exam_id)
    if exam is None:
        raise AnswerCardIssuingError(f"Simulado {mock_exam_id} nao existe")
    if exam.status not in _ISSUABLE_STATUSES:
        raise AnswerCardIssuingError(
            f"Simulado {mock_exam_id} esta em {exam.status}; cartoes so podem ser "
            f"emitidos em {' ou '.join(_ISSUABLE_STATUSES)}. Reemitir agora trocaria "
            "os tokens de folhas que ja estao com os alunos."
        )

    # Isolamento multi-tenant (spec s3.0): a FK composta ja recusaria, mas uma
    # IntegrityError crua nao diz a quem chamou o que aconteceu.
    known = set((await session.execute(
        select(Student.id).where(
            Student.school_id == exam.school_id, Student.id.in_(student_ids)
        )
    )).scalars().all())
    outsiders = [str(sid) for sid in student_ids if sid not in known]
    if outsiders:
        raise AnswerCardIssuingError(
            f"Alunos que nao pertencem a escola do simulado: {sorted(outsiders)}"
        )

    spec = spec or DEFAULT_LAYOUT
    storage = storage or MaterialStorage()
    template = build_template(spec)
    now = datetime.now(timezone.utc)

    issued: list[IssuedCard] = []
    manifest_cards: list[dict[str, Any]] = []
    for recipient in recipients:
        token = secrets.token_urlsafe(token_bytes)
        card = AnswerCard(
            school_id=exam.school_id,
            mock_exam_id=mock_exam_id,
            student_id=recipient.student_id,
            booklet_code=DEFAULT_BOOKLET_CODE,
            qr_token=token,
            template_version=template.template_version,
            printed_at=now,
        )
        session.add(card)
        await session.flush()
        issued.append(IssuedCard(
            answer_card_id=card.id, student_id=recipient.student_id, qr_token=token
        ))
        manifest_cards.append({
            "student_name": recipient.full_name,
            "qr_token": token,
            "class_label": recipient.class_label,
            "student_code": recipient.student_code,
            "booklet_code": DEFAULT_BOOKLET_CODE,
        })

    template_payload = template.to_dict()
    manifest_payload = {
        "manifest_version": MANIFEST_VERSION,
        "exam_name": exam.name,
        "template_version": template.template_version,
        "layout": spec.to_dict(),
        "template": template_payload,
        "cards": manifest_cards,
    }

    template_path, template_hash = _store_json(
        storage, template_payload, f"template_{template.template_version}.json"
    )
    manifest_path, manifest_hash = _store_json(
        storage, manifest_payload, f"print_job_{mock_exam_id}.json"
    )

    exam.status = "PRINTED"
    await session.commit()

    return AnswerCardBatch(
        mock_exam_id=mock_exam_id,
        school_id=exam.school_id,
        template_version=template.template_version,
        cards=tuple(issued),
        template_path=template_path,
        template_sha256=template_hash,
        manifest_path=manifest_path,
        manifest_sha256=manifest_hash,
    )


def _store_json(
    storage: MaterialStorage, payload: dict[str, Any], filename: str
) -> tuple[Path, str]:
    """Serializa ``payload`` num arquivo temporario e o copia para o storage.

    ``MaterialStorage.store`` recebe um caminho de origem, nunca bytes - esta
    funcao e a ponte, e o temporario e descartado logo em seguida. Isso evita
    criar uma segunda API de storage so porque aqui o conteudo nasce em memoria.

    ``sort_keys=True`` nao e estetica: e o que torna o hash de conteudo estavel
    entre execucoes, e portanto o que faz a deduplicacao do MaterialStorage
    funcionar para um template que nao mudou.
    """
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / filename
        source.write_text(body, encoding="utf-8")
        return storage.store(source)


__all__ = [
    "MANIFEST_VERSION",
    "QR_TOKEN_BYTES",
    "AnswerCardBatch",
    "AnswerCardIssuingError",
    "AnswerCardRecipient",
    "IssuedCard",
    "issue_answer_cards",
]
```

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `.venv/bin/python -m pytest tests/test_answer_card_issuing.py -v`
Expected: PASS (8 testes).

- [ ] **Step 5: Fechar o ciclo de ponta a ponta, atravessando a fronteira**

Este é o passo que prova que as duas metades se encaixam. Ele usa os **dois** ambientes.

Run:
```bash
# 1. O core emite: linhas, template e manifesto.
.venv/bin/python - <<'PY'
import asyncio, os, shutil
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import AcademicYear, MockExam, Person, School, Student
from agente_ia_edu.services.answer_card_issuing import AnswerCardRecipient, issue_answer_cards
from agente_ia_edu.services.material_storage import MaterialStorage

user = os.getenv("POSTGRES_USER", "agenteedu"); pwd = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
url = f"postgresql+psycopg://{user}:{pwd}@localhost:5433/agente_ia_edu_e2e_cartao"

async def main():
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        school = School(code="SIM-E2E", name="Escola E2E"); s.add(school); await s.flush()
        year = AcademicYear(school_id=school.id, year=2026, status="ACTIVE"); s.add(year); await s.flush()
        exam = MockExam(school_id=school.id, academic_year_id=year.id, name="Simulado E2E", exam_day=1)
        s.add(exam)
        person = Person(school_id=school.id, full_name="Ana Beatriz de Souza"); s.add(person); await s.flush()
        student = Student(school_id=school.id, person_id=person.id); s.add(student); await s.commit()
        batch = await issue_answer_cards(
            s, mock_exam_id=exam.id,
            recipients=(AnswerCardRecipient(student_id=student.id, full_name=person.full_name,
                                            class_label="3a serie A", student_code="2026-0001"),),
            storage=MaterialStorage(root=Path("/tmp/e2e_cartao_storage")),
        )
        shutil.copy(batch.manifest_path, "/tmp/manifesto_e2e.json")
        print("manifesto:", "/tmp/manifesto_e2e.json")
    await engine.dispose()

asyncio.run(main())
PY

# 2. O ambiente isolado imprime, a partir do manifesto que o core gravou.
workers/simulado/.venv-simulado/bin/python -m simulado_card_renderer \
    /tmp/manifesto_e2e.json --out /tmp/cartoes_e2e.pdf
```
Expected: a segunda etapa imprime `1 cartao(oes) em /tmp/cartoes_e2e.pdf`. Abra o PDF e confirme que é o cartão da Ana. Limpe depois: `rm -rf /tmp/e2e_cartao_storage /tmp/manifesto_e2e.json /tmp/cartoes_e2e.pdf` e derrube o banco `agente_ia_edu_e2e_cartao`.

- [ ] **Step 6: Rodar a suíte inteira do core**

Run: `.venv/bin/python -m pytest -q`
Expected: nenhuma falha nova em relação ao baseline, e `tests/test_core_graphics_boundary.py` continuando a passar.

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/services/answer_card_issuing.py \
        tests/test_answer_card_issuing.py
git commit -m "feat(simulados): emissao de cartoes com token opaco, template e manifesto de impressao

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## O que esta fase deliberadamente NÃO entrega

Registrado aqui para que a ausência seja lida como decisão, não como esquecimento.

- **Leitura óptica** (`omr_worker`, `cv2`, rasterização, homografia, Otsu, fila de conferência humana) — Fase 4 do spec §9. As tabelas `answer_card_scans`, `answer_card_marks` e `omr_jobs` existem, vazias, porque o modelo de dados é entregue inteiro aqui. O ambiente isolado que a Fase 4 vai usar já nasce nesta fase (Task 8), com ReportLab; ela acrescenta OpenCV e PyMuPDF ao mesmo `workers/simulado/pyproject.toml`.
- **Motor de TRI** (EM Bock-Aitkin, EAP, escala de reporte, portões de qualidade, equalização por âncoras) — Fases 3 e 5. As quatro tabelas `tri_*` existem, vazias.
- **Relatórios** de aluno, turma, escola e itens — Fase 3 em diante.
- **Rotas HTTP de portal** (`teacher_portal.py`, `coordination_portal.py`) e qualquer tela. Nesta fase os cartões são emitidos por chamada de serviço, não por botão.
- **Serviço que grava `mock_exam_workflow_audit`.** A tabela existe e é testada, mas quem registra cada transição é o `simulado_service` (spec §2.4), da Fase 2. A única transição desta fase, `DRAFT → PRINTED` (Task 9), muda `mock_exams.status` sem escrever auditoria — deliberado, para não espalhar a regra de auditoria por dois lugares antes de ela ter dono.
- **Digitação manual de respostas.** `mock_exam_responses.source`/`entered_by_external_id` existem, com `created_at` por default do modelo, mas o fluxo que os preenche é a Fase 2.
- **Gravar `tri_scales` no banco a partir do seed.** O seed é lido por `load_reference_scales()`; quem cria a régua é a coordenação (spec §6.3.1), pela rota da Fase 3.
- **Cadernos embaralhados.** `booklet_code` fica em `"UNICO"`; nenhuma lógica de permutação existe (spec, "Fora de escopo").
- **Aceite físico** (§8.3): imprimir, preencher à mão, fotografar e comparar. É portão da Fase 4 e precisa de tempo de calendário, não de esforço de engenharia.

---

## Self-Review

**Cobertura do spec:**

| Requisito do spec | Task |
|---|---|
| §3.0 `school_id` + FK composta `(school_id, parent_id)` em **todas** as tabelas | 1, 2, 3 (as 13 tabelas, sem exceção) |
| §3.0 `UNIQUE(school_id, id)` nos pais | 1 (`mock_exams`, `mock_exam_items`), 2 (`answer_cards`, `answer_card_scans`), 3 (`tri_scales`, `tri_calibrations`) |
| §3 `mock_exams` com `updated_at` (`default` + `onupdate`) e `academic_year_id` NOT NULL | 1 |
| §3 `mock_exam_workflow_audit` no padrão de `assessment_workflow_audit`, ator como identificador **externo** (`String(255)`), `occurred_at` com default | 1 |
| §3 `mock_exam_areas`, `mock_exam_items` | 1 |
| §3 `mock_exam_responses` com `source`, `entered_by_external_id`, `created_at` (com default) | 1 |
| §3 `answer_cards`, `answer_card_scans` | 2 |
| §3 `answer_card_marks` com `resolution` `PENDING\|AUTO\|HUMAN` | 2 |
| §3 `omr_jobs` com `created_at` (FIFO) | 2 |
| §3 `tri_scales`, `tri_calibrations`, `tri_item_parameters`, `tri_student_scores` | 3 |
| §3 migration a partir da 057, sem tocar na 056 | 1, 2, 3 (`057 → 058 → 059`) |
| §3 `qr_token` opaco e único **globalmente** | 2 (coluna + índice), 9 (`secrets.token_urlsafe`) |
| §3 imagens via `MaterialStorage` | 9 |
| §2.1 PDF nominal por aluno | 8 |
| §2.1 QR de token opaco, sem biblioteca de QR adicional | 8 (`QrCodeWidget` do próprio ReportLab) |
| §2.1 quatro ArUco pré-gerados como assets versionados | 5 |
| §2.1 template geométrico emitido no mesmo ato | 6 (geometria), 9 (gravado junto com o manifesto e os tokens) |
| §2.1 tipo do template declarado **uma vez**, importado pelo leitor; chaves do JSON publicadas | 6 (contrato + teste que trava as chaves) |
| §2.1/§2.3 gerador fora do ambiente do core, junto do `omr_worker` | 7 (sentinela), 8 (ambiente isolado + container) |
| §6.3.1 seed `enem_reference_scales.yaml` com os valores da tabela | 4 |
| §6.3.1 script que computa as medianas dos microdados | 4 |
| §6.3.1 régua não verificada (`reference_verified_at` nulo) | 3 (coluna nulável), 4 (`verified: false`) |
| §4 passo 2: status → `PRINTED` | 9 |

**Lacunas conhecidas e onde estão registradas:** as medianas de §6.3.1 saem como `null` + `median_status: PENDING_MICRODATA` (Task 4, "Decisão registrada"); a escrita de `mock_exam_workflow_audit` fica para a Fase 2, registrado na lista acima.

**Tabelas por migration (13 no total — conferido contra o §3 do spec, que lista 13, não 14):**

| Migration | Tabelas |
|---|---|
| `057_mock_exam_core` | `mock_exams`, `mock_exam_workflow_audit`, `mock_exam_areas`, `mock_exam_items`, `mock_exam_responses` (5) |
| `058_answer_card_capture` | `answer_cards`, `answer_card_scans`, `answer_card_marks`, `omr_jobs` (4) |
| `059_tri_scales_and_scores` | `tri_scales`, `tri_calibrations`, `tri_item_parameters`, `tri_student_scores` (4) |

Todas as 13 carregam `school_id` e FKs compostas `(school_id, parent_id) → parent(school_id, id)`, sem exceção. `UNIQUE(school_id, id)` nas seis que são pais: `mock_exams`, `mock_exam_items`, `answer_cards`, `answer_card_scans`, `tri_scales`, `tri_calibrations`.

**Contrato do JSON do template (Task 6) — consumido de fora, inclusive pelo leitor óptico da Fase 4, que importa `agente_ia_edu.simulado_card.template.CardTemplate` em vez de redeclará-lo:**

| Nível | Chaves exatas |
|---|---|
| raiz | `template_version`, `item_count`, `option_codes`, `marker_rect_aspect`, `markers`, `qr`, `bubbles` |
| `markers[]` | `corner`, `marker_id`, `cx`, `cy`, `size` |
| `qr` | `cx`, `cy`, `size` |
| `bubbles[]` | `item_position`, `option`, `cx`, `cy`, `radius` |

`corner` ∈ `TOP_LEFT`, `TOP_RIGHT`, `BOTTOM_RIGHT`, `BOTTOM_LEFT` (nessa ordem, `markers.CORNERS`); `option` ∈ `A`–`E` (`template.OPTION_CODES`); `item_position` é 1-based; `cx`/`cy`/`size`/`radius` são adimensionais, relativos ao retângulo formado pelos **centros** dos quatro marcadores — `(0,0)` no superior-esquerdo, `(1,1)` no inferior-direito, `y` crescendo para baixo — com `size` e `radius` normalizados pela **largura** desse retângulo. `marker_rect_aspect` é a altura/largura desse mesmo retângulo (`1.4278` no layout padrão, de 180 × 257 mm), obrigatória em `from_dict`, e é ela que permite ao leitor retificar sem esticar. `LAYOUT_VERSION = "enem-90x5-v1"` muda junto com qualquer renomeação de chave.

| Requisito do spec | Task |
|---|---|
| §2.1 `marker_rect_aspect` no template, exigida por `from_dict` | 6 |
| §3 ator como identificador externo em **todos** os pontos (`actor_external_id`, `entered_by_external_id`, `resolved_by_external_id`) | 1, 2 |
