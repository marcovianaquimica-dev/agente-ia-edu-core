# R0 Fase 3C — Autorização, onda 8 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fecha 6 das 12 rotas apontadas pela revisão final da onda 3 ("identity injetada e nunca lida") — as que criam/mutam a estrutura curricular e a fila de curadoria de vídeo compartilhadas da plataforma, sem checagem de autorização nenhuma.

**Architecture:** Diferente de todas as sete ondas anteriores desta fase (que corrigiam funções de serviço checando *relação entre identidade e escola*), aqui o recurso protegido não é por-escola — é a estrutura curricular e o catálogo de vídeo, compartilhados por toda a plataforma. A correção troca `Depends(get_current_identity)` por `Depends(require_platform_admin)`, uma dependência FastAPI que já existe e já é usada por todas as rotas de `admin.py` — nenhuma lógica de autorização nova, só reaproveitar o portão que já protege o resto da administração da plataforma.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async, pytest/unittest.

**Spec:** `docs/superpowers/specs/2026-09-13-r0-estrutura-academica-configuracao-design.md` (§7, passo 5 — autorização, sozinha, por último).

## Global Constraints

- Python `>=3.13,<3.14` (de `pyproject.toml`). Nenhuma dependência nova. Nenhuma migração.
- Nenhuma coluna ou campo com formato de credencial (`password`, `token`, `secret`, `credential`, `senha`).
- Testes usam `unittest.IsolatedAsyncioTestCase`/`unittest.TestCase` com `sqlite+aiosqlite` + `StaticPool` (convenção já usada em `test_catalog.py` e nos testes `test_r0_*` desta fase) — nenhum teste precisa de banco real.
- Código e comentários em inglês; mensagem de commit em português.

---

## Contexto verificado por execução antes deste plano

### A varredura das 12 rotas

A onda 3 tinha contado 12 funções de rota com `identity` injetada e nunca referenciada no corpo, em `catalog.py`, `discovery.py` e `question_bank.py`, sem detalhar quais. Reproduzi a contagem com uma varredura AST (não grep) e cheguei exatamente nas mesmas 12:

```
catalog.py:326 create_discipline, catalog.py:368 create_content_node,
catalog.py:624 create_content_resource_link, catalog.py:1355 content_material_availability,
discovery.py:34 discover_videos, discovery.py:77 review_candidate, discovery.py:112 convert_candidate,
question_bank.py:145 list_questions, question_bank.py:201 get_question,
question_bank.py:215 preview_selection, question_bank.py:250 list_config_options,
question_bank.py:259 generate_list
```

Lendo cada uma, elas se dividem em três grupos:

1. **Não é bug (1 de 12):** `list_config_options` (nenhuma sessão de banco aberta — devolve só constantes). `identity` está lá só para exigir "autenticado", sem precisar de escopo — não corrigido.

   **Correção pós-revisão final:** este plano originalmente classificava `content_material_availability` (`catalog.py:1355`) no mesmo grupo, citando o comentário `# any authenticated identity may read this derived, non-sensitive count`. A revisão final provou que essa classificação está **errada**: o serviço que a rota chama, `MaterialAvailabilityService.for_content_codes`, tem seu próprio comentário dizendo explicitamente que **não é seguro** mostrar a um aluno específico, porque não checa `visibility_scope`/`school_id` — um material `PRIVATE` ou `SCHOOL`-scoped de outra escola entra na contagem exposta. `tests/manual/phase25_material_delivery_report.py` chama a mesma rota de "staff-facing". É um vazamento de existência/cardinalidade entre escolas (só um número e um booleano, sem título ou id — severidade baixa), mas o comentário da rota está simplesmente errado. Movido para a seção "próxima onda" abaixo — **não é mais citável como precedente de "sem restrição por design"**.
2. **Escrita sem NENHUMA checagem, escopo desta onda (6 de 12):** `create_discipline`, `create_content_node`, `create_content_resource_link` (`catalog.py`) e `discover_videos`, `review_candidate`, `convert_candidate` (`discovery.py`) — qualquer identidade autenticada cria/muta a estrutura curricular compartilhada ou a fila de curadoria de vídeo. Confirmado pelo usuário: exigir só `PLATFORM_ADMIN`.
3. **Achado diferente, fora do escopo desta onda por decisão do usuário (4 de 12, em `question_bank.py`):** `list_questions`, `get_question`, `preview_selection`, `generate_list` nunca resolvem `identity` → `school_id` para o `DisciplineGate` (o "portão de disciplina" da R0 Fase 2 — ausência de `school_id` significa acesso irrestrito por design, não vazamento no sentido das ondas 1-7). Registrado para uma onda futura, não corrigido aqui.

