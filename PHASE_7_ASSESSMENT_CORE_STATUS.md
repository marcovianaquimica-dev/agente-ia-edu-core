# Phase 7 - Assessment Core

Status: CONCLUIDO

## Implementado

- Professor, coordenacao ou direcao autorizada cria avaliacao no escopo de sua escola, cria versoes incrementais e inclui apenas questoes elegiveis do Question Bank.
- O workflow segue `draft -> review -> approved -> published`; a publicacao explicita a versao aplicada e sua ativacao congela o gabarito oficial por item.
- A atribuicao individual exige aluno ativo na escola autorizada. O escopo de turma do professor tambem e verificado.
- O aluno so inicia tentativa para atribuicao individual pendente/em andamento, durante a janela valida da publicacao e dentro do limite de tentativas.
- O inicio da tentativa persiste snapshot imutavel de enunciado, opcoes, itens ordenados, chave congelada, conteudos e contexto escolar.
- Respostas e resultado usam somente o snapshot. Alteracoes posteriores no Question Bank ou gabarito nao alteram a prova aplicada.
- `UNKNOWN` e preservado como resposta sem correcao objetiva, sem impacto em mastery e separado de itens nao respondidos.
- No envio, respostas objetivas geram `LearningHistory` com `OFFICIAL_ASSESSMENT`; apenas corretas/incorretas atualizam `StudentContentMastery`. A atribuicao passa para `COMPLETED`.
- O resultado informa corretas, incorretas, desconhecidas e nao respondidas separadamente.

## Persistencia

- Migration aditiva `020_assessment_core_alignment` alinha assessments, versions, items, publications, assignments e attempts ao fluxo aplicado.
- O mapeamento das entidades oficiais criadas pela migration `001_core_official` foi alinhado a coluna SQL `metadata`; campos introduzidos pela governance continuam em `metadata_` conforme sua migration.
- A prova de migration executa upgrade, downgrade e novo upgrade em PostgreSQL dedicado.

## Comprovado

- HTTP SQLite: criacao autorizada, versionamento, publicacao, atribuicao, bloqueios de escola/turma, inicio condicionado a atribuicao, snapshot, chave congelada, `UNKNOWN`, evidencia, mastery e Domain Map.
- HTTP PostgreSQL dedicado, criado via Alembic ate `020`: ciclo completo de avaliacao ate evidencia pedagogica e Domain Map.
- Regressao completa: `694 passed`, `3 subtests passed`.
- `compileall -q src tests`: sucesso.
- Alembic: `020_assessment_core_alignment (head)`.
- `git diff --check`: sucesso.

## Fora de Escopo

- Frontend, geracao automatica, simulados ENEM completos, dashboard, embaralhamento, correcao discursiva, TRI/MIRT e ingestao.

## Observacao

- A suite apresenta apenas avisos preexistentes de configuracao Alembic (`path_separator`) e coleta de `TestTokenValidator`; nao ha falhas de teste.