# R0 Fase 2 — Portão de Disciplina: Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ligar o mecanismo `PedagogicalUniverse`, que já existe e hoje não restringe nada, aos três serviços que o ignoram — autorização, banco de questões e busca de estudo — sem trancar fora nenhuma escola existente.

**Architecture:** Um serviço novo, `DisciplineGate`, resolve uma vez por requisição o conjunto de nós de catálogo que uma escola pode ver, e responde `unrestricted` quando ninguém declarou restrição. Os três consumidores passam a consultá-lo. A expansão de descendentes é feita uma vez, de forma ansiosa, para que o filtro vire teste de pertencimento a conjunto — barato o bastante para ser aplicado dentro do SQL de contagem e de página, que é o que mantém a paginação honesta.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x async, `unittest.IsolatedAsyncioTestCase` com `sqlite+aiosqlite:///:memory:` e `StaticPool`.

**Spec:** `docs/superpowers/specs/2026-09-13-r0-estrutura-academica-configuracao-design.md` — §3.3, §6, §8 e §9.

## Global Constraints

- Python `>=3.13,<3.14`. Nenhuma dependência nova.
- **Nenhuma coluna de credencial em lugar nenhum** — nada de `password`, `token`, `secret`, `credential`, `senha`.
- **Esta fase não cria migration.** O schema de `pedagogical_universes` e `pedagogical_universe_catalog_scopes` já existe e basta. Se alguma tarefa parecer exigir migration, pare e reporte: significa que o diagnóstico está errado.
- Testes com `unittest.IsolatedAsyncioTestCase`, `sqlite+aiosqlite:///:memory:` e `StaticPool`. **Não existe `conftest.py` no projeto; não crie um.**
- Código e comentários em inglês; mensagem de commit em português, como o `git log`.
- **Não conecte a banco nenhum além do SQLite em memória dos testes.** O banco de desenvolvimento é compartilhado com outra sessão.
- `.venv` e `var` são symlinks e aparecem como não rastreados — nunca os comite.
- Gate desta fase: `.venv/bin/python -m pytest tests/test_r0_*.py tests/test_platform_administration.py tests/test_pedagogical_universe*.py -q`.

## A regra que governa todas as tarefas

Da §6 da spec, literalmente:

> **Escola sem universo não tem restrição.** Todas as escolas existentes estão nesse estado. Um portão implementado ingenuamente as trancaria fora no dia do deploy.
>
> Ausência de universo significa acesso a tudo; restrição só existe quando alguém a declarou.

O código já contém a armadilha exata contra a qual essa regra adverte. `PedagogicalUniverseService.resolve_active_universe`, em `src/agente_ia_edu/services/pedagogical_universe.py:122`, faz:

```python
if not universes:
    raise PermissionError("No authorized pedagogical universe")
```

**Nenhum consumidor desta fase pode ser construído sobre esse método.** Ele é correto para o que faz — escolher entre universos autorizados — e é veneno como portão, porque transforma ausência em negação. O `DisciplineGate` existe para dar a semântica oposta.

Há **três** formas de ausência, e todas as três significam acesso total:

1. A requisição não tem escola (`school_id is None`) — aluno independente.
2. A escola não tem universo ativo.
3. A escola tem universo ativo, mas ele não declara nenhum escopo de catálogo.

A terceira é a que se esquece. Um universo criado e ainda não configurado restringiria tudo se o portão apenas intersectasse com um conjunto vazio.

