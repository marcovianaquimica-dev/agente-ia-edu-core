# Phase 5 - Diagnostico Inicial - Bloco 1

Data: 2026-08-31
Status: CONCLUIDO

## A) Ja Existia

- `InitialDiagnostic` e `DiagnosticQuestionSelection` para sessao adaptativa e respostas.
- `StudentContentMastery` e `LearningHistory` para mapa de dominio e evidencia por resposta.
- Question Bank versionado, catalogo, classificacoes, contexto de aula, autorizacao e identidade externa.
- Rotas de inicio, retomada, resposta, resultado e mapa de dominio.

## B) Foi Reutilizado

- Question Bank existente por meio de `Question`, `QuestionVersion` e `ContentQuestionLink`.
- Regras de governanca existentes: status publicado, versao oficial, validacao e dificuldade classificada.
- Catalogo para disciplina/conteudo/pre-requisito e `TeachingLesson` para conteudo efetivamente ensinado.
- Identidade externa e `school_id` do contexto autenticado; aluno independente continua com `school_id=None`.
- `LearningHistory` e `StudentContentMastery`; nenhum segundo mapa foi criado.

## C) Foi Alterado

- `InitialDiagnosticService.answer_question()` recebeu contexto autorizado e bloqueia proprietario ou escola divergente antes de qualquer mutacao.
- Respostas duplicadas sao bloqueadas antes da persistencia.
- A rota de inicio ignora `school_id` e turma recebidos do cliente quando a identidade fornece o contexto; ela registra snapshot do contexto no metadata existente.
- A rota de retomada agora busca `InitialDiagnostic`, nao a classe de servico.
- Resultado e resposta agora validam identidade e tenant.
- Selecao passou a limitar-se a disciplina do diagnostico, conteudo ligado ao catalogo, versoes oficiais publicadas, validacao aceita, dificuldade definida e visibilidade compativel.
- Para aluno escolar, aulas registradas no ano/turma priorizam conteudo ensinado; sem aulas, o sistema nao presume que qualquer conteudo foi ensinado.
- Erro gera evidencia de possivel lacuna no pre-requisito, com confianca e origem, sem afirmar ausencia de dominio.
- `metadata` existente registra o snapshot de contexto e o trace deterministico de selecao. Nao foi necessaria migration.

## D) Problemas Encontrados

- IDOR na rota de resposta: verificacao ocorria depois de chamar o metodo mutante.
- Retomada consultava a classe errada.
- Payload podia escolher `school_id` e `classroom_id`.
- Seletor global nao aplicava disciplina, vinculo catalogo-questao, ciclo de vida ou visibilidade.
- Pre-requisito era somente uma flag booleana.

## E) Correcoes Realizadas

As correcoes acima foram implementadas mantendo o agregado de diagnostico e os modelos existentes. O contexto e a trilha permanecem historicos por estarem armazenados no diagnostico no momento de inicio; alteracoes futuras de turma ou serie nao reescrevem esse snapshot.

## F) Testes Executados

- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_initial_diagnostic.py -q`
- Resultado apos as alteracoes: 13 testes passando.
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_initial_diagnostic.py tests/test_diagnostic_http_security.py -q`: 16 testes passando.
- `PYTHONPATH=src .venv/bin/python -m pytest -q`: 609 testes passando, 7 warnings conhecidos e 3 subtests passando.
- `PYTHONPATH=src .venv/bin/python -m compileall -q src tests`: sucesso.
- `alembic heads`, `alembic history` e `alembic upgrade head`: sucesso, head `017_assignment_workflow_audit`.

Cobertura adicionada: escrita nao autorizada sem efeitos colaterais, independente versus escolar, elegibilidade/visibilidade, retomada sem duplicacao, evidencia de pre-requisito e texto livre sem elevacao de autoridade.

Cobertura de fechamento: criterio combinado `SCHOOL + UNIT + SEGMENT + GRADE_LEVEL + CLASSROOM` seleciona somente com correspondencia integral; um mismatch de unidade, segmento ou serie elimina o candidato. Testes HTTP reais comprovam leitura propria permitida, leitura/resposta cross-tenant bloqueadas sem mutacao e `school_id` do payload ignorado em favor da identidade.

## G) Evidencias

- Tentativa de outro aluno nao altera selecao, score, progresso, estado, historico ou mapa de dominio.
- Um independente nao responde diagnostico escolar.
- Questoes privadas ou nao publicadas nao sao selecionadas para independente.
- Inicio repetido retorna a sessao e a questao pendente, preservando contexto e progresso.
- Resultado separa observacao da inferencia `possible_prerequisite_gap` e sua confianca.

## H) Migrations

Nenhuma migration foi criada. `metadata` de `InitialDiagnostic` ja suporta snapshot de contexto e rastreabilidade de selecao, e as tabelas existentes suportam sessao, resposta, historico e dominio.

## I) Riscos

- Unidade, segmento e serie sao aplicados como restricoes de `metadata_` no mesmo Question Bank, junto de `PUBLIC`, `SCHOOL` e `CLASSROOM`. Uma futura normalizacao desses metadados deve ser desenhada como evolucao de governanca, nao como segundo mecanismo de diagnostico.
- A confianca ainda e deterministica e simples; nao e um estimador TRI/MIRT.
- Vínculo responsavel-aluno e consentimento dependem do provedor de identidade e permanecem fora deste bloco.
- `git diff --check` aponta espacos em branco em `src/agente_ia_edu/api/routes/questions.py`, alteracao preexistente e fora deste bloco; os arquivos alterados aqui nao introduziram esse erro.

## J) Ainda Nao Implementado

- Conversa de perfil, interface acolhedora e devolutiva visual.
- Dashboard de responsavel, coordenacao e direcao.
- Funil de conversao do diagnostico avulso e contrato de consentimento.
- TRI, MIRT, gamificacao e recomendacoes avancadas.

## K) Conclusao

**PHASE 5 - DIAGNOSTICO / BLOCO 1: CONCLUIDO.**

O nucleo esta protegido antes da persistencia, deriva tenant da identidade, seleciona conteudo de forma contextual e governada em `SCHOOL`, `UNIT`, `SEGMENT`, `GRADE_LEVEL` e `CLASSROOM`, preserva retomada e suporte independente, registra evidencia de pre-requisito e atualiza o mapa de dominio existente. A Phase 5 inteira nao esta concluida.