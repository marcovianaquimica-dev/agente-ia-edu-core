# Tela de entrada com dois ambientes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Substituir a entrada única do aluno por uma tela que resolve, a partir da identidade do aluno, quais módulos (`AGENTE_IA_EDU`/`REDACAO_IA`) a escola dele tem habilitados, e roteia para o ambiente certo — pulando a escolha quando só um módulo está habilitado, mostrando 2 cards quando os dois estão.

**Architecture:** Uma nova rota de backend (`GET /api/v1/student/modules`) expõe o que já existe em `AuthorizationService.resolve_context(...).modules`. Duas novas páginas estáticas (`entrada.html`, `redacao.html`) montadas em `app.py` seguindo o padrão já usado por `teacher.html`/`coordination.html`. `redacao.html` reaproveita os arquivos `essay*.js` já existentes sem alterá-los (só um novo `<script>window.EssayView.init()</script>` chamando-os direto). `index.html`/`app.js` perdem a aba "Redação" aninhada.

**Tech Stack:** Python/FastAPI (backend), JS vanilla + HTML/CSS server-side estático (frontend), pytest (backend), `node --test` (frontend, onde já há infraestrutura equivalente).

**Spec:** [docs/superpowers/specs/2026-09-28-entrada-dois-ambientes-design.md](../specs/2026-09-28-entrada-dois-ambientes-design.md)

## Global Constraints

- Nunca editar `essay.js`, `essay-annotations.js`, `essay-evolution.js`, `essay-report.js` — só referenciá-los.
- Nunca editar `reception.html`/`reception.js`.
- Nunca fabricar um valor de módulo habilitado — fonte de verdade é sempre `SchoolModule`/`AuthorizationService`.
- Identidade continua via `sessionStorage.studentAccessToken` + `TestExternalIdentityProvider` — nenhum mecanismo de sessão novo.
- TDD obrigatório para toda lógica de backend nova (RED antes de qualquer código de produção).
- Antes de rodar a suíte completa, `ps aux | grep pytest` e esperar qualquer execução concorrente terminar (Postgres descartável compartilhado na porta 5433, várias sessões podem estar ativas).
- Nenhum `git commit`/`git push` sem o gatilho explícito do usuário — deixar tudo no working tree.

---

### Task 1: Rota de backend `GET /api/v1/student/modules`

**Files:**
- Modify: `src/agente_ia_edu/api/routes/student.py` (novo endpoint, seguir exatamente o padrão de `get_student_dashboard`, linhas ~28-61)
- Modify: `src/agente_ia_edu/api/schemas/student.py` (novo `StudentModulesResponse`)
- Test: `tests/test_student_route_coverage_http.py` (ou um novo arquivo `tests/test_student_modules_http.py`, seguir o padrão de fixtures já usado nos testes HTTP existentes de `student.py` — checar qual arquivo já cobre rotas simples desse mesmo router antes de decidir)

**Interfaces:**
- Consumes: `AuthorizationService(session).resolve_context(identity) -> AuthenticatedUserContext` (já existe, `src/agente_ia_edu/services/authorization.py:72`) — `.modules` é uma `tuple[str, ...]` com as chaves de módulo habilitadas (ex.: `("AGENTE_IA_EDU",)`, `("AGENTE_IA_EDU", "REDACAO_IA")`, ou `()` vazio).
- Produces: `GET /api/v1/student/modules` → `200 {"AGENTE_IA_EDU": bool, "REDACAO_IA": bool}` — usado pela Task 2 (`entrada.js`).

- [ ] **Step 1: Escrever o teste RED**