---

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/agente_ia_edu/services/discipline_gate.py` (**criar**) | O portão: resolve o escopo permitido de uma escola e responde se um nó é permitido. Uma responsabilidade só. |
| `src/agente_ia_edu/services/pedagogical_universe.py` (**modificar**) | Ganha dois métodos públicos que o portão consome, porque é este módulo que sabe o que é um universo de escola e como expandir descendentes. |
| `src/agente_ia_edu/services/authorization.py` (**modificar**) | Ganha `require_discipline`, no mesmo formato dos `require_*` que já existem. |
| `src/agente_ia_edu/services/question_bank.py` (**modificar**) | `list_questions` passa a filtrar pelo portão, na contagem e na página. |
| `src/agente_ia_edu/services/study_search.py` (**modificar**) | `search` passa a filtrar pelo portão. |

---

### Task 1: Métodos públicos no serviço de universo

**Files:**
- Modify: `src/agente_ia_edu/services/pedagogical_universe.py`
- Test: `tests/test_r0_universe_school_scope.py` (criar)

**Por que aqui e não no portão.** Saber que um universo de escola é `owner_type == 'SCHOOL'` com `owner_external_id` igual ao id da escola, e saber andar na árvore de catálogo, é conhecimento deste módulo. O portão que duplicasse isso criaria um segundo conceito de escopo que um dia discordaria do primeiro — exatamente o que a §3.3 da spec manda evitar. O módulo já tem `_collect_descendants` privado; esta tarefa expõe o que o portão precisa sem que ele alcance dentro.

**Interfaces:**
- Consumes: nada de tarefas anteriores.
- Produces:
  - `PedagogicalUniverseService.active_school_universe_ids(school_id: str) -> list[uuid.UUID]`
  - `PedagogicalUniverseService.expand_catalog_scope_nodes(universe_ids: Sequence[uuid.UUID]) -> frozenset[uuid.UUID]`
  - `PedagogicalUniverseService.catalog_codes_for(node_ids: Iterable[uuid.UUID]) -> frozenset[str]`

**Por que os códigos também.** `PedagogicalClassification` **não** referencia `catalog_nodes` por chave estrangeira: ela guarda `discipline`, `content` e `subcontent` como texto, e o que fica em `content` é o **código** do nó (veja o fixture em `tests/test_question_bank_core.py:150-160`, que grava `content=code`). O banco de questões resolve o caminho curricular por código, em `_resolve_curriculum_path`. Então o portão precisa das duas formas: ids para quem navega a árvore, códigos para quem já trabalha em código. Derivar os códigos aqui, de uma vez, evita que cada consumidor invente sua própria tradução.

- [ ] **Step 1: Escreva os testes que falham**

Crie `tests/test_r0_universe_school_scope.py`:

```python
# tests/test_r0_universe_school_scope.py
"""Covers the two public helpers the discipline gate is built on.

They live on the universe service because knowing what a school's universe is,
and how to walk the catalog tree, is that module's knowledge - not the gate's.
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.catalog import CatalogNode
from agente_ia_edu.db.models.pedagogical_universe import (
    PedagogicalUniverse,
    PedagogicalUniverseCatalogScope,
)
from agente_ia_edu.services.pedagogical_universe import PedagogicalUniverseService

SCHOOL_A = str(uuid.uuid4())
SCHOOL_B = str(uuid.uuid4())


class UniverseSchoolScopeTests(unittest.IsolatedAsyncioTestCase):
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

    async def _node(self, session, code, parent_id=None):
        node = CatalogNode(
            id=uuid.uuid4(), code=code, name=code, node_type="CONTENT", parent_id=parent_id
        )
        session.add(node)
        await session.flush()
        return node

    async def _universe(self, session, *, slug, owner_type, owner_external_id, status):
        universe = PedagogicalUniverse(
            id=uuid.uuid4(),
            external_id=slug,
            slug=slug,
            name=slug,
            owner_type=owner_type,
            owner_external_id=owner_external_id,
            status=status,
        )
        session.add(universe)
        await session.flush()
        return universe

    async def test_active_school_universe_ids_only_returns_this_schools_active_ones(self):
        async with self.session_factory() as session:
            mine = await self._universe(
                session, slug="mine", owner_type="SCHOOL",
                owner_external_id=SCHOOL_A, status="ACTIVE",
            )
            await self._universe(
                session, slug="draft", owner_type="SCHOOL",
                owner_external_id=SCHOOL_A, status="DRAFT",
            )
            await self._universe(
                session, slug="other-school", owner_type="SCHOOL",
                owner_external_id=SCHOOL_B, status="ACTIVE",
            )
            await self._universe(
                session, slug="platform", owner_type="PLATFORM",
                owner_external_id=None, status="ACTIVE",
            )
            await session.commit()

            service = PedagogicalUniverseService(session)
            found = await service.active_school_universe_ids(SCHOOL_A)

        self.assertEqual(found, [mine.id])

    async def test_expand_catalog_scope_nodes_includes_descendants_when_asked(self):
        async with self.session_factory() as session:
            root = await self._node(session, "DISC")
            child = await self._node(session, "DISC-AREA", parent_id=root.id)
            grandchild = await self._node(session, "DISC-AREA-CONTENT", parent_id=child.id)
            unrelated = await self._node(session, "OTHER")

            universe = await self._universe(
                session, slug="u", owner_type="SCHOOL",
                owner_external_id=SCHOOL_A, status="ACTIVE",
            )
            session.add(
                PedagogicalUniverseCatalogScope(
                    id=uuid.uuid4(), universe_id=universe.id,
                    catalog_node_id=root.id, scope_kind="DISCIPLINE",
                    include_descendants=True,
                )
            )
            await session.commit()

            service = PedagogicalUniverseService(session)
            nodes = await service.expand_catalog_scope_nodes([universe.id])

        self.assertEqual(nodes, frozenset({root.id, child.id, grandchild.id}))
        self.assertNotIn(unrelated.id, nodes)

    async def test_expand_catalog_scope_nodes_stops_at_the_node_when_not_asked(self):
        async with self.session_factory() as session:
            root = await self._node(session, "DISC")
            child = await self._node(session, "DISC-AREA", parent_id=root.id)

            universe = await self._universe(
                session, slug="u", owner_type="SCHOOL",
                owner_external_id=SCHOOL_A, status="ACTIVE",
            )
            session.add(
                PedagogicalUniverseCatalogScope(
                    id=uuid.uuid4(), universe_id=universe.id,
                    catalog_node_id=root.id, scope_kind="DISCIPLINE",
                    include_descendants=False,
                )
            )
            await session.commit()

            service = PedagogicalUniverseService(session)
            nodes = await service.expand_catalog_scope_nodes([universe.id])

        self.assertEqual(nodes, frozenset({root.id}))
        self.assertNotIn(child.id, nodes)

    async def test_expand_catalog_scope_nodes_of_nothing_is_empty(self):
        async with self.session_factory() as session:
            service = PedagogicalUniverseService(session)
            self.assertEqual(await service.expand_catalog_scope_nodes([]), frozenset())

    async def test_catalog_codes_for_translates_ids_to_codes(self):
        """The question bank stores the content CODE, not a foreign key, so the
        gate has to speak both."""
        async with self.session_factory() as session:
            root = await self._node(session, "CHEMISTRY")
            child = await self._node(session, "CHEMISTRY-KINETICS", parent_id=root.id)
            await session.commit()

            service = PedagogicalUniverseService(session)
            codes = await service.catalog_codes_for([root.id, child.id])

        self.assertEqual(codes, frozenset({"CHEMISTRY", "CHEMISTRY-KINETICS"}))

    async def test_catalog_codes_for_nothing_is_empty(self):
        async with self.session_factory() as session:
            service = PedagogicalUniverseService(session)
            self.assertEqual(await service.catalog_codes_for([]), frozenset())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rode para ver falhar**

Run: `.venv/bin/python -m pytest tests/test_r0_universe_school_scope.py -q`
Expected: FAIL com `AttributeError: 'PedagogicalUniverseService' object has no attribute 'active_school_universe_ids'`

- [ ] **Step 3: Implemente os dois métodos**

Em `src/agente_ia_edu/services/pedagogical_universe.py`, acrescente à classe `PedagogicalUniverseService`, logo depois de `require_universe`:

```python
    async def active_school_universe_ids(self, school_id: str) -> list[uuid.UUID]:
        """Ids of the ACTIVE universes owned by this school.

        DRAFT and ARCHIVED are excluded on purpose: a universe that is still
        being configured must not restrict anyone yet.
        """
        result = await self.session.execute(
            select(PedagogicalUniverse.id).where(
                PedagogicalUniverse.owner_type == "SCHOOL",
                PedagogicalUniverse.owner_external_id == str(school_id),
                PedagogicalUniverse.status == "ACTIVE",
            )
        )
        return list(result.scalars().all())

    async def expand_catalog_scope_nodes(
        self, universe_ids: Sequence[uuid.UUID]
    ) -> frozenset[uuid.UUID]:
        """Every catalog node these universes reach, descendants included.

        Expanded eagerly, once, so callers can test membership against a set
        instead of issuing a query per node. That is what makes the gate cheap
        enough to apply inside a paginated SQL query.
        """
        if not universe_ids:
            return frozenset()
        scopes = list(
            (
                await self.session.execute(
                    select(PedagogicalUniverseCatalogScope).where(
                        PedagogicalUniverseCatalogScope.universe_id.in_(list(universe_ids))
                    )
                )
            )
            .scalars()
            .all()
        )
        reachable: set[uuid.UUID] = set()
        for scope in scopes:
            reachable.add(scope.catalog_node_id)
            if scope.include_descendants:
                reachable.update(await self._collect_descendants(scope.catalog_node_id))
        return frozenset(reachable)

    async def catalog_codes_for(
        self, node_ids: Iterable[uuid.UUID]
    ) -> frozenset[str]:
        """The codes of these nodes.

        ``pedagogical_classifications`` stores the content CODE as text rather
        than a foreign key, so a consumer filtering classifications needs codes,
        not ids. Translating once here keeps every consumer from inventing its
        own version of this.
        """
        ids = list(node_ids)
        if not ids:
            return frozenset()
        result = await self.session.execute(
            select(CatalogNode.code).where(CatalogNode.id.in_(ids))
        )
        return frozenset(result.scalars().all())
```

No topo do arquivo, acrescente `Iterable` e `Sequence` ao import de `typing` (a linha existente é `from typing import Any`; passa a ser `from typing import Any, Iterable, Sequence`). `CatalogNode` e `select` já estão importados.

- [ ] **Step 4: Rode para ver passar**

Run: `.venv/bin/python -m pytest tests/test_r0_universe_school_scope.py -q`
Expected: PASS, 6 testes.

- [ ] **Step 5: Commit**

```bash
git add tests/test_r0_universe_school_scope.py src/agente_ia_edu/services/pedagogical_universe.py
git commit -m "feat: universo expoe os universos ativos da escola e a expansao de escopo

O portao precisa das duas coisas e nenhuma delas e conhecimento dele. Expor
aqui evita um segundo conceito de escopo que um dia discordaria do primeiro."
```

---

### Task 2: O portão

**Files:**
- Create: `src/agente_ia_edu/services/discipline_gate.py`
- Test: `tests/test_r0_discipline_gate.py` (criar)

**Interfaces:**
- Consumes: `PedagogicalUniverseService.active_school_universe_ids`, `PedagogicalUniverseService.expand_catalog_scope_nodes`, `PedagogicalUniverseService.catalog_codes_for` (Task 1).
- Produces:
  - `DisciplineScope` — dataclass congelado, com `unrestricted: bool`, `allowed_node_ids: frozenset[uuid.UUID]` e `allowed_codes: frozenset[str]`
  - `DisciplineScope.unrestricted_scope() -> DisciplineScope`
  - `DisciplineScope.permits(catalog_node_id: uuid.UUID | None) -> bool`
  - `DisciplineScope.permits_code(catalog_code: str | None) -> bool`
  - `DisciplineGate(session: AsyncSession)`
  - `DisciplineGate.scope_for_school(school_id: str | uuid.UUID | None) -> DisciplineScope`

**Decisão registrada: o portão considera os três tipos de escopo, não só `DISCIPLINE`.** `scope_kind` aceita `AREA`, `DISCIPLINE` e `CONTENT`. Restringir só por `DISCIPLINE` faria uma escola limitada a uma área ver tudo em silêncio — falha na direção que não aparece em teste nenhum. O nome "portão de disciplina" descreve o caso de uso da §6, não o filtro.

- [ ] **Step 1: Escreva os testes que falham**

Crie `tests/test_r0_discipline_gate.py`:

```python
# tests/test_r0_discipline_gate.py
"""The gate, and above all the three shapes of absence.

