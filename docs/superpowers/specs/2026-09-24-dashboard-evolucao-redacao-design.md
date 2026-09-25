# Dashboard de evolução do aluno em Redação

**Data:** 2026-09-24
**Status:** design aprovado, aguardando plano de implementação
**Sub-projeto C** de três levas relacionadas à devolutiva de redação (A: devolutiva rica; B: exportar PDF — ambas já em produção em `main`, junto com uma leva bounded de cores por competência, também já mesclada).
**Depende de:** a devolutiva rica de redação (spec `docs/superpowers/specs/2026-09-23-devolutiva-rica-redacao-design.md`) — reaproveita a mesma paleta de cores por competência e o mesmo contrato de dados (`final_scores`, `rationales`).

---

## 1. Contexto

O usuário pediu, junto com o pedido de exportação em PDF (leva B), um "dashboard de resultado para um aluno só na aba de Redação: evolução das notas, evolução por competência, o que está bom e o que precisa melhorar". Na época, duas decisões já foram tomadas (brainstorm da leva A): o dashboard deve existir nos **dois portais** (aluno e professor), e o checklist "o que está bom/o que precisa melhorar" deve ser **dinâmico, com ocorrências reais** — não uma lista estática.

Pesquisei o código antes de propor qualquer design:

- **Não existe hoje nenhuma agregação de correções por aluno.** `EssayCorrection` (`src/agente_ia_edu/db/models/essay_correction.py`) guarda `final_scores`/`final_feedback` como JSON, `status`, `published_at`/`reviewed_at` — mas não tem coluna de `student_id` direta (é preciso `JOIN` em `EssaySubmission.student_id`), não tem índice em `(student_id, published_at)`, e nenhuma rota hoje busca "todas as correções aprovadas de um aluno, em ordem cronológica".
- **"Minha Evolução" já existe, mas é outra coisa.** É a rota `GET /api/v1/student/domain-map` (`src/agente_ia_edu/api/routes/domain_map.py`), sobre domínio de conteúdo curricular (ex: "Cinemática") — não tem nenhuma relação com redação. Não é reaproveitável como rota, mas confirma que o padrão de "dashboard de evolução" já existe conceitualmente no produto, só não pra redação.
- **Não existe nenhuma biblioteca de gráfico no frontend** — nem Chart.js, nem D3, nem `<canvas>`. O único precedente visual é CSS puro (`.essay-competency-bar`, já usado na devolutiva). Isso foi confirmado explicitamente com o usuário durante o brainstorm: em vez de trazer uma dependência nova, os gráficos de linha desta leva são **SVG nativo do navegador**, sem biblioteca.
- **Volume de dados atual é pequeno**: no banco de dev, nenhum aluno tem mais que 3 redações corrigidas até agora — um fator considerado ao decidir não introduzir uma lib de gráfico pesada agora.
- **O seletor de aluno do professor NÃO reaproveita a busca de alunos já existente** (`GET /api/v1/teacher/search`, em `teacher_portal.py`). Essa rota existe pra um domínio diferente (mastery de conteúdo curricular) e devolve `student_id` como uma string de identidade externa (`external_identity_id`), não o UUID interno de `Student.id` que `EssaySubmission`/`EssayCorrection` usam — misturar os dois espaços de identificador seria arriscado. Esta leva usa uma listagem própria, pequena, no mesmo padrão de `JOIN EssaySubmission → Student → Person` que `essay_corrections.py`'s `list_essay_corrections` já usa hoje.

---

## 2. Escopo

### Entrega

**Backend — nova rota de agregação, um arquivo de serviço novo (`src/agente_ia_edu/services/essay_evolution.py`):**

- `GET /api/v1/student/essay-evolution` (novo router próprio, `essay_evolution_student_router`, dentro do mesmo arquivo `essay_submissions.py` — esse arquivo já tem precedente direto de mais de um router por arquivo: `essay_submissions_router` e `essay_student_prompts_router` já coexistem lá, cada um registrado separadamente em `app.py`. Mesmo padrão a seguir aqui, em vez de aninhar sob o prefixo de outro router) — reaproveita a autorização já existente (`_authorize_student`, `_resolve_enrollment_or_403`), sem regra nova. Devolve, em ordem cronológica **decrescente** (mais recente primeiro):
  - `entries: list[EssayEvolutionEntry]` — uma por correção **APROVADA** do aluno (só `APPROVED`; `PENDING_REVIEW`/`NEEDS_REVIEW`/`REJECTED` não entram, mesmo critério que a leva B já usa do lado do aluno), cada uma com: `essay_submission_id`, `prompt_title`, `published_at`, `total` (nota total, `None` se o modo for FORMATIVO — ver §3), `per_competency: dict[str, int]` (C1-C5).
  - `rationales`, `intro_message`/`closing_message` **NÃO entram aqui** — o checklist "o que está bom/a melhorar" (§2.3 abaixo) é resolvido separadamente, só da correção mais recente, e reaproveita a rota de correção já existente (`GET .../correction`), não duplica dado.
  - `total_delta: int | None` — `entries[0].total - entries[-1].total` (mais recente menos mais antiga), `None` se só houver 1 correção ou se qualquer uma das duas pontas estiver em modo FORMATIVO (sem nota).