```python
# tests/test_student_modules_http.py (ajustar import/fixture pro padrão real do projeto)
import pytest
from httpx import AsyncClient, ASGITransport

from agente_ia_edu.api.app import app


@pytest.mark.asyncio
async def test_modules_endpoint_reflects_real_school_modules(seeded_school_with_both_modules):
    # seeded_school_with_both_modules: fixture que cria uma escola real com
    # SchoolModule(AGENTE_IA_EDU, enabled=True) e SchoolModule(REDACAO_IA, enabled=True),
    # e um UserSchoolLink STUDENT ativo pra essa escola - reaproveitar o padrão de
    # seed já usado em outros testes HTTP de student.py (grep por "UserSchoolLink" nesse arquivo).
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/student/modules",
            headers={"Authorization": "Bearer student:<external_user_id da fixture>"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"AGENTE_IA_EDU": True, "REDACAO_IA": True}


@pytest.mark.asyncio
async def test_modules_endpoint_only_one_enabled(seeded_school_with_one_module):
    ...  # mesma estrutura, escola com só AGENTE_IA_EDU habilitado
    # assert resp.json() == {"AGENTE_IA_EDU": True, "REDACAO_IA": False}


@pytest.mark.asyncio
async def test_modules_endpoint_no_school_module_rows_defaults_to_all_false(seeded_school_no_modules):
    ...  # escola sem nenhum SchoolModule configurado
    # assert resp.json() == {"AGENTE_IA_EDU": False, "REDACAO_IA": False}
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_student_modules_http.py -v`
Expected: FAIL com 404 (rota não existe) ou `ImportError`/`AttributeError` — nunca um erro de fixture quebrada.

- [ ] **Step 3: Implementar o schema**

```python
# src/agente_ia_edu/api/schemas/student.py (adicionar)
class StudentModulesResponse(BaseModel):
    AGENTE_IA_EDU: bool
    REDACAO_IA: bool
```

- [ ] **Step 4: Implementar a rota**

```python
# src/agente_ia_edu/api/routes/student.py (adicionar; import AuthorizationService no topo do arquivo)
from ...services.authorization import AuthorizationService
from ..schemas.student import StudentModulesResponse  # juntar ao import já existente de schemas.student

@student_router.get(
    "/modules",
    response_model=StudentModulesResponse,
    summary="Get which platform modules are enabled for the student's own school",
)
async def get_student_modules(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> StudentModulesResponse:
    async with session_factory() as session:
        authz = AuthorizationService(session)
        context = await authz.resolve_context(identity)
        enabled = set(context.modules)
        return StudentModulesResponse(
            AGENTE_IA_EDU="AGENTE_IA_EDU" in enabled,
            REDACAO_IA="REDACAO_IA" in enabled,
        )
```

- [ ] **Step 5: Rodar e confirmar GREEN**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_student_modules_http.py -v`
Expected: PASS nos 3 casos.

- [ ] **Step 6: Rodar a suíte completa isolada**

Run: (depois de confirmar `ps aux | grep pytest` limpo) `PYTHONPATH=src .venv/bin/python -m pytest tests/ -q`
Expected: 100% verde, sem novas falhas.

---

### Task 2: Novas páginas `entrada.html`/`entrada.js` e `redacao.html` + mounts em `app.py`

**Files:**
- Create: `src/agente_ia_edu/web/entrada.html`
- Create: `src/agente_ia_edu/web/entrada.js`
- Create: `src/agente_ia_edu/web/entrada.css` (ou reaproveitar `styles.css` já compartilhado — checar se `styles.css` já cobre o necessário antes de criar um arquivo novo)
- Create: `src/agente_ia_edu/web/redacao.html`
- Modify: `src/agente_ia_edu/api/app.py` (novos mounts + rotas `/entrada` e `/redacao`, linhas ~87-91 e ~128 como referência de onde inserir, seguindo EXATAMENTE o padrão de `/teacher`/`/coordination` já existente)
- Test: se houver infraestrutura de teste JS pro padrão de contrato-de-fetch já usado (grep `tests/` por `_frontend.js`), criar `tests/test_entrada_frontend.js` seguindo o mesmo padrão; senão, verificação real via navegador na Task 4 é suficiente.

**Interfaces:**
- Consumes: `GET /api/v1/student/modules` (Task 1) → `{"AGENTE_IA_EDU": bool, "REDACAO_IA": bool}`.
- Consumes: `window.EssayView.init()` (já existe em `essay.js`, não modificado).
- Produces: `/entrada` (ponto de entrada novo do aluno), `/redacao` (ambiente separado de redação) — consumidos pela Task 3 (link "Trocar de ambiente").

- [ ] **Step 1: Criar `entrada.html`**

Estrutura mínima (adaptar visual pro padrão já usado em `teacher.html`/`coordination.html` - mesma fonte, mesma paleta, ver `styles.css`):

```html
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AGENTE IA EDU — Entrada</title>
  <base href="/entrada/assets/">
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <main class="entrada-container">
    <div class="entrada-identity-box">
      <label for="entrada-student-id">Identidade do aluno</label>
      <input type="text" id="entrada-student-id" placeholder="student:alice">
      <button id="entrada-confirm-btn" type="button">Entrar</button>
    </div>
    <div id="entrada-error" class="entrada-error" style="display:none;"></div>
    <div id="entrada-cards" class="entrada-cards" style="display:none;">
      <button class="entrada-card" data-env="agente">
        <h2>Agente IA Edu</h2>
        <p>Diagnóstico, trilha de estudos, prática e materiais.</p>
      </button>
      <button class="entrada-card" data-env="redacao">
        <h2>Plataforma de Redação</h2>
        <p>Treino de redação no padrão ENEM.</p>
      </button>
    </div>
  </main>
  <script src="entrada.js"></script>
