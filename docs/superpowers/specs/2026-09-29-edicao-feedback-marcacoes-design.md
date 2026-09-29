# Edição de feedback e marcações pelo professor — Design

## Contexto e objetivo

Hoje, depois que a IA corrige uma redação, o professor só pode editar as 5 notas por competência e o campo "Próxima redação" (`feedback.next_essay_strategy`) — via `POST /api/v1/teacher/essay-corrections/{id}/edit`, tanto antes (`PENDING_REVIEW`) quanto depois (`APPROVED`) de aprovar. Todo o resto do que a IA gera é somente leitura: os 8 campos estruturados de C2/C3 (`c2_tipologia_textual`, `c2_tema`, `c2_repertorio_sociocultural`, `c2_orientacao_melhoria`, `c3_projeto_argumentativo`, `c3_fatos_informacoes_opinioes`, `c3_autoria`, `c3_orientacao_melhoria`), `feedback.strengths`, `feedback.improvements`, a revisão mecânica (`mechanical_review`), as mensagens de abertura/fechamento, e as marcações no texto (`annotations`) — que hoje só a IA cria, sem nenhum fluxo manual de criação, edição ou remoção.

Este design cobre:
1. Tornar editável todo o texto de feedback que a IA gera.
2. Permitir que o professor adicione, edite e remova marcações no texto da redação.

## Escopo desta leva

- Só correções com `anchor_mode = TEXT_OFFSET` (redação digitada) podem receber marcações **novas**. Correções `IMAGE_REGION` (envio em lote de redação física escaneada) continuam mostrando as marcações que a IA gerou, mas sem o botão "Adicionar marcação" — a UI de seleção de região sobre uma imagem fica fora desta leva.
- Editar o texto de uma marcação existente e removê-la funciona para os dois modos de âncora (não depende de selecionar texto de novo).
- Edição permitida tanto em `PENDING_REVIEW` quanto em `APPROVED` — mesma janela que já vale para notas/próxima-redação hoje.
- O professor pode remover **qualquer** marcação, inclusive as geradas pela IA.

## Arquitetura

Tudo passa pela rota já existente `POST /api/v1/teacher/essay-corrections/{id}/edit`, expandida em vez de rotas paralelas — ela já resolve autorização, escopo por escola (`_correction_for_own_school_or_403`), e a janela de status permitida.

`EditCorrectionRequest` ganha dois campos novos, opcionais, ao lado de `final_scores`/`final_feedback`:

```python
class EditCorrectionRequest(BaseModel):
    final_scores: Optional[dict] = None
    final_feedback: Optional[dict] = None
    structured_feedback: Optional[dict] = None   # os 8 campos de C2/C3
    annotations_patch: Optional[list[dict]] = None  # operações add/edit/remove
```

