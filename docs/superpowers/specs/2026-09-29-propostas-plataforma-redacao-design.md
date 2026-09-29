# Propostas de redação da plataforma (admin, cross-escola)

**Data:** 2026-09-29
**Status:** design aprovado, aguardando plano de implementação

---

## 1. Contexto

Hoje o professor só pode atribuir a uma turma propostas de redação que ele mesmo criou, sempre presas à escola dele (`EssayPrompt.school_id` é `NOT NULL` — confirmado em `db/models/essay_proposal.py:58-60`). Não existe nenhum conceito de proposta compartilhada entre escolas.

O usuário quer que o `PLATFORM_ADMIN` (role já existente no sistema — `db/models/admin.py:163`, `auth/token.py:127`, distinto de `DIRECTOR`/`COORDINATOR`/`TEACHER`, que são todos escopados a uma escola) possa cadastrar propostas "da plataforma", visíveis para professores de **qualquer** escola, a qualquer momento (o admin adiciona quando quiser, continuamente — não é uma carga única).

Pesquisei o código antes de propor o design:

- `EssayPrompt.school_id` (`db/models/essay_proposal.py:58-60`) é `NOT NULL`, com `UniqueConstraint("school_id", "id")` — esse par composto é o que outras tabelas (`PromptAssignment`, e agora `EssayBatchUpload`/`EssayBatchPage`, adicionadas na leva de envio em lote) referenciam via FK composta pra garantir isolamento multi-tenant: uma turma de uma escola nunca pode ser atribuída a uma proposta de outra escola. É um padrão usado em todo o sistema, não só em redação.
- `PLATFORM_ADMIN` já existe como role (não escopado a uma escola) — é o ator natural pra esta funcionalidade, sem precisar de nenhum conceito novo de permissão.
- `admin.html`/`admin.js` hoje não têm nenhuma seção de redação — é uma tela nova.
- `EssayPrompt` já segue a convenção "imutável depois de sair de DRAFT, nunca edita, sempre substitui" (mesmo comentário em `db/models/essay_proposal.py:43-46`) — o design abaixo estende essa mesma filosofia pras propostas da plataforma.

Decisões já tomadas com o usuário durante o brainstorm (não reabrir):

1. Propostas da plataforma são **globais** — visíveis para professores de qualquer escola do sistema, não só da escola do admin que criou.
2. Na tela de escolha de proposta do professor, propostas da plataforma aparecem **na mesma lista** que as propostas dele, com um selo visual "Plataforma" — não uma aba/lista separada.
3. Professor **nunca edita nem exclui** uma proposta da plataforma — é somente-leitura pra ele; só usa (atribui a uma turma).
4. Técnica escolhida para resolver o conflito com a FK composta `(school_id, id)` de `EssayPrompt`: **materializar uma cópia por escola** na primeira vez que um professor daquela escola atribui a proposta a uma turma, em vez de afrouxar a trava de tenant existente. A cópia nunca é editada depois de criada (seguindo a mesma imutabilidade de `EssayPrompt`), e cada escola materializa a mesma proposta da plataforma **no máximo uma vez** (reaproveita a cópia já existente nas próximas vezes).

---

## 2. Escopo

### Entrega

**Migração de banco (aditiva, encadeada depois de `057_essay_batch_upload`):**

- Tabela nova `platform_essay_prompts`: `id` (uuid pk), `title`, `statement`, `status` (`ACTIVE`/`ARCHIVED`, check constraint), `created_by_external_identity`, `created_at`.
- `essay_prompts` ganha uma coluna nova: `materialized_from_platform_prompt_id` (uuid, nullable, FK → `platform_essay_prompts.id` ON DELETE RESTRICT). `NULL` para toda proposta criada normalmente por um professor (o caso de hoje, sem mudança). Constraint única `uq_essay_prompts_school_materialized_from` em `(school_id, materialized_from_platform_prompt_id)` — impede duas materializações da mesma proposta da plataforma na mesma escola. Uma `UNIQUE` comum do Postgres já resolve isso sem precisar de índice parcial: linhas com `NULL` nessa coluna (as propostas normais, criadas por professor) nunca conflitam entre si, porque `NULL` nunca é igual a `NULL` para fins de unicidade — só linhas com o mesmo par `(school_id, materialized_from_platform_prompt_id)` não-nulo colidem.

