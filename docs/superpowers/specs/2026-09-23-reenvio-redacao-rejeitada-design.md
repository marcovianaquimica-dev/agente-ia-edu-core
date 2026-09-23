# Reenvio de redação rejeitada pelo aluno

**Data:** 2026-09-23
**Status:** design aprovado (autonomia total concedida pelo usuário — ver Nota de Autonomia), aguardando plano de implementação
**Sub-projeto:** terceira leva do frontend de redação — fecha o único estado terminal sem saída para o aluno hoje.
**Depende de:** o fluxo de redação (leva 1) e o destaque visual de anotações (leva 2), ambos já mesclados em `main`.

**Nota de autonomia:** o usuário pediu explicitamente para eu brainstormar, especificar, planejar e executar esta leva inteira sem pausar para aprovação ("vamos executar uma tarefa grande que não precise da minha aprovação... use multiagentes"), pois estaria ausente com o computador ligado. Este documento substitui o processo normal de perguntas/aprovação do brainstorming: cada decisão de design que normalmente seria uma pergunta ao usuário está registrada abaixo como uma **Ruling** (decisão + motivo + custo se errada), no mesmo padrão de rulings já usado pelo `subagent-driven-development` nesta sessão.

---

## 1. Contexto

A revisão final da leva 2 (destaque visual) identificou este gap, ainda não corrigido: quando um professor rejeita uma correção (`EssayCorrection.status = "REJECTED"`), a submissão do aluno (`EssaySubmission.status = "SUBMITTED"`) nunca muda de estado, e a devolutiva do aluno (`GET /api/v1/student/essay-submissions/{id}/correction`) colapsa REJECTED na mesma resposta genérica `"PENDING"` que NEEDS_REVIEW/PENDING_REVIEW usam ("ainda sendo corrigida"). O aluno nunca fica sabendo que a redação foi rejeitada, e não existe nenhum botão de reenvio na tela — mesmo o backend já suportando reenvio de ponta a ponta via `resubmit_essay_id` (usado por outro fluxo: professor pede reenvio explícito de uma redação já aprovada anteriormente, cenário de nova tentativa).

Confirmei via pesquisa no código (não suposição): a lógica de reenvio (`_resubmission_target_or_403` + `EssaySubmissionService._validate_resubmission`) só olha para `EssaySubmission.status == "SUBMITTED"` — nunca consulta `EssayCorrection.status`. Ou seja, reenviar depois de um REJECTED já funciona no backend hoje, character por character, sem nenhuma mudança de lógica de negócio — só falta (a) o aluno saber que foi rejeitado, e (b) um botão que chame o `POST` já existente com `resubmit_essay_id` preenchido.

## 2. Escopo

### Entrega

**Backend — 1 rota enriquecida (aditiva, nenhuma mexe em modelo de dados):**
- `GET /api/v1/student/essay-submissions/{id}/correction` passa a expor `status="REJECTED"` distintamente (em vez de colapsar em `"PENDING"`). NEEDS_REVIEW e PENDING_REVIEW continuam colapsados em `"PENDING"` exatamente como hoje — só REJECTED muda. Nenhum campo de conteúdo (`final_scores`/`final_feedback`/`annotations`/etc.) é exposto no estado REJECTED — mesma lógica de "só mostra conteúdo quando aprovado" que já protege o aluno de ver um rascunho de IA não publicado.

**Frontend — aluno (`src/agente_ia_edu/web/essay.js`):**
- `renderDevolutiva` ganha um terceiro ramo (`correction.status === 'REJECTED'`, ao lado dos já existentes `'PENDING'` e "aprovado"): mensagem explicando que a redação foi rejeitada pelo professor e precisa ser reenviada, mais um botão "Reenviar redação" que abre o MESMO formulário de envio já existente (`renderNewSubmissionForm`, as três abas Digitar/Fotografar/PDF), mas desta vez enviando `resubmit_essay_id: prompt.my_submission.essay_id` no corpo do `POST` inicial.
- Tratamento específico do 409 que a API já retorna quando `correction_mode="AVALIATIVO"` bloqueia reenvio (`EssayResubmissionBlockedError`): mensagem clara ("Esta avaliação não permite reenvio") em vez do erro genérico.

### Não entrega — deliberadamente

- Mudança na lista de propostas (`renderList`/`submissionState`) para mostrar "Rejeitada" como badge distinto no card — **Ruling:** deixar a lista genérica como está ("Em correção / ver devolutiva" cobre qualquer submissão `SUBMITTED`, sem diferenciar o resultado), porque saber o resultado exige uma consulta por item (`GET .../correction`), e a lista hoje é deliberadamente barata (uma chamada só, sem N+1 por card). O resultado específico já fica claro assim que o aluno abre o item. Custo se errado: aluno não vê de cara na lista que precisa agir — mitigado porque abrir qualquer item "em correção" já revela o estado real na primeira tela.
- Notificação ao aluno quando a rejeição acontece — sem polling automático, mesmo corte já estabelecido nas levas 1 e 2.
- UI para o professor rejeitar com motivo — a rota `POST .../reject` não aceita corpo (decisão de R3, não revisitada aqui).
- Histórico de versões anteriores (mostrar a redação rejeitada ao lado da nova, comparação lado a lado) — fora de escopo, o aluno só vê a versão atual depois do reenvio, como já acontece hoje para qualquer reenvio.
- Testes automatizados de frontend — mesmo corte das levas anteriores.