`structured_feedback`, quando presente, sobrescreve as chaves correspondentes dentro de `correction.ai_output` (os 8 campos de C2/C3 vivem no nível raiz do `ai_output`, não dentro de `final_feedback` — ver `essay_engine_contract/v5.py`'s `STRUCTURED_FEEDBACK_FIELDS`). Mesma mecânica para os demais campos que hoje só existem em `ai_output` e passam a ser editáveis (`mechanical_review`, `intro_message`, `closing_message`) — cada chave presente no dict sobrescreve a chave homônima em `ai_output`, chaves ausentes ficam como estavam.

`annotations_patch` é uma lista de operações aplicadas em ordem sobre `ai_output["annotations"]` antes de salvar:

```python
[
  {"op": "add", "annotation": {...}},               # sem "letter" - servidor gera
  {"op": "edit", "letter": "B", "changes": {...}},   # muda só os campos presentes em "changes"
  {"op": "remove", "letter": "C"},
]
```

O servidor aplica o patch, grava tudo numa única transação, e grava **um** `AdminAuditLog` por chamada de `/edit` (não um por campo/operação) — `action="ESSAY_CORRECTION_EDITED"`, `metadata_` só com as chaves que de fato mudaram:

```python
{
  "scores": {"before": {...}, "after": {...}},
  "feedback": {"before": {...}, "after": {...}},
  "structured_feedback": {"before": {...}, "after": {...}},
  "annotations_added": [{"letter": "D", "annotation": {...}}],
  "annotations_edited": [{"letter": "B", "before": {...}, "after": {...}}],
  "annotations_removed": [{"letter": "C", "removed": {...}}],
}
```

Isso estende o mesmo padrão que `approve()` já usa hoje (a rota `/edit` atualmente **não** grava `AdminAuditLog` nenhum — essa é uma lacuna real que esta leva fecha).

## Geração de letra para marcação nova

`Annotation.letter` segue o padrão `^[A-Z]{1,2}$` (A-Z, depois AA-ZZ se precisar). O servidor calcula a próxima letra livre olhando as letras já usadas nas anotações atuais da correção **mais** as letras já usadas historicamente nesta correção (nunca reaproveita uma letra removida, mesmo que fique livre — evita colidir com uma `rewrite` antiga que ainda referencia aquela letra pelo nome). Isso exige guardar, em algum lugar do `ai_output` (ex: uma chave `_used_annotation_letters` mantida internamente, não exposta na UI), o conjunto de letras já usadas nesta correção ao longo do tempo — não só as que sobraram no array atual.

## Criação de marcação (seleção de texto)

1. Professor clica "Adicionar marcação" no painel de revisão — entra em modo seleção sobre o texto renderizado da redação.
2. Seleciona um trecho (`window.getSelection()` / `Range`, sobre o texto puro já carregado como `canonical_text`, não sobre o DOM com marcações/tags já destacadas — evita contar caracteres de HTML por engano).
3. Popover pede: competência (C1-C5), tipo (`ACERTO` / `ATENCAO` / `MELHORIA`), comentário curto, comentário longo.
4. Frontend calcula `start`/`end` a partir da seleção e da string `canonical_text`, monta o payload `{"op": "add", "annotation": {competency_code, kind, evidence_kind: "LOCALIZED", anchor: {type: "TEXT_OFFSET", start, end, quote}, short_comment, long_comment}}` e chama `/edit`.

Uma marcação criada por este fluxo é sempre `evidence_kind = "LOCALIZED"` — não existe caminho para criar uma marcação `GLOBAL` manualmente (o ponto de partida é sempre uma seleção real de texto).

## Validação no servidor

- `add`/`edit` com `anchor`: `0 <= start < end <= len(canonical_text)` e `canonical_text[start:end] == quote` **exatamente** — diferente da tolerância a deriva que existe para o output da IA (`_resolve_text_offset`), aqui a posição vem de uma seleção real do professor, então tem que bater sempre, sem re-ancoragem.
- `competency_code` em C1-C5; `kind` em ACERTO/ATENCAO/MELHORIA; `short_comment`/`long_comment` não podem ser vazios.
- `edit`/`remove` com `letter` que não existe na correção: rejeitado com 422.
- `add` com `anchor_mode != "TEXT_OFFSET"` na submissão: rejeitado com 422 (o botão nem aparece na UI, mas o servidor não confia só nisso).
- Remover uma marcação que uma `rewrite` referencia pela letra: **permitido sem bloqueio**. A `rewrite` continua existindo; `renderCompetencyChecklist`/`letterToNumber` já degradam bem hoje quando a letra não é encontrada (o cabeçalho "1 — C1" some, o resto da reescrita continua aparecendo).

## Frontend

O painel de revisão (`essay-review.js`, a mesma tela que já edita notas/próxima-redação) ganha:
- Campos de texto editáveis para os 8 campos estruturados de C2/C3, `feedback.strengths`/`feedback.improvements` (listas), `mechanical_review` (lista de ocorrências), `intro_message`/`closing_message`.
- Um bloco "Marcações" listando as anotações atuais, cada uma com um texto editável (curto/longo) e um botão remover.
- O botão "Adicionar marcação" (só visível quando `anchor_mode === 'TEXT_OFFSET'`) que entra no modo de seleção descrito acima.

Tudo dentro do MESMO botão "Salvar" que já existe para notas/próxima-redação hoje — uma única chamada a `/edit` por vez, não uma por campo.

## Testes

TDD, seguindo os padrões já usados no projeto:
- Serviço (SQLite in-memory): cada campo novo editável em `structured_feedback`; cada operação `add`/`edit`/`remove` de `annotations_patch`; geração de letra nunca reaproveitando uma removida; validação rejeitando posição que não bate com `canonical_text`; validação rejeitando `add` em correção `IMAGE_REGION`; `AdminAuditLog` gravado com o diff certo em cada cenário; edição permitida em `PENDING_REVIEW` e em `APPROVED`.
- Frontend (`node --test`): cálculo de `start`/`end` a partir de uma seleção simulada contra `canonical_text`; renderização do bloco de marcações (editar/remover); botão "Adicionar marcação" ausente quando `anchor_mode` não é `TEXT_OFFSET`.

Nenhum teste chama a IA de verdade — tudo é manipulação determinística de dados já salvos em `ai_output`/`final_scores`/`final_feedback`.
