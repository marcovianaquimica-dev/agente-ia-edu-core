# PHASE 6 - Domain Map, Learning Path and Next Best Action

Status: **INCREMENTO MINIMO IMPLEMENTADO E VALIDADO** em 2026-09-01.

## A) Auditoria atual

Fluxo encontrado:

`DiagnosticQuestionSelection / PracticeQuestionSelection / AssessmentAnswer`
→ `LearningHistory` (evidencia temporal por resposta)
→ `StudentContentMastery` (agregado atual por aluno e conteudo)
→ `RecommendationEngine` (contexto + dominio + recursos)
→ APIs de dashboard, evolucao e trilha.

- `SimpleProficiencyEstimator` calcula proficiencia e confianca do diagnostico sem depender de LLM.
- `ContentMasteryService` e `MasteryCalculationPolicy` atualizam o agregado persistido apos pratica.
- A finalizacao do diagnostico grava `LearningHistory.activity_type = INITIAL_DIAGNOSTIC` e atualiza o mesmo `StudentContentMastery` usado pela pratica.
- Pratica grava `INDIVIDUAL_PRACTICE`; avaliacao usa `OFFICIAL_ASSESSMENT`. As origens permanecem separadas.
- `LearningHistory` preserva data, dificuldade, acerto, resposta, tentativa oficial ou sessao de pratica. UNKNOWN do diagnostico e preservado por `response_text = UNKNOWN`.
- `CatalogNode` fornece arvore, raiz disciplinar e pai hierarquico. O pai ja e usado pelo diagnostico e recomendacao como hipotese operacional de pre-requisito; nao e um grafo curricular explicito.
- Hipoteses evolutivas de pre-requisito ficam no JSON auditavel do `InitialDiagnostic`.
- `PedagogicalContext` ja suporta `TEACHER`, `COORDINATION` e `SCHOOL_PLAN`.
- `TeachingLesson` cria contexto de professor com escola, turma e ano.
- `RecommendationEngine` ja aplica prioridade `TEACHER > COORDINATION > SCHOOL_PLAN > AUTONOMOUS`, alinhamento e penalidade de repeticao.
- `PedagogicalUniverse` limita catalogo por bindings e escopos; o novo mapa aplica esse limite quando existe universo autorizado.
- Objetivos do aluno e conteudo preferido ficam no `entry_profile` e na resolucao server-side do diagnostico; o novo mapa os usa como alinhamento deterministico.
- A evolucao atual calcula desempenho por dia, mas apresenta `initial_score = 0`; nao ha historico de snapshots de mastery.
- O frontend ja possui Diagnostico, Minha Evolucao e Minha Trilha, mas os contratos atuais nao exibem baixa evidencia, UNKNOWN, pre-requisitos ou uma proxima acao estruturada.

## B) O que ja existia

- Um unico historico de evidencias: `LearningHistory`.
- Um unico agregado de dominio: `StudentContentMastery`.
- Um unico catalogo curricular: `CatalogNode`.
- Um unico motor de selecao de questoes: `QuestionSelectionRepository`.
- Estimadores e politicas deterministicas em `proficiency.py` e `learning_path_policies.py`.
- Hipoteses de pre-requisito do diagnostico.
- Contextos de professor, coordenacao e planejamento escolar.
- Universo pedagogico autorizado.
- APIs e frontend existentes, mantidos compativeis.

## C) O que foi implementado

1. `DomainMapService`, projecao somente leitura sobre mastery, evidencias, catalogo, diagnostico, contextos e universo.
2. Contrato `GET /api/v1/student/domain-map`.
3. `NextBestActionPolicy` pura, deterministica e explicavel.
4. `INITIAL_DIAGNOSTIC` declarado em `ActivityType`.
5. Ausencia de evidencia representada por `mastery_score = null`, sem equivaler a nota zero.
6. Contagens separadas de acerto, erro e UNKNOWN.
7. Origens separadas: diagnostico inicial, avaliacao oficial e pratica individual.
8. Tendencia de desempenho calculada das evidencias temporais, sem reescrever mastery.
9. Hipoteses relevantes de pre-requisito projetadas do diagnostico.
10. Contextos recentes filtrados pela `RecencyPolicy` existente e ordenados pela `ContextPriorityPolicy` existente.
11. Objetivo do aluno, repeticao recente e conflito com planejamento incluidos na prioridade.
12. Filtro por universo autorizado para aluno escolar ou independente.

## D) O que nao foi implementado

Nao foram implementados dashboard novo, gamificacao, flashcards, foto, livros, videos, n8n, WhatsApp, landing page, trial, matricula ou modulos comerciais.

