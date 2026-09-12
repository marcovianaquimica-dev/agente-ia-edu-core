# Phase 8C - Teacher Question Modification

Status: ETAPAS 1A, 1B E 2 CONCLUIDAS

## Etapa 1A - Proposta e Cancelamento

- `ModificationProposal` persiste versao original, item da lista, escola, solicitante, instrucao, tipo, conteudo estruturado, provider/modelo e status.
- O adapter usa `ProviderRouter` e valida JSON, enunciado, alternativas distintas, gabarito, dificuldade e tipo de modificacao antes da persistencia.
- Politica, instrucao e `QUESTION_DATA` sao separados no prompt; a questao e tratada como dado nao confiavel.
- O limite por questao/solicitante e configuravel por `QUESTION_MODIFICATION_MAX_PROPOSALS`.
- Cancelamento e idempotente e nao altera questao, versao nem item.

## Etapa 1B - Aceitacao

- `PENDING -> ACCEPTED` ocorre em uma unica transacao.
- A aceitacao revalida escola, ownership da lista, item alvo e universo do professor.
- Cria `QuestionVersion` derivada com `parent_version_id` apontando para a original, copia alternativas propostas e valida o gabarito antes do commit.
- Atualiza somente o `AssessmentItem` vinculado a proposta; outras listas conservam a versao original.
- Repetir `accept` retorna a mesma versao aceita, sem criar outra derivada. Proposta cancelada nao pode ser aceita.
- `AssessmentWorkflowAudit` registra `QUESTION_MODIFICATION_ACCEPTED` com proposta, item e versoes.
- Proposta estruturalmente invalida no momento de aceitar e rejeitada sem versao derivada, alteracao de item ou estado `ACCEPTED`.

## Persistencia e Migration

- `022_modification_proposals` cria a tabela minima para estado, rastreabilidade, cancelamento e versao aceita.
- A migration foi corrigida antes de aplicacao externa para guardar `accepted_question_version_id`, timestamp e autor, necessarios para auditoria e idempotencia.
- Nenhuma migration foi aplicada ao banco principal.

## Provas

- SQLite e PostgreSQL dedicado: `32 passed` nos contratos de proposta, cancelamento, aceitacao, idempotencia, rollback, original preservada, outras listas preservadas, universo e coordenacao da mesma escola.
- Regressao completa antes das ultimas assercoes de teste: `736 passed`, `3 subtests passed`.
- `compileall`, erros estaticos, `git diff --check` e Alembic head `022_modification_proposals`: sucesso.

## Limitacoes

- Nao ha geracao por provedor externo configurada; os testes injetam provider fake estruturado.
- PDF, gabarito de documento, resolucao, IA de listas e ingestao continuam fora do escopo.

## Etapa 2 - UI Modificar Questao

- Cada questao da lista possui `Modificar questao` no proprio card.
- O modal oferece opcoes rapidas e `Outro` com instrucao livre, loading, erro recuperavel e protecao contra clique duplicado.
- A proposta compara original e modificada com enunciado, alternativas e nivel; no desktop aparecem lado a lado e no mobile em coluna.
- Aceitar usa o endpoint existente e recarrega somente a lista atual. Cancelar solicita cancelamento da proposta pendente e fecha o modal.
- Versoes derivadas retornam uma indicacao discreta de questao modificada.
- E2E de navegador com respostas HTTP simuladas comprovou abrir, selecionar `MAKE_EASIER`, gerar proposta e exibir comparacao, sem controles de selecao em massa.
- Suite persistida `tests/test_phase8c_question_modification_frontend.js` cobre controles acessiveis, opcoes rapidas, `Outro`, contratos de proposta/aceitacao/cancelamento, guards contra duplo clique, loading, erro recuperavel, comparacao e regras responsivas.
- O E2E exercitou a chamada de proposta e a comparacao; os contratos de endpoint e a logica de aceitar/cancelar sao verificados pela suite persistida e pelos testes backend da Etapa 1B.
- Desktop `1440x900` mostrou duas colunas sem overflow; mobile `390x844` mostrou uma coluna sem overflow. Os controles possuem nomes acessiveis e labels para instrucao livre.

## Proximo Passo

Nao avancar automaticamente. A proxima fase planejada e 8D, para configurar provider externo de IA e geracao, mantendo este contrato e UI.

## Provider Real de Desenvolvimento

- OpenAI foi conectado como provider padrao da rota de propostas, sempre atraves de `ProviderRouter` e do adapter estruturado existente.
- `OPENAI_API_KEY`, `OPENAI_MODEL` e `OPENAI_TIMEOUT_SECONDS` configuram a integracao por ambiente. Nenhuma credencial esta em codigo, teste ou documento.
- O SDK oficial `openai` foi adicionado como dependencia. Falhas de configuracao, timeout, limite, autenticacao ou indisponibilidade retornam erros controlados.
- `tests/test_openai_provider.py` testa o adapter sem rede; `tests/manual/openai_question_modification_smoke.py` e a prova explicita de uma questao e nao aceita a proposta.
- A prova real permanece pendente nesta sessao porque `OPENAI_API_KEY` e `OPENAI_MODEL` nao estavam configuradas. Nenhuma chamada externa foi feita e nenhuma proposta foi aceita automaticamente.