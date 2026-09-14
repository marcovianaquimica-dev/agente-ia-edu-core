# R0 Fase 3A — Resolução de `external_id` para entidade: Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar o serviço que traduz `scope_external_id` numa entidade real da hierarquia acadêmica — testado sozinho, sem nenhum consumidor.

**Architecture:** Um serviço novo, `ExternalIdResolver`, com um método de resolução que devolve um resultado de **três estados**, não dois. A distinção entre "este escopo não aponta para entidade nenhuma, por desenho" e "este escopo aponta e não achei" é o que permite, no fim da transição, transformar o segundo em erro sem transformar o primeiro.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x async, `unittest.IsolatedAsyncioTestCase` com `sqlite+aiosqlite:///:memory:` e `StaticPool`.

**Spec:** `docs/superpowers/specs/2026-09-13-r0-estrutura-academica-configuracao-design.md` — §3.2, §7 e §9.

## Global Constraints

- Python `>=3.13,<3.14`. Nenhuma dependência nova.
- **Esta fase não cria migration.** As quatro entidades e suas colunas `external_id` já existem. Se alguma tarefa parecer exigir migration, pare e reporte.
- **Esta fase não tem consumidor.** Nada em `src/` passa a chamar o serviço. Ligar consumidores é a Fase 3B.
- **Nenhuma coluna de credencial em lugar nenhum** — nada de `password`, `token`, `secret`, `credential`, `senha`.
- Testes com `unittest.IsolatedAsyncioTestCase`, `sqlite+aiosqlite:///:memory:` e `StaticPool`.
- `tests/conftest.py` monta `DATABASE_URL` a partir do `.env` quando ela não está exportada — saiba qual banco você está atingindo.
- **A suíte completa derruba o banco que `DATABASE_URL` nomeia.** Aponte só para banco descartável. Nunca para `agente_ia_edu`, que é o de desenvolvimento e é compartilhado.
- Código e comentários em inglês; mensagem de commit em português, como o `git log`.
- Gate desta fase: `.venv/bin/python -m pytest tests/test_r0_*.py tests/test_platform_administration.py -q`.

## O que o passo 7 da spec exige, e por que ele molda o desenho

A §7 define o fim da transição:

> **Durante a transição**, um `external_id` que não resolve devolve `None` e o chamador segue pelo caminho antigo. A mudança desse regime é o passo 7, não um efeito colateral de nenhum passo anterior.

> 7. **Fim da transição:** `external_id` que não resolve passa a ser erro em vez de `None`.

Um resultado de dois estados — entidade ou `None` — torna o passo 7 impossível de fazer com segurança. `scope_type = 'PLATFORM'` **nunca** resolve para entidade da hierarquia, e isso é correto, não é dado faltando. Se os dois casos devolverem `None`, o passo 7 vai transformar em erro tanto a ausência real quanto o escopo que legitimamente não tem entidade — e derrubar todo administrador de plataforma.

Por isso o resultado tem três estados desde já. O custo é uma classe pequena hoje; o benefício é que o passo 7 vira uma mudança de uma linha em vez de uma arqueologia.

## Fatos verificados por execução antes de escrever este plano

Não deduza nada disto; já foi conferido rodando:

- As quatro entidades da hierarquia têm `external_id` **e** `school_id`: `SchoolUnit` (`school_units`), `Segment` (`segments`), `GradeLevel` (`grade_levels`), `Class` (`classes`).
- Todas as quatro têm `uq_<tabela>_school_external_id`, ou seja **`(school_id, external_id)` é único**. É isso que torna a resolução não ambígua, e foi decisão deliberada da Fase 1.
- `UserSchoolLink.scope_type` tem CHECK com exatamente `'PLATFORM', 'SCHOOL', 'UNIT', 'SEGMENT', 'GRADE_LEVEL', 'CLASSROOM'` (`db/models/admin.py:161`).
- As colunas-ponte de `user_school_links` são `user_id`, `school_unit_id`, `segment_id`, `grade_level_id`, `class_id`, todas anuláveis (`admin.py:194-198`).