### O portão reutilizável

`require_platform_admin` já existe em `src/agente_ia_edu/api/routes/admin.py:37-54` — uma dependência FastAPI que aceita a identidade se `"PLATFORM_ADMIN"` estiver em `identity.roles`, ou se `identity.external_user_id` for literalmente `"ADMIN"`/`"PLATFORM_ADMIN"` (case-insensitive), ou, por último, consultando `PlatformAdminService.is_platform_admin`. Já é usada por mais de 15 rotas em `admin.py`. Confirmado sem risco de import circular: `admin.py` não importa nada de `catalog.py` ou `discovery.py`.

### Testes existentes que dependiam do comportamento atual

`tests/test_catalog.py::CatalogApiTests` tem 3 métodos que chamam as 3 rotas de `catalog.py` via HTTP (`TestClient`) com `_auth("teacher1")` — que produz `Authorization: Bearer student:teacher1`, uma identidade no formato de ALUNO — e esperam sucesso (`201`). Isso prova que hoje literalmente qualquer identidade autenticada, mesmo uma no formato de aluno, cria disciplinas/nós de conteúdo/vínculos de recurso. Esses 3 testes precisam trocar para uma identidade `PLATFORM_ADMIN` (`Authorization: Bearer platform_admin:admin` — o provedor de teste (`TestExternalIdentityProvider`) usa o formato `role:identifier`, então isso produz `roles=("platform_admin",)`, que `require_platform_admin` aceita). `discovery.py` não tem nenhum teste HTTP hoje — as 3 rotas afetadas ganham uma classe de teste HTTP nova, no mesmo padrão de `test_catalog.py::CatalogApiTests`.

Confirmado por grep: nenhum outro arquivo de teste no repositório chama nenhuma das 6 rotas (nem via HTTP, nem chamando as funções Python diretamente).

### Verificação por execução

A correção foi aplicada de verdade e verificada: `tests/test_catalog.py` (43/43, incluindo 2 testes negativos novos), `tests/test_video_discovery.py` (18/18, incluindo 4 testes novos de autorização HTTP), gate da fase + as duas suítes (291 passed, 87 subtests), e a suíte completa contra um banco descartável reconstruído do zero (**2033 passed, 0 falhas, mesmos 13 erros ambientais pré-existentes de sempre**). Tudo revertido antes de escrever este plano.

---

## Task 1: Gatilha as 6 rotas de escrita atrás de `require_platform_admin`

**Files:**
- Modify: `src/agente_ia_edu/api/routes/catalog.py` (import + 3 assinaturas de rota)
- Modify: `src/agente_ia_edu/api/routes/discovery.py` (import + 3 assinaturas de rota)
- Modify: `tests/test_catalog.py` (3 testes existentes trocam de identidade + 2 testes negativos novos)
- Modify: `tests/test_video_discovery.py` (1 classe de teste HTTP nova, 4 métodos)

**Interfaces:**
- Consumes: `require_platform_admin` (já existe em `admin.py`, assinatura `async def require_platform_admin(identity: ExternalIdentityContext = Depends(get_current_identity), session_factory=Depends(get_session_factory)) -> ExternalIdentityContext`, inalterada).
- Produces: as 6 rotas mantêm a mesma assinatura pública de resposta — só a fonte de `identity` muda. Nenhum chamador externo (frontend, outros serviços) precisa mudar código, só passar a autenticar como `PLATFORM_ADMIN` para essas 6 chamadas especificamente.

- [ ] **Step 1: Escreva os testes negativos que falham**

Em `tests/test_catalog.py`, adicione este helper logo depois de `_auth`:

```python
def _admin_auth() -> dict:
    return {"Authorization": "Bearer platform_admin:admin"}
```

Adicione este teste dentro da classe `CatalogApiTests`, logo depois de `test_create_and_list_disciplines`:

```python
    def test_create_discipline_denies_non_platform_admin(self):
        resp = self.client.post(
            "/api/v1/catalog/disciplines",
            json={"name": "Biologia", "node_type": "DISCIPLINE"},
            headers=_auth("teacher1"),
        )
        self.assertEqual(resp.status_code, 403)
```

E este, logo depois de `test_content_tree_and_resource_query_via_api` (que termina com o `assertEqual(len(resources_resp.json()["links"]), 1)`):

```python
    def test_create_content_resource_link_denies_non_platform_admin(self):
        resp = self.client.post(
            "/api/v1/catalog/content-resource-links",
            json={
                "content_node_id": str(uuid4()),
                "resource_id": str(uuid4()),
                "pedagogical_role": "VIDEO",
            },
            headers=_auth("teacher1"),
        )
        self.assertEqual(resp.status_code, 403)
```

Em `tests/test_video_discovery.py`, adicione ao final do arquivo (antes de `if __name__ == "__main__":`), como um bloco novo — este arquivo ainda não tem nenhum teste HTTP, só de serviço:

```python
from fastapi.testclient import TestClient

from agente_ia_edu.api.app import app
from agente_ia_edu.api.dependencies import get_session_factory


def _auth(subject: str) -> dict:
    return {"Authorization": f"Bearer {subject}"}


class DiscoveryApiAuthorizationTests(unittest.TestCase):
    """The three discovery routes curate the shared, platform-wide video
    catalog - discover_videos, review_candidate, convert_candidate all
    injected identity and never read it, so any authenticated caller could
    queue/approve/convert candidates into the catalog. Gated behind
    require_platform_admin, same dependency already used by admin.py's
    own routes."""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        cls.session_factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _init():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.run(_init())
        app.dependency_overrides[get_session_factory] = lambda: cls.session_factory
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()
        asyncio.run(cls.engine.dispose())

    def test_discover_videos_denies_non_platform_admin(self):
        resp = self.client.post(
            "/api/v1/discovery/search",
            json={"query": "Diluição de soluções"},
            headers=_auth("student:someone"),
        )
        self.assertEqual(resp.status_code, 403)

    def test_review_candidate_denies_non_platform_admin(self):
        resp = self.client.post(
            "/api/v1/discovery/review",
            json={"candidate_id": str(uuid4()), "action": "APPROVE"},
            headers=_auth("teacher:someone"),
        )
        self.assertEqual(resp.status_code, 403)

    def test_convert_candidate_denies_non_platform_admin(self):
        resp = self.client.post(
            "/api/v1/discovery/convert",
            json={"candidate_id": str(uuid4()), "content_node_id": str(uuid4())},
            headers=_auth("coordinator:someone"),
        )
        self.assertEqual(resp.status_code, 403)

    def test_discover_videos_allows_platform_admin(self):
        resp = self.client.post(
            "/api/v1/discovery/search",
            json={"query": "Diluição de soluções"},
            headers=_auth("platform_admin:admin"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
```

`asyncio`, `unittest`, `async_sessionmaker`, `AsyncSession`, `create_async_engine`, `StaticPool`, `Base`, `uuid4` já estão importados no topo do arquivo — só faltam `TestClient`, `app` e `get_session_factory`, importados acima junto com a classe (ou mova para o topo do arquivo junto dos outros imports, tanto faz).

- [ ] **Step 2: Rode para confirmar que os testes negativos falham**

Run: `.venv/bin/python -m pytest tests/test_catalog.py::CatalogApiTests::test_create_discipline_denies_non_platform_admin tests/test_video_discovery.py::DiscoveryApiAuthorizationTests -v`
Expected: `test_create_discipline_denies_non_platform_admin` e os três `..._denies_non_platform_admin` de `DiscoveryApiAuthorizationTests` FALHAM (código atual retorna sucesso, não 403). `test_discover_videos_allows_platform_admin` já PASSA contra o código atual (qualquer identidade é aceita hoje) — não precisa ser confirmado como falhando.

- [ ] **Step 3: Gatilhe as 3 rotas de `catalog.py`**

Em `src/agente_ia_edu/api/routes/catalog.py`, adicione o import logo depois de `from ..dependencies import get_current_identity, get_session_factory`:

```python
from .admin import require_platform_admin
```

Troque `identity: ExternalIdentityContext = Depends(get_current_identity),` por `identity: ExternalIdentityContext = Depends(require_platform_admin),` nas assinaturas de `create_discipline`, `create_content_node` e `create_content_resource_link` (as três funções nomeadas na seção "Contexto" acima — `get_current_identity` continua importado e usado por todas as outras rotas do arquivo, não remova o import).

- [ ] **Step 4: Gatilhe as 3 rotas de `discovery.py`**

Em `src/agente_ia_edu/api/routes/discovery.py`, adicione o import logo depois de `from ..dependencies import get_current_identity, get_session_factory`:

```python
from .admin import require_platform_admin
```

Troque `identity: ExternalIdentityContext = Depends(get_current_identity),` por `identity: ExternalIdentityContext = Depends(require_platform_admin),` nas três funções do arquivo: `discover_videos`, `review_candidate`, `convert_candidate` (são as três únicas rotas do arquivo).

- [ ] **Step 5: Troque a identidade dos 3 testes existentes que agora precisam ser `PLATFORM_ADMIN`**

Em `tests/test_catalog.py`, dentro de `CatalogApiTests`:
- `test_create_and_list_disciplines`: troque `headers=_auth("teacher1"),` por `headers=_admin_auth(),` na chamada a `POST /api/v1/catalog/disciplines`.
- `test_create_node_requires_parent`: troque `headers=_auth("teacher1"),` por `headers=_admin_auth(),` na chamada a `POST /api/v1/catalog/nodes`.
- `test_content_tree_and_resource_query_via_api`: troque `headers=_auth("teacher1"),` por `headers=_admin_auth(),` nas DUAS chamadas a `POST /api/v1/catalog/disciplines` e `POST /api/v1/catalog/nodes`, e na chamada a `POST /api/v1/catalog/content-resource-links`. **NÃO troque** a chamada a `POST /api/v1/catalog/resources` no mesmo teste — essa rota não faz parte desta tarefa, continua com `_auth("teacher1")`.

- [ ] **Step 6: Rode para confirmar que tudo passa**

Run: `.venv/bin/python -m pytest tests/test_catalog.py tests/test_video_discovery.py -v`
Expected: PASS, 43 testes em `test_catalog.py` (41 já existentes + 2 novos) e 18 em `test_video_discovery.py` (14 já existentes + 4 novos). Verificado por execução antes de escrever este plano.

- [ ] **Step 7: Rode o gate inteiro da fase**

Run: `.venv/bin/python -m pytest tests/test_r0_*.py tests/test_platform_administration.py tests/test_coordination_portal.py tests/test_teacher_portal.py tests/test_teaching_context.py tests/test_end_to_end_learning_flow.py tests/test_catalog.py tests/test_video_discovery.py -q`
Expected: zero falhas, 291 passed, 87 subtests.

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/api/routes/catalog.py src/agente_ia_edu/api/routes/discovery.py tests/test_catalog.py tests/test_video_discovery.py
git commit -m "fix: exige PLATFORM_ADMIN para criar catalogo e curar fila de video

create_discipline, create_content_node, create_content_resource_link
(catalog.py) e discover_videos, review_candidate, convert_candidate
(discovery.py) injetavam identity e nunca a liam - qualquer identidade
autenticada, mesmo uma no formato de aluno, criava/mutava a estrutura
curricular compartilhada da plataforma e a fila de curadoria de video.
Provado pelo proprio teste existente: test_create_and_list_disciplines ja
chamava POST /disciplines com Bearer student:teacher1 e esperava 201.

Gatilhadas atras de require_platform_admin, a mesma dependencia que ja
protege as mais de 15 rotas de admin.py - nenhuma logica de autorizacao
nova, so reaproveitar o portao que ja existe.