Absence of a universe means access to everything. Restriction exists only
where somebody declared it (spec section 6). Every school in production today
is in that state, so a gate that gets this wrong locks out the entire base on
deploy day. Three of these tests are that rule.
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.catalog import CatalogNode
from agente_ia_edu.db.models.pedagogical_universe import (
    PedagogicalUniverse,
    PedagogicalUniverseCatalogScope,
)
from agente_ia_edu.services.discipline_gate import DisciplineGate, DisciplineScope

SCHOOL = str(uuid.uuid4())


class DisciplineGateTests(unittest.IsolatedAsyncioTestCase):
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

    async def _node(self, session, code):
        node = CatalogNode(
            id=uuid.uuid4(), code=code, name=code, node_type="DISCIPLINE", parent_id=None
        )
        session.add(node)
        await session.flush()
        return node

    async def _active_universe(self, session):
        universe = PedagogicalUniverse(
            id=uuid.uuid4(), external_id="u", slug="u", name="u",
            owner_type="SCHOOL", owner_external_id=SCHOOL, status="ACTIVE",
        )
        session.add(universe)
        await session.flush()
        return universe

    async def test_absence_1_no_school_means_unrestricted(self):
        async with self.session_factory() as session:
            scope = await DisciplineGate(session).scope_for_school(None)
        self.assertTrue(scope.unrestricted)
        self.assertTrue(scope.permits(uuid.uuid4()))

    async def test_absence_2_school_without_universe_means_unrestricted(self):
        async with self.session_factory() as session:
            scope = await DisciplineGate(session).scope_for_school(SCHOOL)
        self.assertTrue(scope.unrestricted)
        self.assertTrue(scope.permits(uuid.uuid4()))

    async def test_absence_3_universe_without_any_scope_means_unrestricted(self):
        async with self.session_factory() as session:
            await self._active_universe(session)
            await session.commit()
            scope = await DisciplineGate(session).scope_for_school(SCHOOL)
        self.assertTrue(
            scope.unrestricted,
            "a universe that declares no catalog scope restricts nothing - "
            "intersecting with an empty set would block everything",
        )
        self.assertTrue(scope.permits(uuid.uuid4()))

    async def test_a_declared_scope_restricts_to_it(self):
        async with self.session_factory() as session:
            allowed = await self._node(session, "CHEMISTRY")
            denied = await self._node(session, "HISTORY")
            universe = await self._active_universe(session)
            session.add(
                PedagogicalUniverseCatalogScope(
                    id=uuid.uuid4(), universe_id=universe.id,
                    catalog_node_id=allowed.id, scope_kind="DISCIPLINE",
                    include_descendants=True,
                )
            )
            await session.commit()
            scope = await DisciplineGate(session).scope_for_school(SCHOOL)

        self.assertFalse(scope.unrestricted)
        self.assertTrue(scope.permits(allowed.id))
        self.assertFalse(scope.permits(denied.id))
        # The same rule by code, which is how the question bank sees it.
        self.assertTrue(scope.permits_code("CHEMISTRY"))
        self.assertFalse(scope.permits_code("HISTORY"))
        self.assertTrue(scope.permits_code(None), "unclassified stays visible")

    async def test_a_draft_universe_does_not_restrict(self):
        async with self.session_factory() as session:
            allowed = await self._node(session, "CHEMISTRY")
            universe = PedagogicalUniverse(
                id=uuid.uuid4(), external_id="d", slug="d", name="d",
                owner_type="SCHOOL", owner_external_id=SCHOOL, status="DRAFT",
            )
            session.add(universe)
            await session.flush()
            session.add(
                PedagogicalUniverseCatalogScope(
                    id=uuid.uuid4(), universe_id=universe.id,
                    catalog_node_id=allowed.id, scope_kind="DISCIPLINE",
                    include_descendants=True,
                )
            )
            await session.commit()
            scope = await DisciplineGate(session).scope_for_school(SCHOOL)

        self.assertTrue(scope.unrestricted)

    async def test_an_unclassified_item_is_permitted_under_restriction(self):
        """A question with no catalog node at all is not evidence of another
        discipline, and blocking it would hide unclassified content from the
        only people able to classify it."""
        async with self.session_factory() as session:
            allowed = await self._node(session, "CHEMISTRY")
            universe = await self._active_universe(session)
            session.add(
                PedagogicalUniverseCatalogScope(
                    id=uuid.uuid4(), universe_id=universe.id,
                    catalog_node_id=allowed.id, scope_kind="DISCIPLINE",
                    include_descendants=True,
                )
            )
            await session.commit()
            scope = await DisciplineGate(session).scope_for_school(SCHOOL)

        self.assertFalse(scope.unrestricted)
        self.assertTrue(scope.permits(None))

    def test_unrestricted_scope_permits_anything(self):
        scope = DisciplineScope.unrestricted_scope()
        self.assertTrue(scope.permits(uuid.uuid4()))
        self.assertTrue(scope.permits(None))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rode para ver falhar**