---

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/agente_ia_edu/services/external_id_resolution.py` (**criar**) | O serviço e seu tipo de resultado. Uma responsabilidade: traduzir escopo + código externo numa entidade, dizendo em qual dos três estados caiu. |
| `tests/test_r0_external_id_resolution.py` (**criar**) | Os três estados, o isolamento por escola, e os quatro tipos de escopo. |
| `tests/test_r0_fase3a_gate.py` (**criar**) | O gate: o serviço existe, resolve, e **não tem consumidor nenhum**. |

---

### Task 1: O resolvedor e seus três estados

**Files:**
- Create: `src/agente_ia_edu/services/external_id_resolution.py`
- Test: `tests/test_r0_external_id_resolution.py`

**Interfaces:**
- Consumes: `SchoolUnit`, `Segment`, `GradeLevel`, `Class` de `agente_ia_edu.db.models.academic`.
- Produces:
  - `ResolutionState` — enum com `RESOLVED`, `NOT_FOUND`, `NOT_APPLICABLE`
  - `ScopeResolution` — dataclass congelado com `state: ResolutionState` e `entity: object | None`
  - `ScopeResolution.entity_id -> uuid.UUID | None`
  - `ExternalIdResolver(session: AsyncSession)`
  - `ExternalIdResolver.resolve(school_id, scope_type, external_id) -> ScopeResolution`

- [ ] **Step 1: Escreva os testes que falham**

Crie `tests/test_r0_external_id_resolution.py`:

```python
# tests/test_r0_external_id_resolution.py
"""Translating a scope's external id into a real entity.

Three states, not two. "This scope never points at a hierarchy entity" is not
the same as "it should and I could not find it" - and spec section 7 turns the
second into an error while the first must stay legal, or every platform
administrator stops working on the day that lands.
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.academic import AcademicYear, Class, GradeLevel, Segment, SchoolUnit
from agente_ia_edu.services.external_id_resolution import (
    ExternalIdResolver,
    ResolutionState,
)

SCHOOL_A = uuid.uuid4()
SCHOOL_B = uuid.uuid4()


class ExternalIdResolutionTests(unittest.IsolatedAsyncioTestCase):
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

    async def _seed(self, session, school_id, suffix=""):
        """One row of each hierarchy level, external ids suffixed per school."""
        unit = SchoolUnit(
            id=uuid.uuid4(), school_id=school_id, name=f"unit{suffix}",
            external_id=f"UNIT-1{suffix}",
        )
        segment = Segment(
            id=uuid.uuid4(), school_id=school_id, name=f"segment{suffix}",
            external_id=f"SEG-1{suffix}",
        )
        session.add_all([unit, segment])
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school_id, segment_id=segment.id,
            name=f"grade{suffix}", external_id=f"GRADE-1{suffix}",
        )
        year = AcademicYear(
            id=uuid.uuid4(), school_id=school_id, year=2026, external_id=f"YEAR{suffix}",
        )
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school_id, academic_year_id=year.id,
            grade_level_id=grade.id, name=f"class{suffix}",
            external_id=f"TURMA-1{suffix}",
        )
        session.add(klass)
        await session.commit()
        return {"unit": unit, "segment": segment, "grade": grade, "class": klass}

    async def test_each_hierarchy_scope_resolves_to_its_own_entity(self):
        async with self.session_factory() as session:
            seeded = await self._seed(session, SCHOOL_A)
            resolver = ExternalIdResolver(session)

            cases = [
                ("UNIT", "UNIT-1", seeded["unit"]),
                ("SEGMENT", "SEG-1", seeded["segment"]),
                ("GRADE_LEVEL", "GRADE-1", seeded["grade"]),
                ("CLASSROOM", "TURMA-1", seeded["class"]),
            ]
            for scope_type, external_id, expected in cases:
                with self.subTest(scope_type=scope_type):
                    found = await resolver.resolve(SCHOOL_A, scope_type, external_id)
                    self.assertEqual(found.state, ResolutionState.RESOLVED)
                    self.assertEqual(found.entity_id, expected.id)

    async def test_platform_and_school_are_not_applicable_not_missing(self):
        """The distinction spec section 7 depends on: these scopes legitimately
        point at no hierarchy entity, so they must never look like absent data."""
        async with self.session_factory() as session:
            resolver = ExternalIdResolver(session)
            for scope_type in ("PLATFORM", "SCHOOL"):
                with self.subTest(scope_type=scope_type):
                    found = await resolver.resolve(SCHOOL_A, scope_type, "anything")
                    self.assertEqual(found.state, ResolutionState.NOT_APPLICABLE)
                    self.assertIsNone(found.entity_id)

    async def test_an_unknown_code_is_not_found(self):
        async with self.session_factory() as session:
            await self._seed(session, SCHOOL_A)
            resolver = ExternalIdResolver(session)
            found = await resolver.resolve(SCHOOL_A, "CLASSROOM", "TURMA-QUE-NAO-EXISTE")
        self.assertEqual(found.state, ResolutionState.NOT_FOUND)
        self.assertIsNone(found.entity_id)

    async def test_resolution_never_crosses_a_school_boundary(self):
        """Two schools, the same external id. Resolving for one must never
        return the other's row - this is the whole reason external_id is unique
        per school rather than per year or per segment."""
        async with self.session_factory() as session:
            a = await self._seed(session, SCHOOL_A)
            b = await self._seed(session, SCHOOL_B)
            resolver = ExternalIdResolver(session)

            for_a = await resolver.resolve(SCHOOL_A, "CLASSROOM", "TURMA-1")
            for_b = await resolver.resolve(SCHOOL_B, "CLASSROOM", "TURMA-1")

        self.assertEqual(for_a.entity_id, a["class"].id)
        self.assertEqual(for_b.entity_id, b["class"].id)
        self.assertNotEqual(for_a.entity_id, for_b.entity_id)

    async def test_an_empty_or_missing_code_is_not_found_not_a_crash(self):
        async with self.session_factory() as session:
            resolver = ExternalIdResolver(session)
            for external_id in (None, "", "   "):
                with self.subTest(external_id=repr(external_id)):
                    found = await resolver.resolve(SCHOOL_A, "CLASSROOM", external_id)
                    self.assertEqual(found.state, ResolutionState.NOT_FOUND)

    async def test_an_unknown_scope_type_is_rejected_loudly(self):
        """A scope type outside the CHECK constraint is a programming error, not
        a data condition - it must not be silently folded into NOT_FOUND, which
        a caller would read as 'no such class'."""
        async with self.session_factory() as session:
            resolver = ExternalIdResolver(session)
            with self.assertRaises(ValueError):
                await resolver.resolve(SCHOOL_A, "TURMA", "TURMA-1")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rode para ver falhar**

Run: `.venv/bin/python -m pytest tests/test_r0_external_id_resolution.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.services.external_id_resolution'`

- [ ] **Step 3: Implemente o serviço**

Crie `src/agente_ia_edu/services/external_id_resolution.py`:

```python
"""Translate a scope's external id into a real academic entity.

