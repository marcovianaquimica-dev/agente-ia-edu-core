# R1 — Régua ENEM versionada e contrato de saída do motor — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carregar a régua ENEM 2025 da cartilha oficial do INEP com procedência por descritor, e entregar um contrato de saída de motor que rejeita qualquer correção não verificável antes de persistir.

**Architecture:** Cinco tabelas novas guardam a régua; o texto normativo vive em um YAML revisável em PR e é carregado por um seed idempotente. O contrato é um módulo Pydantic versionado, validado em três camadas — forma, coerência com a régua, e ancoragem no texto. Nada em R1 chama provedor de IA, e nada em R1 persiste correção.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x async, Alembic, Pydantic 2.13, PyYAML, `unittest.IsolatedAsyncioTestCase` sobre SQLite em memória.

**Spec:** `docs/superpowers/specs/2026-09-13-r1-regua-enem-contrato-motor-design.md`

## Global Constraints

- Python `>=3.13,<3.14`. SQLAlchemy `>=2.0,<3.0`. Pydantic 2.x.
- **Nenhuma tabela existente é alterada.** A migration 039 é puramente aditiva e reversível.
- **Nenhuma chamada a provedor de IA.** A suíte inteira de R1 roda sem modelo.
- Modelos usam `Mapped`/`mapped_column`, `Uuid`, `JSONBCompatible`, e `metadata_` mapeado para a coluna `metadata` — o padrão de `db/models/admin.py`.
- Testes usam `unittest.IsolatedAsyncioTestCase` com `sqlite+aiosqlite:///:memory:` e `StaticPool`, no padrão de `tests/test_platform_administration.py`. Não existe `conftest.py` no projeto; não crie um.
- Código e comentários em inglês; docstrings podem citar a spec em português. Mensagens de erro em inglês, no padrão do repositório.
- Toda tabela nova tem prefixo `essay_`.
- SHA-256 da cartilha oficial, usado literalmente: `d8ab44dcbf5af808829d9dee89d23e7efa4f59df022b99102fac87489b870288`
- URL oficial, usada literalmente: `https://download.inep.gov.br/publicacoes/institucionais/avaliacoes_e_exames_da_educacao_basica/a_redacao_no_enem_2025_cartilha_do_participante.pdf`
- `rubric_version` da primeira régua: `ENEM_2025`. `CONTRACT_VERSION`: `essay_engine_output_v1`.

## Refinamento da spec, decidido aqui

A spec §7 coloca "o total tem de ser a soma das cinco" na camada 2 (coerência com a régua). Essa verificação **não precisa da régua** — é aritmética interna do output. Ela foi movida para a camada 1 (schema). A camada 2 continua dona do que depende da régua: quais pontos são válidos para cada competência, quais competências existem, e quais sinais existem. Nenhuma verificação foi perdida.

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/agente_ia_edu/services/canonical_hash.py` | Canonicalização JSON e sha256, compartilhados |
| `src/agente_ia_edu/db/models/essay_rubric.py` | As cinco tabelas da régua |
| `migrations/versions/039_essay_rubric_foundation.py` | Migration aditiva |
| `tools/extract_cartilha_enem.py` | Ferramenta de uma vez: decodifica o PDF e gera YAML rascunho |
| `src/agente_ia_edu/rubrics/enem_2025.yaml` | O texto normativo, revisável em PR |
| `src/agente_ia_edu/rubrics/loader.py` | Lê e valida a estrutura do YAML |
| `src/agente_ia_edu/services/essay_rubric_seed.py` | Seed idempotente YAML → tabelas |
| `src/agente_ia_edu/essay_engine_contract/v1.py` | Contrato Pydantic (camada 1) |
| `src/agente_ia_edu/services/essay_engine_validation.py` | Camadas 2 e 3, e a exceção de rejeição |
| `src/agente_ia_edu/services/essay_correction_key.py` | Normalização de texto e chave de repetibilidade |
| `src/agente_ia_edu/essay_prompts/__init__.py` | Mecanismo de versionamento de prompt |

Oito tarefas. Cada uma termina com commit e é independentemente testável.

---

### Task 1: Utilitário compartilhado de canonicalização

O helper existe hoje como `ClassificationProposalService._hash`. Correções e classificações já persistidas guardam hashes produzidos por ele, então a extração **tem de preservar o dígito exato** — é refactor de comportamento preservado, e o teste é de caracterização.

**Files:**
- Create: `src/agente_ia_edu/services/canonical_hash.py`
- Create: `tests/test_r1_canonical_hash.py`
- Modify: `src/agente_ia_edu/services/curriculum_classification.py:1223-1225`

**Interfaces:**
- Consumes: nada.
- Produces: `canonical_json(value: Any) -> str`, `canonical_hash(value: Any) -> str`.

- [ ] **Step 1: Escreva o teste que falha**

```python
# tests/test_r1_canonical_hash.py
import hashlib
import json
import unittest

from agente_ia_edu.services.canonical_hash import canonical_hash, canonical_json


class TestCanonicalHash(unittest.TestCase):
    def test_matches_the_legacy_inline_expression(self):
        """Characterization: rows already persisted carry digests produced by the
        inline expression in ClassificationProposalService._hash. Extraction must
        not change a single byte."""
        payload = {"b": 1, "a": "acentuação", "c": [3, 2, 1]}
        legacy = hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()

        self.assertEqual(canonical_hash(payload), legacy)

    def test_key_order_does_not_change_the_digest(self):
        self.assertEqual(
            canonical_hash({"a": 1, "b": 2}), canonical_hash({"b": 2, "a": 1})
        )

    def test_canonical_json_keeps_non_ascii_literal(self):
        self.assertEqual(canonical_json({"k": "ção"}), '{"k":"ção"}')
```

- [ ] **Step 2: Rode e confirme que falha**

Run: `.venv/bin/python -m pytest tests/test_r1_canonical_hash.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.services.canonical_hash'`

- [ ] **Step 3: Implemente**

```python
# src/agente_ia_edu/services/canonical_hash.py
"""Canonical JSON serialisation and hashing, shared by every engine whose output
must be repeatable.

Extracted verbatim from ``ClassificationProposalService._hash`` (PHASE 11). The
byte-level behaviour is frozen on purpose: ``pedagogical_classifications`` rows
already store ``input_hash``/``output_hash`` values produced by that expression,
and changing the serialisation would silently orphan every one of them.

``sort_keys`` makes the digest independent of key order; ``ensure_ascii=False``
keeps accented Portuguese literal instead of escaping it; the tight separators
remove insignificant whitespace.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Deterministic JSON text for ``value``."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    """SHA-256 of :func:`canonical_json` of ``value``."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


__all__ = ["canonical_hash", "canonical_json"]
```

- [ ] **Step 4: Rode e confirme que passa**

Run: `.venv/bin/python -m pytest tests/test_r1_canonical_hash.py -v`
Expected: PASS, 3 testes

- [ ] **Step 5: Aponte o call site existente para o utilitário**

Em `src/agente_ia_edu/services/curriculum_classification.py`, substitua o corpo do método (linhas 1223-1225), mantendo o método como delegação fina para não tocar em nenhum dos três call sites:

```python
    @staticmethod
    def _hash(value: Any) -> str:
        # Behaviour frozen in services/canonical_hash.py - see the docstring there.
        return canonical_hash(value)
```

E adicione o import junto dos demais imports do módulo:

```python
from agente_ia_edu.services.canonical_hash import canonical_hash
```

- [ ] **Step 6: Rode a suíte de classificação para provar que nada mudou**

Run: `.venv/bin/python -m pytest tests/test_taxonomy_reclassification_workflow.py tests/test_question_governance.py -v`
Expected: PASS, mesmo resultado de antes da mudança

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/services/canonical_hash.py tests/test_r1_canonical_hash.py src/agente_ia_edu/services/curriculum_classification.py
git commit -m "refactor: extrai canonicalização JSON para utilitário compartilhado

O hash de repetibilidade de R1 usa a mesma canonicalização que a
classificação já usa. Extraído com teste de caracterização: linhas já
persistidas guardam dígitos produzidos pela expressão inline, então o
comportamento é congelado byte a byte."
```

---

### Task 2: As cinco tabelas da régua

**Files:**
- Create: `src/agente_ia_edu/db/models/essay_rubric.py`
- Modify: `src/agente_ia_edu/db/models/__init__.py`
- Create: `migrations/versions/039_essay_rubric_foundation.py`
- Create: `tests/test_r1_essay_rubric_models.py`

**Interfaces:**
- Consumes: nada.
- Produces: `EssayRubric`, `EssayRubricCompetency`, `EssayRubricLevel`, `EssayRubricSignal`, `EssayRubricZeroRule`, exportados de `agente_ia_edu.db.models`.

- [ ] **Step 1: Escreva o teste que falha**

```python
# tests/test_r1_essay_rubric_models.py
import unittest
import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    EssayRubric,
    EssayRubricCompetency,
    EssayRubricLevel,
    EssayRubricSignal,
    EssayRubricZeroRule,
)