Run: `.venv/bin/python -m pytest tests/test_r0_discipline_gate.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.services.discipline_gate'`

- [ ] **Step 3: Implemente o portão**

Crie `src/agente_ia_edu/services/discipline_gate.py`:

```python
"""The discipline gate.

``pedagogical_universes`` already had the exact shape this needed and was
consumed by two services out of five, so the mechanism existed and restricted
nothing. This completes it rather than introducing a second notion of scope
that would eventually disagree with the first.

Its whole reason to exist is one rule from the spec, section 6: absence of a
universe means access to everything; restriction exists only where somebody
declared it. ``PedagogicalUniverseService.resolve_active_universe`` raises on
absence, which is right for choosing among authorized universes and wrong as a
gate - nothing here is built on it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from .pedagogical_universe import PedagogicalUniverseService


@dataclass(frozen=True)
class DisciplineScope:
    """What a school is allowed to see, resolved once per request."""

    unrestricted: bool
    allowed_node_ids: frozenset[uuid.UUID]
    allowed_codes: frozenset[str]

    @classmethod
    def unrestricted_scope(cls) -> "DisciplineScope":
        return cls(
            unrestricted=True, allowed_node_ids=frozenset(), allowed_codes=frozenset()
        )

    def permits(self, catalog_node_id: uuid.UUID | None) -> bool:
        if self.unrestricted:
            return True
        if catalog_node_id is None:
            # Unclassified content is not evidence of another discipline, and
            # hiding it would keep it from the only people who can classify it.
            return True
        return catalog_node_id in self.allowed_node_ids

    def permits_code(self, catalog_code: str | None) -> bool:
        """Same rule, for consumers that hold a catalog code rather than an id.

        ``pedagogical_classifications.content`` is one of those: it stores the
        code as text, with no foreign key to ``catalog_nodes``.
        """
        if self.unrestricted:
            return True
        if not catalog_code:
            return True
        return catalog_code in self.allowed_codes


class DisciplineGate:
    def __init__(self, session: AsyncSession):
        self.session = session
        self._universes = PedagogicalUniverseService(session)

    async def scope_for_school(
        self, school_id: str | uuid.UUID | None
    ) -> DisciplineScope:
        if school_id is None:
            return DisciplineScope.unrestricted_scope()

        universe_ids = await self._universes.active_school_universe_ids(str(school_id))
        if not universe_ids:
            return DisciplineScope.unrestricted_scope()

        allowed = await self._universes.expand_catalog_scope_nodes(universe_ids)
        if not allowed:
            # A universe that declares no catalog scope restricts nothing.
            # Intersecting with the empty set would block everything instead.
            return DisciplineScope.unrestricted_scope()

        return DisciplineScope(
            unrestricted=False,
            allowed_node_ids=allowed,
            allowed_codes=await self._universes.catalog_codes_for(allowed),
        )


__all__ = ["DisciplineGate", "DisciplineScope"]
```

- [ ] **Step 4: Rode para ver passar**

Run: `.venv/bin/python -m pytest tests/test_r0_discipline_gate.py -q`
Expected: PASS, 7 testes.

- [ ] **Step 5: Commit**

```bash
git add tests/test_r0_discipline_gate.py src/agente_ia_edu/services/discipline_gate.py
git commit -m "feat: portao de disciplina, com ausencia significando acesso total

Tres formas de ausencia, todas liberando tudo: sem escola, sem universo ativo,
e universo ativo que nao declarou escopo nenhum. A terceira e a que se esquece."
```

---

### Task 3: `require_discipline` na autorização

**Files:**
- Modify: `src/agente_ia_edu/services/authorization.py`
- Test: `tests/test_r0_authorization_discipline.py` (criar)

**Interfaces:**
- Consumes: `DisciplineGate`, `DisciplineScope` (Task 2).
- Produces: `AuthorizationService.require_discipline(context: AuthenticatedUserContext, catalog_node_id: uuid.UUID | None) -> AuthorizationCheckResult`

**Siga o formato que já existe.** `require_module`, em `authorization.py:192`, devolve `AuthorizationCheckResult(True, None)` quando `context.school_id is None`, com o comentário sobre alunos independentes. `require_discipline` tem a mesma forma e a mesma razão.

- [ ] **Step 1: Escreva os testes que falham**