Tambem permanecem futuros: historico persistido de snapshots de mastery, grafo curricular formal de pre-requisitos e execucao automatica da Trilha a partir da proxima acao.

## E) Decisoes arquiteturais

- Nenhum segundo motor de proficiencia, mapa persistido, seletor de questoes ou catalogo foi criado.
- O score e a confianca continuam vindo de `StudentContentMastery` e dos estimadores existentes.
- O mapa calcula somente projecoes derivadas: contagens, tendencia, estados e contexto.
- A decisao nao usa LLM e nao resolve recursos; ela antecede a futura resolucao da Trilha.
- Pratica e avaliacao/diagnostico permanecem separadas por `activity_type` e IDs de sessao/tentativa.
- Aluno independente nao consome contexto escolar.
- Nenhuma migration foi necessaria.

Contrato conceitual por conteudo:

- IDs e nomes de conteudo, disciplina e area;
- `mastery_score` opcional, nunca usando zero para representar ausencia;
- confianca, contagem de evidencias, acertos, erros e UNKNOWN;
- contagens por origem (`INITIAL_DIAGNOSTIC`, `OFFICIAL_ASSESSMENT`, `INDIVIDUAL_PRACTICE`);
- tendencia de desempenho (`IMPROVING`, `STABLE`, `DECLINING`, `INSUFFICIENT_EVIDENCE`);
- ultima evidencia e contexto estruturado da evidencia;
- estado (`NOT_EVALUATED`, `LOW_EVIDENCE`, `POSSIBLE_GAP`, `DEVELOPING`, `MASTERED`);
- contextos pedagogicos ativos;
- pre-requisitos relevantes e hipoteses do diagnostico;
- alinhamento com objetivo do aluno.

A politica de proxima acao recebera apenas dados estruturados e escolhera entre:

- `PRACTICE_CONTENT`;
- `REVIEW_CONTENT`;
- `STUDY_PREREQUISITE`;
- `REINFORCE_CONTENT`;
- `ADVANCE_CONTENT`;
- `COMPLETE_MISSING_EVIDENCE`.

Prioridades: bloqueio de pre-requisito, necessidade comprovada, evidencia insuficiente, contexto recente, objetivo do aluno, tendencia e repeticao recente. Empates usam ID canonico, garantindo determinismo.

## F) Testes

1. dominio alto → `ADVANCE_CONTENT`;
2. dominio baixo → pratica/reforco;
3. baixa confianca → `COMPLETE_MISSING_EVIDENCE`;
4. conteudo sem evidencia → `COMPLETE_MISSING_EVIDENCE` com mastery nulo;
5. pre-requisito com evidencia insuficiente → completar evidencia do pre-requisito;
6. pre-requisito dominado → agir no conteudo alvo;
7. pratica altera evidencia e aparece separada no mapa;
8. aluno independente funciona sem contexto escolar;
9. aluno escolar respeita universo e escopo;
10. conteudo ensinado recentemente ganha prioridade contextual;
11. necessidade forte do aluno vence planejamento escolar de conteudo dominado;
12. mesma entrada produz exatamente a mesma decisao.

Os 12 cenarios solicitados passaram. Tambem passaram testes de contrato HTTP, tres origens de evidencia, UNKNOWN separado de erro, objetivo, penalidade de repeticao, universo independente e regressao adjacente.

Resultado focado: `67 passed` para Phase 6, learning path e practice; regressao de dashboard/recomendacao/E2E: `44 passed`.

Validacao global: `672 passed`, `3 subtests passed`, `10 warnings` nao bloqueantes. `compileall` e `git diff --check` passaram. Alembic code e banco local estao em `019_reception_candidates (head)`.

## G) Riscos

- `CatalogNode.parent_id` e hierarquia, nao ontologia formal. So vira pre-requisito quando existe hipotese diagnostica.
- Tendencia baseada em respostas e tendencia de desempenho, nao historico de score persistido.
- Evidencia diagnostica nao possui FK direta ao diagnostico em `LearningHistory`; a origem permanece identificada pelo tipo.
- Objetivos textuais podem nao resolver para um conteudo, apesar da normalizacao deterministica.
- `StudentContentMastery.content_node_id` possui FK historica para `taxonomy_nodes`, enquanto servicos atuais dependem de IDs alinhados a `CatalogNode`.
- Escopos academicos do universo dependem das regras atuais de autorizacao do `PedagogicalUniverseService`.

## H) Proximo passo

Integrar explicitamente `next_best_action` ao contrato executavel da Trilha e somente entao desenhar a experiencia maior de Minha Evolucao.