**Backend — admin (`services/platform_essay_prompt.py` + `api/routes/admin_essay_prompts.py`, novos):**

- `POST /api/v1/admin/platform-essay-prompts` — cria (`title`, `statement`), `status=ACTIVE`. Exige `PLATFORM_ADMIN`.
- `GET /api/v1/admin/platform-essay-prompts` — lista todas (ACTIVE e ARCHIVED), pra tela de gestão do admin.
- `POST /api/v1/admin/platform-essay-prompts/{id}/archive` — muda pra `ARCHIVED`. Não afeta nenhuma cópia já materializada em nenhuma escola (elas continuam existindo e atribuíveis normalmente — mesma filosofia "nunca edita, sempre substitui"); só impede que novas escolas passem a vê-la na lista de propostas disponíveis.

**Backend — professor (estende o que já existe):**

- A rota de listagem de propostas do professor (`GET` já existente em `essay_prompts.py`) passa a trazer, junto das próprias da escola, as `platform_essay_prompts` com `status=ACTIVE` que essa escola **ainda não materializou** — cada item da resposta ganha um campo `is_platform: bool` (e `platform_prompt_id` quando for o caso, pra a materialização saber qual usar).
- A rota de criar `PromptAssignment` (atribuir proposta a turma) ganha uma etapa de materialização: se o `essay_prompt_id` recebido no corpo na verdade referencia um `platform_essay_prompt_id` (o frontend manda o mesmo campo pros dois casos — o backend resolve qual é qual), busca-ou-cria a cópia materializada da escola (idempotente, respeitando a constraint única acima) antes de seguir o fluxo de criação de atribuição exatamente como hoje. Do ponto de vista de `PromptAssignment` pra frente, nada muda — ele sempre aponta pra um `EssayPrompt` real e escopado à escola, como sempre apontou.

**Frontend — admin (`admin.html`/`admin.js`):**

- Seção nova "Propostas da Plataforma": lista (título, status, quantas escolas já materializaram — contagem simples via `COUNT(*)` em `essay_prompts` filtrando por `materialized_from_platform_prompt_id`), formulário de criação (título + enunciado), botão arquivar por linha.

**Frontend — professor (`essay-review.js`, tela de propostas já existente):**

- A listagem já existente ganha o selo "Plataforma" nos itens com `is_platform: true`. Nenhuma aba nova — mesma lista, mesmo fluxo de "atribuir a uma turma" de hoje; a materialização acontece no backend, invisível pro professor.

### Não entrega — deliberadamente

- Edição de uma proposta da plataforma já criada pelo admin — segue a mesma imutabilidade de `EssayPrompt` (`archive`/criar uma nova é o caminho, não editar in-place).
- Edição da cópia materializada pelo professor — somente-leitura, decisão já tomada.
- Notificação/aviso automático pras escolas quando uma proposta nova da plataforma é publicada — o professor só a vê na próxima vez que abrir a lista de propostas.
- Filtro/categoria/tag temática nas propostas da plataforma além do selo "Plataforma" — fora de escopo desta leva.
- Métricas de uso agregadas além da contagem simples de escolas que materializaram (visível ao admin) — sem dashboard dedicado.

---

## 3. Modelo de dados

```
platform_essay_prompts
├── id                            uuid PK
├── title                         varchar(255) NOT NULL
├── statement                     text NOT NULL
├── status                        varchar(20) NOT NULL CHECK IN ('ACTIVE','ARCHIVED')
├── created_by_external_identity  varchar(255) NOT NULL
└── created_at                    timestamptz NOT NULL

essay_prompts (existente, só ganha 1 coluna)
├── ...colunas existentes sem mudança...
└── materialized_from_platform_prompt_id  uuid NULL, FK -> platform_essay_prompts.id ON DELETE RESTRICT
    UNIQUE (school_id, materialized_from_platform_prompt_id)
```