Crie `tests/test_r0_authorization_discipline.py`:

```python
# tests/test_r0_authorization_discipline.py
"""require_discipline, in the shape of the require_* methods already there."""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.catalog import CatalogNode
from agente_ia_edu.db.models.pedagogical_universe import (
    PedagogicalUniverse,
    PedagogicalUniverseCatalogScope,
)
from agente_ia_edu.identity import AuthenticatedUserContext
from agente_ia_edu.services.authorization import AuthorizationService

SCHOOL = str(uuid.uuid4())


def _context(school_id):
    return AuthenticatedUserContext(
        user_id="u-1",
        external_identity_id="ext-1",
        role="TEACHER",
        school_id=school_id,
    )


class AuthorizationDisciplineTests(unittest.IsolatedAsyncioTestCase):
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

    async def _restrict_school_to(self, session, code):
        node = CatalogNode(
            id=uuid.uuid4(), code=code, name=code, node_type="DISCIPLINE", parent_id=None
        )
        session.add(node)
        universe = PedagogicalUniverse(
            id=uuid.uuid4(), external_id="u", slug="u", name="u",
            owner_type="SCHOOL", owner_external_id=SCHOOL, status="ACTIVE",
        )
        session.add(universe)
        await session.flush()
        session.add(
            PedagogicalUniverseCatalogScope(
                id=uuid.uuid4(), universe_id=universe.id,
                catalog_node_id=node.id, scope_kind="DISCIPLINE",
                include_descendants=True,
            )
        )
        await session.commit()
        return node

    async def test_school_without_universe_is_allowed(self):
        async with self.session_factory() as session:
            result = await AuthorizationService(session).require_discipline(
                _context(SCHOOL), uuid.uuid4()
            )
        self.assertTrue(result.allowed)
        self.assertIsNone(result.reason)

    async def test_independent_student_is_allowed(self):
        async with self.session_factory() as session:
            result = await AuthorizationService(session).require_discipline(
                _context(None), uuid.uuid4()
            )
        self.assertTrue(result.allowed)

    async def test_restricted_school_is_allowed_inside_its_scope(self):
        async with self.session_factory() as session:
            node = await self._restrict_school_to(session, "CHEMISTRY")
            result = await AuthorizationService(session).require_discipline(
                _context(SCHOOL), node.id
            )
        self.assertTrue(result.allowed)

    async def test_restricted_school_is_refused_outside_its_scope(self):
        async with self.session_factory() as session:
            await self._restrict_school_to(session, "CHEMISTRY")
            outsider = CatalogNode(
                id=uuid.uuid4(), code="HISTORY", name="HISTORY",
                node_type="DISCIPLINE", parent_id=None,
            )
            session.add(outsider)
            await session.commit()
            result = await AuthorizationService(session).require_discipline(
                _context(SCHOOL), outsider.id
            )
        self.assertFalse(result.allowed)
        self.assertIn("discipline", (result.reason or "").lower())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rode para ver falhar**

Run: `.venv/bin/python -m pytest tests/test_r0_authorization_discipline.py -q`
Expected: FAIL com `AttributeError: 'AuthorizationService' object has no attribute 'require_discipline'`

- [ ] **Step 3: Implemente**

Em `src/agente_ia_edu/services/authorization.py`, acrescente depois de `require_module`:

```python
    async def require_discipline(
        self,
        context: AuthenticatedUserContext,
        catalog_node_id: uuid.UUID | None,
    ) -> AuthorizationCheckResult:
        """Refuse content outside the school's declared pedagogical scope.

        A school that declared no scope is unrestricted - which is every school
        in production today, so this must never refuse on absence.
        """
        scope = await DisciplineGate(self.session).scope_for_school(context.school_id)
        if scope.permits(catalog_node_id):
            return AuthorizationCheckResult(True, None)
        return AuthorizationCheckResult(
            False, "This discipline is outside the school's pedagogical scope."
        )
```

No topo do arquivo, acrescente `import uuid` se ainda não existir e `from .discipline_gate import DisciplineGate`.

- [ ] **Step 4: Rode para ver passar**

Run: `.venv/bin/python -m pytest tests/test_r0_authorization_discipline.py -q`
Expected: PASS, 4 testes.

- [ ] **Step 5: Rode o gate da fase para garantir que nada regrediu**

Run: `.venv/bin/python -m pytest tests/test_r0_*.py tests/test_platform_administration.py tests/test_pedagogical_universe*.py -q`
Expected: zero falhas.

- [ ] **Step 6: Commit**

```bash
git add tests/test_r0_authorization_discipline.py src/agente_ia_edu/services/authorization.py
git commit -m "feat: require_discipline na autorizacao, no formato dos require_* existentes

