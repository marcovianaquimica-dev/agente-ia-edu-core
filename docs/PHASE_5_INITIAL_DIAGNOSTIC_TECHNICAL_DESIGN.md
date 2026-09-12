# Phase 5 - Diagnostico Inicial: Auditoria e Desenho Tecnico

Data: 2026-08-31

Escopo desta etapa: auditoria baseada no codigo existente e desenho incremental. Nenhum modelo, migration, rota ou regra de negocio foi alterado.

## Decisao de Arquitetura

O repositorio ja possui um nucleo de diagnostico inicial reutilizavel: `InitialDiagnostic`, `DiagnosticQuestionSelection`, `InitialDiagnosticService` e rotas de aluno. A Phase 5 deve fortalecer esse nucleo. Nao deve criar `DiagnosticQuestion`, banco exclusivo de questoes, copia de identidade, copia de taxonomia ou uma segunda representacao de dominio.

O nucleo atual foi validado com `tests/test_initial_diagnostic.py`: 6 testes passando. Essa cobertura confirma sessao, adaptacao basica, parada, persistencia de dominio e separacao basica por escola, mas nao comprova todos os requisitos desta fase.

## A) Arquitetura Existente Reutilizavel

| Componente | Reuso | Base real |
| --- | --- | --- |
| Identidade do aluno | `ExternalIdentityContext` traz identidade externa, escola, unidade, serie e turma. Nao ha tabela local de usuario como fonte primaria. | `identity.py` |
| Autorizacao | `AuthorizationService` e `UserSchoolLink` separam papel de escopo: escola, unidade, segmento, serie e turma. | `services/authorization.py`, `db/models/admin.py` |
| Questoes | `Question` e `QuestionVersion` preservam versao, dificuldade, status, visibilidade e escola. | `db/models/official.py` |
| Elegibilidade | `QuestionEligibilityCalculator` e autorizacao da questao devem continuar sendo a porta de selecao. | `services/question_governance.py` |
| Conhecimento e taxonomia | `CatalogNode` organiza disciplina e conteudo; `TaxonomyNode` e classificacoes conectam questoes a habilidades. | `db/models/catalog.py`, `db/models/pedagogical.py` |
| Contexto escolar | `TeachingLesson` registra conteudo ensinado por escola, unidade, segmento, serie, turma e ano letivo. | `db/models/teaching_context.py` |
| Avaliacao e tentativa | `AssessmentAttempt` e `AssessmentAnswer` sao adequados para avaliacoes publicadas; nao devem ser duplicados. O diagnostico ja tem selecoes dinamicas proprias. | `db/models/assessments.py` |
| Diagnostico adaptativo | `InitialDiagnostic` persiste sessao; `DiagnosticQuestionSelection` persiste questao, ordem e resposta. `InitialDiagnosticService` adapta EASY/MEDIUM/HARD e encerra por politica. | `db/models/diagnostic.py`, `services/initial_diagnostic.py` |
| Mapa de dominio e trilha | `StudentContentMastery` guarda dominio, confianca e nivel; `LearningHistory` guarda evidencia por resposta com `INITIAL_DIAGNOSTIC`; recomendacoes ja consomem dominio e pre-requisito hierarquico. | `db/models/learning_path.py`, `services/recommendation.py` |
| Linguagem natural | `StudySearchService.resolve_context()` extrai disciplina, conteudo, nivel e intencao sem alterar identidade ou autorizacao. | `services/study_search.py` |
| Devolutivas institucionais | Portais de professor e coordenacao ja agregam dominio, historico e contexto pedagogico sob escopo. | `services/teacher_portal.py`, `services/coordination_portal.py` |

## B) Gaps Encontrados

