# Phase 8A - Teacher List Builder MVP

Status: CONCLUIDO

## Objetivo

Permitir que professor crie e retome rascunhos de listas de exercicios com questoes existentes, completas e autorizadas do Question Bank.

## Implementado

- Area `Listas e Avaliacoes` no portal docente, com criacao de rascunho, configuracao, candidatos, revisao completa, remocao e controles de mover para cima/baixo.
- Configuracao persistida: titulo, conteudo curricular canonico, quantidade, distribuicao `EASY`/`MEDIUM`/`HARD` e contexto academico do vinculo escolar.
- Politica centralizada e configuravel por `TEACHER_MATERIAL_MAX_QUESTIONS` (padrao `50`). Ela valida quantidade positiva, distribuicao exata e bloqueia requisicoes grandes no backend.
- Candidatos sao consultados no banco por `ContentQuestionLink`, `QuestionVersion`, status, validacao, dificuldade, visibilidade/escola e conteudo pertencente ao universo pedagogico ativo.
- Itens usam `Assessment`, `AssessmentVersion` e `AssessmentItem`; `EXERCISE_LIST` apenas distingue o material no mesmo Assessment Core.
- A lista grava a versao especifica da questao. A constraint impede a mesma `question_version_id` duas vezes na mesma versao; a ordem persistida usa `AssessmentItem.position`.
- A remocao so remove o vinculo da lista. Reordenacao e compactacao usam posicoes temporarias para respeitar a constraint de posicao unica em SQLite e PostgreSQL.

## Arquivos Alterados

- `src/agente_ia_edu/db/models/assessments.py`
- `migrations/versions/021_teacher_list_builder.py`
- `src/agente_ia_edu/services/teacher_material_policy.py`
- `src/agente_ia_edu/api/routes/teacher_materials.py`
- `src/agente_ia_edu/api/schemas/assessments.py`
- `src/agente_ia_edu/api/app.py`
- `src/agente_ia_edu/web/teacher.html`
- `src/agente_ia_edu/web/teacher.js`
- `src/agente_ia_edu/web/teacher.css`
- `tests/test_phase8a_teacher_list_builder_http.py`
- `tests/test_phase8a_teacher_list_builder_postgresql.py`
- `tests/test_phase7_assessment_postgresql_e2e.py` somente para migrar seu fixture ao head atual.

## APIs

- `POST /api/v1/teacher/materials`
- `GET /api/v1/teacher/materials`
- `GET /api/v1/teacher/materials/{material_id}`
- `GET /api/v1/teacher/materials/{material_id}/candidates`
- `POST /api/v1/teacher/materials/{material_id}/items`
- `DELETE /api/v1/teacher/materials/{material_id}/items/{item_id}`
- `PATCH /api/v1/teacher/materials/{material_id}/items/reorder`

## Seguranca

- Exige `TEACHER`, `COORDINATOR` ou `DIRECTOR` e contexto escolar.
- Professor acessa somente seus proprios rascunhos, dentro de escola e escopo academico equivalentes.
- O servidor resolve o universo ativo da identidade e recusa conteudo ou questao fora dele.
- `school_id`, ownership, conteudo e question version nunca sao aceitos como autorizacao vinda do frontend.

## Migrations

- `021_teacher_list_builder` adiciona `assessments.material_type` com default `ASSESSMENT`, preservando registros Phase 7.
- Adiciona a constraint de nao duplicidade por versao de lista e `question_version_id`.
- PostgreSQL dedicado comprovou upgrade, downgrade e re-upgrade.

## Testes

- E2E HTTP SQLite: configuracao, quantidade invalida, bypass `1000`, selecao curricular, preview completo, adicionar, duplicidade, limite configurado, remocao, reordenacao, recuperacao e isolamento escolar.
- E2E HTTP PostgreSQL dedicado: os mesmos cenarios com migrations ate `021`.
- Regressao focal: `145 passed`.
- Suite completa: `701 passed`, `3 subtests passed`.
- `compileall -q src tests`, `node --check src/agente_ia_edu/web/teacher.js`, Alembic e `git diff --check`: sucesso.

## Limitacoes e Proximo Passo

- Nao inclui IA, modificacao/edicao de questoes, PDF, cabecalho escolar, gabarito, resolucao, publicacao de lista ou simulados.
- A proxima fase pode adicionar PDF e cabecalho institucional sobre este rascunho versionado, sem criar outro motor de lista.