Escola sem escopo declarado passa, que e o estado de toda escola hoje."
```

---

### Task 4: Banco de questões

**Files:**
- Modify: `src/agente_ia_edu/services/question_bank.py:422-484` (`list_questions`)
- Test: `tests/test_r0_question_bank_discipline.py` (criar)

**Interfaces:**
- Consumes: `DisciplineGate`, `DisciplineScope` (Task 2).
- Produces: `QuestionBankService.list_questions(..., school_id: str | uuid.UUID | None = None)` — parâmetro novo, nomeado, com default `None`, de modo que todo chamador existente continua irrestrito.

**A armadilha desta tarefa é a paginação.** `list_questions` monta duas consultas: uma `count_base` e a da página. Filtrar só a segunda faz o total mentir — a interface mostraria "48 questões" e entregaria 12, e nenhum teste de uma página só perceberia. **As duas consultas recebem o mesmo filtro.** O teste abaixo verifica o total, não só os itens.

- [ ] **Step 1: Escreva o teste que falha**

Crie `tests/test_r0_question_bank_discipline.py`:

```python
# tests/test_r0_question_bank_discipline.py
"""The gate applied to the question bank - and to its count, not just its page.

Filtering the page query alone makes the total lie: the UI would say 48 and
hand back 12. The count assertion here is the point of the file.
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.services.question_bank import QuestionBankService

SCHOOL = str(uuid.uuid4())


class QuestionBankDisciplineTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_no_school_id_lists_everything(self):
        """Every existing caller passes no school_id, and must stay unrestricted."""
        async with self.session_factory() as session:
            page = await QuestionBankService(session).list_questions()
        self.assertEqual(page.total, 0)

    async def test_school_without_universe_lists_everything(self):
        async with self.session_factory() as session:
            page = await QuestionBankService(session).list_questions(school_id=SCHOOL)
        self.assertEqual(page.total, 0)


if __name__ == "__main__":
    unittest.main()
```

Acrescente ao mesmo arquivo o teste que de fato prova o filtro, reusando o `_Fixture` que já existe em `tests/test_question_bank_core.py:53` — ele semeia um banco compacto com **duas disciplinas**, e devolve os nós em `nodes["bio_content"]` e `nodes["math_content"]`:

```python
from tests.test_question_bank_core import _Fixture

from agente_ia_edu.db.models.pedagogical_universe import (
    PedagogicalUniverse,
    PedagogicalUniverseCatalogScope,
)


class QuestionBankRestrictedSchoolTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_restricted_school_sees_only_its_own_discipline(self):
        async with self.session_factory() as session:
            seeded = await _Fixture().build(session)
            biology = seeded["nodes"]["bio_content"]

            universe = PedagogicalUniverse(
                id=uuid.uuid4(), external_id="u", slug="u", name="u",
                owner_type="SCHOOL", owner_external_id=SCHOOL, status="ACTIVE",
            )
            session.add(universe)
            await session.flush()
            session.add(
                PedagogicalUniverseCatalogScope(
                    id=uuid.uuid4(), universe_id=universe.id,
                    catalog_node_id=biology.id, scope_kind="DISCIPLINE",
                    include_descendants=True,
                )
            )
            await session.commit()

            service = QuestionBankService(session)
            everything = await service.list_questions()
            restricted = await service.list_questions(school_id=SCHOOL)

        self.assertGreater(everything.total, restricted.total,
                           "the fixture must contain more than one discipline")
        self.assertGreater(restricted.total, 0,
                           "the allowed discipline must survive the filter")
        # The count and the page must agree - this is the assertion that catches
        # a filter applied to only one of the two queries.
        self.assertEqual(restricted.total, len(restricted.items))
        for item in restricted.items:
            self.assertNotEqual(item.content_code, "MATH-ALGEBRA-FUNCTIONS")
```

**Valores verificados antes de escrever este plano, rodando o fixture:** ele semeia **8 questões** em duas disciplinas, e os dois nós são `bio_content` com código `BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS` e `math_content` com código `MATH-ALGEBRA-FUNCTIONS`. As 8 cabem numa página só (o default é 20), então `restricted.total == len(restricted.items)` é asserção válida e não um acidente de paginação. Se os dois divergirem, o filtro está numa consulta e não na outra — que é exatamente o defeito que esta tarefa precisa impedir.

- [ ] **Step 2: Rode para ver falhar**

Run: `.venv/bin/python -m pytest tests/test_r0_question_bank_discipline.py -q`
Expected: FAIL com `TypeError: list_questions() got an unexpected keyword argument 'school_id'`

- [ ] **Step 3: Implemente o filtro nas duas consultas**

Em `list_questions`, acrescente o parâmetro e resolva o escopo antes de montar as consultas:

```python
    async def list_questions(
        self,
        filters: QuestionBankFilters | None = None,
        *,
        page: int = 1,
        page_size: int = 20,
        order_by: str = "official_number",
        order_direction: str = "asc",
        school_id: str | UUID | None = None,
    ) -> QuestionBankPage:
```

Logo depois de `await self._load_catalog()`, acrescente:

```python
        # Resolved once and applied to BOTH queries below. Filtering only the
        # page query would make `total` lie about how many rows exist.
        scope = await DisciplineGate(self.session).scope_for_school(school_id)
```

Defina o predicado uma vez e aplique-o a `count_base` e à consulta da página:

```python
        def _apply_discipline_scope(statement):
            if scope.unrestricted:
                return statement
            return statement.where(
                or_(
                    pc.content.is_(None),
                    pc.content.in_(sorted(scope.allowed_codes)),
                )
            )
```

`pc` é o alias de `PedagogicalClassification` que a função já cria. **Filtra-se por `pc.content`, que é texto, e não por chave estrangeira**: `pedagogical_classifications` não referencia `catalog_nodes` — guarda `discipline`, `content` e `subcontent` como código em texto (`pedagogical.py:249-251`), e é por isso que o portão expõe `allowed_codes`. O `is_(None)` preserva a regra: conteúdo não classificado não é prova de outra disciplina.

Acrescente `from sqlalchemy import or_` ao bloco de imports do SQLAlchemy se ainda não estiver lá, e `from .discipline_gate import DisciplineGate`.

**Confirme, lendo o código, que `pc` está de fato juntado às duas consultas.** Se `count_base` não juntar `pc`, junte com `outerjoin`, e não com `join` — um `join` interno descartaria toda questão sem classificação e mudaria o total para todo mundo, inclusive para quem não tem restrição nenhuma. Se essa mudança parecer maior do que isso, **pare e reporte** em vez de reestruturar a consulta.

**Atenção a uma linha vizinha.** O `_Fixture` grava, de propósito, uma classificação com `lifecycle="SUPERSEDED"` e `content="STALE-CODE"` para cada questão, porque o serviço deve ignorá-la. Se o seu filtro atingir linhas superseded, uma questão permitida pode sumir por causa de um código velho. Verifique que o predicado convive com o filtro de `lifecycle` que o serviço já aplica.

- [ ] **Step 4: Rode para ver passar**

Run: `.venv/bin/python -m pytest tests/test_r0_question_bank_discipline.py -q`
Expected: PASS, 3 testes.

- [ ] **Step 5: Rode os testes existentes do banco de questões**

Run: `.venv/bin/python -m pytest tests/test_question_bank*.py -q`
Expected: zero falhas. Nenhum chamador existente passa `school_id`, então nenhum deve mudar de comportamento.

- [ ] **Step 6: Commit**

```bash
git add tests/test_r0_question_bank_discipline.py src/agente_ia_edu/services/question_bank.py
git commit -m "feat: banco de questoes respeita o portao, na contagem e na pagina

Filtrar so a pagina faria o total mentir: a interface diria 48 e entregaria 12."
```

---

### Task 5: Busca de estudo

**Files:**
- Modify: `src/agente_ia_edu/services/study_search.py:201-311` (`search`)
- Test: `tests/test_r0_study_search_discipline.py` (criar)

**Interfaces:**
- Consumes: `DisciplineGate`, `DisciplineScope` (Task 2).
- Produces: `StudySearchService.search` passa a respeitar o portão quando recebe `session` e `institution_id` — parâmetros que ela **já tem**. Nenhuma assinatura muda.

**Esta tarefa não acrescenta parâmetro.** `search` já recebe `institution_id: str | None`. O portão usa esse valor. Quando `session is None`, `search` devolve o payload sem tocar no banco e continua fazendo isso — sem sessão não há portão a consultar, e não há resultado a filtrar.

- [ ] **Step 1: Escreva os testes que falham**

Crie `tests/test_r0_study_search_discipline.py`:

```python
# tests/test_r0_study_search_discipline.py
"""The gate applied to study search, through the institution_id it already takes."""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.services.study_search import StudySearchService

SCHOOL = str(uuid.uuid4())


class StudySearchDisciplineTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_without_session_it_still_returns_the_parsed_payload(self):
        result = await StudySearchService.search("cinetica quimica", session=None)
        self.assertIn("resolved_context", result)

    async def test_school_without_universe_is_not_filtered(self):
        async with self.session_factory() as session:
            result = await StudySearchService.search(
                "cinetica quimica", session=session, institution_id=SCHOOL
            )
        self.assertIn("resolved_context", result)


if __name__ == "__main__":
    unittest.main()
```

Acrescente ao mesmo arquivo o teste que prova o filtro, reusando o mesmo `_Fixture` da Task 4:

```python
from tests.test_question_bank_core import _Fixture

from agente_ia_edu.db.models.pedagogical_universe import (
    PedagogicalUniverse,
    PedagogicalUniverseCatalogScope,
)


class StudySearchRestrictedSchoolTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_restricted_school_gets_nothing_from_another_discipline(self):
        async with self.session_factory() as session:
            seeded = await _Fixture().build(session)
            biology = seeded["nodes"]["bio_content"]
            maths = seeded["nodes"]["math_content"]

            universe = PedagogicalUniverse(
                id=uuid.uuid4(), external_id="u", slug="u", name="u",
                owner_type="SCHOOL", owner_external_id=SCHOOL, status="ACTIVE",
            )
            session.add(universe)
            await session.flush()
            session.add(
                PedagogicalUniverseCatalogScope(
                    id=uuid.uuid4(), universe_id=universe.id,
                    catalog_node_id=biology.id, scope_kind="DISCIPLINE",
                    include_descendants=True,
                )
            )
            await session.commit()

            unrestricted = await StudySearchService.search(
                maths.code, session=session, institution_id=None
            )
            restricted = await StudySearchService.search(
                maths.code, session=session, institution_id=SCHOOL
            )

        self.assertGreater(
            len(unrestricted.get("questions") or []), 0,
            "the fixture must return something for this content when unrestricted",
        )
        self.assertEqual(
            restricted.get("questions") or [], [],
            "a school scoped to biology must get nothing from mathematics",
        )
```

**Confirme a chave antes de escrever as asserções.** Leia o que `StudySearchService.search` devolve quando recebe sessão — se os resultados não vierem sob `questions`, use a chave real. Se `search` não devolver nada para o código do fixture nem no caminho irrestrito, o teste está medindo a busca e não o portão: ajuste o termo de busca até o caminho irrestrito devolver conteúdo, senão a asserção do caminho restrito passa por vazio e não prova nada.

- [ ] **Step 2: Rode para ver falhar**

Run: `.venv/bin/python -m pytest tests/test_r0_study_search_discipline.py -q`
Expected: os dois primeiros passam desde já, porque o comportamento irrestrito é o atual; `test_restricted_school_gets_nothing_from_another_discipline` falha, porque nada filtra ainda.

- [ ] **Step 3: Implemente**

Em `search`, depois da guarda `if session is None: return payload` e antes de montar `KnowledgeService`, acrescente:

```python
        scope = await DisciplineGate(session).scope_for_school(institution_id)
```

Depois de coletar `questions` e `materials`, filtre ambas as listas pelo escopo, mantendo o item cujo nó de catálogo seja desconhecido:

```python
        if not scope.unrestricted:
            def _permitted(item: dict[str, Any]) -> bool:
                classification = item.get("classification") or {}
                return scope.permits_code(classification.get("content"))

            questions = [item for item in questions if _permitted(item)]
            materials = [item for item in materials if _permitted(item)]
```

`KnowledgeService.find_questions_by_content` devolve cada item com um bloco `classification` contendo `discipline`, `content` e `subcontent` (`knowledge.py:181-192`) — é o `content` que traz o código, e é por isso que se usa `permits_code` e não `permits`.

**Confirme que os materiais têm a mesma forma antes de aplicar o mesmo filtro aos dois.** Se a lista de materiais não trouxer `classification`, não invente a chave: filtre pelo campo que existir, ou deixe os materiais fora desta tarefa e **reporte** — entregar metade explicada é melhor que um filtro que não filtra.

- [ ] **Step 4: Rode para ver passar**

Run: `.venv/bin/python -m pytest tests/test_r0_study_search_discipline.py -q`
Expected: PASS, 3 testes.

- [ ] **Step 5: Rode os testes existentes da busca**

Run: `.venv/bin/python -m pytest tests/test_study_search*.py -q`
Expected: zero falhas.

- [ ] **Step 6: Commit**

```bash
git add tests/test_r0_study_search_discipline.py src/agente_ia_edu/services/study_search.py
git commit -m "feat: busca de estudo respeita o portao, pelo institution_id que ja recebia

Nenhuma assinatura muda: o parametro ja existia e nao era usado para nada."
```

---

### Task 6: O gate da fase

**Files:**
- Test: `tests/test_r0_fase2_gate.py` (criar)

**Interfaces:**
- Consumes: tudo das Tasks 2 a 5.
- Produces: nada. É um portão de verificação, não código de produção.

**Por que esta tarefa existe.** Cada tarefa anterior testou o serviço que alterou. Nenhuma testou a afirmação central da fase — que uma escola sem universo continua vendo tudo, **nos três serviços ao mesmo tempo**. É o tipo de afirmação em que se acredita em vez de conferir, e o custo de estar errado é a base inteira trancada no dia do deploy.

Na Fase 1 essa tarefa achou dois defeitos reais. Espere que ache.

- [ ] **Step 1: Escreva o gate**

Crie `tests/test_r0_fase2_gate.py`:

```python
# tests/test_r0_fase2_gate.py
"""Proves the phase's central claim across all three consumers at once.