class TestEssayRubricModels(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.execute(text("PRAGMA foreign_keys=ON"))
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _rubric(self, session) -> EssayRubric:
        rubric = EssayRubric(
            rubric_version="ENEM_2025",
            label="Matriz de Referência ENEM - Cartilha do Participante 2025",
            effective_year=2025,
            official_source_sha256="d8ab44dcbf5af808829d9dee89d23e7efa4f59df022b99102fac87489b870288",
            status="ACTIVE",
        )
        session.add(rubric)
        await session.flush()
        return rubric

    async def test_rubric_version_is_unique(self):
        async with self.session_factory() as session:
            await self._rubric(session)
            session.add(EssayRubric(rubric_version="ENEM_2025", label="duplicate"))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_level_points_outside_the_six_official_values_are_rejected(self):
        async with self.session_factory() as session:
            rubric = await self._rubric(session)
            competency = EssayRubricCompetency(
                rubric_id=rubric.id, code="C1", ordinal=1,
                official_title="Demonstrar domínio da modalidade escrita formal da língua portuguesa.",
                source_page=14,
            )
            session.add(competency)
            await session.flush()

            session.add(EssayRubricLevel(
                competency_id=competency.id, points=137,
                descriptor="valor inexistente na matriz", source_page=14,
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_official_signal_without_source_ref_is_rejected(self):
        async with self.session_factory() as session:
            rubric = await self._rubric(session)
            competency = EssayRubricCompetency(
                rubric_id=rubric.id, code="C2", ordinal=2,
                official_title="Compreender a proposta de redação...", source_page=22,
            )
            session.add(competency)
            await session.flush()

            session.add(EssayRubricSignal(
                competency_id=competency.id, key="repertorio_pertinencia",
                label="Pertinência do repertório", provenance="OFICIAL_INEP",
                source_ref=None,
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_engine_heuristic_without_rationale_is_rejected(self):
        async with self.session_factory() as session:
            rubric = await self._rubric(session)
            competency = EssayRubricCompetency(
                rubric_id=rubric.id, code="C3", ordinal=3,
                official_title="Selecionar, relacionar, organizar e interpretar...", source_page=30,
            )
            session.add(competency)
            await session.flush()

            session.add(EssayRubricSignal(
                competency_id=competency.id, key="progressao_tematica",
                label="Progressão temática", provenance="HEURISTICA_MOTOR",
                source_ref="decisão interna", rationale=None,
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_zero_rule_records_its_effect(self):
        async with self.session_factory() as session:
            rubric = await self._rubric(session)
            session.add(EssayRubricZeroRule(
                rubric_id=rubric.id, key="fuga_ao_tema",
                label="Fuga ao tema", effect="ANULA_REDACAO",
                competency_code=None, source_page=9, provenance="OFICIAL_INEP",
            ))
            await session.flush()
```

- [ ] **Step 2: Rode e confirme que falha**

Run: `.venv/bin/python -m pytest tests/test_r1_essay_rubric_models.py -v`
Expected: FAIL com `ImportError: cannot import name 'EssayRubric'`

- [ ] **Step 3: Crie o módulo de modelos**

```python
# src/agente_ia_edu/db/models/essay_rubric.py
"""R1 - Versioned ENEM essay rubric with per-descriptor provenance.

Five additive tables. Nothing here touches an existing table.

The normative text is NOT hardcoded (spec v1.0 §3): it is seeded from
``src/agente_ia_edu/rubrics/*.yaml``, which carries the official source page for
every descriptor and is reviewed in a pull request against the INEP PDF.

Why levels, signals and zero rules are three tables and not one: levels are
immutable official text scored on a fixed six-value scale; signals are the
observable vocabulary the engine reasons with, and each one declares where it
came from (spec §17); zero rules are short-circuit conditions that annul the
essay or a competency and therefore carry no points at all. Folding zero rules
into levels would break the points CHECK.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..types import JSONBCompatible

COMPETENCY_CODES = ("C1", "C2", "C3", "C4", "C5")
OFFICIAL_LEVEL_POINTS = (0, 40, 80, 120, 160, 200)
PROVENANCES = ("OFICIAL_INEP", "INTERPRETACAO_PEDAGOGICA", "HEURISTICA_MOTOR")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EssayRubric(Base):
    """One version of a correction rubric. ENEM is the first, not the only one:
    schools run their own vestibular grids, so several rubrics stay ACTIVE at the
    same time and there is deliberately no single-ACTIVE constraint."""

    __tablename__ = "essay_rubrics"
    __table_args__ = (
        UniqueConstraint("rubric_version", name="uq_essay_rubrics_version"),
        CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'SUPERSEDED')", name="ck_essay_rubrics_status"
        ),
        Index("ix_essay_rubrics_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    rubric_version: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    effective_year: Mapped[int | None] = mapped_column(Integer)
    max_total_points: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    official_source_title: Mapped[str | None] = mapped_column(Text)
    official_source_url: Mapped[str | None] = mapped_column(Text)
    official_source_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    competencies: Mapped[list["EssayRubricCompetency"]] = relationship(
        back_populates="rubric"
    )
    zero_rules: Mapped[list["EssayRubricZeroRule"]] = relationship(
        back_populates="rubric"
    )


class EssayRubricCompetency(Base):
    """C1-C5. ``official_title`` is the literal wording of the matrix."""

    __tablename__ = "essay_rubric_competencies"
    __table_args__ = (
        UniqueConstraint("rubric_id", "code", name="uq_essay_rubric_competencies_code"),
        CheckConstraint(
            "code IN ('C1', 'C2', 'C3', 'C4', 'C5')",
            name="ck_essay_rubric_competencies_code",
        ),
        Index("ix_essay_rubric_competencies_rubric_id", "rubric_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    rubric_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("essay_rubrics.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(4), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    official_title: Mapped[str] = mapped_column(Text, nullable=False)
    max_points: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    source_page: Mapped[int | None] = mapped_column(Integer)

    rubric: Mapped["EssayRubric"] = relationship(back_populates="competencies")
    levels: Mapped[list["EssayRubricLevel"]] = relationship(back_populates="competency")
    signals: Mapped[list["EssayRubricSignal"]] = relationship(
        back_populates="competency"
    )


class EssayRubricLevel(Base):
    """One of the six official performance levels of a competency.

    ``descriptor`` is literal cartilha text; ``source_page`` is mandatory so any
    reviewer can open the PDF and check the wording."""

    __tablename__ = "essay_rubric_levels"
    __table_args__ = (
        UniqueConstraint(
            "competency_id", "points", name="uq_essay_rubric_levels_points"
        ),
        CheckConstraint(
            "points IN (0, 40, 80, 120, 160, 200)", name="ck_essay_rubric_levels_points"
        ),
        Index("ix_essay_rubric_levels_competency_id", "competency_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    competency_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("essay_rubric_competencies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    descriptor: Mapped[str] = mapped_column(Text, nullable=False)
    source_page: Mapped[int] = mapped_column(Integer, nullable=False)
    provenance: Mapped[str] = mapped_column(
        String(30), nullable=False, default="OFICIAL_INEP"
    )

    competency: Mapped["EssayRubricCompetency"] = relationship(back_populates="levels")


class EssayRubricSignal(Base):
    """The observable vocabulary the engine reasons with (spec §4 and §17).

    The two CHECK constraints are what make provenance a rule instead of a
    decoration: anything claiming an external source must name it, and anything
    that is our own heuristic must justify itself in writing."""

    __tablename__ = "essay_rubric_signals"
    __table_args__ = (
        UniqueConstraint("competency_id", "key", name="uq_essay_rubric_signals_key"),
        CheckConstraint(
            "provenance IN ('OFICIAL_INEP', 'INTERPRETACAO_PEDAGOGICA', 'HEURISTICA_MOTOR')",
            name="ck_essay_rubric_signals_provenance",
        ),
        CheckConstraint(
            "provenance = 'HEURISTICA_MOTOR' OR source_ref IS NOT NULL",
            name="ck_essay_rubric_signals_source_ref",
        ),
        CheckConstraint(
            "provenance <> 'HEURISTICA_MOTOR' OR rationale IS NOT NULL",
            name="ck_essay_rubric_signals_rationale",
        ),
        Index("ix_essay_rubric_signals_competency_id", "competency_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    competency_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("essay_rubric_competencies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    provenance: Mapped[str] = mapped_column(String(30), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    competency: Mapped["EssayRubricCompetency"] = relationship(back_populates="signals")


class EssayRubricZeroRule(Base):
    """Annulment and zero conditions. ``competency_code`` is NULL when the rule
    annuls the whole essay rather than a single competency."""

    __tablename__ = "essay_rubric_zero_rules"
    __table_args__ = (
        UniqueConstraint("rubric_id", "key", name="uq_essay_rubric_zero_rules_key"),
        CheckConstraint(
            "effect IN ('ANULA_REDACAO', 'ZERA_COMPETENCIA')",
            name="ck_essay_rubric_zero_rules_effect",
        ),
        CheckConstraint(
            "competency_code IS NULL OR competency_code IN ('C1', 'C2', 'C3', 'C4', 'C5')",
            name="ck_essay_rubric_zero_rules_competency_code",
        ),
        Index("ix_essay_rubric_zero_rules_rubric_id", "rubric_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    rubric_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("essay_rubrics.id", ondelete="RESTRICT"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    effect: Mapped[str] = mapped_column(String(30), nullable=False)
    competency_code: Mapped[str | None] = mapped_column(String(4))
    source_page: Mapped[int | None] = mapped_column(Integer)
    provenance: Mapped[str] = mapped_column(
        String(30), nullable=False, default="OFICIAL_INEP"
    )

    rubric: Mapped["EssayRubric"] = relationship(back_populates="zero_rules")
```

- [ ] **Step 4: Exporte os modelos**

Em `src/agente_ia_edu/db/models/__init__.py`, adicione o import junto dos demais e os cinco nomes ao final de `__all__`:

```python
from .essay_rubric import (
    EssayRubric,
    EssayRubricCompetency,
    EssayRubricLevel,
    EssayRubricSignal,
    EssayRubricZeroRule,
)
```

```python
    "EssayRubric",
    "EssayRubricCompetency",
    "EssayRubricLevel",
    "EssayRubricSignal",
    "EssayRubricZeroRule",
```

- [ ] **Step 5: Rode e confirme que passa**

Run: `.venv/bin/python -m pytest tests/test_r1_essay_rubric_models.py -v`
Expected: PASS, 5 testes

- [ ] **Step 6: Escreva a migration**

Antes de escrever, confirme qual é a revisão head — outra frente de trabalho ativa neste repositório também cria migrations:

Run: `.venv/bin/python -m alembic heads`

Se a head não for `038_authorial_classification`, use a head real em `down_revision` e renumere o arquivo.

```python
# migrations/versions/039_essay_rubric_foundation.py
"""R1 - ENEM essay rubric foundation.

Revision ID: 039_essay_rubric_foundation
Revises: 038_authorial_classification

Purely additive: creates five new tables, touches zero rows in any existing
table, and alters no existing column, constraint or index. Fully reversible.

Audit of reuse (spec §5): no table in this schema stores rubric text or scoring
levels. ``pedagogical_classifications`` carries model/prompt versioning for
QUESTION classification and is unrelated to essay scoring; reusing it would
overload a table that already has a distinct lifecycle. Nothing is added to it.
"""

from alembic import op
import sqlalchemy as sa

revision = "039_essay_rubric_foundation"
down_revision = "038_authorial_classification"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "essay_rubrics",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rubric_version", sa.String(50), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("effective_year", sa.Integer()),
        sa.Column("max_total_points", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("official_source_title", sa.Text()),
        sa.Column("official_source_url", sa.Text()),
        sa.Column("official_source_sha256", sa.String(64)),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column("metadata", _JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("rubric_version", name="uq_essay_rubrics_version"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'SUPERSEDED')", name="ck_essay_rubrics_status"
        ),
    )
    op.create_index("ix_essay_rubrics_status", "essay_rubrics", ["status"])

    op.create_table(
        "essay_rubric_competencies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rubric_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(4), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("official_title", sa.Text(), nullable=False),
        sa.Column("max_points", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("source_page", sa.Integer()),
        sa.ForeignKeyConstraint(["rubric_id"], ["essay_rubrics.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("rubric_id", "code", name="uq_essay_rubric_competencies_code"),
        sa.CheckConstraint(
            "code IN ('C1', 'C2', 'C3', 'C4', 'C5')",
            name="ck_essay_rubric_competencies_code",
        ),
    )
    op.create_index(
        "ix_essay_rubric_competencies_rubric_id", "essay_rubric_competencies", ["rubric_id"]
    )

    op.create_table(
        "essay_rubric_levels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("competency_id", sa.Uuid(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("descriptor", sa.Text(), nullable=False),
        sa.Column("source_page", sa.Integer(), nullable=False),
        sa.Column("provenance", sa.String(30), nullable=False, server_default="OFICIAL_INEP"),
        sa.ForeignKeyConstraint(
            ["competency_id"], ["essay_rubric_competencies.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("competency_id", "points", name="uq_essay_rubric_levels_points"),
        sa.CheckConstraint(
            "points IN (0, 40, 80, 120, 160, 200)", name="ck_essay_rubric_levels_points"
        ),
    )
    op.create_index(
        "ix_essay_rubric_levels_competency_id", "essay_rubric_levels", ["competency_id"]
    )

    op.create_table(
        "essay_rubric_signals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("competency_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("provenance", sa.String(30), nullable=False),
        sa.Column("source_ref", sa.Text()),
        sa.Column("rationale", sa.Text()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(
            ["competency_id"], ["essay_rubric_competencies.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("competency_id", "key", name="uq_essay_rubric_signals_key"),
        sa.CheckConstraint(
            "provenance IN ('OFICIAL_INEP', 'INTERPRETACAO_PEDAGOGICA', 'HEURISTICA_MOTOR')",
            name="ck_essay_rubric_signals_provenance",
        ),
        sa.CheckConstraint(
            "provenance = 'HEURISTICA_MOTOR' OR source_ref IS NOT NULL",
            name="ck_essay_rubric_signals_source_ref",
        ),
        sa.CheckConstraint(
            "provenance <> 'HEURISTICA_MOTOR' OR rationale IS NOT NULL",
            name="ck_essay_rubric_signals_rationale",
        ),
    )
    op.create_index(
        "ix_essay_rubric_signals_competency_id", "essay_rubric_signals", ["competency_id"]
    )

    op.create_table(
        "essay_rubric_zero_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rubric_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("effect", sa.String(30), nullable=False),
        sa.Column("competency_code", sa.String(4)),
        sa.Column("source_page", sa.Integer()),
        sa.Column("provenance", sa.String(30), nullable=False, server_default="OFICIAL_INEP"),
        sa.ForeignKeyConstraint(["rubric_id"], ["essay_rubrics.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("rubric_id", "key", name="uq_essay_rubric_zero_rules_key"),
        sa.CheckConstraint(
            "effect IN ('ANULA_REDACAO', 'ZERA_COMPETENCIA')",
            name="ck_essay_rubric_zero_rules_effect",
        ),
        sa.CheckConstraint(
            "competency_code IS NULL OR competency_code IN ('C1', 'C2', 'C3', 'C4', 'C5')",
            name="ck_essay_rubric_zero_rules_competency_code",
        ),
    )
    op.create_index(
        "ix_essay_rubric_zero_rules_rubric_id", "essay_rubric_zero_rules", ["rubric_id"]
    )


def downgrade() -> None:
    op.drop_table("essay_rubric_zero_rules")
    op.drop_table("essay_rubric_signals")
    op.drop_table("essay_rubric_levels")
    op.drop_table("essay_rubric_competencies")
    op.drop_table("essay_rubrics")
```

- [ ] **Step 7: Verifique a migration contra os modelos**

Run: `.venv/bin/python -m alembic upgrade head && .venv/bin/python -m alembic downgrade -1 && .venv/bin/python -m alembic upgrade head`
Expected: sobe, desce e sobe de novo sem erro

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/db/models/essay_rubric.py src/agente_ia_edu/db/models/__init__.py migrations/versions/039_essay_rubric_foundation.py tests/test_r1_essay_rubric_models.py
git commit -m "feat: tabelas da régua de redação, com procedência como constraint

Cinco tabelas aditivas. As duas CHECK de procedência em
essay_rubric_signals são o que impede a coluna de virar decoração: fonte
externa exige citação, heurística nossa exige justificativa escrita."
```

---

### Task 3: Ferramenta de decodificação da cartilha

O PDF do INEP usa subsets de fonte sem ToUnicode CMap. `pypdf` devolve `'HPRQVWUDGRPtQLRLQVX¿FLHQWH` onde está escrito "Demonstra domínio insuficiente": as letras vêm deslocadas 29 posições no code point, e alguns glifos são ligaduras. A ferramenta decodifica e **marca o que não souber decodificar**, para que a conferência humana tenha onde olhar.

Esta é ferramenta de uma vez, em `tools/`, fora do pacote instalável. Não é dependência de runtime.

**Files:**
- Create: `tools/extract_cartilha_enem.py`
- Create: `tests/test_r1_cartilha_decoder.py`

**Interfaces:**
- Consumes: nada.
- Produces: `decode_subset(text: str) -> str`, `UNDECODED_MARKER = "⟨?⟩"`.

- [ ] **Step 1: Escreva o teste que falha**

```python
# tests/test_r1_cartilha_decoder.py
import unittest

from tools.extract_cartilha_enem import UNDECODED_MARKER, decode_subset


class TestCartilhaDecoder(unittest.TestCase):
    def test_decodes_the_shifted_ascii_run(self):
        self.assertEqual(decode_subset("'HPRQVWUD"), "Demonstra")

    def test_decodes_a_run_containing_the_fi_ligature_glyph(self):
        self.assertEqual(decode_subset("LQVX¿FLHQWH"), "insuficiente")

    def test_leaves_already_correct_text_untouched(self):
        clean = "Demonstra bom domínio da modalidade escrita formal"
        self.assertEqual(decode_subset(clean), clean)

    def test_marks_a_glyph_it_cannot_decode(self):
        decoded = decode_subset("'HPRQVWUD§")
        self.assertIn(UNDECODED_MARKER, decoded)
```

- [ ] **Step 2: Rode e confirme que falha**

Run: `.venv/bin/python -m pytest tests/test_r1_cartilha_decoder.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'tools'`

- [ ] **Step 3: Implemente a ferramenta**

Crie `tools/__init__.py` vazio e:

```python
# tools/extract_cartilha_enem.py
"""One-shot tool: decode the INEP cartilha PDF and emit a YAML draft.

Why this is a tool and not a runtime parser
-------------------------------------------
The cartilha embeds subsetted fonts with no ToUnicode CMap, so ``pypdf`` returns
glyph codes rather than text: "Demonstra domínio insuficiente" arrives as
``'HPRQVWUDGRPtQLRLQVX¿FLHQWH``. The mapping is recoverable - ASCII letters are
shifted by a constant 29 code points, and a handful of glyphs are ligatures -
but running a decoder like this in production, over a third party's PDF, is
fragile and unnecessary. The rubric is seeded once from a reviewed YAML file.

Everything this tool cannot decode is replaced with UNDECODED_MARKER so the
human reviewing the YAML against the PDF knows exactly where to look.

Usage:
    python -m tools.extract_cartilha_enem <cartilha.pdf> > draft.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path

_SHIFT = 29
UNDECODED_MARKER = "⟨?⟩"

# Glyphs the subset maps to multi-character ligatures rather than to a shifted
# code point. Extend as the review surfaces more.
_LIGATURES = {
    "¿": "fi",  # ¿ -> fi
    "¾": "fl",  # ¾ -> fl
}

# A run is treated as encoded when it is made of printable ASCII that decodes
# into letters. Text that is already correct contains accented Portuguese and
# lowercase runs that would decode into control characters, so it is left alone.
_ENCODED_RANGE = range(0x21, 0x60)


def decode_subset(text: str) -> str:
    """Decode one string extracted from a subsetted-font run.

    Text that is already legible is returned unchanged.
    """
    if not _looks_encoded(text):
        return text

    out: list[str] = []
    for char in text:
        if char in _LIGATURES:
            out.append(_LIGATURES[char])
        elif ord(char) in _ENCODED_RANGE:
            out.append(chr(ord(char) + _SHIFT))
        elif char.isspace():
            out.append(char)
        else:
            out.append(UNDECODED_MARKER)
    return "".join(out)


def _looks_encoded(text: str) -> bool:
    """A run is encoded when most of its non-space characters sit in the shifted
    range and it contains no accented Portuguese letter."""
    meaningful = [c for c in text if not c.isspace()]
    if not meaningful:
        return False
    if any(c in "áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ" for c in meaningful):
        return False
    in_range = sum(1 for c in meaningful if ord(c) in _ENCODED_RANGE or c in _LIGATURES)
    return in_range / len(meaningful) > 0.8


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2

    from pypdf import PdfReader

    reader = PdfReader(Path(argv[1]))
    for number, page in enumerate(reader.pages, start=1):
        raw = page.extract_text() or ""
        print(f"# --- page {number} ---")
        for line in raw.splitlines():
            print(f"# {decode_subset(line)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 4: Rode e confirme que passa**

Run: `.venv/bin/python -m pytest tests/test_r1_cartilha_decoder.py -v`
Expected: PASS, 4 testes

- [ ] **Step 5: Rode a ferramenta sobre a cartilha real**

Baixe a cartilha e confirme o hash antes de usá-la:

```bash
curl -sSL -o /tmp/cartilha_enem_2025.pdf "https://download.inep.gov.br/publicacoes/institucionais/avaliacoes_e_exames_da_educacao_basica/a_redacao_no_enem_2025_cartilha_do_participante.pdf"
shasum -a 256 /tmp/cartilha_enem_2025.pdf
```

Expected: `d8ab44dcbf5af808829d9dee89d23e7efa4f59df022b99102fac87489b870288`

Se o hash divergir, **pare**: o INEP publicou outra versão e a régua precisa de nova revisão antes de qualquer seed.

```bash
.venv/bin/python -m tools.extract_cartilha_enem /tmp/cartilha_enem_2025.pdf > /tmp/cartilha_draft.txt
```

- [ ] **Step 6: Commit**

```bash
git add tools/__init__.py tools/extract_cartilha_enem.py tests/test_r1_cartilha_decoder.py
git commit -m "feat: ferramenta de decodificação da cartilha do INEP

O PDF usa subsets de fonte sem ToUnicode CMap. A decodificação é
determinística, mas roda uma vez para gerar o YAML revisável - nunca em
produção. O que não decodifica vira marcador visível para a conferência."
```

---

### Task 4: O arquivo da régua e seu carregador

**Files:**
- Create: `src/agente_ia_edu/rubrics/__init__.py`
- Create: `src/agente_ia_edu/rubrics/enem_2025.yaml`
- Create: `src/agente_ia_edu/rubrics/loader.py`
- Create: `tests/test_r1_rubric_file.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: nada.
- Produces: `load_rubric_file(name: str) -> RubricFile`, com `RubricFile` expondo `rubric_version`, `label`, `effective_year`, `official_source_title`, `official_source_url`, `official_source_sha256`, `competencies` (lista de `CompetencyEntry` com `code`, `ordinal`, `official_title`, `source_page`, `levels`, `signals`) e `zero_rules`.

- [ ] **Step 1: Declare PyYAML como dependência**

PyYAML chega hoje por via transitiva, através de `uvicorn[standard]`. Passamos a importá-lo diretamente, então ele tem de ser declarado. Em `pyproject.toml`, dentro de `[project].dependencies`:

```toml
	"PyYAML>=6.0,<7.0",
```

- [ ] **Step 2: Escreva o teste que falha**

```python
# tests/test_r1_rubric_file.py
import unittest

from agente_ia_edu.rubrics.loader import load_rubric_file

OFFICIAL_SHA256 = "d8ab44dcbf5af808829d9dee89d23e7efa4f59df022b99102fac87489b870288"


class TestEnem2025RubricFile(unittest.TestCase):
    def setUp(self):
        self.rubric = load_rubric_file("enem_2025")

    def test_declares_the_official_source_and_its_hash(self):
        self.assertEqual(self.rubric.rubric_version, "ENEM_2025")
        self.assertEqual(self.rubric.official_source_sha256, OFFICIAL_SHA256)
        self.assertTrue(self.rubric.official_source_url.startswith("https://download.inep.gov.br/"))

    def test_has_the_five_competencies_in_order(self):
        self.assertEqual(
            [c.code for c in self.rubric.competencies], ["C1", "C2", "C3", "C4", "C5"]
        )

    def test_has_exactly_six_levels_per_competency(self):
        for competency in self.rubric.competencies:
            with self.subTest(competency=competency.code):
                self.assertEqual(
                    sorted(level.points for level in competency.levels),
                    [0, 40, 80, 120, 160, 200],
                )

    def test_every_level_cites_its_source_page(self):
        for competency in self.rubric.competencies:
            for level in competency.levels:
                with self.subTest(competency=competency.code, points=level.points):
                    self.assertIsInstance(level.source_page, int)
                    self.assertGreater(level.source_page, 0)

    def test_no_descriptor_carries_an_undecoded_glyph(self):
        """The decoder marks what it could not read. A marker surviving into the
        reviewed file means the human review missed a cell."""
        for competency in self.rubric.competencies:
            for level in competency.levels:
                with self.subTest(competency=competency.code, points=level.points):
                    self.assertNotIn("⟨?⟩", level.descriptor)

    def test_every_signal_declares_provenance_and_backs_it_up(self):
        for competency in self.rubric.competencies:
            for signal in competency.signals:
                with self.subTest(signal=signal.key):
                    self.assertIn(
                        signal.provenance,
                        ("OFICIAL_INEP", "INTERPRETACAO_PEDAGOGICA", "HEURISTICA_MOTOR"),
                    )
                    if signal.provenance == "HEURISTICA_MOTOR":
                        self.assertTrue(signal.rationale)
                    else:
                        self.assertTrue(signal.source_ref)

    def test_declares_the_annulment_rules(self):
        keys = {rule.key for rule in self.rubric.zero_rules}
        self.assertIn("fuga_ao_tema", keys)

    def test_rejects_a_competency_missing_a_level(self):
        from agente_ia_edu.rubrics.loader import RubricFileError, parse_rubric_mapping

        broken = {
            "rubric_version": "BROKEN",
            "label": "broken",
            "competencies": [
                {
                    "code": "C1", "ordinal": 1, "official_title": "t", "source_page": 1,
                    "levels": [{"points": 200, "descriptor": "d", "source_page": 1}],
                    "signals": [],
                }
            ],
            "zero_rules": [],
        }
        with self.assertRaises(RubricFileError):
            parse_rubric_mapping(broken)
```

- [ ] **Step 3: Rode e confirme que falha**

Run: `.venv/bin/python -m pytest tests/test_r1_rubric_file.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.rubrics'`

- [ ] **Step 4: Implemente o carregador**

```python
# src/agente_ia_edu/rubrics/__init__.py
"""Versioned rubric source files. The normative text lives here, not in code."""
```

```python
# src/agente_ia_edu/rubrics/loader.py
"""Read and structurally validate a rubric source file.

The YAML is the reviewable artifact: it carries the literal cartilha wording and
the page each descriptor came from, and it is reviewed in a pull request against
the official PDF. This loader refuses a file that is structurally incomplete, so
a half-transcribed rubric can never reach the seed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_RUBRIC_DIR = Path(__file__).parent
OFFICIAL_LEVEL_POINTS = (0, 40, 80, 120, 160, 200)
COMPETENCY_CODES = ("C1", "C2", "C3", "C4", "C5")
PROVENANCES = ("OFICIAL_INEP", "INTERPRETACAO_PEDAGOGICA", "HEURISTICA_MOTOR")


class RubricFileError(ValueError):
    """The rubric file is structurally invalid and must not be seeded."""


@dataclass(frozen=True)
class LevelEntry:
    points: int
    descriptor: str
    source_page: int


@dataclass(frozen=True)
class SignalEntry:
    key: str
    label: str
    description: str | None
    provenance: str
    source_ref: str | None
    rationale: str | None


@dataclass(frozen=True)
class CompetencyEntry:
    code: str
    ordinal: int
    official_title: str
    source_page: int | None
    levels: tuple[LevelEntry, ...]
    signals: tuple[SignalEntry, ...]


@dataclass(frozen=True)
class ZeroRuleEntry:
    key: str
    label: str
    description: str | None
    effect: str
    competency_code: str | None
    source_page: int | None
    provenance: str


@dataclass(frozen=True)
class RubricFile:
    rubric_version: str
    label: str
    effective_year: int | None
    max_total_points: int
    official_source_title: str | None
    official_source_url: str | None
    official_source_sha256: str | None
    competencies: tuple[CompetencyEntry, ...]
    zero_rules: tuple[ZeroRuleEntry, ...]


def load_rubric_file(name: str) -> RubricFile:
    """Load ``<name>.yaml`` from this package."""
    path = _RUBRIC_DIR / f"{name}.yaml"
    if not path.exists():
        raise RubricFileError(f"Unknown rubric file {name!r} ({path})")
    return parse_rubric_mapping(yaml.safe_load(path.read_text(encoding="utf-8")))


def parse_rubric_mapping(raw: Any) -> RubricFile:
    if not isinstance(raw, dict):
        raise RubricFileError("Rubric file must be a mapping")

    competencies = tuple(
        _parse_competency(entry) for entry in raw.get("competencies", [])
    )
    codes = [c.code for c in competencies]
    if codes != list(COMPETENCY_CODES):
        raise RubricFileError(
            f"Rubric must declare {list(COMPETENCY_CODES)} in order; got {codes}"
        )

    return RubricFile(
        rubric_version=_required(raw, "rubric_version"),
        label=_required(raw, "label"),
        effective_year=raw.get("effective_year"),
        max_total_points=raw.get("max_total_points", 1000),
        official_source_title=raw.get("official_source_title"),
        official_source_url=raw.get("official_source_url"),
        official_source_sha256=raw.get("official_source_sha256"),
        competencies=competencies,
        zero_rules=tuple(_parse_zero_rule(entry) for entry in raw.get("zero_rules", [])),
    )


def _parse_competency(raw: Any) -> CompetencyEntry:
    if not isinstance(raw, dict):
        raise RubricFileError("Each competency must be a mapping")

    levels = tuple(_parse_level(entry) for entry in raw.get("levels", []))
    points = sorted(level.points for level in levels)
    if points != sorted(OFFICIAL_LEVEL_POINTS):
        raise RubricFileError(
            f"Competency {raw.get('code')!r} must declare levels "
            f"{sorted(OFFICIAL_LEVEL_POINTS)}; got {points}"
        )

    return CompetencyEntry(
        code=_required(raw, "code"),
        ordinal=_required(raw, "ordinal"),
        official_title=_required(raw, "official_title"),
        source_page=raw.get("source_page"),
        levels=levels,
        signals=tuple(_parse_signal(entry) for entry in raw.get("signals", [])),
    )


def _parse_level(raw: Any) -> LevelEntry:
    if not isinstance(raw, dict):
        raise RubricFileError("Each level must be a mapping")
    source_page = raw.get("source_page")
    if not isinstance(source_page, int) or source_page <= 0:
        raise RubricFileError(
            f"Level {raw.get('points')!r} must cite a positive source_page"
        )
    return LevelEntry(
        points=_required(raw, "points"),
        descriptor=_required(raw, "descriptor"),
        source_page=source_page,
    )


def _parse_signal(raw: Any) -> SignalEntry:
    if not isinstance(raw, dict):
        raise RubricFileError("Each signal must be a mapping")
    provenance = _required(raw, "provenance")
    if provenance not in PROVENANCES:
        raise RubricFileError(f"Unknown provenance {provenance!r}")
    source_ref = raw.get("source_ref")
    rationale = raw.get("rationale")
    if provenance == "HEURISTICA_MOTOR" and not rationale:
        raise RubricFileError(
            f"Signal {raw.get('key')!r} is an engine heuristic and must carry a rationale"
        )
    if provenance != "HEURISTICA_MOTOR" and not source_ref:
        raise RubricFileError(
            f"Signal {raw.get('key')!r} claims an external source and must cite it"
        )
    return SignalEntry(
        key=_required(raw, "key"),
        label=_required(raw, "label"),
        description=raw.get("description"),
        provenance=provenance,
        source_ref=source_ref,
        rationale=rationale,
    )


def _parse_zero_rule(raw: Any) -> ZeroRuleEntry:
    if not isinstance(raw, dict):
        raise RubricFileError("Each zero rule must be a mapping")
    effect = _required(raw, "effect")
    if effect not in ("ANULA_REDACAO", "ZERA_COMPETENCIA"):
        raise RubricFileError(f"Unknown zero-rule effect {effect!r}")
    return ZeroRuleEntry(
        key=_required(raw, "key"),
        label=_required(raw, "label"),
        description=raw.get("description"),
        effect=effect,
        competency_code=raw.get("competency_code"),
        source_page=raw.get("source_page"),
        provenance=raw.get("provenance", "OFICIAL_INEP"),
    )


def _required(raw: dict[str, Any], field: str) -> Any:
    if field not in raw or raw[field] in (None, ""):
        raise RubricFileError(f"Missing required field {field!r}")
    return raw[field]


__all__ = [
    "CompetencyEntry",
    "LevelEntry",
    "RubricFile",
    "RubricFileError",
    "SignalEntry",
    "ZeroRuleEntry",
    "load_rubric_file",
    "parse_rubric_mapping",
]
```

- [ ] **Step 5: Transcreva a régua e confira contra o PDF**

Use `/tmp/cartilha_draft.txt` da Task 3 e o PDF aberto lado a lado. **Confira as 30 células uma a uma.** Este é o passo que separa uma régua com procedência de uma paráfrase — não pule, não delegue ao decodificador.

Comece do esqueleto abaixo, que traz o cabeçalho e as três primeiras células de C1 já conferidas contra as páginas 14 e 16 do PDF:

```yaml
# src/agente_ia_edu/rubrics/enem_2025.yaml
#
# Texto normativo da Matriz de Referência do ENEM, transcrito da Cartilha do
# Participante 2025 do INEP. Cada descritor cita a página do PDF cujo hash está
# declarado abaixo. Conferido célula a célula contra o documento oficial.
#
# NÃO edite um descritor para "melhorar" a redação. Se o INEP mudar a matriz,
# crie um novo arquivo e uma nova rubric_version.

rubric_version: ENEM_2025
label: "Matriz de Referência ENEM - Cartilha do Participante 2025"
effective_year: 2025
max_total_points: 1000
official_source_title: "A Redação do Enem 2025 - Cartilha do(a) Participante"
official_source_url: "https://download.inep.gov.br/publicacoes/institucionais/avaliacoes_e_exames_da_educacao_basica/a_redacao_no_enem_2025_cartilha_do_participante.pdf"
official_source_sha256: "d8ab44dcbf5af808829d9dee89d23e7efa4f59df022b99102fac87489b870288"

competencies:
  - code: C1
    ordinal: 1
    official_title: "Demonstrar domínio da modalidade escrita formal da língua portuguesa."
    source_page: 14
    levels:
      - points: 200
        source_page: 16
        descriptor: >-
          Demonstra excelente domínio da modalidade escrita formal da língua
          portuguesa e de escolha de registro. Desvios gramaticais ou de
          convenções da escrita serão aceitos somente como excepcionalidade e
          quando não caracterizarem reincidência.
      - points: 160
        source_page: 16
        descriptor: >-
          Demonstra bom domínio da modalidade escrita formal da língua
          portuguesa e de escolha de registro, com poucos desvios gramaticais e
          de convenções da escrita.
      - points: 120
        source_page: 16
        descriptor: >-
          Demonstra domínio mediano da modalidade escrita formal da língua
          portuguesa e de escolha de registro, com alguns desvios gramaticais e
          de convenções da escrita.
      # 80, 40 e 0: transcreva da página 16. No draft da ferramenta estas três
      # células vêm codificadas - confira cada palavra contra o PDF.
    signals:
      - key: ortografia_acentuacao
        label: "Ortografia e acentuação"
        provenance: OFICIAL_INEP
        source_ref: "Cartilha do Participante 2025, p. 14-15"
      # demais sinais de C1 conforme spec v1.0 §4

  # C2, C3, C4 e C5 na mesma forma.

zero_rules:
  - key: fuga_ao_tema
    label: "Fuga ao tema"
    effect: ANULA_REDACAO
    competency_code: null
    source_page: 9
    provenance: OFICIAL_INEP
    description: >-
      Transcreva da página 9 do PDF a redação literal da regra.
  # demais situações de anulação conforme a cartilha
```

Os sinais de C2 ligados a repertório vêm da spec v1.0 §17 e devem distinguir
`repertorio_legitimidade`, `repertorio_pertinencia` e `repertorio_produtividade`.
O que vier da *Cartilha Redação a Mil 8.0 (2026)* entra como
`INTERPRETACAO_PEDAGOGICA` com `source_ref` citando documento e página — **nunca**
como `OFICIAL_INEP`.

- [ ] **Step 6: Rode e confirme que passa**

Run: `.venv/bin/python -m pytest tests/test_r1_rubric_file.py -v`
Expected: PASS, 8 testes. Se `test_has_exactly_six_levels_per_competency` falhar, a transcrição está incompleta — termine antes de seguir.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/agente_ia_edu/rubrics/ tests/test_r1_rubric_file.py
git commit -m "feat: régua ENEM 2025 transcrita da cartilha oficial

Texto normativo em arquivo revisável, com página de origem por descritor
e o hash do PDF do INEP. O carregador recusa régua estruturalmente
incompleta, para que uma transcrição pela metade nunca chegue ao seed."
```

---

### Task 5: Seed idempotente

**Files:**
- Create: `src/agente_ia_edu/services/essay_rubric_seed.py`
- Create: `tests/test_r1_essay_rubric_seed.py`

**Interfaces:**
- Consumes: `load_rubric_file` da Task 4; os modelos da Task 2.
- Produces: `EssayRubricSeeder(session).seed(rubric_file, *, activate: bool = True) -> EssayRubric`, e `load_rubric_view(session, rubric_version) -> RubricView` (definido na Task 7, consumido aqui apenas como contrato de leitura — implemente-o na Task 7).

- [ ] **Step 1: Escreva o teste que falha**

```python
# tests/test_r1_essay_rubric_seed.py
import unittest

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    EssayRubric,
    EssayRubricCompetency,
    EssayRubricLevel,
)
from agente_ia_edu.rubrics.loader import load_rubric_file
from agente_ia_edu.services.essay_rubric_seed import EssayRubricSeeder


class TestEssayRubricSeed(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.rubric_file = load_rubric_file("enem_2025")

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_seeds_five_competencies_and_thirty_levels(self):
        async with self.session_factory() as session:
            await EssayRubricSeeder(session).seed(self.rubric_file)
            await session.commit()

            competencies = await session.scalar(
                select(func.count()).select_from(EssayRubricCompetency)
            )
            levels = await session.scalar(
                select(func.count()).select_from(EssayRubricLevel)
            )
            self.assertEqual(competencies, 5)
            self.assertEqual(levels, 30)

    async def test_seeding_twice_does_not_duplicate(self):
        async with self.session_factory() as session:
            seeder = EssayRubricSeeder(session)
            await seeder.seed(self.rubric_file)
            await session.commit()
            await seeder.seed(self.rubric_file)
            await session.commit()

            rubrics = await session.scalar(select(func.count()).select_from(EssayRubric))
            levels = await session.scalar(
                select(func.count()).select_from(EssayRubricLevel)
            )
            self.assertEqual(rubrics, 1)
            self.assertEqual(levels, 30)

    async def test_records_the_official_source_hash(self):
        async with self.session_factory() as session:
            rubric = await EssayRubricSeeder(session).seed(self.rubric_file)
            await session.commit()
            self.assertEqual(
                rubric.official_source_sha256,
                "d8ab44dcbf5af808829d9dee89d23e7efa4f59df022b99102fac87489b870288",
            )
            self.assertEqual(rubric.status, "ACTIVE")

    async def test_a_superseded_rubric_stays_readable(self):
        async with self.session_factory() as session:
            seeder = EssayRubricSeeder(session)
            rubric = await seeder.seed(self.rubric_file)
            await session.commit()

            await seeder.supersede(rubric.rubric_version)
            await session.commit()

            stored = await session.scalar(
                select(EssayRubric).where(EssayRubric.rubric_version == "ENEM_2025")
            )
            self.assertEqual(stored.status, "SUPERSEDED")
            self.assertIsNotNone(stored.superseded_at)
            levels = await session.scalar(
                select(func.count()).select_from(EssayRubricLevel)
            )
            self.assertEqual(levels, 30)
```

- [ ] **Step 2: Rode e confirme que falha**

Run: `.venv/bin/python -m pytest tests/test_r1_essay_rubric_seed.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.services.essay_rubric_seed'`

- [ ] **Step 3: Implemente**

```python
# src/agente_ia_edu/services/essay_rubric_seed.py
"""Load a reviewed rubric file into the database, idempotently.

Idempotence matters because the seed runs on every environment and every fresh
database, and because a rubric that is already seeded must never be silently
rewritten - a correction persisted under ENEM_2025 has to keep meaning exactly
what it meant when it was produced (spec §19). Re-seeding an existing version is
therefore a no-op, not an update.

Superseding a rubric flips its status and stamps the time; it never deletes a
level, so historical corrections stay readable.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import (
    EssayRubric,
    EssayRubricCompetency,
    EssayRubricLevel,
    EssayRubricSignal,
    EssayRubricZeroRule,
)
from agente_ia_edu.rubrics.loader import RubricFile


class EssayRubricSeeder:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def seed(self, rubric_file: RubricFile, *, activate: bool = True) -> EssayRubric:
        """Insert ``rubric_file`` if its version is not present yet.

        Returns the stored rubric either way. An already-seeded version is
        returned untouched.
        """
        existing = await self.session.scalar(
            select(EssayRubric).where(
                EssayRubric.rubric_version == rubric_file.rubric_version
            )
        )
        if existing is not None:
            return existing

        rubric = EssayRubric(
            rubric_version=rubric_file.rubric_version,
            label=rubric_file.label,
            effective_year=rubric_file.effective_year,
            max_total_points=rubric_file.max_total_points,
            official_source_title=rubric_file.official_source_title,
            official_source_url=rubric_file.official_source_url,
            official_source_sha256=rubric_file.official_source_sha256,
            status="ACTIVE" if activate else "DRAFT",
            published_at=datetime.now(timezone.utc) if activate else None,
        )
        self.session.add(rubric)
        await self.session.flush()

        for entry in rubric_file.competencies:
            competency = EssayRubricCompetency(
                rubric_id=rubric.id,
                code=entry.code,
                ordinal=entry.ordinal,
                official_title=entry.official_title,
                source_page=entry.source_page,
            )
            self.session.add(competency)
            await self.session.flush()

            for level in entry.levels:
                self.session.add(
                    EssayRubricLevel(
                        competency_id=competency.id,
                        points=level.points,
                        descriptor=level.descriptor,
                        source_page=level.source_page,
                        provenance="OFICIAL_INEP",
                    )
                )

            for signal in entry.signals:
                self.session.add(
                    EssayRubricSignal(
                        competency_id=competency.id,
                        key=signal.key,
                        label=signal.label,
                        description=signal.description,
                        provenance=signal.provenance,
                        source_ref=signal.source_ref,
                        rationale=signal.rationale,
                        active=True,
                    )
                )

        for rule in rubric_file.zero_rules:
            self.session.add(
                EssayRubricZeroRule(
                    rubric_id=rubric.id,
                    key=rule.key,
                    label=rule.label,
                    description=rule.description,
                    effect=rule.effect,
                    competency_code=rule.competency_code,
                    source_page=rule.source_page,
                    provenance=rule.provenance,
                )
            )

        await self.session.flush()
        return rubric

    async def supersede(self, rubric_version: str) -> EssayRubric:
        """Mark a rubric superseded. Nothing is deleted."""
        rubric = await self.session.scalar(
            select(EssayRubric).where(EssayRubric.rubric_version == rubric_version)
        )
        if rubric is None:
            raise ValueError(f"Unknown rubric version {rubric_version!r}")
        rubric.status = "SUPERSEDED"
        rubric.superseded_at = datetime.now(timezone.utc)
        await self.session.flush()
        return rubric


__all__ = ["EssayRubricSeeder"]
```

- [ ] **Step 4: Rode e confirme que passa**

Run: `.venv/bin/python -m pytest tests/test_r1_essay_rubric_seed.py -v`
Expected: PASS, 4 testes

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/essay_rubric_seed.py tests/test_r1_essay_rubric_seed.py
git commit -m "feat: seed idempotente da régua

Re-semear uma versão existente é no-op, não update: uma correção feita
sob ENEM_2025 tem de continuar significando o que significava. Supersede
muda status e carimba a hora, sem apagar nível nenhum."
```

---

### Task 6: O contrato de saída (camada 1)

**Files:**
- Create: `src/agente_ia_edu/essay_engine_contract/__init__.py`
- Create: `src/agente_ia_edu/essay_engine_contract/v1.py`
- Create: `tests/test_r1_engine_contract_shape.py`

**Interfaces:**
- Consumes: nada.
- Produces: `CONTRACT_VERSION`, `EssayEngineOutput`, `Identification`, `Scores`, `CompetencyScore`, `Annotation`, `TextOffsetAnchor`, `ImageRegionAnchor`, `CompetencyRationale`, `Rewrite`, `Feedback`, `InterventionBreakdown`, `Alert`.

- [ ] **Step 1: Escreva o teste que falha**

```python
# tests/test_r1_engine_contract_shape.py
import unittest
import uuid

from pydantic import ValidationError

from agente_ia_edu.essay_engine_contract.v1 import CONTRACT_VERSION, EssayEngineOutput


def minimal_payload(**overrides):
    payload = {
        "identification": {
            "essay_id": str(uuid.uuid4()),
            "essay_version_id": str(uuid.uuid4()),
            "rubric_version": "ENEM_2025",
            "model_version": "fake-model-1",
            "prompt_version": "v1",
            "engine_version": "r1.0.0",
            "contract_version": CONTRACT_VERSION,
            "anchor_mode": "TEXT_OFFSET",
        },
        "scores": {
            "per_competency": {
                code: {"points": 160, "confidence": 0.8}
                for code in ("C1", "C2", "C3", "C4", "C5")
            },
            "total": 800,
        },
        "rationales": [
            {"competency_code": c, "summary": "resumo", "signal_keys": []}
            for c in ("C1", "C2", "C3", "C4", "C5")
        ],
        "annotations": [
            {
                "letter": "A",
                "competency_code": "C1",
                "kind": "MELHORIA",
                "evidence_kind": "LOCALIZED",
                "anchor": {"type": "TEXT_OFFSET", "start": 0, "end": 5, "quote": "Texto"},
                "short_comment": "curto",
                "long_comment": "longo",
            }
        ],
        "rewrites": [],
        "feedback": {"strengths": [], "improvements": [], "next_essay_strategy": "..."},
        "intervention": {
            "agente": "Ministério da Educação", "acao": "ampliar formação",
            "meio_modo": "por programa federal", "finalidade": "reduzir a evasão",
            "detalhamento": "com metas anuais", "respeita_direitos_humanos": True,
        },
        "alerts": [],
    }
    payload.update(overrides)
    return payload


class TestEngineContractShape(unittest.TestCase):
    def test_accepts_a_complete_valid_output(self):
        output = EssayEngineOutput.model_validate(minimal_payload())
        self.assertEqual(output.scores.total, 800)

    def test_accepts_an_output_with_no_scores_formative_mode(self):
        """Rejection case: none. In FORMATIVO the engine produces no score at all
        and the output must still be valid (spec §3)."""
        output = EssayEngineOutput.model_validate(minimal_payload(scores=None))
        self.assertIsNone(output.scores)

    def test_rejects_partial_scores(self):
        """Rejection 3: three competencies out of five."""
        partial = {
            "per_competency": {
                c: {"points": 160, "confidence": 0.8} for c in ("C1", "C2", "C3")
            },
            "total": 480,
        }
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(minimal_payload(scores=partial))

    def test_rejects_a_total_that_is_not_the_sum(self):
        """Rejection 2."""
        scores = {
            "per_competency": {
                c: {"points": 160, "confidence": 0.8}
                for c in ("C1", "C2", "C3", "C4", "C5")
            },
            "total": 999,
        }
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(minimal_payload(scores=scores))

    def test_rejects_an_unknown_contract_version(self):
        """Rejection 12."""
        payload = minimal_payload()
        payload["identification"]["contract_version"] = "essay_engine_output_v99"
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)

    def test_rejects_an_anchor_that_disagrees_with_anchor_mode(self):
        """Rejection 10: the output declares TEXT_OFFSET but anchors on an image."""
        payload = minimal_payload()
        payload["annotations"][0]["anchor"] = {
            "type": "IMAGE_REGION", "page": 1, "x": 10.0, "y": 10.0,
            "width": 50.0, "height": 20.0, "read_text": "Texto",
        }
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)

    def test_rejects_mixed_anchors_in_one_output(self):
        """Rejection 11."""
        payload = minimal_payload()
        payload["identification"]["anchor_mode"] = "IMAGE_REGION"
        payload["annotations"][0]["anchor"] = {
            "type": "IMAGE_REGION", "page": 1, "x": 10.0, "y": 10.0,
            "width": 50.0, "height": 20.0, "read_text": "Texto",
        }
        payload["annotations"].append({
            "letter": "B", "competency_code": "C2", "kind": "ACERTO",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 0, "end": 5, "quote": "Texto"},
            "short_comment": "curto", "long_comment": "longo",
        })
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)

    def test_rejects_an_offset_anchor_whose_end_precedes_start(self):
        payload = minimal_payload()
        payload["annotations"][0]["anchor"] = {
            "type": "TEXT_OFFSET", "start": 10, "end": 3, "quote": "x"
        }
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)
```

- [ ] **Step 2: Rode e confirme que falha**

Run: `.venv/bin/python -m pytest tests/test_r1_engine_contract_shape.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.essay_engine_contract'`

- [ ] **Step 3: Implemente**

```python
# src/agente_ia_edu/essay_engine_contract/__init__.py
"""Versioned engine-output contracts.

A correction persisted under ``essay_engine_output_v1`` must stay readable by the
schema it was born with, so a shape change is a NEW ``vN`` module, never an edit
to an existing one - the same rule ``classification_prompts`` follows.
"""
```

```python
# src/agente_ia_edu/essay_engine_contract/v1.py
"""Engine output contract, artifact version v1 (spec v1.0 §18).

Layer 1 of validation: shape. Everything checkable without the rubric and without
the essay text lives here - field types, the six-value score scale, the total
being the sum of five competencies, and anchor consistency. The rubric-aware and
text-aware checks are layers 2 and 3, in services/essay_engine_validation.py.

Scores are OPTIONAL on purpose. In an institution configured as FORMATIVO the
engine produces analysis and no grade at all, and that output is valid. What is
not valid is a partial score: three competencies out of five means the engine
failed, not that it was being careful.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "essay_engine_output_v1"
COMPETENCY_CODES: tuple[str, ...] = ("C1", "C2", "C3", "C4", "C5")
OFFICIAL_LEVEL_POINTS: tuple[int, ...] = (0, 40, 80, 120, 160, 200)

CompetencyCode = Literal["C1", "C2", "C3", "C4", "C5"]
AnchorMode = Literal["TEXT_OFFSET", "IMAGE_REGION"]

_Strict = ConfigDict(extra="forbid")


class TextOffsetAnchor(BaseModel):
    """Verifiable anchor: the quote can be checked against the canonical text."""

    model_config = _Strict

    type: Literal["TEXT_OFFSET"]
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def _end_follows_start(self) -> "TextOffsetAnchor":
        if self.end <= self.start:
            raise ValueError("TEXT_OFFSET anchor requires end > start")
        return self


class ImageRegionAnchor(BaseModel):
    """Weaker anchor: only the page bounds are verifiable. ``read_text`` is what
    the model says it read in that region and cannot be checked against anything."""

    model_config = _Strict

    type: Literal["IMAGE_REGION"]
    page: int = Field(ge=1)
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    read_text: str = Field(min_length=1)


Anchor = Annotated[
    Union[TextOffsetAnchor, ImageRegionAnchor], Field(discriminator="type")
]


class Identification(BaseModel):
    model_config = _Strict

    essay_id: UUID
    essay_version_id: UUID
    rubric_version: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    engine_version: str = Field(min_length=1)
    contract_version: Literal["essay_engine_output_v1"]
    anchor_mode: AnchorMode


class CompetencyScore(BaseModel):
    model_config = _Strict

    points: int
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _points_on_the_official_scale(self) -> "CompetencyScore":
        if self.points not in OFFICIAL_LEVEL_POINTS:
            raise ValueError(
                f"points must be one of {list(OFFICIAL_LEVEL_POINTS)}; got {self.points}"
            )
        return self


class Scores(BaseModel):
    model_config = _Strict

    per_competency: dict[str, CompetencyScore]
    total: int

    @model_validator(mode="after")
    def _all_five_and_total_is_the_sum(self) -> "Scores":
        if tuple(sorted(self.per_competency)) != COMPETENCY_CODES:
            raise ValueError(
                f"scores must cover exactly {list(COMPETENCY_CODES)}; "
                f"got {sorted(self.per_competency)}"
            )
        expected = sum(score.points for score in self.per_competency.values())
        if self.total != expected:
            raise ValueError(
                f"total must be the sum of the five competencies ({expected}); "
                f"got {self.total}"
            )
        return self


class CompetencyRationale(BaseModel):
    model_config = _Strict

    competency_code: CompetencyCode
    summary: str
    signal_keys: tuple[str, ...] = ()


class Annotation(BaseModel):
    model_config = _Strict

    letter: str = Field(pattern=r"^[A-Z]{1,2}$")
    competency_code: CompetencyCode
    kind: Literal["ACERTO", "ATENCAO", "MELHORIA"]
    evidence_kind: Literal["LOCALIZED", "GLOBAL"]
    anchor: Anchor
    short_comment: str = Field(min_length=1)
    long_comment: str = Field(min_length=1)
    pedagogical_suggestion: str | None = None
    signal_keys: tuple[str, ...] = ()


class Rewrite(BaseModel):
    model_config = _Strict

    original: str = Field(min_length=1)
    suggestion: str = Field(min_length=1)
    pedagogical_goal: str = Field(min_length=1)


class Feedback(BaseModel):
    model_config = _Strict

    strengths: tuple[str, ...] = ()
    improvements: tuple[str, ...] = ()
    next_essay_strategy: str = Field(min_length=1)


class InterventionBreakdown(BaseModel):
    """C5 decomposed into verifiable elements (spec §4)."""

    model_config = _Strict

    agente: str | None = None
    acao: str | None = None
    meio_modo: str | None = None
    finalidade: str | None = None
    detalhamento: str | None = None
    respeita_direitos_humanos: bool


class Alert(BaseModel):
    model_config = _Strict

    code: Literal[
        "FUGA_AO_TEMA",
        "TIPO_TEXTUAL",
        "TEXTO_INSUFICIENTE",
        "OCR_DUVIDOSO",
        "POSSIVEL_DUPLICIDADE",
    ]
    detail: str | None = None


class EssayEngineOutput(BaseModel):
    model_config = _Strict

    identification: Identification
    scores: Scores | None = None
    rationales: tuple[CompetencyRationale, ...]
    annotations: tuple[Annotation, ...]
    rewrites: tuple[Rewrite, ...] = ()
    feedback: Feedback
    intervention: InterventionBreakdown
    alerts: tuple[Alert, ...] = ()

    @model_validator(mode="after")
    def _anchors_agree_with_declared_mode(self) -> "EssayEngineOutput":
        declared = self.identification.anchor_mode
        for annotation in self.annotations:
            if annotation.anchor.type != declared:
                raise ValueError(
                    f"annotation {annotation.letter!r} anchors on "
                    f"{annotation.anchor.type} but the output declares {declared}"
                )
        return self


__all__ = [
    "Alert",
    "Annotation",
    "Anchor",
    "CONTRACT_VERSION",
    "COMPETENCY_CODES",
    "CompetencyRationale",
    "CompetencyScore",
    "EssayEngineOutput",
    "Feedback",
    "Identification",
    "ImageRegionAnchor",
    "InterventionBreakdown",
    "OFFICIAL_LEVEL_POINTS",
    "Rewrite",
    "Scores",
    "TextOffsetAnchor",
]
```

- [ ] **Step 4: Rode e confirme que passa**

Run: `.venv/bin/python -m pytest tests/test_r1_engine_contract_shape.py -v`
Expected: PASS, 8 testes

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/essay_engine_contract/ tests/test_r1_engine_contract_shape.py
git commit -m "feat: contrato de saída do motor de redação, versão v1

Pontuação é opcional: em modo FORMATIVO o motor produz análise sem nota e
isso é saída válida. Pontuação parcial não é - três competências de cinco
significa que o motor falhou, não que foi cauteloso."
```

---

### Task 7: Validação contra a régua e contra o texto (camadas 2 e 3)

**Files:**
- Create: `src/agente_ia_edu/services/essay_engine_validation.py`
- Create: `tests/test_r1_engine_validation.py`

**Interfaces:**
- Consumes: `EssayEngineOutput` da Task 6; os modelos da Task 2; `canonical_hash` da Task 1.
- Produces: `RubricView`, `load_rubric_view(session, rubric_version) -> RubricView`, `validate_engine_output(output, *, rubric, text=None, page_boxes=None, raw_output=None, input_hash=None) -> None`, `EssayEngineOutputRejected` com atributos `reason_code`, `raw_output`, `input_hash`.

- [ ] **Step 1: Escreva o teste que falha**

```python
# tests/test_r1_engine_validation.py
import unittest
import uuid

from agente_ia_edu.essay_engine_contract.v1 import CONTRACT_VERSION, EssayEngineOutput
from agente_ia_edu.services.essay_engine_validation import (
    EssayEngineOutputRejected,
    RubricView,
    validate_engine_output,
)

TEXT = "A valorização da cultura é um direito de todos os brasileiros."

RUBRIC = RubricView(
    rubric_version="ENEM_2025",
    levels={c: frozenset((0, 40, 80, 120, 160, 200)) for c in ("C1", "C2", "C3", "C4", "C5")},
    signal_keys=frozenset({"ortografia_acentuacao", "repertorio_pertinencia"}),
)


def build_output(**overrides) -> EssayEngineOutput:
    payload = {
        "identification": {
            "essay_id": str(uuid.uuid4()),
            "essay_version_id": str(uuid.uuid4()),
            "rubric_version": "ENEM_2025",
            "model_version": "fake-model-1",
            "prompt_version": "v1",
            "engine_version": "r1.0.0",
            "contract_version": CONTRACT_VERSION,
            "anchor_mode": "TEXT_OFFSET",
        },
        "scores": {
            "per_competency": {
                c: {"points": 160, "confidence": 0.8}
                for c in ("C1", "C2", "C3", "C4", "C5")
            },
            "total": 800,
        },
        "rationales": [
            {"competency_code": c, "summary": "resumo", "signal_keys": []}
            for c in ("C1", "C2", "C3", "C4", "C5")
        ],
        "annotations": [
            {
                "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
                "evidence_kind": "LOCALIZED",
                "anchor": {
                    "type": "TEXT_OFFSET", "start": 2, "end": 13, "quote": "valorização"
                },
                "short_comment": "curto", "long_comment": "longo",
                "signal_keys": ["ortografia_acentuacao"],
            }
        ],
        "rewrites": [],
        "feedback": {"strengths": [], "improvements": [], "next_essay_strategy": "..."},
        "intervention": {"respeita_direitos_humanos": True},
        "alerts": [],
    }
    for key, value in overrides.items():
        payload[key] = value
    return EssayEngineOutput.model_validate(payload)


class TestEngineValidation(unittest.TestCase):
    def test_accepts_a_verifiable_output(self):
        validate_engine_output(build_output(), rubric=RUBRIC, text=TEXT)

    def _assert_rejected(self, output, *, reason_code, **kwargs):
        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output(output, rubric=RUBRIC, text=TEXT, **kwargs)
        self.assertEqual(caught.exception.reason_code, reason_code)

    def test_rejects_a_score_not_in_the_rubric_levels(self):
        """Rejection 1: C3 only allows 0/40/80 in this rubric."""
        rubric = RubricView(
            rubric_version="ENEM_2025",
            levels={
                **{c: frozenset((0, 40, 80, 120, 160, 200)) for c in ("C1", "C2", "C4", "C5")},
                "C3": frozenset((0, 40, 80)),
            },
            signal_keys=RUBRIC.signal_keys,
        )
        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output(build_output(), rubric=rubric, text=TEXT)
        self.assertEqual(caught.exception.reason_code, "SCORE_NOT_IN_RUBRIC_LEVELS")

    def test_rejects_a_rubric_version_mismatch(self):
        rubric = RubricView(
            rubric_version="ENEM_2024", levels=RUBRIC.levels, signal_keys=RUBRIC.signal_keys
        )
        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output(build_output(), rubric=rubric, text=TEXT)
        self.assertEqual(caught.exception.reason_code, "RUBRIC_VERSION_MISMATCH")

    def test_rejects_an_annotation_on_a_competency_absent_from_the_rubric(self):
        """Rejection 6."""
        rubric = RubricView(
            rubric_version="ENEM_2025",
            levels={c: frozenset((0, 40, 80, 120, 160, 200)) for c in ("C2", "C3", "C4", "C5")},
            signal_keys=RUBRIC.signal_keys,
        )
        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output(build_output(scores=None), rubric=rubric, text=TEXT)
        self.assertEqual(caught.exception.reason_code, "UNKNOWN_COMPETENCY")

    def test_rejects_an_unknown_signal_key(self):
        """Rejection 7."""
        output = build_output(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 2, "end": 13, "quote": "valorização"},
            "short_comment": "curto", "long_comment": "longo",
            "signal_keys": ["sinal_inventado"],
        }])
        self._assert_rejected(output, reason_code="UNKNOWN_SIGNAL_KEY")

    def test_rejects_a_quote_that_does_not_match_the_text(self):
        """Rejection 4: the anti-hallucination guard."""
        output = build_output(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 2, "end": 13, "quote": "desvalorização"},
            "short_comment": "curto", "long_comment": "longo",
        }])
        self._assert_rejected(output, reason_code="QUOTE_DOES_NOT_MATCH_TEXT")

    def test_rejects_an_offset_beyond_the_text(self):
        """Rejection 5."""
        output = build_output(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 5000, "end": 5010, "quote": "x"},
            "short_comment": "curto", "long_comment": "longo",
        }])
        self._assert_rejected(output, reason_code="OFFSET_OUT_OF_BOUNDS")

    def test_rejects_a_specific_critique_with_no_resolvable_evidence(self):
        """Rejection 8: spec §4's pedagogical safety rule, as a rejection condition.

        A MELHORIA annotation declaring itself LOCALIZED must point somewhere; if
        it has nothing to point at, it must declare itself GLOBAL instead."""
        output = build_output(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 0, "end": 1, "quote": "A"},
            "short_comment": "curto", "long_comment": "longo",
        }])
        # This one is well-formed; the rejection comes from the empty-evidence case.
        validate_engine_output(output, rubric=RUBRIC, text=TEXT)

        # TEXT[1:2] is the space after "A", so the quote MATCHES the text and the
        # rejection can only come from the evidence rule - not from layer 3's
        # quote check, which would otherwise fire first and mask it.
        self.assertEqual(TEXT[1:2], " ")
        blank = build_output(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 1, "end": 2, "quote": " "},
            "short_comment": "curto", "long_comment": "longo",
        }])
        self._assert_rejected(blank, reason_code="SPECIFIC_CRITIQUE_WITHOUT_EVIDENCE")

    def test_rejects_a_region_outside_the_page(self):
        """Rejection 9."""
        output = build_output(
            identification={
                "essay_id": str(uuid.uuid4()), "essay_version_id": str(uuid.uuid4()),
                "rubric_version": "ENEM_2025", "model_version": "fake-model-1",
                "prompt_version": "v1", "engine_version": "r1.0.0",
                "contract_version": CONTRACT_VERSION, "anchor_mode": "IMAGE_REGION",
            },
            annotations=[{
                "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
                "evidence_kind": "LOCALIZED",
                "anchor": {
                    "type": "IMAGE_REGION", "page": 1, "x": 500.0, "y": 10.0,
                    "width": 400.0, "height": 20.0, "read_text": "Texto",
                },
                "short_comment": "curto", "long_comment": "longo",
            }],
        )
        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output(
                output, rubric=RUBRIC, text=None, page_boxes={1: (600.0, 800.0)}
            )
        self.assertEqual(caught.exception.reason_code, "REGION_OUT_OF_PAGE")

    def test_the_rejection_carries_the_raw_output_and_input_hash(self):
        raw = {"whatever": "the model returned"}
        output = build_output(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 2, "end": 13, "quote": "errado"},
            "short_comment": "curto", "long_comment": "longo",
        }])
        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output(
                output, rubric=RUBRIC, text=TEXT, raw_output=raw, input_hash="abc123"
            )
        self.assertEqual(caught.exception.raw_output, raw)
        self.assertEqual(caught.exception.input_hash, "abc123")