The core adopted the academic hierarchy as real tables, and every existing
consumer still identifies a scope by the host's free-text code. This is the
bridge between the two, and it exists on its own - with no consumer - so that
the consumers can migrate one at a time, each with its own commit and test.

The result has THREE states on purpose. Spec section 7 ends the transition by
turning "did not resolve" into an error; a two-state result would turn
PLATFORM and SCHOOL scopes into errors too, and those legitimately point at no
hierarchy entity at all. Keeping the distinction now makes that step a one-line
change instead of an excavation.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.academic import Class, GradeLevel, Segment, SchoolUnit


class ResolutionState(enum.Enum):
    RESOLVED = "RESOLVED"
    NOT_FOUND = "NOT_FOUND"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class ScopeResolution:
    state: ResolutionState
    entity: object | None = None

    @property
    def entity_id(self) -> uuid.UUID | None:
        return getattr(self.entity, "id", None)


# Scope types that address a row in the academic hierarchy, and the model each
# one addresses. PLATFORM and SCHOOL are deliberately absent: they are valid
# scopes that point at no hierarchy entity.
_SCOPE_MODELS = {
    "UNIT": SchoolUnit,
    "SEGMENT": Segment,
    "GRADE_LEVEL": GradeLevel,
    "CLASSROOM": Class,
}

_SCOPES_WITHOUT_ENTITY = {"PLATFORM", "SCHOOL"}


class ExternalIdResolver:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def resolve(
        self,
        school_id: uuid.UUID | str,
        scope_type: str,
        external_id: str | None,
    ) -> ScopeResolution:
        """Find the entity a scope addresses, within one school.

        Returns NOT_APPLICABLE for scopes that address no hierarchy entity,
        NOT_FOUND when one is addressed but absent, and RESOLVED otherwise.
        Raises ValueError for a scope type that does not exist at all - that is
        a programming error, and folding it into NOT_FOUND would read to a
        caller as "no such class".
        """
        normalized = (scope_type or "").upper()

        if normalized in _SCOPES_WITHOUT_ENTITY:
            return ScopeResolution(ResolutionState.NOT_APPLICABLE)

        model = _SCOPE_MODELS.get(normalized)
        if model is None:
            raise ValueError(f"unknown scope type: {scope_type!r}")

        code = (external_id or "").strip()
        if not code:
            return ScopeResolution(ResolutionState.NOT_FOUND)

        result = await self.session.execute(
            select(model).where(
                model.school_id == school_id,
                model.external_id == code,
            )
        )
        entity = result.scalars().first()
        if entity is None:
            return ScopeResolution(ResolutionState.NOT_FOUND)
        return ScopeResolution(ResolutionState.RESOLVED, entity)