Each earlier task tested the service it changed. None tested that a school
with no universe still sees everything everywhere - which is the state of
every school in production today, and the failure mode that would lock out
the entire base on deploy day.
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.identity import AuthenticatedUserContext
from agente_ia_edu.services.authorization import AuthorizationService
from agente_ia_edu.services.discipline_gate import DisciplineGate
from agente_ia_edu.services.question_bank import QuestionBankService
from agente_ia_edu.services.study_search import StudySearchService

SCHOOL_WITHOUT_UNIVERSE = str(uuid.uuid4())


class Fase2GateTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_a_school_with_no_universe_is_unrestricted_in_all_three(self):
        async with self.session_factory() as session:
            scope = await DisciplineGate(session).scope_for_school(
                SCHOOL_WITHOUT_UNIVERSE
            )
            self.assertTrue(scope.unrestricted, "the gate itself")

            allowed = await AuthorizationService(session).require_discipline(
                AuthenticatedUserContext(
                    user_id="u-1", external_identity_id="ext-1",
                    role="TEACHER", school_id=SCHOOL_WITHOUT_UNIVERSE,
                ),
                uuid.uuid4(),
            )
            self.assertTrue(allowed.allowed, "authorization")

            page = await QuestionBankService(session).list_questions(
                school_id=SCHOOL_WITHOUT_UNIVERSE
            )
            self.assertEqual(page.total, 0, "question bank ran unfiltered")

            found = await StudySearchService.search(
                "cinetica quimica",
                session=session,
                institution_id=SCHOOL_WITHOUT_UNIVERSE,
            )
            self.assertIn("resolved_context", found, "study search")

    async def test_no_consumer_refuses_on_absence(self):
        """The naive gate would raise instead of allowing. This is the test
        that would catch anyone rebuilding it on resolve_active_universe,
        which raises PermissionError when no universe exists."""
        async with self.session_factory() as session:
            for school in (None, SCHOOL_WITHOUT_UNIVERSE):
                with self.subTest(school=school):
                    scope = await DisciplineGate(session).scope_for_school(school)
                    self.assertTrue(scope.unrestricted)
                    self.assertTrue(scope.permits(uuid.uuid4()))
                    self.assertTrue(scope.permits(None))


