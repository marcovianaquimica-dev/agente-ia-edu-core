# Phase 5 - Diagnostico Inicial - Bloco 3C

Data: 2026-08-31
Status: NAO CONCLUIDO

## A) Implementado

- Modos de diagnostico persistidos no perfil existente: `DISCIPLINE`, `OBJECTIVE`, `GLOBAL`, `ENEM` e `UNSPECIFIED`.
- `priority_disciplines` permite restringir o universo no modo `OBJECTIVE` sem criar nova taxonomia.
- Em `GLOBAL`, `ENEM` e `UNSPECIFIED`, o seletor considera todas as raizes `CatalogNode` do tipo `DISCIPLINE` elegiveis no contexto atual.
- O seletor contabiliza as selecoes por `root_id` e ordena os conteudos da disciplina com menor quantidade de evidencias antes das ja investigadas.
- O trace de selecao grava raiz da disciplina e modo, preservando auditoria e determinismo.
- O modo `DISCIPLINE` preserva o universo monodisciplinar existente.

## B) Comprovado

- Um diagnostico global com Matematica e Quimica seleciona Matematica inicialmente e, apos a resposta, seleciona Quimica, que ainda nao tinha evidencia.
- Um diagnostico por disciplina nao sai da raiz solicitada.
- Um diagnostico por objetivo seleciona apenas as disciplinas priorizadas.
- O Question Bank continua sendo consultado com elegibilidade, visibilidade, tenant e escopo antes da selecao.
- Blocos anteriores seguem cobrindo conteudo ensinado, aluno escolar e independente, IDOR, tenant, retomada e linguagem natural sem bypass.
- Testes focados: 30 passed. Suite completa: 623 passed.

## C) Nao Comprovado

- Area pedagogica formal: o catalogo atual possui raiz de disciplina e arvore livre, mas nao ha convencao de area exercida pelo orquestrador (`AREA -> DISCIPLINE`).
- Modo ENEM usa o universo global por design generico; nao existe configuracao pedagogica versionada de areas/disciplina do ENEM.
- Fundamental e Medio em modo multidisciplinar, com universo e cobertura proprios, nao foram executados em teste de sessao.
- Limite de concentracao por disciplina e excecao temporaria por lacuna critica ainda nao foram integrados ao seletor.
- Atualizacao gradual de hipotese de pre-requisito apos evidencias contrarias ainda nao foi implementada.
- `GlobalDiagnosticCoveragePolicy` nao esta ligado ao encerramento real de uma sessao multidisciplinar; a parada da sessao continua baseada na cobertura local e teto maximo.

## D) Pendente

- Definir no catalogo a convencao ou configuracao de area pedagogica reutilizavel.
- Conectar cobertura global, area e disciplina ao loop de selecao/parada da sessao.
- Adicionar cenarios reais Fundamental, Medio, ENEM configurado, incerteza residual final e concentracao maxima.

## E) Riscos

- Equilibrio atual e por numero de selecoes por disciplina, nao por cobertura de area; isto evita concentracao inicial, mas nao substitui politica curricular configurada.
- O catalogo livre nao deve receber uma nova taxonomia paralela; a hierarquia de area precisa ser acordada sobre `CatalogNode`/metadata existente.

## F) Proximo Bloco

Formalizar configuracao de areas e integracao de cobertura global no ciclo completo da sessao, sem implementar interface, dashboards, devolutiva ou TRI/MIRT.

## G) Migration

Nenhuma migration criada. Os modos e prioridades pertencem ao perfil pedagogico historico em `InitialDiagnostic.metadata_`.