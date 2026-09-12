# Phase 5 - Pedagogical Universe - Bloco 3D.3

Data: 2026-08-31
Status: CONCLUIDO

## A) Implementado

- API administrativa minima em `/api/v1/admin/pedagogical-universes` para criar, consultar, atualizar configuracao, associar escopo de catalogo e criar binding.
- Operacoes estruturais exigem `PLATFORM_ADMIN`; professor, coordenacao e direcao nao receberam permissao estrutural implicita.
- `GET /api/v1/student/diagnostic/universes` lista somente universos resolvidos pelos bindings da identidade autenticada.
- `requested_universe_id` e apenas uma preferencia: e validado pelo resolvedor e retorna `403` quando externo.
- Inicio normal e entrada do diagnostico resolvem servidor-side o universo e congelam id, owner, external id e `configuration_version` no snapshot existente.
- Seletor do diagnostico elimina nos de catalogo fora do universo antes de consultar candidatos no Question Bank.
- Listagem administrativa, ativacao/desativacao e remocao auditada de catalog scopes e bindings foram adicionadas sob `PLATFORM_ADMIN`.
- A consulta de candidatos agora recebe `universe_id` e aplica predicado SQL de escopo curricular antes de materializar QuestionVersions.
- Cenario HTTP generico de parceiro `QUIMICA_ENEM`, configurado por universe e catalog scope, sem entidade ou regra especial para Quimica.

## B) Comprovado

- HTTP: aluno sem papel administrativo recebe `403` para criar universo; `PLATFORM_ADMIN` cria universo.
- HTTP: aluno lista somente universo vinculado a sua escola, e o inicio do diagnostico congela esse universo.
- HTTP: solicitar universo externo recebe `403` antes de criar diagnostico.
- Dominio: escola A resolve seus dois universos e nao resolve universo de escola B; independente e parceiro resolvem por binding sem `school_id`.
- Question Bank: descendente de AREA pertence ao universo; Matematica fora da AREA e sua versao de questao nao pertencem.
- Focados: 26 passed. Suite completa: 629 passed. Compilacao e Alembic: sucesso, head `018_pedagogical_universe`.
- A regressao apos lifecycle e consulta SQL: 29 testes focados e 629 na suite completa.
- Gate SQL: `test_candidate_query_filters_competing_eligible_questions_by_universe` prova que candidatos publicados, oficiais e elegiveis concorrentes retornam Quimica sob scope ancestral `AREA` e excluem Matematica fora do universo no repositorio.
- Gate parceiro HTTP: `test_partner_chemistry_universe_selects_only_chemistry_and_blocks_escape` prova snapshot de universo/owner/versao, selecao exclusiva de Quimica apos entrada, texto livre sem expansao e `403` sem nova sessao ao solicitar universo externo.
- Validacao final: 31 testes focados, 631 testes na suite completa, compilacao e Alembic em `018_pedagogical_universe` passaram; `git diff --check` dos arquivos deste fechamento passou.

## C) Nao Comprovado

- Contexto de parceiro de producao depende de `product_context` autenticado fornecido pelo host; o contrato ja e suportado, mas o provedor real nao foi exercido.

## D) Pendente

- Integrar o resolvedor em pratica, busca, materiais e listas em blocos posteriores.

## E) Riscos

- A migracao usa owner polimorfico externo sem FK para parceiro/produto, por decisao arquitetural; a confianca depende do host autenticar `product_context` corretamente.
- Diagnosticos sem binding mantem compatibilidade para fluxos existentes; uma futura regra de produto pode tornar universo obrigatorio de forma versionada.

## F) Proximo Bloco

**BLOCO 3D.3: CONCLUIDO.** Os gates de consulta SQL e parceiro HTTP foram comprovados. O proximo trabalho, quando solicitado, deve integrar o resolvedor aos demais consumidores, sem avancar automaticamente para dashboard, devolutiva, interface ou TRI/MIRT.