```

- [ ] **Step 2: Rode e confirme que falha**

Run: `.venv/bin/python -m pytest tests/test_r1_engine_validation.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.services.essay_engine_validation'`

- [ ] **Step 3: Implemente**

```python
# src/agente_ia_edu/services/essay_engine_validation.py
"""Layers 2 and 3 of engine-output validation (spec v1.0 §7, §18).

Layer 2 checks the output against the rubric it claims to have used. Layer 3
checks that every specific claim points at something real.

Layer 3 is the reason this module exists. Spec §4 states a "regra de segurança
pedagógica": the engine must not invent an error to justify a score. As prose,
that is a paragraph in a PDF. Here it is a rejection condition - an annotation
that criticises a specific passage must resolve to that passage, or declare
itself a global judgement of the competency.

``RubricView`` is a plain frozen snapshot rather than an ORM object so the
validator stays pure: no session, no I/O, no async. ``load_rubric_view`` is the
only part that touches the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import (
    EssayRubric,
    EssayRubricCompetency,
    EssayRubricLevel,
    EssayRubricSignal,
)
from agente_ia_edu.essay_engine_contract.v1 import EssayEngineOutput


class EssayEngineOutputRejected(ValueError):
    """The output did not survive validation and must not be published.

    Carries what a reprocessing run needs: why it failed, what the model actually
    returned, and the hash of the input that produced it.
    """

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        raw_output: Any = None,
        input_hash: str | None = None,
    ) -> None:
        super().__init__(f"{reason_code}: {message}")
        self.reason_code = reason_code
        self.raw_output = raw_output
        self.input_hash = input_hash


@dataclass(frozen=True)
class RubricView:
    """Read-only snapshot of the parts of a rubric the validator needs."""

    rubric_version: str
    levels: Mapping[str, frozenset[int]]
    signal_keys: frozenset[str]


async def load_rubric_view(session: AsyncSession, rubric_version: str) -> RubricView:
    rubric = await session.scalar(
        select(EssayRubric).where(EssayRubric.rubric_version == rubric_version)
    )
    if rubric is None:
        raise ValueError(f"Unknown rubric version {rubric_version!r}")

    rows = (
        await session.execute(
            select(EssayRubricCompetency.code, EssayRubricLevel.points)
            .join(
                EssayRubricLevel,
                EssayRubricLevel.competency_id == EssayRubricCompetency.id,
            )
            .where(EssayRubricCompetency.rubric_id == rubric.id)
        )
    ).all()

    levels: dict[str, set[int]] = {}
    for code, points in rows:
        levels.setdefault(code, set()).add(points)

    signal_rows = (
        await session.execute(
            select(EssayRubricSignal.key)
            .join(
                EssayRubricCompetency,
                EssayRubricCompetency.id == EssayRubricSignal.competency_id,
            )
            .where(
                EssayRubricCompetency.rubric_id == rubric.id,
                EssayRubricSignal.active.is_(True),
            )
        )
    ).all()

    return RubricView(
        rubric_version=rubric.rubric_version,
        levels={code: frozenset(points) for code, points in levels.items()},
        signal_keys=frozenset(key for (key,) in signal_rows),
    )


def validate_engine_output(
    output: EssayEngineOutput,
    *,
    rubric: RubricView,
    text: str | None = None,
    page_boxes: Mapping[int, tuple[float, float]] | None = None,
    raw_output: Any = None,
    input_hash: str | None = None,
) -> None:
    """Raise :class:`EssayEngineOutputRejected` unless the output is publishable.

    ``text`` is required for TEXT_OFFSET outputs; ``page_boxes`` maps page number
    to ``(width, height)`` and is required for IMAGE_REGION outputs.
    """

    def reject(reason_code: str, message: str) -> None:
        raise EssayEngineOutputRejected(
            reason_code, message, raw_output=raw_output, input_hash=input_hash
        )

    # --- Layer 2: coherence with the rubric -------------------------------
    if output.identification.rubric_version != rubric.rubric_version:
        reject(
            "RUBRIC_VERSION_MISMATCH",
            f"output claims {output.identification.rubric_version!r} but was "
            f"validated against {rubric.rubric_version!r}",
        )

    if output.scores is not None:
        for code, score in output.scores.per_competency.items():
            allowed = rubric.levels.get(code)
            if allowed is None:
                reject("UNKNOWN_COMPETENCY", f"rubric has no competency {code!r}")
            if score.points not in allowed:
                reject(
                    "SCORE_NOT_IN_RUBRIC_LEVELS",
                    f"{code} scored {score.points}, which is not a level of "
                    f"{rubric.rubric_version}",
                )

    for rationale in output.rationales:
        if rationale.competency_code not in rubric.levels:
            reject(
                "UNKNOWN_COMPETENCY",
                f"rubric has no competency {rationale.competency_code!r}",
            )
        for key in rationale.signal_keys:
            if key not in rubric.signal_keys:
                reject("UNKNOWN_SIGNAL_KEY", f"unknown or inactive signal {key!r}")

    for annotation in output.annotations:
        if annotation.competency_code not in rubric.levels:
            reject(
                "UNKNOWN_COMPETENCY",
                f"rubric has no competency {annotation.competency_code!r}",
            )
        for key in annotation.signal_keys:
            if key not in rubric.signal_keys:
                reject("UNKNOWN_SIGNAL_KEY", f"unknown or inactive signal {key!r}")

    # --- Layer 3: anchoring ------------------------------------------------
    mode = output.identification.anchor_mode

    if mode == "TEXT_OFFSET":
        if text is None:
            reject(
                "MISSING_CANONICAL_TEXT",
                "a TEXT_OFFSET output can only be validated against its text",
            )
        for annotation in output.annotations:
            anchor = annotation.anchor
            if anchor.end > len(text):
                reject(
                    "OFFSET_OUT_OF_BOUNDS",
                    f"annotation {annotation.letter!r} ends at {anchor.end} but the "
                    f"text has {len(text)} characters",
                )
            if text[anchor.start : anchor.end] != anchor.quote:
                reject(
                    "QUOTE_DOES_NOT_MATCH_TEXT",
                    f"annotation {annotation.letter!r} quotes {anchor.quote!r} but "
                    f"the text reads {text[anchor.start : anchor.end]!r}",
                )
            _require_evidence(annotation, anchor.quote, reject)
    else:
        if page_boxes is None:
            reject(
                "MISSING_PAGE_BOXES",
                "an IMAGE_REGION output can only be validated against page sizes",
            )
        for annotation in output.annotations:
            anchor = annotation.anchor
            box = page_boxes.get(anchor.page)
            if box is None:
                reject(
                    "REGION_OUT_OF_PAGE",
                    f"annotation {annotation.letter!r} anchors on page "
                    f"{anchor.page}, which does not exist",
                )
            width, height = box
            if anchor.x + anchor.width > width or anchor.y + anchor.height > height:
                reject(
                    "REGION_OUT_OF_PAGE",
                    f"annotation {annotation.letter!r} anchors outside page "
                    f"{anchor.page} ({width}x{height})",
                )
            _require_evidence(annotation, anchor.read_text, reject)


def _require_evidence(annotation, evidence: str, reject) -> None:
    """Spec §4: a specific critique must point at something, or say it is global."""
    if annotation.evidence_kind == "GLOBAL":
        return
    if not evidence.strip():
        reject(
            "SPECIFIC_CRITIQUE_WITHOUT_EVIDENCE",
            f"annotation {annotation.letter!r} claims localized evidence but "
            f"anchors on blank text; it must declare evidence_kind=GLOBAL instead",
        )


__all__ = [
    "EssayEngineOutputRejected",
    "RubricView",
    "load_rubric_view",
    "validate_engine_output",
]
```

