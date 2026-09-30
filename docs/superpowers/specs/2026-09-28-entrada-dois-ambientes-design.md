# Tela de entrada com dois ambientes — Agente IA Edu e Plataforma de Redação

**Data:** 2026-09-28
**Status:** design aprovado, aguardando plano de implementação
**Projeto:** `agente-ia-edu-core` (mesmo repositório)

---

## 1. Contexto e objetivo

Hoje o aluno entra direto em `index.html`/`app.js`, um único app onde "Redação" é apenas
mais uma aba do menu lateral (`data-view="essay"`), ao lado de Dashboard, Trilha, Materiais
etc. Isso não reflete o modelo de negócio real: uma escola pode contratar só o módulo
Agente IA Edu, só o módulo Redação, ou os dois — e hoje essa distinção não é respeitada em
lugar nenhum do lado do aluno.

O sistema de módulos por escola já existe e está em produção (`SchoolModule`,
`PlatformModuleKey.AGENTE_IA_EDU`/`REDACAO_IA`, `PlatformAdminService.is_module_enabled`,
UI de admin para ligar/desligar com auditoria real) — mas é pura configuração hoje: nenhuma
rota nem tela do lado do aluno consulta esse valor.

**Objetivo:** o aluno passa a entrar por uma tela única que resolve, a partir da sua
identidade, quais módulos a escola dele tem habilitados, e:
- se só um módulo está habilitado, o aluno vai direto para esse ambiente, sem escolha
  nenhuma;