1. **Autorizacao deve ocorrer antes da mutacao.** A rota de resposta chama o servico, que persiste a resposta, e somente depois compara o aluno do diagnostico com a identidade autenticada. Isso permite uma tentativa de escrita indevida antes do retorno 403. A fase deve corrigir esse ponto antes de ampliar o fluxo.
2. **`GET /student/diagnostic/{id}` esta incorreta.** A rota usa `session.get(InitialDiagnosticService, diagnostic_id)` em vez do modelo `InitialDiagnostic`; a retomada nao esta comprovada por teste HTTP.
3. **O `school_id` recebido no start nao pode decidir o tenant.** A rota aceita `request.school_id` antes do fallback da identidade. Para aluno escolar, escola, turma, unidade e serie devem vir do contexto autenticado e/ou vinculos persistidos, nunca do payload.
4. **Selecao ainda e global.** `_select_next_question_for_diagnostic()` percorre todos os `CatalogNode` ativos fora da raiz, sem filtrar disciplina, segmento, serie, turma, ano letivo, conteudo ensinado ou visibilidade/autorizacao da questao.
5. **Selecao nao aplica elegibilidade de governanca.** `QuestionSelectionRepository.list_candidate_versions()` filtra classificacao e dificuldade, mas nao exige questao PUBLISHED, versao oficial, validacao elegivel nem permissao do solicitante.
6. **Pre-requisito e apenas sinalizado.** O resultado usa `parent_id` para definir `prerequisite_check_required`, mas nao muda a proxima questao para investigar esse pai. A arvore e uma boa base; a regra de investigacao ainda falta.
7. **Confianca atual mede volume, nao estabilidade.** Ela e calculada como respostas/maximo. A politica configura minimo, maximo e alvo, mas nao mede consistencia por conteudo/dificuldade ou evidencia de pre-requisito.
8. **Conversa e perfil progressivo nao existem.** O diagnostico recebe campos tecnicos no request; nao ha estado de conversa, nome preferido, objetivo, autopercepcao nem contrato para atualizar o perfil no provedor de identidade.
9. **Diagnostico avulso e responsavel nao tem contrato completo.** A identidade externa suporta aluno independente; nao ha representacao de responsavel, consentimento, vinculacao posterior ou regra de visualizacao de devolutiva para esse papel.
10. **Devolutiva atual e tecnica.** O resultado expoe `mastery_map`, `probable_gaps` e acerto por item. Faltam apresentadores acolhedores, prioridades e visoes diferenciadas para aluno, responsavel e escola.
11. **Inconsistencia de chave pedagogica deve ser resolvida no desenho.** Selecao diagnostica referencia `CatalogNode`, enquanto `StudentContentMastery` referencia `TaxonomyNode`. Os testes usam o mesmo UUID para ambos; o contrato de mapeamento Catalogo -> Taxonomia deve ser explicitado antes de integrar dados reais.

## C) Proposta de Arquitetura do Diagnostico

Manter um unico agregado: `InitialDiagnostic` e suas selecoes. Introduzir, dentro do mesmo servico, quatro colaboracoes sem duplicar dominios:

1. **Context resolver:** monta um snapshot imutavel do inicio. Para escola, deriva escola/unidade/segmento/serie/turma/ano da identidade e `UserSchoolLink`, e consulta `TeachingLesson` para separar conteudo ensinado de conteudo previsto. Para independente, usa o texto do aluno somente como prioridade inicial, processado por `StudySearchService`.
2. **Candidate policy:** consulta Question Bank existente com classificacao, elegibilidade, visibilidade e autorizacao. Prioriza pre-requisitos e conteudo ensinado/relevante; exclui questoes ja respondidas e repete somente por regra configurada.
3. **Adaptive evidence policy:** usa resultado anterior, dificuldade, cobertura por conteudo e estabilidade. Acerto avanca quando ha evidencia; erro em item complexo investiga pre-requisito antes de concluir lacuna. A politica retorna tambem a razao de selecao para auditoria.
4. **Feedback assembler:** transforma selecoes e `StudentContentMastery` em linguagem por audiencia. Ele nao devolve nota como primeiro resultado e nao reinterpreta um erro isolado como ausencia de dominio.

O algoritmo e deterministico, versionado por `diagnostic_version` e parametrizado por configuracao injetada. Parametros iniciais: minimo/maximo por escopo, alvo de confianca por conteudo, cobertura minima de pre-requisitos, estabilidade e parada por indisponibilidade de itens elegiveis.

## D) Modelo de Dados Proposto

**Agora:** nenhum novo modelo ou migration. A sessao, respostas, versao, tenant, evidencia e mapa de dominio ja possuem destino em tabelas existentes.

**Extensoes candidatas, somente apos implementar e provar a necessidade:**

| Alvo | Campos aditivos candidatos | Motivo |
| --- | --- | --- |
| `InitialDiagnostic` | `entry_mode`, `initiated_by_external_id`, `context_snapshot`, `consent_snapshot` | Distinguir escolar, independente e avulso; preservar contexto e LGPD sem criar outra identidade. |
| `DiagnosticQuestionSelection` | `selection_reason`, `response_time_ms`, `evidence_snapshot` | Tornar a inferencia auditavel sem criar questao diagnostica. |