</body>
</html>
```

- [ ] **Step 2: Criar `entrada.js`**

Seguir o padrão de `studentHeaders()`/fetch já usado em `app.js` (sem reinventar convenção de erro/loading):

```js
document.addEventListener('DOMContentLoaded', () => {
  const idInput = document.getElementById('entrada-student-id');
  const confirmBtn = document.getElementById('entrada-confirm-btn');
  const errorBox = document.getElementById('entrada-error');
  const cardsBox = document.getElementById('entrada-cards');

  function showError(msg) {
    errorBox.textContent = msg;
    errorBox.style.display = 'block';
    cardsBox.style.display = 'none';
  }

  confirmBtn.addEventListener('click', async () => {
    const studentId = idInput.value.trim();
    if (!studentId) return;
    sessionStorage.setItem('studentAccessToken', studentId);
    errorBox.style.display = 'none';

    let resp;
    try {
      resp = await fetch('/api/v1/student/modules', {
        headers: { 'Authorization': `Bearer ${studentId}` },
      });
    } catch (e) {
      showError('Não foi possível conectar ao servidor. Tente novamente.');
      return;
    }
    if (!resp.ok) {
      showError('Não foi possível confirmar sua identidade. Verifique com a coordenação.');
      return;
    }
    const modules = await resp.json();
    const enabled = Object.entries(modules).filter(([, v]) => v).map(([k]) => k);

    if (enabled.length === 0) {
      showError('Nenhum módulo habilitado para sua escola. Fale com a coordenação.');
    } else if (enabled.length === 1) {
      window.location.href = enabled[0] === 'AGENTE_IA_EDU' ? '/student' : '/redacao';
    } else {
      cardsBox.style.display = 'grid';
    }
  });

  cardsBox.addEventListener('click', (e) => {
    const card = e.target.closest('.entrada-card');
    if (!card) return;
    window.location.href = card.dataset.env === 'agente' ? '/student' : '/redacao';
  });
});
```

- [ ] **Step 3: Criar `entrada.css`** (se decidir não reaproveitar `styles.css` sozinho)

Estilo mínimo pros dois cards lado a lado, seguindo as variáveis de cor/fonte já definidas em `styles.css` (não inventar paleta nova).

- [ ] **Step 4: Criar `redacao.html`**

```html
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AGENTE IA EDU — Redação</title>
  <base href="/redacao/assets/">
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <header class="redacao-header">
    <div class="redacao-brand">AGENTE IA <span>EDU</span> — Redação</div>
    <a href="/entrada" class="redacao-switch-link">Trocar de ambiente</a>
  </header>
  <div id="essay-root" aria-live="polite"></div>
  <script src="essay-annotations.js"></script>
  <script src="essay-report.js"></script>
  <script src="essay-evolution.js"></script>
  <script src="essay.js"></script>
  <script>window.EssayView.init();</script>