- `GET /api/v1/teacher/essay-evolution?student_id=...` (novo router próprio, `essay_evolution_teacher_router`, dentro de `essay_corrections.py`, mesmo padrão de múltiplos routers por arquivo justificado acima) — mesmo formato de resposta, de qualquer aluno da MESMA ESCOLA do professor (reaproveita `_authorize`, sem restrição por turma — mesmo critério que `list_essay_corrections` já usa hoje, não introduz um modelo de escopo mais restrito emprestado de outro domínio).
- `GET /api/v1/teacher/essay-evolution/students?q=...` — lista de alunos da escola do professor com pelo menos uma correção aprovada, pra alimentar o seletor (nome + `student_id`), usando o mesmo padrão de `JOIN` já citado acima. `q` filtra por nome (opcional — sem `q`, lista os primeiros N alunos).

O formato `entries` é deliberadamente o mesmo shape que uma biblioteca de gráfico (tipo Chart.js) consumiria direto — uma lista ordenada de pontos com data e valores. Trocar as barras/SVG por uma lib de gráfico de verdade, se um dia fizer sentido com mais histórico, é trabalho só de frontend; o backend não muda.

**Frontend — dois componentes visuais, sem biblioteca nova:**

1. **Linha do tempo de cards** (reaproveita a mesma estrutura visual já usada na devolutiva — card por redação, mais recente primeiro): data, título da proposta, nota total, e as 5 barrinhas de competência coloridas (mesma paleta já estabelecida). No topo, um resumo de uma linha: "Sua nota subiu N pontos desde a primeira redação" (ou "desceu", ou omitido se só há 1 correção ou se `total_delta` é `None`).
2. **Seção "Evolução por competência"**, depois da linha do tempo: **6 gráficos de linha em SVG**, um para cada competência (C1-C5, na cor já estabelecida de cada uma) mais um para a nota total (cor neutra escura) — cada gráfico com um ponto por redação (eixo X = ordem cronológica, eixo Y = pontos 0-200 pra competências / 0-1000 pra total), pontos conectados por uma linha, e cada ponto rotulado com a redação correspondente (mesmo padrão de popover on-hover/tap já usado nas marcações de texto da devolutiva). Com só 1 redação aprovada, um gráfico de 1 ponto (sem linha) — não é tratado como erro.
3. **Checklist "o que está bom / o que precisa melhorar"**: reaproveita a tabela de competência já existente em `essay-report.js` (`_competencyTableHtml`-equivalente), alimentada pelos `rationales` da correção **mais recente** (busca separada, `GET .../correction`, já existente — não uma rota nova).

**Pontos de entrada:**

- **Aluno**: nova seção dentro do módulo Redação (`essay.js`), abaixo da lista de propostas — não uma aba nova separada, já que o módulo do aluno hoje não tem sistema de abas (`teacher.js` tem; `essay.js` não).
- **Professor**: nova aba "Evolução" dentro do módulo Redação (`essay-review.js`), ao lado de "Propostas"/"Fila de Revisão" — com um campo de busca de aluno (usa a rota `.../students` acima) que, ao selecionar, carrega o mesmo componente reaproveitado.

### Não entrega — deliberadamente

- Detecção de padrão recorrente entre redações (ex: "crase apareceu em 2 de 3 redações") — o checklist usa só a correção mais recente, como já decidido no brainstorm. Fica pra uma leva futura se fizer sentido com mais histórico.
- Biblioteca de gráfico (Chart.js ou similar) — SVG nativo por enquanto; o formato de dados já fica pronto pra essa troca futura, mas a troca em si não é desta leva.
- Comparação entre alunos, ranking de turma, ou qualquer agregação além de "evolução de um aluno específico".
- Qualquer mudança no fluxo de aprovação/rejeição/correção já existente — esta leva é só leitura, sem efeito colateral.
- Testes automatizados de frontend (mesmo corte de todas as levas anteriores).

