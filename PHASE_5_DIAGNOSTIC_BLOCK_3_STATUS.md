# Phase 5 - Diagnostico Inicial - Bloco 3

Data: 2026-08-31
Status: NAO CONCLUIDO

## A) Implementado

- Contrato agnostico `ProficiencyEstimator`, `PedagogicalEvidence` e `ProficiencyEstimate`.
- `SimpleProficiencyEstimator` deterministico, ponderado por dificuldade, com score, confianca, faixa pedagogica e quantidade de evidencias.
- Registro de evidencia pedagogica por resposta no snapshot do diagnostico: versao da questao, conteudo, dificuldade, acerto, estimativa posterior, confianca e faixa.
- Finalizacao usa o estimador para atualizar o `StudentContentMastery` existente. Nenhum segundo mapa de dominio foi criado.

## B) Comprovado

- Mesmo conjunto de evidencias produz a mesma estimativa, incluindo score, confianca e faixa.
- Dificuldade influencia a estimativa simples.
- Uma resposta isolada produz confianca baixa, portanto nao constitui afirmacao definitiva de dominio.
- Resposta do diagnostico persiste evidencia com questao, versao, conteudo, dificuldade, resultado e estimativa.
- Selecao adaptativa, pre-requisito, elegibilidade, escopo, seguranca e retomada dos blocos anteriores continuam verdes.

## C) Nao Comprovado

- Cobertura nova e explicita de multiplas disciplinas, Fundamental e Medio no estimador.
- Politica de parada por suficiencia por conteudo/area e consistencia. A politica existente ainda usa minimo, maximo e confianca geral.
- Investigacao de pre-requisito apos evidencia consistente: a infraestrutura existe, mas o limiar de consistencia ainda nao foi formalizado e provado.
- Estimativa anterior por resposta nao esta persistida: o contrato atual registra a estimativa posterior e o resultado anterior pode ser reconstruido pela sequencia. Um campo/snapshot explicito deve ser avaliado em bloco futuro.

## D) Pendente

- Completar os criterios acima antes de declarar o motor adaptativo pronto.
- Integrar uma estrategia de cobertura equilibrada quando o objetivo for global, sem hardcode de disciplinas.
- TRI e MIRT permanecem deliberadamente fora do escopo; podem implementar `ProficiencyEstimator` no futuro.

## E) Riscos

- O estimador simples e uma linha de base pedagogica, nao uma medida psicometrica.
- Evidencias estao no metadata historico do diagnostico; consultas analiticas de grande volume podem justificar persistencia normalizada futura, mas nao houve necessidade comprovada de migration neste bloco.

## F) Testes e Resultados

- Testes focados: `tests/test_diagnostic_proficiency.py`, `tests/test_initial_diagnostic.py` e `tests/test_diagnostic_http_security.py`: 22 passed.
- Suite completa: 615 passed, 7 warnings conhecidos e 3 subtests passed.
- `compileall -q src tests`: sucesso.
- Alembic: `017_assignment_workflow_audit` e `upgrade head` executado com sucesso.

## G) Migration

Nenhuma migration criada. `LearningHistory`, `StudentContentMastery`, selecoes e `InitialDiagnostic.metadata_` atendem a persistencia desta primeira evidencia.

## H) Proximo Bloco

Formalizar cobertura e parada por suficiencia, e provar o motor em cenarios multidisciplinares e de segmentos antes de qualquer TRI/MIRT ou devolutiva visual.