</body>
</html>
```

Nenhum dos 4 `<script src="essay-*.js">` é editado — só referenciado, exatamente como já acontece em `index.html`.

- [ ] **Step 5: Adicionar mounts e rotas em `app.py`**

```python
# adicionar junto aos mounts existentes (~linha 91, depois de admin-assets)
app.mount("/entrada/assets", StaticFiles(directory=str(web_dir), html=False), name="entrada-assets")
app.mount("/redacao/assets", StaticFiles(directory=str(web_dir), html=False), name="redacao-assets")

# adicionar junto às rotas existentes (~linha antes do "return app")
@app.get("/entrada", include_in_schema=False)
@app.get("/entrada/", include_in_schema=False)
async def serve_entrada():
    return FileResponse(web_dir / "entrada.html")

@app.get("/redacao", include_in_schema=False)
@app.get("/redacao/", include_in_schema=False)
async def serve_redacao():
    return FileResponse(web_dir / "redacao.html")
```

- [ ] **Step 6: Verificação manual mínima (sem depender da Task 1 já estar mergeada no mesmo processo rodando)**

Suba o dev server (`preview_start` ou equivalente), acesse `/entrada` e `/redacao` diretamente, confirme que carregam sem erro 404/500 e sem erro de console por script faltando. Não é preciso o fluxo completo funcionar ainda (isso é a Task 4) — só confirmar que as páginas novas servem corretamente.

---

### Task 3: Remover a aba "Redação" de `index.html`/`app.js` + link "Trocar de ambiente"

**Files:**
- Modify: `src/agente_ia_edu/web/index.html` (remover nav item, section, scripts de essay)
- Modify: `src/agente_ia_edu/web/app.js` (remover entrada `'essay'` do mapa de views e a chamada de init)

**Interfaces:**
- Consumes: `/entrada` (Task 2) — destino do novo link "Trocar de ambiente".
- Produces: nenhum consumidor downstream além da Task 4 (verificação).

- [ ] **Step 1: Remover de `index.html`**

Remover (ler o arquivo primeiro para confirmar os números de linha exatos no momento da execução, podem ter mudado):
- `<button class="nav-item" data-view="essay">...</button>` (nav lateral)
- `<section id="view-essay" class="view-panel">...</section>` (linhas ~579-580)
- Os 4 `<script src="essay-*.js">` (linhas ~601-604)

Adicionar um item de navegação simples no lugar do antigo botão de essay (ou em outro ponto do menu, à critério de quem implementar, mantendo o padrão visual):

```html
<a class="nav-item" href="/entrada">
  <span class="icon">🔀</span> Trocar de ambiente