---

## 3. Compatibilidade — casos de borda

| Situação | Comportamento |
|---|---|
| Aluno sem nenhuma correção aprovada ainda | `entries: []`, `total_delta: None`. Frontend mostra estado vazio: "Vamos ver sua evolução assim que sua primeira redação for corrigida." |
| Só 1 correção aprovada | `entries` com 1 item, `total_delta: None` (sem "desde a primeira" pra comparar), gráficos de linha com 1 ponto só (sem linha, só o ponto). |
| Correção em modo FORMATIVO (sem nota) — `total`/`per_competency` ausentes no `final_scores` | Essa entrada aparece na linha do tempo (card sem a barra de nota total, já que não existe), mas é **excluída** do cálculo de `total_delta` e dos gráficos de linha de nota (a linha simplesmente pula esse ponto no eixo X, sem quebrar o SVG) — mesma lógica de "campo pode faltar, degrada sem erro" já usada em toda a devolutiva rica. |
| Dado antigo sem os campos novos da leva A (`rationales[].strengths`/`.growth_area`) na correção mais recente | O checklist cai no mesmo fallback que a devolutiva rica já implementa (mostra `summary` como parágrafo único) — reaproveita a função já existente, não duplica a regra. |

---

## 4. Autorização

Nenhuma rota nova de padrão de autorização — as três rotas reaproveitam, sem modificação: `_authorize_student`/`_resolve_enrollment_or_403` (aluno, já em `essay_submissions.py`) e `_authorize`/`_correction_for_own_school_or_403` (professor, já em `essay_corrections.py`). A listagem de alunos pro seletor do professor usa a mesma checagem de escola que `_authorize` já resolve — sem checagem por turma (mesmo critério, deliberadamente consistente, que `list_essay_corrections` já usa).

## 5. O que fica para depois

Ver §2 "Não entrega". Detecção de padrão recorrente e gráfico com biblioteca de verdade ficam pra quando houver mais histórico de dado real que justifique.

## 6. Rastreabilidade

- `src/agente_ia_edu/db/models/essay_correction.py` — `final_scores`, `final_feedback`, `status`, `published_at` (campos a agregar); nenhuma coluna de `student_id` direta, exige `JOIN` em `EssaySubmission`.
- `src/agente_ia_edu/essay_engine_contract/v1.py` — shape de `Scores`/`CompetencyScore` (`per_competency`, `total`) que `final_scores` já segue.
- `src/agente_ia_edu/api/routes/essay_submissions.py` — `_authorize_student`, `_resolve_enrollment_or_403`, `get_essay_submission_correction` (rota de correção individual já existente, reaproveitada pro checklist); `essay_submissions_router` e `essay_student_prompts_router` já coexistem nesse arquivo como precedente de múltiplos routers por arquivo — novo `essay_evolution_student_router` entra aqui do mesmo jeito.
- `src/agente_ia_edu/api/routes/essay_corrections.py` — `_authorize`, `_correction_for_own_school_or_403`, `list_essay_corrections` (padrão de `JOIN EssaySubmission → Student → Person` a replicar na listagem de alunos) — novo `essay_evolution_teacher_router` entra aqui.
- `src/agente_ia_edu/api/app.py` — cada router é importado e registrado individualmente (`app.include_router(...)`, linhas 59-61 já mostram o padrão pros routers de redação existentes) — os dois routers novos precisam do mesmo registro.
- `src/agente_ia_edu/api/routes/teacher_portal.py` — `search_students`/`StudentSearchItem` — confirmado NÃO reaproveitável (espaço de identificador diferente, `external_identity_id` em vez de `Student.id`), documentado aqui pra não ser redescoberto como opção numa leva futura.
- `src/agente_ia_edu/web/essay-report.js` — tabela de competência (`strengths`/`growth_area`/fallback pra `summary`) a reaproveitar pro checklist, sem duplicar a regra de fallback.
- `src/agente_ia_edu/web/essay-annotations.js` — padrão de popover on-hover/tap a replicar nos pontos dos gráficos de linha.
- `src/agente_ia_edu/web/styles.css` — paleta de cores por competência (`--primary`/`--accent`/`--danger`/`--warning`/`--success`, já estendida na leva bounded de cores) a reaproveitar nos 6 gráficos.
- `src/agente_ia_edu/web/essay.js` — módulo do aluno, sem sistema de abas — nova seção entra aqui.
- `src/agente_ia_edu/web/essay-review.js` — módulo do professor, já tem abas ("Propostas"/"Fila de Revisão") — nova aba "Evolução" entra aqui.