- se os dois estão habilitados, o aluno vê dois cards ("Agente IA Edu" / "Plataforma de
  Redação") e escolhe onde entrar;
- os dois ambientes passam a ser páginas/URLs de fato separadas, cada uma com sua própria
  casca visual — não mais uma aba aninhada dentro da outra.

### Fora de escopo

- Qualquer mudança no fluxo de pré-cadastro da recepção (`reception.html`/`reception.js`,
  telas Pré-cadastro → Diagnóstico liberado → ... → Conversão). Esse fluxo é de outro
  perfil de usuário (secretaria/recepção) e não é tocado por este trabalho.
- Qualquer edição no conteúdo de `essay.js`, `essay-annotations.js`, `essay-evolution.js`,
  `essay-report.js` — esses arquivos pertencem à área de redação, ativamente trabalhada por
  outra sessão em paralelo. Este spec só cria uma casca nova (`redacao.html`) que os
  referencia via `<script>`, exatamente como `index.html` já faz hoje, e chama
  `window.EssayView.init()` — nenhuma linha interna desses arquivos muda.
- Autenticação real (login com senha/SSO). Continua usando o mesmo mecanismo de identidade
  auto-declarada (`TestExternalIdentityProvider`) já usado em todo o resto da plataforma —
  isso é um item separado, já adiado pelo usuário anteriormente.
- Portais de professor/coordenação/admin/recepção — nenhum deles muda de URL ou de shell.
  Este trabalho é só sobre a entrada do **aluno**.

---

## 2. Arquitetura

### 2.1 Identidade compartilhada (já existe, não muda)

Confirmado por leitura direta do código: `app.js` e `essay.js` já leem o token do aluno do
mesmo lugar — `sessionStorage.getItem('studentAccessToken')` (`app.js:31,156`; mesmo padrão
em `essay.js`, comentário próprio confirma "Mirrors app.js's studentHeaders() default
exactly"). O backend resolve esse token para o mesmo `external_user_id`/`Student`/`Person`
independente de qual módulo fez a chamada — não existem duas identidades por aluno.
Consequência prática: a nova tela de entrada só precisa gravar a identidade **uma vez** em
`sessionStorage.studentAccessToken` antes de navegar para qualquer um dos dois ambientes;
nenhuma ponte ou mapeamento novo é necessário.

### 2.2 Nova rota de leitura de módulos (backend, pequena)

Hoje só existe `GET /schools/{school_id}/modules`, atrás de `require_platform_admin`
(`api/routes/admin.py:321`) — inadequado para o aluno consultar sua própria escola. Nova
rota, autenticada pela identidade do próprio aluno (mesmo padrão de dependência já usado em
`api/routes/student.py`):

```
GET /api/v1/student/modules
→ 200 { "AGENTE_IA_EDU": true, "REDACAO_IA": false }
```

Resolve `school_id` a partir da identidade do chamador (mesma resolução já usada pelas
outras rotas de `student.py` — reaproveitar, não reimplementar), consulta
`PlatformAdminService.is_module_enabled` (ou uma consulta direta equivalente,
reaproveitando o modelo `SchoolModule` sem duplicar lógica) para cada uma das duas chaves
de `PlatformModuleKey.ALL_MODULES`, e retorna o mapa. Nunca inventa um valor — se a escola
não tiver nenhum `SchoolModule` configurado para uma chave, o valor é `false` (mesma
semântica que `is_module_enabled` já usa hoje, confirmar lendo a implementação exata antes
de escrever a rota nova).

### 2.3 Nova tela de entrada (`entrada.html` + `entrada.js`)

Nova página, montada seguindo o padrão visual já usado nas outras telas do projeto
(`teacher.html`/`coordination.html` como referência de estrutura, não de conteúdo):

1. Campo de identidade do aluno (mesmo padrão de input já usado em `app.js` — não uma tela
   de login nova, é o mesmo mecanismo de identidade auto-declarada de sempre).
2. Ao confirmar a identidade: grava em `sessionStorage.studentAccessToken`, chama
   `GET /api/v1/student/modules`.
3. Regra de exibição:
   - nenhum módulo habilitado → mensagem de erro honesta ("Nenhum módulo habilitado para
     sua escola. Fale com a coordenação."), sem redirecionar para lugar nenhum.
   - só `AGENTE_IA_EDU` → redireciona direto para `/student` (index.html atual).
   - só `REDACAO_IA` → redireciona direto para `/redacao` (nova página, ver §2.4).
   - os dois habilitados → mostra os dois cards; aluno clica e é redirecionado para o
     ambiente escolhido.

Esta página se torna o novo ponto de entrada do aluno. `/student` continua existindo como
URL (index.html sem mudança de rota), mas deixa de ser o primeiro lugar que um aluno
"cru" acessa — a documentação/link enviado ao aluno passa a apontar para `/entrada`.

### 2.4 Novo ambiente "Redação" (`redacao.html`)

Página nova, leve — cabeçalho simples (logo + nome do aluno + botão "trocar de ambiente"
que volta pra `/entrada`), **sem** a barra lateral completa do Agente IA Edu (Dashboard,
Trilha, Materiais, Vídeos etc. não fazem sentido aqui). Estrutura mínima:

```html
<div id="essay-root" aria-live="polite"></div>
<script src="essay-annotations.js"></script>
<script src="essay-report.js"></script>
<script src="essay-evolution.js"></script>
<script src="essay.js"></script>
<script>window.EssayView.init();</script>
```

Idêntico ao que `index.html` já carrega para a aba de redação hoje (mesmos 4 arquivos, na
mesma ordem), só que `init()` é chamado direto ao carregar a página em vez de esperar um
clique de navegação. Nenhuma linha de `essay*.js` muda.

### 2.5 Ambiente "Agente IA Edu" (`index.html`/`app.js`) — remoção da aba aninhada

`index.html`: remove o `<button class="nav-item" data-view="essay">` (linha ~56) e os 4
`<script>` de essay (linhas ~601-604) e a `<section id="view-essay">` (linha ~579-580).
`app.js`: remove a entrada `'essay': { title: ..., sub: ... }` (linha ~130) e o `if
(viewName === 'essay') window.EssayView.init();` (linha ~144). Adiciona um item de
navegação simples "Trocar de ambiente" que volta para `/entrada` (só relevante para escolas
com os dois módulos — para escola com um módulo só, não teria pra onde trocar; decidir na
implementação se o botão aparece condicionalmente, consultando `GET
/api/v1/student/modules` novamente, ou sempre aparece e deixa a tela de entrada decidir).

---

## 3. Fluxo completo (aluno com os dois módulos habilitados)

```
aluno acessa /entrada
  → digita identidade → grava sessionStorage.studentAccessToken
  → GET /api/v1/student/modules → { AGENTE_IA_EDU: true, REDACAO_IA: true }
  → mostra 2 cards
  → aluno clica "Plataforma de Redação"
  → navega para /redacao
  → redacao.html carrega, EssayView.init() roda, lê sessionStorage.studentAccessToken
  → aluno usa o módulo de redação normalmente (nada mudou no comportamento interno)
  → aluno clica "Trocar de ambiente" → volta pra /entrada → escolhe "Agente IA Edu"
  → navega para /student → index.html carrega normalmente, sem a aba de redação
```

## 4. Fluxo (aluno com só um módulo habilitado)

```
aluno acessa /entrada
  → digita identidade → GET /api/v1/student/modules → { AGENTE_IA_EDU: true, REDACAO_IA: false }
  → redireciona direto para /student, sem mostrar card nenhum
```

---

## 5. Tratamento de erro

- Identidade que não resolve pra nenhuma escola/vínculo real: mesma mensagem de erro já
  usada hoje nos outros portais quando a identidade não bate com nenhum `UserSchoolLink`
  (reaproveitar o texto/padrão existente, não inventar um novo).
- `GET /api/v1/student/modules` falha (erro de rede/servidor): mensagem honesta de erro,
  nunca assume um módulo como habilitado por padrão só para não travar a tela.
- Escola sem nenhum módulo habilitado: ver §2.3 — mensagem explícita, sem redirecionamento
  silencioso para lugar nenhum.

---

## 6. Testes

- Backend: TDD para a nova rota `GET /api/v1/student/modules` — casos: os dois módulos
  habilitados, só um, nenhum, escola sem nenhum `SchoolModule` configurado (deve tratar
  como todos `false`, nunca erro nem fabricação de `true`).
- Frontend: se houver infraestrutura de teste JS já usada para as outras telas (grep
  `tests/` por `*_frontend.js`), seguir o mesmo padrão para `entrada.js`; testar as 4
  combinações de módulos (nenhum/só um/o outro/os dois) e a navegação resultante.
- Verificação end-to-end real no navegador (dev server), para os cenários de 1 e 2 módulos
  habilitados, usando escolas reais já existentes no ambiente de dev (ex. a Escola ABC
  criada nesta mesma sessão, e a Escola Partner do seed original) — nunca só teste
  automatizado sem essa checagem visual, dado que é uma mudança de navegação real.
- Confirmar explicitamente, via teste ou verificação manual, que o ambiente de Redação
  funciona igual a antes (upload de redação, ver devolutiva) rodando dentro de
  `redacao.html` — nenhuma regressão no fluxo que a outra sessão está construindo.

---

## 7. Restrições globais (valem para toda a implementação)

- Nunca editar `essay.js`, `essay-annotations.js`, `essay-evolution.js`, `essay-report.js`
  — só referenciá-los via `<script>` em `redacao.html`, exatamente como já são referenciados
  em `index.html` hoje.
- Nunca editar `reception.html`/`reception.js` — fluxo de pré-cadastro fica intacto por
  construção, já que não é tocado.
- Nunca fabricar um valor de módulo habilitado — a fonte de verdade é sempre
  `SchoolModule`/`is_module_enabled`, nunca um `true` assumido para "destravar" a tela.
- Seguir o padrão de identidade auto-declarada já usado em todo o projeto
  (`TestExternalIdentityProvider`, `sessionStorage.studentAccessToken`) — não introduzir um
  mecanismo de sessão novo.
- TDD para toda lógica de backend nova; suíte completa isolada (sem outra sessão rodando
  pytest ao mesmo tempo no Postgres descartável da porta 5433) verde antes de considerar
  qualquer etapa concluída.
- Nenhum `git commit`/`git push` sem o gatilho explícito do usuário.

---

## 8. Auto-revisão do spec

- **Placeholders:** nenhum "TBD"/"TODO" — todas as seções têm conteúdo concreto e acionável.
- **Consistência interna:** §2.1-2.5 descrevem o mesmo fluxo de §3-4 sem contradição; os
  arquivos citados (`app.js:31,144,130,156`; `index.html:56,579-580,601-604`) foram lidos
  diretamente do código real durante a investigação, não presumidos.
- **Escopo:** focado, cobre um único fluxo de ponta a ponta (entrada do aluno); não tenta
  resolver autenticação real nem mexer em outros portais.
- **Ambiguidade:** o único ponto deixado como decisão de implementação (não de design) é se
  o botão "Trocar de ambiente" dentro de cada ambiente aparece sempre ou só quando os dois
  módulos estão habilitados — marcado explicitamente em §2.5, não é uma lacuna escondida.