</a>
```

- [ ] **Step 2: Remover de `app.js`**

Remover:
- `'essay': { title: 'Módulo Redação IA', sub: 'Treino de redação no padrão ENEM' },` (linha ~130)
- `if (viewName === 'essay') window.EssayView.init();` (linha ~144)

- [ ] **Step 3: Verificar que nada mais em `app.js` referencia `data-view="essay"` ou `EssayView`**

Run: `grep -n "essay\|Essay" src/agente_ia_edu/web/app.js`
Expected: nenhum resultado (ou só resultados claramente não relacionados, revisar manualmente).

- [ ] **Step 4: Rodar testes de frontend estáticos existentes que tocam `app.js`**

Run: `node --test tests/test_*frontend*.js` (os que cobrem `app.js` especificamente - grep primeiro pra saber quais)
Expected: nenhuma quebra por causa da remoção (se algum teste antigo afirmava a existência da aba essay dentro do app.js, corrigir a asserção, documentando por quê — mesmo padrão já usado várias vezes nesta sessão).

---

### Task 4: Verificação de ponta a ponta (bloqueada até Tasks 1-3 estarem todas prontas)

**Files:** nenhum arquivo de produção novo — só verificação.

**Interfaces:**
- Consumes: tudo das Tasks 1-3.

- [ ] **Step 1: Rodar a suíte completa isolada**

Run: (confirmar `ps aux | grep pytest` limpo antes) `PYTHONPATH=src .venv/bin/python -m pytest tests/ -q`
Expected: 100% verde.

- [ ] **Step 2: Cenário "só um módulo habilitado"**

No navegador real (dev server rodando), usando uma escola de dev com só `AGENTE_IA_EDU` habilitado (confirmar/ajustar via a tela de admin já existente, ou via `scripts/seed_demo_data.py`/`scripts/seed_escola_abc.py` se for preciso um estado novo): acessar `/entrada`, digitar a identidade, confirmar redirecionamento automático e direto pra `/student`, sem tela de cards.

- [ ] **Step 3: Cenário "os dois módulos habilitados"**

Habilitar `REDACAO_IA` pra uma escola de dev (via tela de admin já existente — `admin.js`, seção de módulos, já auditada e funcional). Acessar `/entrada`, digitar identidade, confirmar que os 2 cards aparecem, clicar em "Plataforma de Redação", confirmar navegação pra `/redacao` com o módulo de redação funcionando (ex.: abrir a lista de propostas de redação, se houver alguma disponível pro aluno de teste). Clicar em "Trocar de ambiente", confirmar volta pra `/entrada`, escolher "Agente IA Edu", confirmar `/student` carrega sem a aba de redação no menu.

- [ ] **Step 4: Confirmar via `read_network_requests` que `GET /api/v1/student/modules` foi realmente chamado (não um valor mockado no frontend)**

- [ ] **Step 5: Capturar screenshot de cada tela nova (`/entrada` com 1 card, `/entrada` com 2 cards, `/redacao`) como evidência**

---

## Self-Review

1. **Cobertura do spec:** §2.2 (rota nova) → Task 1. §2.3 (`/entrada`) e §2.4 (`/redacao`) → Task 2. §2.5 (remoção da aba) → Task 3. §3-4 (fluxos) → Task 4. §5 (erro) → coberto nos steps de `entrada.js` (Task 2, Step 2) e nos testes da Task 1 (caso "nenhum módulo"). §6 (testes) → coberto em cada task. §7 (restrições globais) → repetidas no cabeçalho do plano.
2. **Placeholders:** nenhum "implementar depois"/"adicionar validação" vago — cada step tem código ou comando concreto. Os poucos pontos deixados como "critério de quem implementar" (posição exata do link "Trocar de ambiente", se `entrada.css` reaproveita `styles.css`) são decisões de estilo sem impacto funcional, não lacunas de comportamento.
3. **Consistência de tipos:** `StudentModulesResponse` (Task 1) tem exatamente os 2 campos que `entrada.js` (Task 2) espera (`AGENTE_IA_EDU`, `REDACAO_IA`, ambos bool) — mesmos nomes nos dois lados.

## Execution Handoff

Plano salvo em `docs/superpowers/plans/2026-09-28-entrada-dois-ambientes.md`. Duas opções:

**1. Subagent-Driven (recomendado, usuário pediu multiagentes)** — Task 1 e Task 2 são independentes entre si (interface já documentada acima, cada uma pode ser implementada contra o contrato sem esperar a outra terminar) e serão despachadas em paralelo; Task 3 depende só da Task 2 existir para verificação end-to-end mas seu próprio diff não depende de nenhuma das duas, então também pode rodar em paralelo com cuidado de revisão; Task 4 roda sozinha, por último, depois que as três anteriores estiverem prontas e revisadas.

**2. Inline Execution** — Executo as tasks nesta sessão, em sequência, com checkpoints de revisão.

Vou seguir com a opção 1 (multiagentes), como pedido.