__all__ = ["ExternalIdResolver", "ResolutionState", "ScopeResolution"]
```

- [ ] **Step 4: Rode para ver passar**

Run: `.venv/bin/python -m pytest tests/test_r0_external_id_resolution.py -q`
Expected: PASS, 6 testes.

- [ ] **Step 5: Rode o gate da fase**

Run: `.venv/bin/python -m pytest tests/test_r0_*.py tests/test_platform_administration.py -q`
Expected: zero falhas.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/external_id_resolution.py tests/test_r0_external_id_resolution.py
git commit -m "feat: resolucao de external_id para entidade, com tres estados

Dois estados tornariam o passo 7 impossivel de fazer com seguranca: PLATFORM
nunca resolve para entidade da hierarquia, e isso e correto, nao dado faltando."
```

---

### Task 2: O gate da fase

**Files:**
- Test: `tests/test_r0_fase3a_gate.py` (criar)

**Interfaces:**
- Consumes: tudo da Task 1.
- Produces: nada. É verificação.

**Por que esta tarefa existe.** A afirmação central desta fase é que ela **não muda nada**: entrega um serviço e nenhum consumidor. Isso é fácil de afirmar e fácil de violar sem perceber — basta alguém ligar uma chamada "só para testar". A Fase 1 e a Fase 2 tiveram gates equivalentes, e os dois acharam defeitos reais.

- [ ] **Step 1: Escreva o gate**

Crie `tests/test_r0_fase3a_gate.py`:

```python
# tests/test_r0_fase3a_gate.py
"""Proves this phase delivers a service and changes nothing else.

Spec section 7 orders the migration: the resolver ships alone, tested, before
any consumer uses it. That ordering exists because the last consumer to migrate
is authorization, and an error there does not crash - it shows the wrong data
to the wrong person. This file is what keeps the ordering honest.
"""

import pathlib
import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.services.external_id_resolution import (
    ExternalIdResolver,
    ResolutionState,
)

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "agente_ia_edu"


class Fase3AGateTests(unittest.IsolatedAsyncioTestCase):
    def test_the_resolver_has_no_consumer_yet(self):
        """The phase's central claim. Consumers arrive in 3B, one at a time,
        each with its own commit and test - not as a side effect of this one."""
        importers = []
        for path in SRC.rglob("*.py"):
            if path.name == "external_id_resolution.py":
                continue
            text = path.read_text(encoding="utf-8")
            if "external_id_resolution" in text or "ExternalIdResolver" in text:
                importers.append(str(path.relative_to(SRC)))
        self.assertEqual(
            importers, [], f"o resolvedor ganhou consumidor antes da Fase 3B: {importers}"
        )

    async def test_the_resolver_works_at_all(self):
        """A gate asserting only absence would pass if the service were broken
        or missing entirely, so it also has to prove the thing exists and runs."""
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                found = await ExternalIdResolver(session).resolve(
                    uuid.uuid4(), "CLASSROOM", "TURMA-INEXISTENTE"
                )
            self.assertEqual(found.state, ResolutionState.NOT_FOUND)
        finally:
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rode**

Run: `.venv/bin/python -m pytest tests/test_r0_fase3a_gate.py -q`
Expected: PASS, 2 testes.

**Se o teste de ausência de consumidor falhar, pare e reporte.** Não remova o consumidor nem ajuste o teste: significa que alguém ligou o serviço antes da hora, e saber disso é o objetivo desta tarefa.

- [ ] **Step 3: Prove que o gate consegue falhar**

Acrescente temporariamente um import do resolvedor a qualquer módulo de `src/agente_ia_edu/services/`, rode o teste, confirme que ele fica vermelho nomeando o arquivo, e reverta. Relate o que viu. Um gate de ausência que não detecta presença não guarda nada.

- [ ] **Step 4: Rode o gate da fase inteiro**

Run: `.venv/bin/python -m pytest tests/test_r0_*.py tests/test_platform_administration.py -q`
Expected: zero falhas.

- [ ] **Step 5: Commit**

```bash
git add tests/test_r0_fase3a_gate.py
git commit -m "test: prova que a 3A entrega servico e nenhum consumidor

A ordem da §7 existe porque o ultimo consumidor a migrar e a autorizacao, e erro
ali nao quebra: mostra dado errado para a pessoa errada."
```

---

## Depois da última tarefa

Rode a suíte completa contra um banco descartável e compare com o baseline em `44b54bf`, que é `1957 passed, 0 failed, 4 skipped, 13 errors`. Os 13 erros são `tests/test_phase9u2h4_review_packet.py` e vêm de um arquivo no `.gitignore` que existe só no checkout principal — ambientais, não desta fase.

Esta fase é aditiva de verdade: cria um arquivo de serviço e dois de teste, e não toca em nada existente. Ainda assim, meça — a Fase 1 declarou-se aditiva com três verificações estáticas limpas e tinha dezessete testes vermelhos.
