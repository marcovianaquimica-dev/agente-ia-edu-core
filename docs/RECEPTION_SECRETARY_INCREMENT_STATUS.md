# Reception / SECRETARY Increment Status

Status: **CONCLUIDO** em 2026-09-01.

A migration 019 foi validada em PostgreSQL 16 real, em banco dedicado, e a suite integral foi executada com o PostgreSQL local do projeto.

## Contratos auditados

- `ReceptionCandidate` representa somente candidato/lead anterior a matricula.
- `ExternalIdentityContext` e `UserSchoolLink` representam identidade e papel/escopo; nao sao matricula.
- `InitialDiagnostic` permanece o unico fluxo de diagnostico.
- `external_student_id` associa o lead a uma identidade ativada sem converte-lo em aluno matriculado.
- Nao existe neste incremento entidade de matricula ou vinculo academico anual.

## SECRETARY

- Papel incluido em ORM, servico administrativo, token de teste e constraints da migration 019.
- Precedencia: `STUDENT < SECRETARY < TEACHER < COORDINATOR < DIRECTOR < PLATFORM_ADMIN`.
- Reception aceita `SECRETARY`, `COORDINATOR` e `DIRECTOR`.
- SECRETARY e filtrada pelo escopo `SCHOOL`, `UNIT`, `SEGMENT`, `GRADE_LEVEL` ou `CLASSROOM`.
- APIs de negocio fora de Reception rejeitam identidades que o provedor autentica com `SECRETARY` em `ExternalIdentityContext.roles`.
- Criacao do vinculo SECRETARY usa `PlatformAdminService` e gera `AdminAuditLog` com `USER_LINKED`.

Risco residual: o provedor de identidade de producao deve declarar corretamente o papel `SECRETARY` em `ExternalIdentityContext.roles`. O vinculo persistido continua sendo a fonte de escola e escopo dentro de Reception.

## Migration 019

A 019 foi mantida como unica nova migration e agora:

1. substitui as constraints de papel de `user_school_links` e `user_invitations` para incluir `SECRETARY`;
2. converte `user_invitations.metadata_` de PostgreSQL `JSON` para `JSONB`;
3. renomeia a coluna para `metadata`, alinhando-a ao ORM;
4. cria `reception_candidates` com FKs e unicidade por `(school_id, email)` e `(school_id, phone)`;
5. restaura os contratos anteriores no downgrade.

O SQL PostgreSQL offline foi gerado e a migration tambem foi aplicada em PostgreSQL real com upgrade, downgrade para 018 e re-upgrade para 019.

Teste seguro: `tests/test_reception_migration_postgresql.py`. Ele cria e remove somente `agente_ia_edu_reception_test` e comprovou coluna `metadata` em `JSONB`, constraints de papel, indices, unicidade e foreign keys.

## Evidencias locais

- Fluxo SECRETARY -> pre-cadastro -> liberacao -> ativacao da identidade -> diagnostico real -> conclusao -> devolutiva: aprovado em SQLite.
- Acesso negado a administracao, coordenacao, banco de questoes e diagnostico direto: aprovado.
- Isolamento entre escolas e unidades: aprovado.
- Auditoria de criacao de vinculo, pre-cadastro, liberacao e ativacao: aprovado.
- `compileall`: aprovado.
- Alembic: uma cabeca (`019_reception_candidates`).
- Migration PostgreSQL dedicada: `1 passed`.
- Suite integral: `655 passed`, `3 subtests passed`, `10 warnings` nao bloqueantes.

## Observacoes nao bloqueantes

1. Alembic emite aviso deprecado porque `alembic.ini` ainda nao define `path_separator`.
2. Pytest avisa que `TestTokenValidator` nao e coletada como classe de teste por possuir `__init__`.