if __name__ == "__main__":
    unittest.main()
```

Acrescente ao mesmo arquivo o espelho deste — o critério de aceite da §9 escrito palavra por palavra, nos três serviços de uma vez:

```python
from tests.test_question_bank_core import _Fixture

from agente_ia_edu.db.models.pedagogical_universe import (
    PedagogicalUniverse,
    PedagogicalUniverseCatalogScope,
)

RESTRICTED_SCHOOL = str(uuid.uuid4())


class Fase2RestrictedGateTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_a_restricted_school_gets_no_other_discipline_anywhere(self):
        """Spec section 9: a school restricted to one discipline receives no
        content from another - in search, in the bank, nor in authorization."""
        async with self.session_factory() as session:
            seeded = await _Fixture().build(session)
            biology = seeded["nodes"]["bio_content"]
            maths = seeded["nodes"]["math_content"]

            universe = PedagogicalUniverse(
                id=uuid.uuid4(), external_id="u", slug="u", name="u",
                owner_type="SCHOOL", owner_external_id=RESTRICTED_SCHOOL,
                status="ACTIVE",
            )
            session.add(universe)
            await session.flush()
            session.add(
                PedagogicalUniverseCatalogScope(
                    id=uuid.uuid4(), universe_id=universe.id,
                    catalog_node_id=biology.id, scope_kind="DISCIPLINE",
                    include_descendants=True,
                )
            )
            await session.commit()

            refused = await AuthorizationService(session).require_discipline(
                AuthenticatedUserContext(
                    user_id="u-1", external_identity_id="ext-1",
                    role="TEACHER", school_id=RESTRICTED_SCHOOL,
                ),
                maths.id,
            )
            self.assertFalse(refused.allowed, "authorization")

            page = await QuestionBankService(session).list_questions(
                school_id=RESTRICTED_SCHOOL
            )
            everything = await QuestionBankService(session).list_questions()
            self.assertLess(page.total, everything.total, "question bank")
            self.assertEqual(page.total, len(page.items), "count and page agree")

            found = await StudySearchService.search(
                maths.code, session=session, institution_id=RESTRICTED_SCHOOL
            )
            self.assertEqual(found.get("questions") or [], [], "study search")
```

- [ ] **Step 2: Rode**

Run: `.venv/bin/python -m pytest tests/test_r0_fase2_gate.py -q`
Expected: PASS.

**Se algum falhar, pare e reporte.** Não ajuste o teste e não mexa nos serviços para fazê-lo passar. Uma falha aqui é achado sobre cinco tarefas, não problema desta, e expô-la é o sucesso da tarefa.

- [ ] **Step 3: Rode o gate da fase inteiro**

Run: `.venv/bin/python -m pytest tests/test_r0_*.py tests/test_platform_administration.py tests/test_pedagogical_universe*.py -q`
Expected: zero falhas.

- [ ] **Step 4: Commit**

```bash
git add tests/test_r0_fase2_gate.py
git commit -m "test: prova que escola sem universo segue vendo tudo, nos tres servicos

Cada tarefa testou o servico que mudou. Nada testava a afirmacao central da
fase - e trancar a base inteira no dia do deploy e o custo de erra-la."
```

---

## Depois da última tarefa

Rode a suíte completa e compare com o baseline em `9b70e58`, contra o banco descartável `agente_ia_edu_r0_migcheck` na porta 5433. A Fase 1 declarou-se aditiva com três verificações estáticas limpas e tinha dezessete testes vermelhos; esta fase altera cinco serviços em uso, então a comparação não é opcional.

O baseline em `9b70e58` é `4 failed, 1907 passed, 2 skipped, 13 errors`, e as quatro falhas são pré-existentes e conhecidas: `test_phase9u1_production::test_local_migration_chain_is_valid` e `test_phase9u1e_q128_correction::test_production_executor_chain_still_valid` (ambas fixam `EXPECTED_SINGLE_HEAD` em `025`), `test_phase9u2_g4_generic_binding::test_registry_has_kinetics_plus_curriculum_v2` (espera 19, recebe 29) e `test_ingestion_classifier::test_13_isolation_between_documents` (dois documentos com o mesmo UUID).