Achado pela revisao final da onda 3 desta fase (12 rotas com identity
nunca lida); esta onda fecha as 6 que sao escrita sem checagem nenhuma. As
outras 6 (2 legitimamente nao precisam de escopo, 4 sao sobre o portao de
disciplina da R0 Fase 2, nao sobre esta autorizacao) ficam fora."
```

---

## Depois da última tarefa

Rode o gate desta fase e a suíte completa contra um banco descartável, comparando com o baseline pós-onda-7 (2033 passed, 4 skipped, 448 subtests, 13 erros ambientais pré-existentes — esse número já inclui os 6 testes novos desta onda). Esta onda não deveria mover o número além disso.

## O que fica para a próxima onda de 3C

**Quatro achados novos da revisão final desta onda:**

- **`content_material_availability` (`catalog.py:1355`) vaza existência/cardinalidade entre escolas.** Ver a correção na seção "Contexto verificado" acima — a rota chama `MaterialAvailabilityService.for_content_codes`, que o próprio serviço documenta como não-seguro para exposição direta (não checa `visibility_scope`/`school_id`). Fix sugerido pela revisão: trocar para `resolve_for_one(content_code, requester_school_id=...)` (mesmo serviço), ou aplicar a checagem de papel que `create_material` já usa no mesmo arquivo.
- **`create_resource` (`catalog.py:454`) lê `identity` só para atribuição, nunca para autorização.** Qualquer identidade autenticada cria um `EducationalResource` com qualquer `owner_external_id`/`visibility_scope` — a varredura AST desta onda e da onda 3 não pegou porque `identity` É referenciada no corpo (só não para checar nada). `tests/test_catalog.py` ainda posta essa rota com `Bearer student:teacher1` e recebe 201.
- **`get_resources_for_content` (`catalog.py:655`) nem tem parâmetro `identity`.** Devolve todo `ContentResourceLink` de um nó, sem filtro de visibilidade nenhum, para qualquer chamador (nem precisa estar autenticado). É a contraparte de leitura exata da rota (`create_content_resource_link`) que esta onda acabou de fechar para escrita.
- **A metodologia da varredura precisa cobrir três formatos, não um só:** "identity nunca referenciada no corpo" (o que a onda 3 e esta onda usaram), "sem parâmetro `identity` nenhum" (o caso de `get_resources_for_content`), e "identity lida só para atribuição/auditoria, nunca para autorização" (o caso de `create_resource`). Os dois últimos formatos não aparecem em nenhuma contagem anterior desta fase.

**Uma pergunta em aberto, não um bug:** `create_content_resource_link` exigir `PLATFORM_ADMIN` pode ser restritivo demais — `EducationalResource` (ao contrário de `CatalogNode`) tem `owner_external_id`/`visibility_scope` próprios, e `ContentResourceLink` não tem coluna de dono nenhuma, então anexar um recurso de escola ao nó curricular global só pode ser expresso através dessa rota. `create_material` (mesmo arquivo) já usa um idioma mais fino (`authz.require_role(TEACHER, COORDINATOR, DIRECTOR, PLATFORM_ADMIN)`) que talvez sirva melhor aqui. Decisão do usuário nesta onda foi `PLATFORM_ADMIN` para os dois grupos — não revertido, só registrado para a próxima onda avaliar se um papel mais fino é necessário.

Restam também, do que já foi mapeado antes desta onda:

- A ausência de branch para papel `STUDENT` em `verify_teacher_classroom_scope` (onda 3) — confirmado sem consumidor no repo até a onda 3, não reconfirmado desde então.
- `coordination_portal.py`'s fallback de dado fabricado ("Prof. Mendes") por volta da linha 598-609 — não é bypass de autorização, é dado de demonstração; fica para quando alguém for limpar placeholders.
- O acoplamento entre o andaime `TURMA_3A` e o guard de `verify_student_access` (onda 7) — comentado no código, mas ainda não resolvido de verdade.
- A mudança de comportamento em `_resolve_scope_classrooms` para coordenadores `GRADE_LEVEL`/`UNIT` (onda 7) — sem teste cobrindo.
- A assimetria `SCHOOL`/`PLATFORM` pré-existente em `_fetch_students_in_classrooms` (onda 7).
- `knowledge.py::_is_question_visible`, `question_governance.py`, `reception.py`, `teacher_materials.py`, `assessments.py`, `study_session.py` — os seis arquivos nomeados desde a onda 2 desta fase, nunca lidos linha a linha para o mesmo padrão.
- A instabilidade de ordem de execução em `test_phase8a_teacher_list_builder_postgresql.py` (achada na onda 7) — não é autorização, mas fica registrada.
