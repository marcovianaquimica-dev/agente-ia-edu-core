# PHASE 6C - Adaptive Practice and Learning Path

## A) IMPLEMENTADO

- `POST /api/v1/practice/sessions` consulta o `DomainMapService` e usa a `NextBestActionPolicy` quando `content_node_id` nao e informado.
- Conteudo explicito e validado quanto a existencia, universo autorizado e disponibilidade de questoes elegiveis.
- Elegibilidade canonica compartilhada entre diagnostico e pratica por `QuestionSelectionRepository.list_eligible_candidate_versions`.
- Selecao canonica considera publicacao, validacao, visibilidade, `ContentQuestionLink`, dificuldade, escopo academico, universo e historico recente.
- Questoes frescas em outras dificuldades precedem repeticoes; repeticao controlada permanece disponivel quando nao ha alternativa.
- Consultas de candidatos frescos/repetidos recebem `LIMIT` no SQL.
- Listagem de questoes da sessao carrega versoes/opcoes em lote, removendo N+1.
- `is_unknown` adicionado ao contrato de resposta da pratica.
- UNKNOWN e persistido como `response_text = UNKNOWN`, `is_correct = null`, sem ser contabilizado como erro ou acerto.
- Atualizacao de dominio permanece exclusivamente na conclusao da sessao, evitando dupla contabilizacao.
- `PracticeSession.metadata` registra snapshot da recomendacao, universo, conteudo, origem da selecao, contexto academico e contexto pedagogico.
- Identidade escolar da pratica usa a mesma resolucao enriquecida do diagnostico.
- Import legado incorreto do resolvedor de gabarito foi corrigido para o servico compartilhado existente.
- Caminho legado por `TaxonomyNode/QuestionClassification` foi preservado para chamadas antigas sem universo; o caminho atual usa `CatalogNode/ContentQuestionLink`.

## B) COMPROVADO

- Next Best Action e a fonte de uma pratica sem conteudo explicito.
- Conteudo recomendado chega a `PracticeSession.content_node_id`.
- Conteudo explicito inexistente retorna 404.
- Conteudo fora do universo retorna 403.
- Conteudo sem questoes elegiveis retorna 409 e nao deixa sessao orfa.
- Questao fora do conteudo, privada, nao publicada ou nao validada nao e selecionada.
- Questao recente e evitada quando existe alternativa fresca.
- Questao recente pode ser reutilizada quando e a unica alternativa elegivel.
- Alternativa fresca em outra dificuldade precede repeticao e registra a dificuldade real.
- UNKNOWN permanece separado de erro no historico e no Domain Map.
- Conclusao registra `INDIVIDUAL_PRACTICE` em `LearningHistory`.
- Conclusao atualiza o mesmo `StudentContentMastery` existente.
- Domain Map reflete a nova evidencia e o novo dominio.
- Next Best Action e recalculada sob demanda e muda coerentemente apos a pratica.
- Contexto de professor influencia prioridade, sem alterar artificialmente mastery.
- Aluno independente percorre Domain Map -> Next Best Action -> pratica sem escola.
- Diagnostico, avaliacao oficial e pratica continuam separados por `activity_type`.
- Snapshot registra universo, configuracao, owner, decisao e contexto.

## C) NAO COMPROVADO

- Provedor de identidade de producao; os E2Es usam o contrato de teste existente.
- Sessao multidisciplinar com questoes misturadas. O bloco seleciona uma proxima acao/conteudo por sessao; o balanceamento global continua no diagnostico/Domain Map.
- Agregacao de sessoes por escola, pois `PracticeSession` nao possui coluna escolar. Isso nao e necessario para isolamento por aluno nem para o ciclo Phase 6C.

## D) PENDENCIAS

- Unificar futuramente o endpoint visual `/student/learning-path` com a mesma Next Best Action usada pela criacao de pratica.
- Avaliar paginacao/snapshots incrementais do Domain Map para volumes muito grandes.
- Decidir em bloco proprio se UNKNOWN deve alterar um agregado persistido de incerteza alem do historico atual.
- Remover o caminho legado Taxonomy somente apos migracao completa dos consumidores para CatalogNode.

O Question Bank Ingestion Engine nao foi iniciado. Quando necessario alimentar o banco, o piloto planejado permanece Quimica ENEM 2020-2025 em bloco separado.

## E) RISCOS

- `StudentContentMastery.content_node_id` e `PracticeSession.content_node_id` mantem FK historica para `taxonomy_nodes`; o caminho canonico depende de IDs alinhados entre `CatalogNode` e `TaxonomyNode`.
- Domain Map ainda carrega todo o historico do aluno; para 100.000+ alunos, a consulta e por aluno e indexada, mas historicos individuais muito longos exigirao janela/agregacao futura.
- A recomendacao gravada no metadata e snapshot auditavel; ela nao e reutilizada como decisao atual depois da conclusao.
- Sessoes criadas antes deste incremento nao possuem o novo snapshot, por desenho compativel.

## F) ARQUIVOS ALTERADOS

- `src/agente_ia_edu/api/routes/learning_path.py`
- `src/agente_ia_edu/api/schemas/learning_path.py`
- `src/agente_ia_edu/services/learning_path.py`
- `src/agente_ia_edu/repositories/learning_path.py`
- `tests/test_learning_path_flow.py` (fixture ajustada para o requisito de questao elegivel)
- `tests/test_phase6c_adaptive_practice.py`
- `tests/test_phase6c_postgresql_e2e.py`
- `PHASE_6C_ADAPTIVE_PRACTICE_LEARNING_PATH_STATUS.md`

Nenhuma migration ou arquivo frontend foi alterado neste incremento.

## G) TESTES

- Phase 6C integrado + PostgreSQL: `12 passed`.
- Phase 6C + practice/learning path legado: `62 passed` antes do snapshot final.
- Regressao pedagogica ampliada: `112 passed`.
- Regressao focada com E2E legado: `63 passed`.
- Suite completa final: `684 passed`, `3 subtests passed`, `10 warnings` nao bloqueantes.
- Frontend Phase 6B: `11 passed`.
- `compileall`: OK.
- Alembic code/banco principal: `019_reception_candidates (head)`.
- `git diff --check`: OK.

## H) E2E

O teste `tests/test_phase6c_postgresql_e2e.py` cria e remove exclusivamente `agente_ia_edu_phase6c_test` e prova via FastAPI + PostgreSQL:

`GET Domain Map`
-> `POST Practice Session` sem conteudo
-> conteudo da Next Best Action
-> `GET Next Question`
-> `POST Answer`
-> `POST Complete`
-> `GET Result`
-> `GET Domain Map` novamente
-> nova evidencia, mastery atualizado e Next Best Action recalculada.

O E2E nao usa sleep nem relogio como criterio de decisao e limpa o banco dedicado ao final.

## I) DECISAO FINAL

**PHASE 6C - CONCLUIDO**

O ciclo `TRILHA -> PRATICA -> EVIDENCIA -> DOMINIO -> NOVA TRILHA` foi comprovado em HTTP com PostgreSQL real. Testes focados e suite completa passaram, compileall esta limpo, Alembic esta alinhado e `git diff --check` passou.