- [ ] **Step 4: Rode e confirme que passa**

Run: `.venv/bin/python -m pytest tests/test_r1_engine_validation.py -v`
Expected: PASS, 10 testes

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/essay_engine_validation.py tests/test_r1_engine_validation.py
git commit -m "feat: validação da saída do motor contra a régua e contra o texto

A camada 3 é a razão do módulo existir: a regra de segurança pedagógica
da §4 vira condição de rejeição. Uma crítica específica ou aponta para um
trecho real, ou se declara avaliação global da competência."
```

---

### Task 8: Chave de repetibilidade e versionamento de prompt

**Files:**
- Create: `src/agente_ia_edu/services/essay_correction_key.py`
- Create: `src/agente_ia_edu/essay_prompts/__init__.py`
- Create: `tests/test_r1_correction_key.py`
- Create: `tests/test_r1_essay_prompts.py`

**Interfaces:**
- Consumes: `canonical_hash` da Task 1.
- Produces: `normalize_essay_text(text) -> str`, `essay_text_hash(text) -> str`, `correction_key(...) -> str`; `get_essay_prompt(version)`, `available_versions()`.

- [ ] **Step 1: Escreva os testes que falham**

```python
# tests/test_r1_correction_key.py
import unittest

from agente_ia_edu.services.essay_correction_key import (
    correction_key,
    essay_text_hash,
    normalize_essay_text,
)

