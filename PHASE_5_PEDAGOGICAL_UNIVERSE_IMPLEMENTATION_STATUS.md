# Phase 5 - Pedagogical Universe - Bloco 3D.2

Data: 2026-08-31
Status: NAO CONCLUIDO

## A) Implementado

- `PedagogicalUniverse` com owner generico, slug/external id unicos, status, configuracao versionada, timestamps e configuracao para marca futura.
- Escopos normalizados de catalogo, academicos e bindings de contexto autorizado.
- Migration aditiva `018_pedagogical_universe` a partir da head `017`.
- `PedagogicalUniverseService` para criacao, atualizacao de configuracao auditada, escopos, bindings, resolucao por identidade e verificacao de pertencimento no Question Bank.
- `AdminAuditLog` reutilizado para criacao e alteracao de configuracao.

## B) Comprovado

- Escola A resolve somente seus universos e pode disponibilizar mais de um ao mesmo aluno.
- Escola A nao resolve universo da Escola B mesmo com ID conhecido.
- Binding por identidade externa e por `product_context` permitem independente e parceiro sem `school_id`.
- Universo de area inclui descendente Quimica e exclui Matematica; o mesmo teste prova que uma `QuestionVersion` de Quimica pertence ao universo e uma de Matematica nao pertence.
- Configuracao versionada gera auditoria de criacao e atualizacao.
- 23 testes focados passaram; suite completa com 626 testes passou; compilacao e Alembic passaram.

## C) Nao Comprovado

- Endpoints administrativos/HTTP para CRUD e leitura de universos.
- Integracao da resolucao autorizada com o start/seletor do diagnostico. Isso foi explicitamente mantido fora deste incremento para nao antecipar o Bloco 3D.
- Restricao SQL integrada de candidatos do diagnostico por universo; hoje a infraestrutura prova pertencimento, mas o seletor ainda nao a consome.
- Validacao HTTP de payload com `universe_id`/`partner_id` malicioso.

## D) Pendente

- Criar API administrativa minima com autorizacao de plataforma/owner e endpoints de universos autorizados.
- Integrar o resolvedor servidor-side no diagnostico, busca, pratica e demais consumidores de forma incremental.
- Decidir como o host autenticado entrega `product_context` em producao.

## E) Riscos

- O owner polimorfico nao tem FK para parceiro/produto por decisao: essas identidades pertencem ao host e devem ser validadas pelo contexto autenticado antes do binding.
- `contains_question_version` e adequado como infraestrutura/teste; o seletor futuro deve aplicar escopos no SQL para evitar carregamento excessivo.

## F) Proximo Bloco

Adicionar API segura e integrar `PedagogicalUniverseService.resolve_active_universe()` ao inicio/selecao do diagnostico, com testes HTTP de IDOR, tenant e conflito objetivo versus universo.

## G) Migration

- Head validada: `018_pedagogical_universe`.
- `alembic upgrade head`: sucesso.
- Nenhuma migration historica foi modificada.