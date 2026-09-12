# Phase 8B - Teacher Question Selection

Status: CONCLUIDO

## Objetivo e UX

- O portal docente permite escolher um caminho curricular em cascata e montar uma lista por selecao individual de questoes completas.
- Nao ha controle de selecao em massa.

## Arvore e Seguranca

- `GET /api/v1/catalog/nodes?parent_id=...` carrega apenas um nivel, em ordem deterministica.
- O endpoint resolve identidade e universo ativo no servidor; ramos externos retornam vazio.
- O ultimo `CatalogNode` selecionado e o conteudo canonico persistido no rascunho.

## Selecao

- Candidatos exibem enunciado, alternativas, dificuldade armazenada e fonte `Banco de Questoes`.
- Filtros de dificuldade e paginação nao removem itens ja selecionados.
- Revisao usa os itens persistidos do Assessment Core, com remover e reordenar por `AssessmentItem.position`.

## Provas

- HTTP SQLite: 4 testes, incluindo bypass de `parent_id`, paginação, filtros, persistencia e selecao individual.
- PostgreSQL dedicado: 9 testes passaram, incluindo a cobertura herdada de catalogo e builder.
- Suite completa: `707 passed`, `3 subtests passed`.
- Browser estatico do portal: desktop `1440x900` e mobile `390x844` sem overflow horizontal; quatro selects possuem labels e nao ha texto de selecao em massa no DOM.
- `compileall`, `node --check`, `git diff --check` e Alembic head `021_teacher_list_builder`: sucesso.

## Migrations e Limitacoes

- Nenhuma migration nova foi necessaria para 8B.
- O banco principal permaneceu em `019_reception_candidates`; PostgreSQL foi comprovado apenas em bancos descartaveis dedicados.
- A verificacao visual foi estatica porque o servidor compartilhado estava indisponivel; persistencia e seguranca foram comprovadas pelos E2Es HTTP.
- IA, edicao, PDF, cabecalho, gabarito e resolucao permanecem fora do escopo.

## Proximo Passo

- Phase 8C pode acrescentar derivacao versionada para edicao manual e modificacao de questoes. Coordenacao futura pode ampliar a consulta dentro de sua propria escola, mantendo a mesma autorizacao por tenant e universo.