`ON DELETE RESTRICT`: uma `platform_essay_prompts` nunca pode ser apagada enquanto alguma escola tiver uma cópia materializada dela — mesma convenção de integridade já usada em `essay_prompts.school_id → schools.id` e nas FKs compostas do envio em lote. Como o admin nunca apaga (só arquiva), essa trava é defensiva, não deve ser exercitada no fluxo normal.

---

## 4. Fluxo de materialização (a parte nova)

1. Professor abre a lista de propostas → vê as próprias + as da plataforma ainda não materializadas pra escola dele, com o selo.
2. Escolhe uma da plataforma, atribui a uma turma (mesmo botão/fluxo de sempre).
3. Backend recebe o pedido de atribuição, identifica que o ID é de uma `platform_essay_prompts`:
   a. Busca em `essay_prompts` se já existe uma linha com `school_id = <escola do professor>` e `materialized_from_platform_prompt_id = <id da proposta da plataforma>`.
   b. Se existe, reaproveita.
   c. Se não existe, cria uma nova `EssayPrompt` (título e enunciado copiados da proposta da plataforma, `school_id` da escola do professor, `materialized_from_platform_prompt_id` apontando pra origem, `status=ACTIVE`).
4. Segue o fluxo de criação de `PromptAssignment` normalmente, usando o `id` da linha materializada (existente ou recém-criada).

Da próxima vez que qualquer professor da mesma escola for atribuir essa mesma proposta da plataforma (a outra turma, por exemplo), o passo 3 já encontra a cópia e reaproveita — nunca duas cópias na mesma escola pra mesma origem.

---

## 5. Testes

- Admin: criar proposta da plataforma, listar, arquivar; arquivar não apaga nem desabilita cópias já materializadas em nenhuma escola; rotas exigem `PLATFORM_ADMIN` (403 pra `TEACHER`/`DIRECTOR`/`COORDINATOR`).
- Listagem do professor: propostas da plataforma `ACTIVE` aparecem com `is_platform=true`; uma `ARCHIVED` não aparece pra quem ainda não materializou; uma já materializada pela escola do professor aparece só uma vez (como a cópia normal, não duplicada com a origem).
- Materialização: duas atribuições seguidas da mesma proposta da plataforma pela mesma escola (turmas diferentes) reaproveitam a mesma linha de `essay_prompts` (mesmo `id` nas duas `PromptAssignment` resultantes) — não duas cópias.
- Isolamento entre escolas: duas escolas diferentes materializando a mesma proposta da plataforma geram cópias **independentes** (`id`s diferentes); nenhuma FK composta existente (`PromptAssignment`, `EssayBatchUpload`/`EssayBatchPage`) precisa mudar — a cópia materializada é um `EssayPrompt` real e passa em todo teste de isolamento que já existe pra propostas normais.
- Constraint única: tentar materializar duas vezes pra mesma escola/mesma origem (ex: corrida de duas requisições simultâneas) não deve criar linha duplicada — cobrir com teste de banco real (Postgres), já que o comportamento de "buscar-ou-criar" idempotente depende da constraint realmente rejeitar a segunda inserção.

---

## 6. Global Constraints

- Nunca afrouxar a FK composta `(school_id, id)` de `essay_prompts` nem as FKs compostas que dependem dela (`PromptAssignment`, `EssayBatchUpload`, `EssayBatchPage`) — a materialização existe exatamente pra evitar isso.
- `platform_essay_prompts` nunca é referenciada diretamente por `PromptAssignment`, `EssaySubmission`, `EssayBatchUpload` ou qualquer tabela escopada a uma escola — só por `essay_prompts.materialized_from_platform_prompt_id`, e só como proveniência/auditoria.
- Rotas de admin (`/api/v1/admin/platform-essay-prompts*`) exigem `PLATFORM_ADMIN`; nenhuma outra role tem acesso de escrita.
