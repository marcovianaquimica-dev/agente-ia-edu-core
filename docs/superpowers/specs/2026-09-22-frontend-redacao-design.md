# Frontend do fluxo de redação (propostas → envio → correção → devolutiva)

**Data:** 2026-09-22
**Status:** design aprovado, aguardando plano de implementação
**Sub-projeto:** primeira UI real do módulo `REDACAO_IA`, cobrindo o ciclo completo já construído no backend por R2 (propostas e envio) e R3 (motor de correção)
**Depende de:** R0 (estrutura acadêmica + autorização), R1 (contrato do motor), R2 (propostas e envio de redação), R3 (motor de correção) — todos prontos e mesclados em `main`.

---

## 1. Contexto

R2 e R3 construíram um backend completo para o ciclo de redação: professor cria proposta → atribui a uma turma → aluno envia (digitado, foto ou PDF) → páginas são revisadas (quando há transcrição) → submissão é confirmada → `EssayCorrectionService` corrige automaticamente → conforme a política da escola, publica direto ou espera revisão docente → professor aprova/rejeita/tenta de novo. Nenhuma dessas dezenas de rotas tem uma linha de UI hoje — o item de navegação "Redação" já existe no portal do aluno (`index.html`), mas é um placeholder estático ("Próximo Lançamento") sem nenhuma chamada de API.

O frontend deste repositório é vanilla HTML/CSS/JS, sem build step, sem framework, servido como arquivos estáticos pelo FastAPI. Cada papel tem seu próprio portal (`index.html`/`app.js` para aluno, `teacher.html`/`teacher.js` para professor, etc.), cada um com seu próprio objeto de estado mutável, sem router (troca de view é manual: botão com `data-view` → mostra/esconde uma `<section>` correspondente), sem wrapper de HTTP compartilhado entre portais, e renderização via template literals + `innerHTML`. A autenticação hoje é um bypass de desenvolvimento: cada portal manda `Authorization: Bearer <papel>:<id>` como uma string de texto simples, sem verificação real — construir um login de verdade está fora do escopo deste documento.

Ao mapear a API existente para desenhar as telas, apareceram lacunas reais no backend que o próprio R3 deixou de fora deliberadamente (a leitura pelo aluno da própria correção nunca foi construída) ou que R2 nunca precisou (nenhuma rota de listagem para propostas, nem enriquecimento da lista de correções com nome do aluno). Este documento cobre tanto essas rotas novas, pequenas e aditivas, quanto as telas que as consomem.

---

## 2. Escopo

### Entrega

**Backend — rotas novas (todas aditivas, nenhuma mexe em modelo de dados):**
1. `GET /api/v1/student/essay-prompts` — lista propostas abertas para a turma do aluno, com o status do envio dele (se já começou).
2. `GET /api/v1/student/essay-submissions/{id}/correction` — devolutiva do aluno. Colapsa `NEEDS_REVIEW`/`PENDING_REVIEW`/`REJECTED` em um único estado `"PENDING"` do ponto de vista do aluno; só expõe notas/feedback/anotações quando `status="APPROVED"`.
3. `GET /api/v1/catalog/essay-prompts` — lista propostas da escola (professor/coordenação).
4. `GET /api/v1/catalog/essay-prompts/{id}` — detalhe de uma proposta: materiais + atribuições existentes.
5. Enriquecer `GET /api/v1/teacher/essay-corrections` com nome do aluno, título da proposta e `submitted_at` da submissão (via join `EssaySubmission`→`Student`/`Person` e `PromptAssignment`→`EssayPrompt`) — sem isso a fila de revisão é ilegível.

**Frontend — aluno (`src/agente_ia_edu/web/essay.js`, plugado no `data-view="essay"` já existente em `index.html`):**
- Lista de propostas abertas para a turma, com estado visual por proposta (enviar / continuar envio / em correção / ver devolutiva).
- Envio com 3 modos espelhando `mode` da API: digitado (textarea), foto (`<input type="file" multiple accept="image/*" capture="environment">`, um upload por página), PDF (`<input type="file" accept=".pdf">`, upload único).
- Revisão de página (só quando `anchor_mode=TEXT_OFFSET`): card por página com imagem + texto do OCR editável; confirmação só habilita quando toda página tem `reviewed_text`.
- Tela de devolutiva: nota total + barra por competência (C1-C5, escala 0/40/80/120/160/200), pontos fortes/a melhorar, lista de anotações com o trecho citado (sem overlay visual sobre o texto/imagem), alertas, bloco da competência 5 (proposta de intervenção) como checklist.