BASE = dict(
    normalized_text_hash="a" * 64,
    essay_prompt_id="11111111-1111-1111-1111-111111111111",
    rubric_version="ENEM_2025",
    model_version="fake-model-1",
    prompt_version="v1",
    engine_version="r1.0.0",
)


class TestCorrectionKey(unittest.TestCase):
    def test_is_stable_for_the_same_input(self):
        self.assertEqual(correction_key(**BASE), correction_key(**BASE))

    def test_every_version_field_changes_the_key(self):
        baseline = correction_key(**BASE)
        for field in (
            "rubric_version", "model_version", "prompt_version", "engine_version",
            "essay_prompt_id", "normalized_text_hash",
        ):
            with self.subTest(field=field):
                changed = dict(BASE)
                changed[field] = changed[field] + "-x"
                self.assertNotEqual(correction_key(**changed), baseline)

    def test_normalisation_collapses_line_endings_but_not_words(self):
        self.assertEqual(
            normalize_essay_text("Primeira linha\r\nSegunda linha"),
            "Primeira linha\nSegunda linha",
        )

    def test_normalisation_is_unicode_stable(self):
        import unicodedata

        composed = unicodedata.normalize("NFC", "valorização")
        decomposed = unicodedata.normalize("NFD", "valorização")
        self.assertNotEqual(composed, decomposed)  # different bytes on the wire
        self.assertEqual(essay_text_hash(composed), essay_text_hash(decomposed))

    def test_normalisation_is_idempotent(self):
        once = normalize_essay_text("  Texto  \r\n\r\n  com espaços \n")
        self.assertEqual(normalize_essay_text(once), once)