## 3. Rota de backend enriquecida

### `GET /api/v1/student/essay-submissions/{essay_submission_id}/correction`

Mesma rota, mesma autorização (`_authorize_student` + `_submission_for_own_school_or_403`, sem mudança). `StudentCorrectionResponse.status` passa a ter três valores possíveis em vez de dois: `"PENDING" | "APPROVED" | "REJECTED"`.

Lógica: onde hoje a rota faz
```python
if correction is None or correction.status != "APPROVED":
    return StudentCorrectionResponse(essay_submission_id=submission.id, status="PENDING")
```
passa a ser
```python
if correction is None or correction.status in ("NEEDS_REVIEW", "PENDING_REVIEW"):
    return StudentCorrectionResponse(essay_submission_id=submission.id, status="PENDING")
if correction.status == "REJECTED":
    return StudentCorrectionResponse(essay_submission_id=submission.id, status="REJECTED")
# resta status == "APPROVED", caminho já existente sem mudança
```
Nenhum campo de conteúdo é preenchido no ramo REJECTED (mesmo padrão do PENDING — só `essay_submission_id`/`status`).

**Ruling:** não é preciso expor `failure_reason`/motivo de rejeição ao aluno (a rota `POST .../reject` do professor nunca aceitou um motivo — R3 não guarda essa informação em lugar nenhum). A mensagem do aluno é genérica ("seu professor pediu um reenvio desta redação"), não específica. Custo se errado: aluno sem saber o motivo exato — aceitável, já que a informação nunca existiu no sistema; se o produto quiser motivo de rejeição no futuro, é uma mudança de R3 (rota `reject`), fora do escopo desta leva.

## 4. Fluxo do aluno

1. Aluno abre uma proposta com `my_submission.status === 'SUBMITTED'` → `renderDevolutiva` busca `GET .../correction` como já faz hoje.
2. Se `status === 'REJECTED'`: mostra card com título da proposta, mensagem de rejeição, e botão "Reenviar redação".
3. Clicar em "Reenviar redação" abre o MESMO formulário de três abas (Digitar/Fotografar/PDF) que uma submissão nova usa — **Ruling:** reaproveitar `renderNewSubmissionForm` integralmente em vez de criar uma tela nova, passando um parâmetro extra (`resubmitEssayId`) que percorre até o corpo do `POST` inicial (tanto no envio digitado quanto na criação lazy do upload de foto/PDF, os dois pontos que hoje fazem `POST /api/v1/student/essay-submissions`). Custo se errado (parâmetro não propagar corretamente): o reenvio criaria uma submissão nova sem vínculo com a antiga, seria tratado como uma tentativa completamente nova em vez de nova versão — testável e revisável no diff, não um risco silencioso.
4. Se o `POST` retornar 409 (bloqueio de reenvio para `correction_mode="AVALIATIVO"`): mensagem específica em vez do erro genérico já tratado por `translateDetail`.
5. Reenvio bem-sucedido segue o fluxo já existente (confirmação, correção automática, nova devolutiva) sem nenhuma mudança — a submissão antiga vira `SUPERSEDED` pelo backend automaticamente (já comportamento existente, não modificado aqui).

## 5. Autorização

Nenhuma mudança de padrão de autorização — a rota enriquecida reaproveita `_authorize_student`/`_submission_for_own_school_or_403` sem modificação; o reenvio em si reaproveita `_resubmission_target_or_403` sem modificação (já é aplicado hoje a qualquer reenvio, independente do motivo).

## 6. O que fica para depois

Ver §2 "Não entrega". Resumo: badge de "rejeitada" na lista, motivo de rejeição visível ao aluno, notificação automática, histórico de versões lado a lado.

## 7. Rastreabilidade

- `src/agente_ia_edu/api/routes/essay_submissions.py` — `create_essay_submission`, `_resubmission_target_or_403`, `StudentCorrectionResponse`, `get_essay_submission_correction` — todos já existentes, só o último ganha um `elif` novo.
- `src/agente_ia_edu/services/essay_submission.py` — `_validate_resubmission`, `EssayResubmissionBlockedError`, a lógica de `SUPERSEDED` em `start_typed_submission`/`confirm_submission` — reaproveitados sem modificação (já provados por `tests/test_r2_essay_submission_typed.py`).
- `src/agente_ia_edu/web/essay.js` — `renderDevolutiva`, `renderNewSubmissionForm`, `renderTypedForm`, `renderUploadArea` — pontos de extensão para o novo ramo REJECTED e o parâmetro `resubmitEssayId`.
- `src/agente_ia_edu/db/models/essay_proposal.py` — `EssaySubmission.essay_id`/`.id`, já expostos ao frontend hoje via `MySubmissionSummary.essay_id` — nenhuma mudança de schema necessária.