**Frontend — professor (`src/agente_ia_edu/web/essay-review.js`, novo nav item em `teacher.html`):**
- Gerenciar propostas: criar (título/enunciado/ano), adicionar material de apoio (só tipo TEXTO nesta leva), atribuir a uma turma (reaproveitando `GET /api/v1/teacher/classrooms`, já existente).
- Fila de revisão: lista filtrável por status (`PENDING_REVIEW` padrão, `NEEDS_REVIEW`, `APPROVED`, `REJECTED`), mostrando aluno + proposta + data de envio.
- Painel de revisão: anotações da IA, notas editáveis por competência, feedback editável, alertas, bloco da competência 5; ações Aprovar (com edições, se houver)/Rejeitar/Tentar novamente (só quando `NEEDS_REVIEW`).

**Convenções compartilhadas:**
- Tratamento de erro de módulo desabilitado: replica o tradutor de `reception.js` (regex sobre a mensagem `"Module 'X' is not enabled..."`) em cada arquivo novo — mesmo padrão já duplicado nesta base, não centralizado.
- Badges de status seguem o idioma `.badge.is-X` já usado (`teacher.css`/`styles.css`).
- Gating de módulo é reativo: deixa a API retornar 403 e traduz a mensagem; não há checagem proativa nem endpoint novo para "meus módulos habilitados".

### Não entrega — deliberadamente

- Login real / autenticação real — continua o bypass de desenvolvimento (`Bearer papel:id`) já usado em todos os portais.
- Overlay visual das anotações sobre o texto/imagem da redação — a devolutiva mostra as anotações como lista estruturada com o trecho citado, não marcações desenhadas sobre o conteúdo original.
- Material de apoio tipo IMAGEM nas propostas — exigiria um endpoint de upload que não existe hoje para este recurso específico; só material de TEXTO nesta leva.
- Configurações de escola (`correction_mode`, `validation_default`, limiar de nota, etc.) — não existe rota nem UI para isso hoje; fora de escopo, assume-se que a escola de demonstração já está configurada como necessário.
- UI de aprovação em lote — o endpoint (`POST .../bulk-approve`) já existe desde R3 e continua sem UI, mesmo corte que o próprio R3 já assumiu.
- Notificação ao professor quando uma correção cai em `PENDING_REVIEW`, ou ao aluno quando a devolutiva fica pronta — sem polling automático nesta leva; o aluno vê a devolutiva ao reabrir/recarregar a tela.
- Testes automatizados de frontend — não existe framework de teste JS no repositório (sem build step); a verificação das telas é manual, via browser real contra o servidor de desenvolvimento. As rotas novas de backend recebem testes Python normais, no mesmo padrão de R0-R3.
- Captura de câmera testada em dispositivo físico — o atributo `capture="environment"` é adicionado seguindo a especificação HTML padrão, mas não há como este processo testar em um celular real; a verificação fica limitada ao comportamento do input no navegador de desktop.

---

## 3. Rotas novas de backend

### `GET /api/v1/student/essay-prompts`

Novo router próprio (`essay_student_prompts_router`, prefix `/api/v1/student/essay-prompts`), definido no mesmo arquivo `essay_submissions.py` — um `APIRouter` tem um único prefixo fixo, e este caminho não compartilha prefixo com `essay_submissions_router` (`/api/v1/student/essay-submissions`), então precisa do seu próprio. Registrado em `api/app.py` ao lado do router existente do mesmo arquivo.

Autorização: mesma `_authorize_student` já existente (papel STUDENT + módulo REDACAO_IA + matrícula ativa).

Resposta 200 — `list[EssayPromptForStudentResponse]`, uma entrada por `PromptAssignment` com `status="OPEN"` cuja `class_id` é a turma do aluno:
```
[
  {
    "prompt_assignment_id": UUID,
    "title": str,
    "statement": str,
    "due_at": datetime | null,
    "status": "OPEN" | "CLOSED",
    "my_submission": { "essay_id": UUID, "status": str } | null
  }
]
```
`my_submission` é `null` se o aluno nunca criou uma `EssaySubmission` para este `prompt_assignment_id`; caso contrário, reflete a versão mais recente (`SUBMITTED` mais recente, ou a última `PENDING_*` se ainda não confirmou).

### `GET /api/v1/student/essay-submissions/{essay_submission_id}/correction`

Mesmo router `essay_submissions_router`. Autorização: `_authorize_student` + `_submission_for_own_school_or_403` (já existentes, sem mudança).