```

```python
# tests/test_r1_essay_prompts.py
import unittest
from unittest import mock

from agente_ia_edu import essay_prompts


class _StubArtifact:
    VERSION = "v1"
    RESPONSE_SCHEMA = {"scores": "object"}

    @staticmethod
    def build_prompt(**kwargs) -> str:
        return "prompt text"


class TestEssayPromptRegistry(unittest.TestCase):
    def test_an_unknown_version_fails_loudly(self):
        """No silent default: R3 registers the real artifact, and until then
        asking for one is an error, not a fallback."""
        with self.assertRaises(ValueError):
            essay_prompts.get_essay_prompt("v99")

    def test_resolves_a_registered_artifact(self):
        with mock.patch.dict(
            essay_prompts._ARTIFACTS, {"v1": _StubArtifact}, clear=True
        ):
            prompt = essay_prompts.get_essay_prompt("v1")
            self.assertEqual(prompt.version, "v1")
            self.assertEqual(prompt.response_schema, {"scores": "object"})
            self.assertEqual(prompt.build(), "prompt text")

    def test_available_versions_reflects_the_registry(self):
        with mock.patch.dict(
            essay_prompts._ARTIFACTS, {"v1": _StubArtifact}, clear=True
        ):
            self.assertEqual(essay_prompts.available_versions(), ("v1",))
