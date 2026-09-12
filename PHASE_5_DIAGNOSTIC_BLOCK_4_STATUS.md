# Phase 5 - Diagnostico Pedagogico Adaptativo - Bloco 4

Data: 2026-08-31
Status: NAO CONCLUIDO

## Auditoria Inicial e Arquitetura Reutilizada

O bloco reutiliza `InitialDiagnostic`, selecoes, `LearningHistory`, `StudentContentMastery`, `ProficiencyEstimator`, `DiagnosticCoveragePolicy`, `GlobalDiagnosticCoveragePolicy`, `TeachingLesson`, Question Bank, CatalogNode e PedagogicalUniverse. Nenhuma identidade, banco de questoes, mapa de dominio ou modelo estatistico paralelo foi criado.

## A) Implementado

- Resposta explicita `is_unknown` na API e persistencia como `UNKNOWN`, separada de resposta incorreta comum.
- Evidencia por resposta preserva `response_kind`, versao da questao, conteudo, dificuldade, acerto, estimativa posterior, confianca, faixa e cobertura.
- Resultado do diagnostico agora usa `ProficiencyEstimator` e `DiagnosticCoveragePolicy`, em vez de reverter para divisao bruta de acertos.
- Saida separa `raw_result` (corretas, incorretas, desconhecidas) de `pedagogical_states` (cobertura, confianca, inconsistencia).
- Selecao permanece deterministica, contextual, escopada pelo PedagogicalUniverse e orientada por dificuldade/pre-requisito ja existentes.
- `DiagnosticDecisionPolicy` pura centraliza decisoes `CONTINUE`, `FINISH_SUFFICIENT`, `FINISH_TIME` e `CONTINUE_PREREQUISITE_INVESTIGATION` com janelas temporais configuraveis por modo.
- O ciclo real calcula tempo por timestamps existentes, persiste decisao/tempo no diagnostico e encerra por suficiencia, hard limit ou teto de questoes.
- O resultado agora inclui duracao, motivo de encerramento e ultima decisao pedagogica.
- Em `GLOBAL`, `ENEM` e `UNSPECIFIED`, o ciclo agora deriva cobertura por disciplina das selecoes persistidas, inclui disciplinas elegiveis sem evidencia como incerteza residual e impede encerramento local enquanto a cobertura global estiver incompleta.
- `PrerequisiteHypothesisPolicy` pura cria e evolui hipoteses no snapshot existente: `SUSPECTED`, `UNDER_INVESTIGATION`, `SUPPORTED`, `REFUTED` e `INCONCLUSIVE`, com contagens favoraveis, contrarias, UNKNOWN, confianca, timestamps e IDs de evidencia.
- `DiagnosticConcentrationPolicy` pura e configuravel limita repeticao consecutiva por conteudo e disciplina, retornando `CONTINUE_SAME_CONTENT`, `SWITCH_CONTENT` ou `SWITCH_DISCIPLINE`; o seletor registra a decisao no trace.
- O seletor passou a aceitar tanto catalogos legados com `DISCIPLINE` como raiz quanto `AREA -> DISCIPLINE -> CONTENT`, reutilizando `root_id` sem nova taxonomia ou schema.

## B) Comprovado

- "Nao sei" e registrado e contabilizado como `unknown`, sem ser classificado como resposta incorreta no resultado bruto.
- Suficiencia encerra cedo; evidencia insuficiente continua; soft limit exige prioridade informacional; hard limit encerra obrigatoriamente; lacuna consistente direciona investigacao de prerequisito.
- Um ciclo GLOBAL com evidencia em Matematica registra Quimica como incerteza residual, decide `GLOBAL_COVERAGE_INCOMPLETE` e seleciona Quimica antes de encerrar.
- Erros no pre-requisito evoluem gradualmente para suporte; acertos evoluem para refutacao; UNKNOWN nao conta como suporte ou contradicao e preserva incerteza.
- A politica de concentracao decide deterministicamente troca de conteudo/disciplina quando ha alternativa e permite continuidade quando nao ha alternativa disponivel.
- Integracao real: duas QuestionVersions elegiveis em Citologia e uma em Genetica, com limite de uma questao consecutiva, levam o seletor a Genetica e persistem `SWITCH_CONTENT` no `selection_trace`.
- Testes de catalogo, diagnostico multidisciplinar e universo confirmam a compatibilidade da selecao com a hierarquia curricular existente.
- E2E HTTP: Ensino Fundamental (9_ANO) e Ensino Medio (3_SERIE) selecionam apenas questoes do segmento/serie autenticados, atravessando entrada, perfil, selecao, resposta/UNKNOWN e resultado.
- E2E HTTP ENEM: parceiro `QUIMICA_ENEM` com `AREA -> DISCIPLINE -> CONTENT` congela universo/owner/versao, seleciona somente Quimica e bloqueia `requested_universe_id` externo.
- Evidencia, estimador, cobertura, sufficiencia, modos DISCIPLINE/GLOBAL/OBJECTIVE/ENEM/UNSPECIFIED, restricao de universo, IDOR e retomada continuam sob teste.
- Testes focados: 49 passed.
- Suite completa: 642 passed, 7 warnings conhecidos e 3 subtests passed.
- `compileall`: sucesso; Alembic head `018_pedagogical_universe`, upgrade sucesso; `git diff --check` dos arquivos do bloco passou.

## C) Nao Comprovado

- Nao ha teste E2E unico cobrindo entrada, varias adaptacoes, sufficiencia global e encerramento.
- O E2E combinado foi iniciado com relogio HTTP injetavel e catalogo real, mas revelou um defeito de prioridade: em uma arvore `DISCIPLINE -> prerequisite -> target`, o seletor pode escolher o prerequisito antes de acumular a evidencia minima no conteudo-alvo. Sem corrigir essa regra, nao e possivel provar honestamente a sequencia `target com baixa evidencia -> hipotese -> investigacao do prerequisito` na mesma sessao.
- A investigacao posterior confirmou que o perfil HTTP persiste o conteudo-alvo, mas o seletor GLOBAL ainda pode escolher uma disciplina alternativa antes dele. A prioridade de conteudo-alvo precisa ser diagnosticada no caminho de candidatos/autorizacao antes de uma nova tentativa de E2E combinado.

## D) Pendente

- Diagnosticar e corrigir a prioridade de conteudo-alvo no caminho HTTP GLOBAL e, entao, criar o E2E unico que combina hipotese, concentracao, tempo controlado, cobertura e encerramento.

## E) Riscos

- "UNKNOWN" representa incerteza declarada, mas a inferencia continua simples e nao diagnostica psicologicamente motivo ou chute.
- O estimador simples permanece substituivel pelo contrato `ProficiencyEstimator`; TRI/MIRT continuam fora de escopo.

## F) Decisao Final do Bloco 4

**BLOCO 4 - NAO CONCLUIDO.** A decisao temporal/local, cobertura global multidisciplinar, evolucao de hipoteses, concentracao integrada e E2Es HTTP de segmentos/ENEM foram comprovados, mas ainda falta o E2E combinado do ciclo adaptativo completo, bloqueado pela prioridade do conteudo-alvo versus seu prerequisito.