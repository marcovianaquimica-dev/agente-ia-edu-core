# Phase 5 - Diagnostico Inicial - Bloco 3A

Data: 2026-08-31
Status: NAO CONCLUIDO

## A) Implementado

- `DiagnosticCoveragePolicy` deterministica e configuravel, separada do estimador de proficiencia.
- Estado derivado por conteudo: `NOT_EVALUATED`, `INSUFFICIENT_EVIDENCE`, `IN_PROGRESS`, `SUFFICIENT_EVIDENCE`, `POSSIBLE_GAP` e `CONSOLIDATED`.
- Suficiencia exige quantidade minima, diversidade de dificuldade e consistencia das respostas; nao depende apenas de contagem.
- Evidencia inconsistente permanece em investigacao e nao reduz conclusivamente o dominio.
- Possivel lacuna e investigacao de pre-requisito so surgem apos evidencia consistente de baixa estimativa.
- A evidencia persistida inclui estado de cobertura, inconsistencia e necessidade de investigar pre-requisito.
- A parada por confianca agora exige tambem suficiência do conteudo; o limite maximo continua encerrando com seguranca.

## B) Comprovado

- Evidencia consistente e diversa gera estado consolidado quando a estimativa suporta essa conclusao.
- Evidencia conflitante permanece `IN_PROGRESS`.
- Tres erros consistentes e diversos geram `POSSIBLE_GAP` e investigacao de pre-requisito.
- A parada por alvo de confianca nao ocorre se o conteudo ainda estiver insuficiente ou inconsistente.
- Selecao, elegibilidade, escopo, seguranca HTTP, retomada, aluno escolar e independente permanecem cobertos pelos blocos anteriores.

## C) Nao Comprovado

- Cobertura por area/disciplina com amostragem representativa de multiplos conteudos.
- Diagnostico multidisciplinar global, Fundamental e Medio com politica de cobertura dedicada.
- Reducao progressiva da hipotese de pre-requisito depois de resposta positiva no proprio pre-requisito.
- Incerteza residual explicitamente serializada no resultado final ao atingir limite maximo.

## D) Pendente

- Politica de cobertura entre conteudos e disciplinas, orientada pelos objetivos e contexto escolar.
- Testes de Fundamental, Medio e multiplas disciplinas para essa politica.
- Resultado final com inventario de conteudos nao avaliados e incertezas residuais.

## E) Riscos

- A politica atual avalia estados por conteudo; ainda nao decide quais conteudos compoem uma amostra global.
- A confianca simples continua intencionalmente anterior a TRI/MIRT.

## F) Testes e Resultados

- Focados: `tests/test_diagnostic_proficiency.py`, `tests/test_initial_diagnostic.py` e `tests/test_diagnostic_http_security.py`: 24 passed.
- Suite completa: 617 passed, 7 warnings conhecidos e 3 subtests passed.
- Compilacao: sucesso.
- Alembic: head `017_assignment_workflow_audit`; upgrade validado.

## G) Migration

Nenhuma migration criada. Estados e evidencias sao derivaveis das selecoes e persistidos no snapshot de diagnostico existente.

## H) Proximo Bloco

Implementar cobertura por area e multidisciplinaridade como politica de selecao, incluindo incerteza residual e testes de segmentos. TRI/MIRT, devolutiva e interface continuam fora de escopo.