```

- [ ] **Step 2: Rode e confirme que falham**

Run: `.venv/bin/python -m pytest tests/test_r1_correction_key.py tests/test_r1_essay_prompts.py -v`
Expected: FAIL, dois `ModuleNotFoundError`

- [ ] **Step 3: Implemente a chave**

```python
# src/agente_ia_edu/services/essay_correction_key.py
"""Text normalisation and the repeatability key (spec v1.0 §19).

Offsets always index the NORMALISED text. Normalisation therefore happens once,
when the essay version is stored (R2), and the normalised string is the canonical
text the engine reads and the annotations anchor into. Normalising again later,
after offsets exist, would silently move every anchor - so this function is
idempotent and R2 applies it exactly once.

What normalisation does and does not do: it unifies line endings, applies Unicode
NFC so that "ção" typed two different ways hashes the same, strips trailing
whitespace per line, and trims the edges. It does NOT collapse internal runs of
spaces or touch punctuation, because both would change what the student wrote.
"""

from __future__ import annotations

import unicodedata

from agente_ia_edu.services.canonical_hash import canonical_hash


def normalize_essay_text(text: str) -> str:
    """Canonical form of an essay text. Idempotent."""
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    composed = unicodedata.normalize("NFC", unified)
    lines = [line.rstrip() for line in composed.split("\n")]
    return "\n".join(lines).strip()


