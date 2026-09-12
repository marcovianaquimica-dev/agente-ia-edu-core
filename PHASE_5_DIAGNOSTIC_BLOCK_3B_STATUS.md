# Phase 5 - Diagnostico Inicial - Bloco 3B

Data: 2026-08-31
Status: NAO CONCLUIDO

## A) Implementado

- `GlobalDiagnosticCoveragePolicy` pura, configuravel e deterministica.
- Resumo por disciplina com evidencias, conteudos suficientes, confianca, cobertura percentual e investigacoes criticas.
- Priorizacao balanceada: disciplina sem cobertura precede disciplina ja suficientemente coberta; investigacao critica tem prioridade temporaria.
- Parada global exige minimo de evidencias, cobertura minima por disciplina, confianca alvo e ausencia de investigacao critica; `maximum_evidence` encerra sempre.
- `GlobalDiagnosticSummary` preserva incerteza residual como disciplinas abaixo da cobertura minima.

Valores iniciais documentados na politica: minimo global 6 evidencias, maximo 20, cobertura minima por disciplina 50% e confianca alvo 0.6. Sao defaults configuraveis, nao regras psicometricas permanentes.

## B) Comprovado

- Cobertura baixa em Biologia e cobertura alta em Matematica faz Biologia ser priorizada.
- Incerteza residual lista a disciplina ainda insuficientemente conhecida.
- Parada global nao ocorre com uma disciplina sem evidencia, mesmo com outras cobertas.
- Maximo de evidencias encerra com incerteza residual preservada.
- Resumo e ordem de prioridade sao identicos para os mesmos dados, independentemente da ordem de entrada.
- Testes dos blocos anteriores continuam validando aluno escolar/independente, conteudo ensinado, escopos, elegibilidade, IDOR e retomada.

## C) Nao Comprovado

- Uma sessao `InitialDiagnostic` ainda seleciona dentro de uma unica disciplina ativa. A politica global existe, mas nao esta conectada a um orquestrador que alterne disciplinas/areas numa mesma sessao.
- Areas pedagogicas superiores dependem de uma convencao de `CatalogNode.node_type`/hierarquia ainda nao exercida pela selecao.
- Evolucao da hipotese de pre-requisito apos respostas diretas no prerequisito nao foi implementada.
- Fundamental e Medio, ENEM e objetivo global nao possuem cenarios executados pela politica de sessao multidisciplinar.

## D) Pendente

- Orquestrador de sessao global que consulte `GlobalDiagnosticCoveragePolicy`, escolha a proxima disciplina e registre resumo no diagnostico.
- Mapeamento de area no catalogo e testes reais de Fundamental, Medio, ENEM e objetivo "ainda nao sei" na mesma sessao.
- Atualizacao gradual de hipotese de pre-requisito com evidencia favoravel e contraria.

## E) Riscos

- A politica pronta sem orquestracao de sessao nao altera, por si so, o fluxo de uma sessao monodisciplinar.
- O modelo simples continua uma base pedagogica, nao TRI/MIRT.

## F) Testes e Resultados

- Focados: `tests/test_diagnostic_proficiency.py`, `tests/test_initial_diagnostic.py` e `tests/test_diagnostic_http_security.py`: 27 passed.
- Suite completa: 620 passed, 7 warnings conhecidos e 3 subtests passed.
- Compilacao: sucesso.
- Alembic: head `017_assignment_workflow_audit`; upgrade validado.

## G) Migration

Nenhuma migration criada. A politica deriva cobertura das evidencias existentes e nao cria novo banco pedagogico.

## H) Proximo Bloco

Conectar a politica global a uma sessao explicitamente multidisciplinar, com hierarquia de areas do catalogo e testes de segmentos/objetivos. Nao avancar para interface, dashboards ou devolutiva.