Resposta 200:
```
{
  "essay_submission_id": UUID,
  "status": "PENDING" | "APPROVED",
  "final_scores": Scores | null,
  "final_feedback": Feedback | null,
  "annotations": [Annotation, ...] | null,
  "rewrites": [Rewrite, ...] | null,
  "intervention": InterventionBreakdown | null,
  "alerts": [Alert, ...] | null
}
```
Lógica: busca a `EssayCorrection` da submissão. Se não existe (submissão ainda não confirmada) ou `status` é `NEEDS_REVIEW`/`PENDING_REVIEW`/`REJECTED`, retorna `status="PENDING"` com todos os campos de conteúdo `null`. Se `status="APPROVED"`, retorna `status="APPROVED"` com `final_scores`/`final_feedback` (as cópias editáveis pelo professor) e `annotations`/`rewrites`/`intervention`/`alerts` extraídos de `ai_output` (que não tem cópia editável própria — vem direto do output da IA mesmo após aprovação).

### `GET /api/v1/catalog/essay-prompts`

Router: `essay_prompts_router`. Autorização: mesma `_authorize` já existente (TEACHER/COORDINATOR/DIRECTOR/PLATFORM_ADMIN).

Resposta 200 — `list[EssayPromptResponse]` (schema já existe, reaproveitado), todas as propostas da escola do chamador, mais recentes primeiro.

### `GET /api/v1/catalog/essay-prompts/{essay_prompt_id}`

Mesmo router. Autorização: `_authorize` + `_prompt_for_own_school_or_403` (já existentes).

Resposta 200:
```
{
  ...todos os campos de EssayPromptResponse...,
  "materials": [PromptMaterialResponse, ...],
  "assignments": [PromptAssignmentResponse, ...]
}
```
(Os três schemas de item já existem; esta rota só os agrupa em uma resposta compostas.)

### Enriquecimento de `GET /api/v1/teacher/essay-corrections`

Mesma rota, mesma autorização, mesmo filtro por `status`. `EssayCorrectionResponse` ganha três campos novos, populados via join a partir de `EssaySubmission`:
```
student_name: str        # Person.full_name do aluno dono da submissão
prompt_title: str        # EssayPrompt.title da proposta atribuída
submitted_at: datetime | null   # EssaySubmission.submitted_at
```
Sem mudança de assinatura em `approve`/`reject`/`retry`/`bulk-approve` — só o `GET` de listagem muda.

---

## 4. Fluxo do aluno

1. **Lista** (`GET /api/v1/student/essay-prompts`): um card por proposta aberta. Estado visual por card, derivado de `my_submission`:
   - `null` → "Enviar redação"
   - `status` em `PENDING_TRANSCRIPTION`/`PENDING_CONFIRMATION` → "Continuar envio"
   - `status="SUBMITTED"` → busca a devolutiva (`GET .../correction`); se `PENDING`, mostra "Em correção"; se `APPROVED`, mostra "Ver devolutiva"
2. **Envio**: ao clicar "Enviar redação", três abas espelhando `mode` (Digitar/Fotografar/PDF). Digitado submete atômico (`POST` já retorna `SUBMITTED`). Foto/PDF criam a submissão em `PENDING_TRANSCRIPTION` e abrem a área de upload.
3. **Upload**: foto permite múltiplos arquivos de uma vez, um `POST .../pages` por arquivo com `page_number` sequencial; PDF é um único `POST .../document`.
4. **Revisão de página**: só aparece quando `anchor_mode="TEXT_OFFSET"` (escola com transcrição habilitada). Um card por página, imagem ao lado de textarea pré-preenchida com o texto do OCR (concatenação de `ocr_tokens[].text`), editável via `PATCH .../pages/{page_number}`. Botão de confirmar só habilita no client quando toda página tem texto revisado (o backend já garante isso com 409, mas a validação client-side evita a viagem desnecessária).
5. **Confirmação**: `POST .../confirm`. A correção já roda de forma síncrona no backend durante essa chamada — a tela não precisa (nem deve) tentar disparar nada além disso.
6. **Devolutiva**: `GET .../correction`, buscada sob demanda quando o aluno abre a tela (sem polling). `PENDING` mostra uma mensagem de "ainda em correção". `APPROVED` mostra a nota total, barra por competência, pontos fortes/a melhorar, lista de anotações com trecho citado, alertas e o bloco da competência 5.

## 5. Fluxo do professor