def essay_text_hash(text: str) -> str:
    """SHA-256 of the normalised essay text."""
    return canonical_hash(normalize_essay_text(text))


def correction_key(
    *,
    normalized_text_hash: str,
    essay_prompt_id: str,
    rubric_version: str,
    model_version: str,
    prompt_version: str,
    engine_version: str,
) -> str:
    """Identity of a correction under a fixed set of versions.

    ``essay_prompt_id`` is the PROPOSAL the student answered (EssayPrompt);
    ``prompt_version`` is the version of the prompt sent to the model. The spec
    uses "proposta" and "prompt" interchangeably in §19; these two names do not.

    Same text, same proposal and same four versions must produce the same key, so
    R3 can recognise a correction it has already made instead of paying for it
    twice - and so a historical correction is never silently recomputed.
    """
    return canonical_hash(
        {
            "normalized_text_hash": normalized_text_hash,
            "essay_prompt_id": essay_prompt_id,
            "rubric_version": rubric_version,
            "model_version": model_version,
            "prompt_version": prompt_version,
            "engine_version": engine_version,
        }
    )


__all__ = ["correction_key", "essay_text_hash", "normalize_essay_text"]
```

- [ ] **Step 4: Implemente o registro de prompts**

```python
# src/agente_ia_edu/essay_prompts/__init__.py
"""System-owned, versioned essay-correction prompt artifacts.

Mirrors ``classification_prompts``: the engine asks for a prompt by version and
never embeds prompt text; each artifact is provider-independent and immutable, so
a wording change is a new ``vN.py`` module plus a registry entry, never an edit.

The registry is EMPTY in R1 by design. R1 owns the versioning mechanism; the
correction prompt itself is written in R3, alongside the engine that uses it.
Asking for a version that is not registered raises - there is deliberately no
silent default, because a correction whose prompt cannot be identified cannot be
audited.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

# artifact version id -> module exposing VERSION, RESPONSE_SCHEMA, build_prompt(...)
_ARTIFACTS: dict[str, Any] = {}


@dataclass(frozen=True)
class EssayPrompt:
    """A resolved, provider-independent essay-correction prompt artifact."""

    version: str
    response_schema: Mapping[str, Any]
    _build: Callable[..., str]

    def build(self, **kwargs: Any) -> str:
        return self._build(**kwargs)


def available_versions() -> tuple[str, ...]:
    return tuple(sorted(_ARTIFACTS))


def get_essay_prompt(version: str) -> EssayPrompt:
    """Return the artifact for ``version``, or raise :class:`ValueError`."""
    artifact = _ARTIFACTS.get(version)
    if artifact is None:
        raise ValueError(
            f"Unknown essay prompt version {version!r}; "
            f"available: {list(available_versions())}"
        )
    return EssayPrompt(
        version=artifact.VERSION,
        response_schema=artifact.RESPONSE_SCHEMA,
        _build=artifact.build_prompt,
    )


__all__ = ["EssayPrompt", "available_versions", "get_essay_prompt"]
```

- [ ] **Step 5: Rode e confirme que passam**

Run: `.venv/bin/python -m pytest tests/test_r1_correction_key.py tests/test_r1_essay_prompts.py -v`
Expected: PASS, 8 testes

- [ ] **Step 6: Rode a suíte inteira de R1**

Run: `.venv/bin/python -m pytest tests/test_r1_*.py -v`
Expected: PASS, todos. Nenhum teste chama provedor de IA.

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/services/essay_correction_key.py src/agente_ia_edu/essay_prompts/ tests/test_r1_correction_key.py tests/test_r1_essay_prompts.py
git commit -m "feat: chave de repetibilidade e versionamento de prompt de redação

Offsets indexam o texto normalizado, então a normalização acontece uma
vez em R2 e é idempotente - normalizar de novo depois moveria toda
marcação em silêncio. O registro de prompts nasce vazio: R3 escreve o
prompt, e pedir versão não registrada falha em vez de cair em default."
```

---

## Verificação final

- [ ] `.venv/bin/python -m pytest tests/ -q` — a suíte inteira do projeto passa, não só a de R1
- [ ] `.venv/bin/python -m alembic upgrade head && .venv/bin/python -m alembic downgrade -1 && .venv/bin/python -m alembic upgrade head`
- [ ] `git diff --stat 038_authorial_classification..HEAD -- src/agente_ia_edu/db/models/` mostra apenas o arquivo novo e as linhas de export em `__init__.py`

## Critérios de aceite do spec, e onde cada um é verificado

| Critério (spec §11) | Verificado em |
|---|---|
| Régua 2025 com 5 competências, 30 níveis e regras de anulação, com página conferida | Task 4, `test_r1_rubric_file.py` |
| `essay_rubrics` registra título, URL e SHA-256 | Task 4 e 5 |
| Nenhum sinal sem procedência; constraints impedem | Task 2, `test_r1_essay_rubric_models.py` |
| Output válido passa; cada inválido rejeita com motivo próprio | Tasks 6 e 7 |
| Output sem `scores` é válido | Task 6 |
| Output com `scores` parcial é rejeitado | Task 6 |
| `correction_key` estável e sensível às quatro versões | Task 8 |
| Canonicalização compartilhada, não copiada | Task 1 |
| A suíte roda sem provedor de IA | Task 8, step 6 |
| Migration 039 aditiva e reversível | Task 2, step 7 |
| `essay_prompts` falha em versão desconhecida | Task 8 |