Nome preferido e demais atributos de identidade devem ser atualizados no provedor hospedeiro quando ele oferecer contrato de escrita. Enquanto isso, apenas um snapshot minimo consentido pode integrar `context_snapshot`; nao criar uma tabela de usuario paralela. Um vinculo responsavel-aluno depende de contrato de identidade/consentimento ainda inexistente, portanto nao deve ser inventado no dominio pedagogico.

## E) Fluxo do Aluno Escolar

1. Identidade autenticada resolve papel de estudante e escopo escolar; payload nao escolhe tenant.
2. Companheiro de estudos acolhe pelo nome exibivel da identidade e pergunta apenas o que ainda nao esta disponivel, progressivamente.
3. Context resolver usa ano, unidade, segmento, serie, turma e aulas registradas para formar o conjunto de conteudos relevantes. Conteudo previsto, mas ainda nao ensinado, nao entra como evidencia negativa.
4. Candidate policy inicia por pre-requisitos e conteudo ensinado/relevante, sempre com questoes elegiveis e visiveis no escopo.
5. Cada resposta atualiza a evidencia; erro complexo dispara investigacao de pre-requisito quando aplicavel.
6. Parada ocorre por suficiencia configurada, maximo ou falta de candidatos; insuficiencia e comunicada sem rotulo negativo.
7. Finalizacao atualiza `StudentContentMastery`, `LearningHistory` e devolutiva; recomendacao/trilha consome a mesma evidencia.

## F) Fluxo do Aluno Independente

1. Sem `school_id`, a identidade e independente e recebe conversa sobre objetivo, disciplina e tema desejado.
2. Texto aberto passa por `StudySearchService` para produzir contexto estruturado. Esse contexto prioriza a sondagem, mas nao limita a investigacao a um unico topico.
3. Candidate policy usa apenas conteudo publico/plataforma autorizado para aluno independente.
4. O mesmo motor investiga amplitude minima e pre-requisitos relacionados, gera mapa e proximos passos.

## G) Fluxo do Diagnostico Avulso

1. Uma identidade externa ja existente inicia com `entry_mode=STANDALONE` depois que a regra de consentimento for definida.
2. A sessao e independente ou vinculada a escola pelo contexto autenticado; nunca por um identificador livre do cliente.
3. Resultado permanece associado ao mesmo `external_identity_id` e pode ser reutilizado se esse sujeito receber posteriormente um `UserSchoolLink` consentido.
4. Sem contrato de identidade e responsavel, nao expor uma API de cadastro avulso nesta fase; isso evitaria identidade duplicada e coleta LGPD sem base definida.

## H) Fluxo da Devolutiva

O `FeedbackAssembler` deve produzir dados estruturados, com texto por audiencia:

| Audiencia | Conteudo |
| --- | --- |
| Aluno | "Voce ja domina", "Vale fortalecer", "Vamos aprender" e "Proximo passo"; sempre evidencia, confianca e recomendacao, sem nota inicial ou linguagem punitiva. |
| Responsavel | Visao individual, dominio por disciplina/conteudo, pre-requisitos, prioridade e proximos passos; somente apos contrato de vinculo e consentimento. |
| Escola | Agregados por escopo; nenhum dado individual fora da permissao concedida. |

Cada item deve incluir `evidence_count`, `confidence`, versao do algoritmo e marca de que e estimativa. "Lacuna" e uma hipotese com evidencia, nao um diagnostico definitivo.

## I) Fluxo de Acesso por Papel

| Papel | Acesso proposto |
| --- | --- |
| Aluno | Somente seus diagnosticos, retomada, resultado e mapa. |
| Responsavel | Somente alunos vinculados e consentidos; requer novo contrato de identidade. |
| Professor | Dados individuais de alunos em turmas autorizadas e agregados dessas turmas, usando `TeacherPortalService.verify_student_access`. |
| Coordenacao | Agregados e individuos somente em unidade/segmento/serie/turma do `UserSchoolLink`. |
| Direcao | Agregados institucionais no tenant autorizado; individuais apenas se a politica LGPD permitir explicitamente. |

Todas as verificacoes devem ocorrer antes de leitura ou escrita. Linguagem natural so pode preencher preferencia pedagogica; nunca papel, escola, escopo ou permissao.

## J) API Proposta

Evoluir o prefixo existente `/api/v1/student`, preservando os contratos atuais quando possivel:

| Operacao | Endpoint proposto | Observacao |
| --- | --- | --- |
| Criar/retomar | `POST /api/v1/student/diagnostic/start` | Contexto de tenant vem da identidade; request aceita somente preferencia pedagogica permitida. |
| Ler sessao | `GET /api/v1/student/diagnostic/{diagnostic_id}` | Corrigir leitura do modelo e validar propriedade antes de devolver. |
| Responder | `POST /api/v1/student/diagnostic/{diagnostic_id}/questions/{selection_id}/answer` | Validar propriedade e selecao antes de chamar o servico mutante. |
| Resultado | `GET /api/v1/student/diagnostic/{diagnostic_id}/result` | Adaptar para devolutiva acolhedora e estrutura de evidencia. |
| Mapa atual | `GET /api/v1/student/mastery-map` | Manter como visao consolidada. |

Rotas de professor/coordenacao/direcao devem ser adicionadas somente quando os servicos de portal receberem apresentadores de diagnostico e testes de escopo. Rota de responsavel depende do contrato pendente.

## K) Testes Necessarios

1. Contexto escolar deriva tenant, unidade, segmento, serie e turma da identidade e rejeita payload divergente.
2. Conteudo previsto/nao ensinado nao reduz dominio; conteudo ensinado e pre-requisitos relevantes sao priorizados.
3. Independente usa texto aberto para priorizar, preserva autorizacao e explora conteudos relacionados.
4. Questoes selecionadas sao PUBLISHED, elegiveis, classificadas, autorizadas e nunca repetidas indevidamente.
5. Acerto avanca com evidencia suficiente; erro complexo investiga pre-requisito antes de classificar lacuna.
6. Parada por minimo, maximo, estabilidade, cobertura e falta de candidatos; todos configuraveis e deterministas.
7. Retomada retorna somente a questao pendente do mesmo aluno.
8. IDOR: outro aluno, escola, turma, unidade ou serie nao le nem responde o diagnostico; a verificacao ocorre antes da escrita.
9. Fundamental e Medio, disciplinas distintas e diagnostico multidisciplinar quando definido pela politica.
10. Finalizacao grava resultados por questao e inferencias separadamente em `LearningHistory` e `StudentContentMastery`.
11. Devolutiva do aluno nao expoe nota punitiva; responsavel/escola respeitam consentimento e escopo.
12. Avulso preserva a mesma identidade ao receber vinculo escolar consentido.

## L) Migrations Necessarias

Nenhuma nesta etapa. A migration `013_initial_diagnostic` ja criou as tabelas do nucleo atual.

Uma migration posterior somente sera justificada por testes que demonstrem necessidade de persistir `entry_mode`, snapshots de contexto/consentimento, motivo de selecao ou tempo de resposta. Ela deve ser aditiva, auditavel e seguir a migration atual de cabeca; nao criar tabelas paralelas para questoes, conhecimento ou aluno.

## M) Riscos

1. Selecionar do catalogo global pode avaliar conteudo nao ensinado ou fora do tenant; e bloqueador pedagogico e de seguranca para expansao.
2. Autorizacao apos escrita e um risco IDOR bloqueador para endpoint de resposta.
3. A correspondencia Catalogo -> Taxonomia precisa ser definida para dados reais; UUIDs coincidentes em fixture nao constituem contrato.
4. Dados de responsavel, consentimento e perfil dependem do provedor de identidade; implementar localmente sem esse contrato criaria risco LGPD e identidade duplicada.
5. Cobertura atual do diagnostico e de servico, com apenas 6 testes; faltam testes HTTP, escopo real e cenarios pedagogicos.

## N) Ordem Recomendada de Implementacao

1. Corrigir e cobrir IDOR, origem do tenant e retomada antes de qualquer nova experiencia.
2. Definir contrato Catalogo -> Taxonomia e construir candidate policy com elegibilidade, visibilidade e escopo.
3. Integrar contexto escolar e `TeachingLesson`, diferenciando ensinado, previsto e pre-requisito.
4. Evoluir politica adaptativa e de parada com testes deterministas de evidencia por conteudo.
5. Criar apresentadores de devolutiva do aluno e integrar trilha/recomendacao usando os dados existentes.
6. Ampliar portais institucionais sob escopo real.
7. Somente apos decisao de identidade/consentimento, implementar perfil conversacional persistido, responsavel e diagnostico avulso completo.
8. Propor migration aditiva apenas para campos que os testes comprovarem nao caber nos contratos existentes.