1. **Gerenciar propostas** (`GET /api/v1/catalog/essay-prompts`): lista de propostas da escola com status (DRAFT/ACTIVE) e contagem de atribuições.
2. **Criar proposta**: formulário (título/enunciado/ano) → `POST` → tela de detalhe (`GET .../{id}`) para adicionar material de texto e criar atribuições.
3. **Atribuir a uma turma**: seletor de turma populado por `GET /api/v1/teacher/classrooms` (já existente, reaproveitado sem mudança), campo de prazo opcional, checkbox de validação obrigatória (se o `POST` falhar com 422 por `validation_teacher_can_disable=false`, mostra a mensagem traduzida — tratamento reativo, sem checagem prévia).
4. **Fila de revisão** (`GET /api/v1/teacher/essay-corrections?status=`): filtro por status (padrão `PENDING_REVIEW`), tabela com aluno/proposta/data de envio (dos campos enriquecidos) e status.
5. **Painel de revisão**: ao abrir uma correção, mostra anotações (com trecho citado), notas por competência em campos editáveis (pré-preenchidos com o valor da IA), textos de feedback editáveis, alertas, bloco da competência 5. Botões:
   - **Aprovar**: `POST .../approve` — se algum campo de nota/feedback foi alterado, envia `final_scores`/`final_feedback` no corpo; senão, corpo vazio.
   - **Rejeitar**: `POST .../reject`, sem corpo (a API não aceita motivo).
   - **Tentar novamente**: só visível quando `status="NEEDS_REVIEW"`, mostra `failure_reason` de forma destacada antes do botão; `POST .../retry`.

---

## 6. Autorização

Nenhuma rota nova introduz um padrão de autorização diferente do que já existe:
- As duas rotas do aluno reaproveitam `_authorize_student`/`_submission_for_own_school_or_403` (R2, já implementadas) — 403, nunca 404, para o que não é do aluno.
- As duas rotas do professor reaproveitam `_authorize`/`_prompt_for_own_school_or_403` (R2, já implementadas).
- O enriquecimento de `GET /api/v1/teacher/essay-corrections` não muda a autorização da rota, só o `SELECT` que monta a resposta.

## 7. O que fica para depois, explicitamente

Ver §2 "Não entrega" para a lista completa. Resumo dos itens mais relevantes para o planejamento: login real, overlay visual de anotações sobre o texto/imagem, material de apoio tipo imagem, UI de configuração de escola, UI de aprovação em lote, notificações/polling automático, testes automatizados de frontend.

## 8. Rastreabilidade com R0-R3

Toda rota/campo referenciado abaixo já existe hoje, sem modificação, e este documento é responsável por produzir dados compatíveis com eles:

- `src/agente_ia_edu/api/routes/essay_submissions.py` (R2/R3) — `POST ""`, `POST .../pages`, `POST .../document`, `GET .../pages`, `PATCH .../pages/{n}`, `POST .../confirm`, e os helpers `_authorize_student`/`_resolve_enrollment_or_403`/`_assignment_for_own_class_or_403`/`_submission_for_own_school_or_403`/`_resubmission_target_or_403` — reaproveitados sem modificação pelas duas rotas novas do aluno.
- `src/agente_ia_edu/api/routes/essay_prompts.py` (R2) — `POST ""`, `POST .../materials`, `POST .../assignments`, e os helpers `_authorize`/`_prompt_for_own_school_or_403` — reaproveitados sem modificação pelas duas rotas novas do professor.
- `src/agente_ia_edu/api/routes/essay_corrections.py` (R3) — `GET ""`, `POST .../approve`, `POST .../reject`, `POST .../retry`, `POST .../bulk-approve` — `approve`/`reject`/`retry`/`bulk-approve` reaproveitadas sem modificação; só `GET ""` ganha os três campos enriquecidos.
- `src/agente_ia_edu/api/routes/teacher_portal.py` (fase anterior) — `GET /api/v1/teacher/classrooms` (`ClassroomSummaryItem`) — reaproveitado sem modificação para o seletor de turma.
- `src/agente_ia_edu/essay_engine_contract/v1.py` (R1) — `Scores`/`CompetencyScore`/`Annotation`/`Anchor`/`Rewrite`/`Feedback`/`InterventionBreakdown`/`Alert` — a forma exata do conteúdo retornado por `GET .../correction` e consumido pelas duas telas de devolutiva/revisão.
- `src/agente_ia_edu/web/index.html` (fase anterior) — o nav item `data-view="essay"` e o painel `#view-essay` já existentes, que passam a ser preenchidos de verdade em vez do placeholder "Próximo Lançamento".
- `src/agente_ia_edu/web/evolution.js` (fase anterior) — único precedente de módulo JS separado (`window.EvolutionView`), padrão seguido por `essay.js`/`essay-review.js`.
- `src/agente_ia_edu/web/teacher.js` PHASE 29 (fila de revisão de questões, linhas ~1481-1790) — precedente direto para a fila de revisão de correções e o painel de aprovar/rejeitar com campos editáveis.
- `src/agente_ia_edu/web/teacher.js` (upload de material, linhas ~1261-1288) — precedente direto para o padrão de upload (`FormData`, sem `Content-Type` manual, botão desabilitado durante o envio).
- `src/agente_ia_edu/web/reception.js` (`translateDetail`, linhas ~46-94) — precedente para a tradução de erros de módulo desabilitado.
