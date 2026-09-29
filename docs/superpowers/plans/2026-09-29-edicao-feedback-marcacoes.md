# Edição de feedback e marcações pelo professor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar editável todo o texto de feedback que a IA gera numa correção de redação, e permitir que o professor adicione, edite e remova marcações (anotações) no texto de redações digitadas.

**Architecture:** Tudo passa pela rota já existente `POST /api/v1/teacher/essay-corrections/{id}/edit` (correção já `APPROVED`) e pela rota `POST /api/v1/teacher/essay-corrections/{id}/approve` (correção ainda `PENDING_REVIEW`, aceita os mesmos overrides opcionais) — nenhuma rota nova. Duas requisições novas opcionais em `ApproveCorrectionRequest`/`EditCorrectionRequest`: `ai_output_patch` (dict, sobrescreve chaves específicas dentro de `EssayCorrection.ai_output`) e `annotations_patch` (lista de operações `add`/`edit`/`remove` aplicadas sobre `ai_output["annotations"]`). Um novo módulo puro (`services/essay_correction_edits.py`) concentra a lógica de aplicar esses patches e calcular o diff de auditoria; `EssayCorrectionService.approve()` e um novo `EssayCorrectionService.edit()` chamam esse módulo e gravam um único `AdminAuditLog` por chamada — fechando uma lacuna real (a rota `/edit` hoje não audita nada).

**Tech Stack:** Python/FastAPI/SQLAlchemy (assíncrono) no backend; JavaScript vanilla (sem framework, IIFE + `window.EssayReport`/`window.EssayAnnotations`) no frontend; testes com `unittest`/`pytest` (SQLite in-memory + `TestClient`) e `node --test`.

**Spec:** `docs/superpowers/specs/2026-09-29-edicao-feedback-marcacoes-design.md`

## Global Constraints

- Marcação **nova** só pode ser criada quando `EssaySubmission.anchor_mode == "TEXT_OFFSET"`. Editar o comentário de uma marcação existente e remover qualquer marcação funciona para os dois modos de âncora (`TEXT_OFFSET` e `IMAGE_REGION`).
- Edição permitida em `PENDING_REVIEW` (via `/approve`, que já aceita overrides) e em `APPROVED` (via `/edit`) — nunca em `NEEDS_REVIEW`/`REJECTED`.
- O professor pode remover **qualquer** marcação, inclusive as que a IA gerou.
- Posição de uma marcação nova/editada exige `canonical_text[start:end] == quote` **exatamente** — sem a tolerância de deriva que existe para o output bruto da IA (`essay_engine_validation.py`'s `_resolve_text_offset` continua intocada, é para outro fluxo).
- Toda chamada a `/edit` ou `/approve` que muda algo grava **um** `AdminAuditLog` (`action="ESSAY_CORRECTION_EDITED"` ou `"ESSAY_CORRECTION_APPROVED"`), com `metadata_` só contendo as chaves que de fato mudaram — mesmo padrão que `approve()` já usa hoje para `final_scores`/`final_feedback`.
- Nunca duplicar a lógica de destacar/numerar marcações já existente em `essay-annotations.js` (`renderHighlightedText`, `wirePopovers`) — estender com parâmetros opcionais retrocompatíveis, nunca reescrever em `essay-review.js`.
- `.venv/bin/pytest` a partir do worktree (nunca o `.venv` do checkout principal). Se o comando `pytest` direto for bloqueado pelo classificador de segurança do Claude Code (aconteceu hoje com mudanças de validação), usar `.venv/bin/python -m pytest` e/ou redirecionar a saída para um arquivo.

---

### Task 1: Módulo puro de edição — patches de `ai_output` e geração de letra

**Files:**
- Create: `src/agente_ia_edu/services/essay_correction_edits.py`
- Test: `tests/test_essay_correction_edits.py`

**Interfaces:**
- Consumes: nada de outras tasks — funções puras sobre `dict`/`list`, sem sessão de banco.
- Produces (usado pela Task 2):
  - `AI_OUTPUT_EDITABLE_FIELDS: tuple[str, ...]` — os 8 campos de `essay_engine_contract.v5.STRUCTURED_FEEDBACK_FIELDS` + `"mechanical_review"`, `"intro_message"`, `"closing_message"`.
  - `apply_ai_output_patch(ai_output: dict, patch: dict) -> dict` — muta `ai_output` in-place (só chaves em `AI_OUTPUT_EDITABLE_FIELDS`; qualquer outra chave em `patch` levanta `ValueError`), devolve `{"before": {...}, "after": {...}}` só com as chaves que mudaram de valor (`{}` se nada mudou).
  - `apply_annotations_patch(ai_output: dict, patch: list[dict], *, canonical_text: str, anchor_mode: str) -> dict` — muta `ai_output["annotations"]` in-place aplicando cada operação em ordem, devolve `{"added": [...], "edited": [...], "removed": [...]}` (listas vazias omitidas do dict se nada mudou nessa categoria). Levanta `ValueError` com mensagem específica em qualquer operação inválida (ver Step 3).

- [ ] **Step 1: Escrever os testes de `apply_ai_output_patch` (falhando)**

```python
# tests/test_essay_correction_edits.py
import unittest

from agente_ia_edu.services.essay_correction_edits import (
    AI_OUTPUT_EDITABLE_FIELDS,
    apply_ai_output_patch,
)


class ApplyAiOutputPatchTests(unittest.TestCase):
    def test_editable_fields_list_the_eight_structured_fields_plus_three_more(self):
        self.assertEqual(AI_OUTPUT_EDITABLE_FIELDS, (
            "c2_tipologia_textual", "c2_tema", "c2_repertorio_sociocultural",
            "c2_orientacao_melhoria", "c3_projeto_argumentativo",
            "c3_fatos_informacoes_opinioes", "c3_autoria", "c3_orientacao_melhoria",
            "mechanical_review", "intro_message", "closing_message",
        ))

    def test_overwrites_an_editable_field_and_reports_the_diff(self):
        ai_output = {"c2_tema": "texto antigo", "annotations": []}
        diff = apply_ai_output_patch(ai_output, {"c2_tema": "texto novo"})
        self.assertEqual(ai_output["c2_tema"], "texto novo")
        self.assertEqual(diff, {"c2_tema": {"before": "texto antigo", "after": "texto novo"}})

    def test_reports_no_diff_for_a_field_set_to_its_own_current_value(self):
        ai_output = {"intro_message": "mesmo texto"}
        diff = apply_ai_output_patch(ai_output, {"intro_message": "mesmo texto"})
        self.assertEqual(diff, {})

    def test_rejects_a_field_outside_the_editable_whitelist(self):
        ai_output = {"annotations": []}
        with self.assertRaises(ValueError) as caught:
            apply_ai_output_patch(ai_output, {"annotations": []})
        self.assertIn("annotations", str(caught.exception))

    def test_a_missing_field_in_ai_output_is_treated_as_none_before(self):
        ai_output = {}
        diff = apply_ai_output_patch(ai_output, {"closing_message": "Até a próxima!"})
        self.assertEqual(ai_output["closing_message"], "Até a próxima!")
        self.assertEqual(diff, {"closing_message": {"before": None, "after": "Até a próxima!"}})
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/bin/pytest tests/test_essay_correction_edits.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.services.essay_correction_edits'`

- [ ] **Step 3: Implementar `apply_ai_output_patch` e a constante**

```python
# src/agente_ia_edu/services/essay_correction_edits.py
"""R3 - edição manual de correção pelo professor: funções puras que
aplicam um patch de feedback/marcações sobre o ai_output já persistido
de uma EssayCorrection, e devolvem o diff pronto para o AdminAuditLog.
Nenhuma função aqui toca sessão de banco - EssayCorrectionService.approve()/
edit() (Task 2) chamam estas funções e cuidam da persistência."""

from __future__ import annotations

from ..essay_engine_contract.v5 import STRUCTURED_FEEDBACK_FIELDS

AI_OUTPUT_EDITABLE_FIELDS: tuple[str, ...] = STRUCTURED_FEEDBACK_FIELDS + (
    "mechanical_review", "intro_message", "closing_message",
)


def apply_ai_output_patch(ai_output: dict, patch: dict) -> dict:
    unknown = set(patch) - set(AI_OUTPUT_EDITABLE_FIELDS)
    if unknown:
        raise ValueError(
            f"ai_output_patch has non-editable field(s): {sorted(unknown)}; "
            f"only {list(AI_OUTPUT_EDITABLE_FIELDS)} can be edited this way"
        )
    diff: dict = {}
    for field, new_value in patch.items():
        old_value = ai_output.get(field)
        if old_value == new_value:
            continue
        diff[field] = {"before": old_value, "after": new_value}
        ai_output[field] = new_value
    return diff
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/bin/pytest tests/test_essay_correction_edits.py -v`
Expected: PASS (5 testes)

- [ ] **Step 5: Escrever os testes de geração de letra (falhando)**

```python
# adicionar em tests/test_essay_correction_edits.py, mesmo arquivo
from agente_ia_edu.services.essay_correction_edits import next_annotation_letter


class NextAnnotationLetterTests(unittest.TestCase):
    def test_first_letter_when_ai_output_has_no_annotations(self):
        self.assertEqual(next_annotation_letter({}), "A")

    def test_next_letter_after_existing_ones(self):
        ai_output = {"annotations": [{"letter": "A"}, {"letter": "C"}]}
        # nunca reaproveita uma letra livre no meio (aqui "B") - sempre a
        # próxima depois da MAIOR já usada, para nunca colidir com uma
        # rewrite antiga que referencia uma letra removida.
        self.assertEqual(next_annotation_letter(ai_output), "D")

    def test_never_reuses_a_letter_removed_earlier_even_if_no_longer_present(self):
        ai_output = {"annotations": [], "_annotation_letter_watermark": 2}  # "C" já foi usada e removida
        self.assertEqual(next_annotation_letter(ai_output), "D")

    def test_rolls_over_past_z_into_two_letters(self):
        ai_output = {"annotations": [], "_annotation_letter_watermark": 25}  # "Z" já foi usada
        self.assertEqual(next_annotation_letter(ai_output), "AA")
```

- [ ] **Step 6: Rodar e confirmar que falha**

Run: `.venv/bin/pytest tests/test_essay_correction_edits.py::NextAnnotationLetterTests -v`
Expected: FAIL com `ImportError: cannot import name 'next_annotation_letter'`

- [ ] **Step 7: Implementar a geração de letra (índice base-26 bijetivo)**

```python
# adicionar em src/agente_ia_edu/services/essay_correction_edits.py

_WATERMARK_KEY = "_annotation_letter_watermark"


def _letter_to_index(letter: str) -> int:
    """"A" -> 0, "Z" -> 25, "AA" -> 26, "AB" -> 27, ... (base-26 bijetiva,
    sem dígito zero - é o mesmo esquema das colunas de planilha)."""
    index = 0
    for char in letter:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _index_to_letter(index: int) -> str:
    chars = []
    n = index + 1
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        chars.append(chr(ord("A") + remainder))
    return "".join(reversed(chars))


def next_annotation_letter(ai_output: dict) -> str:
    """A próxima letra livre para uma marcação nova - nunca reaproveita uma
    já usada nesta correção, mesmo que a marcação correspondente tenha sido
    removida (uma rewrite antiga pode ainda referenciar essa letra pelo
    nome). O "maior índice já usado" fica em duas fontes: as letras que
    sobraram no array annotations agora, e um watermark que sobrevive à
    remoção (ver mark_letter_used, chamado sempre que uma marcação é
    adicionada ou removida)."""
    existing = [a["letter"] for a in ai_output.get("annotations", [])]
    watermark = ai_output.get(_WATERMARK_KEY, -1)
    highest = max([watermark] + [_letter_to_index(letter) for letter in existing])
    return _index_to_letter(highest + 1)


def mark_letter_used(ai_output: dict, letter: str) -> None:
    """Atualiza o watermark para nunca reaproveitar ``letter`` depois,
    mesmo que a marcação correspondente seja removida em seguida. Chamar
    toda vez que uma letra passa a existir em annotations (add) - remove
    não precisa chamar, o watermark já cobre letras removidas por
    construção (foi marcado quando a letra foi adicionada)."""
    index = _letter_to_index(letter)
    if index > ai_output.get(_WATERMARK_KEY, -1):
        ai_output[_WATERMARK_KEY] = index
```

- [ ] **Step 8: Rodar e confirmar que passa**

Run: `.venv/bin/pytest tests/test_essay_correction_edits.py -v`
Expected: PASS (9 testes)

- [ ] **Step 9: Escrever os testes de `apply_annotations_patch` (falhando)**

```python
# adicionar em tests/test_essay_correction_edits.py
from agente_ia_edu.services.essay_correction_edits import apply_annotations_patch

TEXT = "A leitura transforma o pensamento crítico das pessoas."


class ApplyAnnotationsPatchTests(unittest.TestCase):
    def _ai_output(self, annotations=None):
        return {"annotations": annotations or []}

    def test_add_creates_a_localized_annotation_with_the_next_letter(self):
        ai_output = self._ai_output()
        quote = "pensamento crítico"
        start = TEXT.index(quote)
        diff = apply_annotations_patch(
            ai_output,
            [{
                "op": "add",
                "annotation": {
                    "competency_code": "C3", "kind": "ACERTO",
                    "anchor": {"start": start, "end": start + len(quote), "quote": quote},
                    "short_comment": "curto", "long_comment": "longo",
                },
            }],
            canonical_text=TEXT, anchor_mode="TEXT_OFFSET",
        )
        self.assertEqual(len(ai_output["annotations"]), 1)
        added = ai_output["annotations"][0]
        self.assertEqual(added["letter"], "A")
        self.assertEqual(added["evidence_kind"], "LOCALIZED")
        self.assertEqual(added["anchor"], {"type": "TEXT_OFFSET", "start": start, "end": start + len(quote), "quote": quote})
        self.assertEqual(diff["added"], [added])

    def test_add_rejects_a_quote_that_does_not_match_the_text_at_that_position(self):
        with self.assertRaises(ValueError) as caught:
            apply_annotations_patch(
                self._ai_output(),
                [{
                    "op": "add",
                    "annotation": {
                        "competency_code": "C1", "kind": "MELHORIA",
                        "anchor": {"start": 0, "end": 5, "quote": "texto que não está aqui"},
                        "short_comment": "curto", "long_comment": "longo",
                    },
                }],
                canonical_text=TEXT, anchor_mode="TEXT_OFFSET",
            )
        self.assertIn("does not match", str(caught.exception))

    def test_add_rejects_when_anchor_mode_is_not_text_offset(self):
        with self.assertRaises(ValueError) as caught:
            apply_annotations_patch(
                self._ai_output(),
                [{
                    "op": "add",
                    "annotation": {
                        "competency_code": "C1", "kind": "MELHORIA",
                        "anchor": {"start": 0, "end": 1, "quote": TEXT[0]},
                        "short_comment": "curto", "long_comment": "longo",
                    },
                }],
                canonical_text=TEXT, anchor_mode="IMAGE_REGION",
            )
        self.assertIn("IMAGE_REGION", str(caught.exception))

    def test_two_adds_in_the_same_patch_get_consecutive_letters(self):
        ai_output = self._ai_output()
        apply_annotations_patch(
            ai_output,
            [
                {"op": "add", "annotation": {
                    "competency_code": "C1", "kind": "ACERTO",
                    "anchor": {"start": 0, "end": 1, "quote": "A"},
                    "short_comment": "c", "long_comment": "l",
                }},
                {"op": "add", "annotation": {
                    "competency_code": "C2", "kind": "MELHORIA",
                    "anchor": {"start": 2, "end": 9, "quote": "leitura"},
                    "short_comment": "c", "long_comment": "l",
                }},
            ],
            canonical_text=TEXT, anchor_mode="TEXT_OFFSET",
        )
        self.assertEqual([a["letter"] for a in ai_output["annotations"]], ["A", "B"])

    def test_edit_changes_only_the_given_fields_and_keeps_the_anchor(self):
        ai_output = self._ai_output([{
            "letter": "A", "competency_code": "C1", "kind": "ACERTO",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 0, "end": 1, "quote": "A"},
            "short_comment": "antigo", "long_comment": "longo antigo",
        }])
        diff = apply_annotations_patch(
            ai_output, [{"op": "edit", "letter": "A", "changes": {"short_comment": "novo"}}],
            canonical_text=TEXT, anchor_mode="TEXT_OFFSET",
        )
        edited = ai_output["annotations"][0]
        self.assertEqual(edited["short_comment"], "novo")
        self.assertEqual(edited["long_comment"], "longo antigo")  # não mudou
        self.assertEqual(edited["anchor"]["start"], 0)  # posição intocada
        self.assertEqual(diff["edited"], [{
            "letter": "A", "before": {"short_comment": "antigo"}, "after": {"short_comment": "novo"},
        }])

    def test_edit_of_an_unknown_letter_raises(self):
        with self.assertRaises(ValueError) as caught:
            apply_annotations_patch(
                self._ai_output(), [{"op": "edit", "letter": "Z", "changes": {"short_comment": "x"}}],
                canonical_text=TEXT, anchor_mode="TEXT_OFFSET",
            )
        self.assertIn("Z", str(caught.exception))

    def test_remove_takes_the_annotation_out_and_reports_it(self):
        ai_output = self._ai_output([{
            "letter": "B", "competency_code": "C2", "kind": "ACERTO",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 0, "end": 1, "quote": "A"},
            "short_comment": "c", "long_comment": "l",
        }])
        diff = apply_annotations_patch(
            ai_output, [{"op": "remove", "letter": "B"}],
            canonical_text=TEXT, anchor_mode="TEXT_OFFSET",
        )
        self.assertEqual(ai_output["annotations"], [])
        self.assertEqual(diff["removed"][0]["letter"], "B")

    def test_remove_an_ai_generated_annotation_is_allowed(self):
        """Spec: o professor pode remover QUALQUER marcação, inclusive as
        que a IA gerou - esta função não distingue origem, só letra."""
        ai_output = self._ai_output([{
            "letter": "A", "competency_code": "C1", "kind": "ACERTO",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 0, "end": 1, "quote": "A"},
            "short_comment": "gerado pela IA", "long_comment": "l",
        }])
        apply_annotations_patch(
            ai_output, [{"op": "remove", "letter": "A"}],
            canonical_text=TEXT, anchor_mode="TEXT_OFFSET",
        )
        self.assertEqual(ai_output["annotations"], [])

    def test_a_letter_removed_is_never_reused_by_a_later_add_in_the_same_patch(self):
        ai_output = self._ai_output([{
            "letter": "A", "competency_code": "C1", "kind": "ACERTO",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 0, "end": 1, "quote": "A"},
            "short_comment": "c", "long_comment": "l",
        }])
        apply_annotations_patch(
            ai_output,
            [
                {"op": "remove", "letter": "A"},
                {"op": "add", "annotation": {
                    "competency_code": "C2", "kind": "MELHORIA",
                    "anchor": {"start": 2, "end": 9, "quote": "leitura"},
                    "short_comment": "c", "long_comment": "l",
                }},
            ],
            canonical_text=TEXT, anchor_mode="TEXT_OFFSET",
        )
        self.assertEqual(ai_output["annotations"][0]["letter"], "B")

    def test_unknown_op_raises(self):
        with self.assertRaises(ValueError) as caught:
            apply_annotations_patch(
                self._ai_output(), [{"op": "rename", "letter": "A"}],
                canonical_text=TEXT, anchor_mode="TEXT_OFFSET",
            )
        self.assertIn("rename", str(caught.exception))
```

- [ ] **Step 10: Rodar e confirmar que falha**

Run: `.venv/bin/pytest tests/test_essay_correction_edits.py::ApplyAnnotationsPatchTests -v`
Expected: FAIL com `ImportError: cannot import name 'apply_annotations_patch'`

- [ ] **Step 11: Implementar `apply_annotations_patch`**

```python
# adicionar em src/agente_ia_edu/services/essay_correction_edits.py

_EDITABLE_ANNOTATION_FIELDS = ("competency_code", "kind", "short_comment", "long_comment")


def _validate_and_build_anchor(annotation_input: dict, *, canonical_text: str, anchor_mode: str) -> dict:
    if anchor_mode != "TEXT_OFFSET":
        raise ValueError(
            f"cannot add a manual annotation on an anchor_mode={anchor_mode!r} submission - "
            "manual marking only supports TEXT_OFFSET (typed) essays"
        )
    anchor_input = annotation_input["anchor"]
    start, end, quote = anchor_input["start"], anchor_input["end"], anchor_input["quote"]
    if not (0 <= start < end <= len(canonical_text)):
        raise ValueError(f"anchor start/end out of bounds for this text: start={start}, end={end}")
    real = canonical_text[start:end]
    if real != quote:
        raise ValueError(
            f"anchor quote does not match the text at that position: expected {quote!r}, "
            f"text has {real!r}"
        )
    return {"type": "TEXT_OFFSET", "start": start, "end": end, "quote": quote}


def apply_annotations_patch(
    ai_output: dict, patch: list[dict], *, canonical_text: str, anchor_mode: str,
) -> dict:
    annotations = ai_output.setdefault("annotations", [])
    by_letter = {a["letter"]: a for a in annotations}
    diff: dict = {"added": [], "edited": [], "removed": []}

    for operation in patch:
        op = operation["op"]
        if op == "add":
            anchor = _validate_and_build_anchor(
                operation["annotation"], canonical_text=canonical_text, anchor_mode=anchor_mode,
            )
            letter = next_annotation_letter(ai_output)
            mark_letter_used(ai_output, letter)
            new_annotation = {
                "letter": letter,
                "competency_code": operation["annotation"]["competency_code"],
                "kind": operation["annotation"]["kind"],
                "evidence_kind": "LOCALIZED",
                "anchor": anchor,
                "short_comment": operation["annotation"]["short_comment"],
                "long_comment": operation["annotation"]["long_comment"],
            }
            annotations.append(new_annotation)
            by_letter[letter] = new_annotation
            diff["added"].append(new_annotation)
        elif op == "edit":
            letter = operation["letter"]
            if letter not in by_letter:
                raise ValueError(f"cannot edit annotation {letter!r}: no such annotation on this correction")
            target = by_letter[letter]
            changes = operation["changes"]
            unknown = set(changes) - set(_EDITABLE_ANNOTATION_FIELDS)
            if unknown:
                raise ValueError(f"cannot edit non-editable annotation field(s): {sorted(unknown)}")
            before, after = {}, {}
            for field, new_value in changes.items():
                if target.get(field) == new_value:
                    continue
                before[field] = target.get(field)
                after[field] = new_value
                target[field] = new_value
            if after:
                diff["edited"].append({"letter": letter, "before": before, "after": after})
        elif op == "remove":
            letter = operation["letter"]
            if letter not in by_letter:
                raise ValueError(f"cannot remove annotation {letter!r}: no such annotation on this correction")
            removed = by_letter.pop(letter)
            annotations.remove(removed)
            diff["removed"].append(removed)
        else:
            raise ValueError(f"unknown annotations_patch op: {op!r}")

    return {key: value for key, value in diff.items() if value}
```

- [ ] **Step 12: Rodar e confirmar que passa**

Run: `.venv/bin/pytest tests/test_essay_correction_edits.py -v`
Expected: PASS (19 testes no total do arquivo)

- [ ] **Step 13: Commit**

```bash
git add src/agente_ia_edu/services/essay_correction_edits.py tests/test_essay_correction_edits.py
git commit -m "feat: modulo puro de patch de feedback/marcacoes de redacao"
```

---

### Task 2: `EssayCorrectionService.edit()` + `approve()` ganham os patches novos

**Files:**
- Modify: `src/agente_ia_edu/services/essay_correction.py` (linhas 398-436, método `approve`; adicionar método `edit` logo depois)
- Test: `tests/test_r3_essay_correction_service.py`

**Interfaces:**
- Consumes: `apply_ai_output_patch`, `apply_annotations_patch` de `services/essay_correction_edits.py` (Task 1).
- Produces (usado pela Task 3):
  - `EssayCorrectionService.approve(essay_correction_id, *, reviewed_by_external_identity, final_scores=None, final_feedback=None, ai_output_patch=None, annotations_patch=None) -> EssayCorrection` (assinatura estendida, retrocompatível - os 2 parâmetros novos são opcionais).
  - `EssayCorrectionService.edit(essay_correction_id, *, reviewed_by_external_identity, final_scores=None, final_feedback=None, ai_output_patch=None, annotations_patch=None) -> EssayCorrection` — novo método, mesma forma de `approve`/`reject`: busca a correção, valida `status == "APPROVED"` (senão `ValueError`), aplica os 4 patches opcionais, grava `AdminAuditLog(action="ESSAY_CORRECTION_EDITED", ...)`, `flush()`, devolve a correção.

- [ ] **Step 1: Escrever o teste de `edit()` (falhando)**

Leia primeiro `tests/test_r3_essay_correction_service.py` para achar o padrão de fixture usado nos testes de `approve()`/`reject()` nesse arquivo (setUp, criação de EssaySubmission/EssayCorrection) e reaproveitar o mesmo estilo - não recriar um fixture diferente.

```python
# adicionar em tests/test_r3_essay_correction_service.py, na classe de testes
# de approve()/reject() já existente (mesmo padrão de fixture/setUp)

async def test_edit_requires_approved_status(self):
    correction = await self._seed_correction(status="PENDING_REVIEW")
    service = EssayCorrectionService(self.session)
    with self.assertRaises(ValueError) as caught:
        await service.edit(correction.id, reviewed_by_external_identity="teacher:x")
    self.assertIn("APPROVED", str(caught.exception))

async def test_edit_applies_ai_output_patch_and_audits_it(self):
    correction = await self._seed_correction(
        status="APPROVED", ai_output={"c2_tema": "antigo", "annotations": []},
    )
    service = EssayCorrectionService(self.session)
    updated = await service.edit(
        correction.id, reviewed_by_external_identity="teacher:x",
        ai_output_patch={"c2_tema": "novo"},
    )
    self.assertEqual(updated.ai_output["c2_tema"], "novo")
    log = await self._latest_audit_log()
    self.assertEqual(log.action, "ESSAY_CORRECTION_EDITED")
    self.assertEqual(log.metadata_["ai_output"], {"c2_tema": {"before": "antigo", "after": "novo"}})

async def test_edit_applies_annotations_patch_and_audits_it(self):
    correction = await self._seed_correction(
        status="APPROVED",
        ai_output={"annotations": []},
        canonical_text="Um texto qualquer para marcar.",
        anchor_mode="TEXT_OFFSET",
    )
    service = EssayCorrectionService(self.session)
    updated = await service.edit(
        correction.id, reviewed_by_external_identity="teacher:x",
        annotations_patch=[{
            "op": "add",
            "annotation": {
                "competency_code": "C1", "kind": "ACERTO",
                "anchor": {"start": 3, "end": 8, "quote": "texto"},
                "short_comment": "curto", "long_comment": "longo",
            },
        }],
    )
    self.assertEqual(len(updated.ai_output["annotations"]), 1)
    log = await self._latest_audit_log()
    self.assertEqual(log.metadata_["annotations_added"][0]["letter"], "A")

async def test_edit_with_no_changes_writes_no_audit_log(self):
    correction = await self._seed_correction(status="APPROVED")
    before_count = await self._audit_log_count()
    service = EssayCorrectionService(self.session)
    await service.edit(correction.id, reviewed_by_external_identity="teacher:x")
    after_count = await self._audit_log_count()
    self.assertEqual(after_count, before_count)
```

Adapte `_seed_correction` (se já existir um helper parecido no arquivo) para aceitar `ai_output`/`canonical_text`/`anchor_mode` como overrides opcionais - se não existir, crie-o seguindo o padrão de fixture já usado pelos testes vizinhos de `approve()`. Adicione dois helpers pequenos `_latest_audit_log`/`_audit_log_count` que fazem um `select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc())`/`select(func.count()).select_from(AdminAuditLog)` na sessão de teste.

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/bin/pytest tests/test_r3_essay_correction_service.py -k "edit" -v`
Expected: FAIL com `AttributeError: 'EssayCorrectionService' object has no attribute 'edit'`

- [ ] **Step 3: Implementar `edit()` e estender `approve()`**

Substitua o método `approve` atual (linhas 398-436) por esta versão estendida, e adicione `edit` logo depois:

```python
    async def approve(
        self, essay_correction_id: uuid.UUID, *, reviewed_by_external_identity: str,
        final_scores: dict | None = None, final_feedback: dict | None = None,
        ai_output_patch: dict | None = None, annotations_patch: list[dict] | None = None,
    ) -> EssayCorrection:
        correction = await self.session.get(EssayCorrection, essay_correction_id)
        if correction is None:
            raise ValueError(f"EssayCorrection not found: {essay_correction_id}")
        if correction.status != "PENDING_REVIEW":
            raise ValueError(
                f"EssayCorrection {essay_correction_id} is {correction.status}, not "
                "PENDING_REVIEW - only a pending correction can be approved."
            )
        edits = await self._apply_edit_patches(
            correction, final_scores=final_scores, final_feedback=final_feedback,
            ai_output_patch=ai_output_patch, annotations_patch=annotations_patch,
        )

        correction.status = "APPROVED"
        correction.reviewed_by_external_identity = reviewed_by_external_identity
        correction.reviewed_at = _utcnow()
        correction.published_at = _utcnow()
        self.session.add(AdminAuditLog(
            school_id=correction.school_id, performed_by_external_id=reviewed_by_external_identity,
            action="ESSAY_CORRECTION_APPROVED", entity_type="ESSAY_CORRECTION",
            entity_id=str(correction.id), metadata_=edits or None,
        ))
        await self.session.flush()
        return correction

    async def edit(
        self, essay_correction_id: uuid.UUID, *, reviewed_by_external_identity: str,
        final_scores: dict | None = None, final_feedback: dict | None = None,
        ai_output_patch: dict | None = None, annotations_patch: list[dict] | None = None,
    ) -> EssayCorrection:
        """Revisa uma correção JÁ APROVADA (publicada) - status, published_at
        e reviewed_at ficam intocados de propósito (continuam representando
        quando a correção foi originalmente aprovada/publicada, então um
        edit aqui não reordena a redação na linha do tempo de evolução do
        aluno). reviewed_by_external_identity É atualizado, mesmo campo que
        approve() já usa, para refletir quem mexeu por último."""
        correction = await self.session.get(EssayCorrection, essay_correction_id)
        if correction is None:
            raise ValueError(f"EssayCorrection not found: {essay_correction_id}")
        if correction.status != "APPROVED":
            raise ValueError(
                f"EssayCorrection {essay_correction_id} is {correction.status}, not "
                "APPROVED - only an approved correction can be edited this way."
            )
        edits = await self._apply_edit_patches(
            correction, final_scores=final_scores, final_feedback=final_feedback,
            ai_output_patch=ai_output_patch, annotations_patch=annotations_patch,
        )
        correction.reviewed_by_external_identity = reviewed_by_external_identity
        if edits:
            self.session.add(AdminAuditLog(
                school_id=correction.school_id, performed_by_external_id=reviewed_by_external_identity,
                action="ESSAY_CORRECTION_EDITED", entity_type="ESSAY_CORRECTION",
                entity_id=str(correction.id), metadata_=edits,
            ))
        await self.session.flush()
        return correction

    async def _apply_edit_patches(
        self, correction: EssayCorrection, *, final_scores, final_feedback,
        ai_output_patch, annotations_patch,
    ) -> dict:
        """Compartilhado por approve() e edit() - valida e aplica os 4
        patches opcionais, devolve o dict pronto para AdminAuditLog.metadata_
        (só as chaves que de fato mudaram)."""
        edits: dict = {}
        if final_scores is not None:
            try:
                Scores.model_validate(final_scores)
            except ValidationError as exc:
                raise ValueError(f"final_scores is not a valid Scores payload: {exc}") from exc
            if final_scores != correction.final_scores:
                edits["final_scores"] = {"before": correction.final_scores, "after": final_scores}
                correction.final_scores = final_scores
        if final_feedback is not None:
            try:
                Feedback.model_validate(final_feedback)
            except ValidationError as exc:
                raise ValueError(f"final_feedback is not a valid Feedback payload: {exc}") from exc
            if final_feedback != correction.final_feedback:
                edits["final_feedback"] = {"before": correction.final_feedback, "after": final_feedback}
                correction.final_feedback = final_feedback
        if ai_output_patch is not None or annotations_patch is not None:
            ai_output = dict(correction.ai_output or {})
            if ai_output_patch is not None:
                ai_output_diff = apply_ai_output_patch(ai_output, ai_output_patch)
                if ai_output_diff:
                    edits["ai_output"] = ai_output_diff
            if annotations_patch is not None:
                submission = await self.session.get(EssaySubmission, correction.essay_submission_id)
                annotations_diff = apply_annotations_patch(
                    ai_output, annotations_patch,
                    canonical_text=submission.canonical_text or "", anchor_mode=submission.anchor_mode,
                )
                if annotations_diff.get("added"):
                    edits["annotations_added"] = annotations_diff["added"]
                if annotations_diff.get("edited"):
                    edits["annotations_edited"] = annotations_diff["edited"]
                if annotations_diff.get("removed"):
                    edits["annotations_removed"] = annotations_diff["removed"]
            correction.ai_output = ai_output
        return edits
```

Adicione o import no topo do arquivo (perto dos outros imports de `.essay_correction_edits` a criar):

```python
from .essay_correction_edits import apply_ai_output_patch, apply_annotations_patch
```

`correction.ai_output = ai_output` (reatribuição, não mutação in-place do dict já anexado ao objeto) é necessário para o SQLAlchemy detectar a mudança na coluna JSON e persistir - mutar um dict já lido de uma coluna JSON in-place nem sempre é detectado automaticamente pelo tracker de mudanças.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/bin/pytest tests/test_r3_essay_correction_service.py -v`
Expected: PASS, incluindo os 4 testes novos de `edit()` e os testes já existentes de `approve()`/`reject()` continuando verdes (nenhuma regressão).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/essay_correction.py tests/test_r3_essay_correction_service.py
git commit -m "feat: EssayCorrectionService.edit() e approve() aplicam patches de feedback/marcacoes com auditoria"
```

---

### Task 3: Rotas `/approve` e `/edit` expõem os campos novos

**Files:**
- Modify: `src/agente_ia_edu/api/routes/essay_corrections.py` (linhas 39-46 `ApproveCorrectionRequest`/`EditCorrectionRequest`; linhas 170-234 as duas rotas)
- Test: `tests/test_r3_essay_corrections_routes.py`

**Interfaces:**
- Consumes: `EssayCorrectionService.approve`/`.edit` estendidos (Task 2).
- Produces: nada consumido por outra task Python - a Task 4/5/6 (frontend) consomem isso só via HTTP (payload JSON), documentado aqui.

- [ ] **Step 1: Escrever os testes de rota (falhando)**

```python
# adicionar em tests/test_r3_essay_corrections_routes.py, na classe
# EssayCorrectionsRoutesTests (reaproveita _seed_pending_correction já
# existente, mas cada teste que precisa de anotações/canonical_text
# customizado passa ai_output/canonical_text pelo helper - se
# _seed_pending_correction não aceitar esses overrides ainda, estenda-o
# com parâmetros opcionais em vez de duplicar o fixture)

def test_approve_with_annotations_patch_adds_a_marking(self):
    correction_id, _school_id = self._seed_pending_correction(
        "40", ai_output={"annotations": []}, canonical_text="Um texto qualquer.",
    )
    self._as("teacher_40")
    resp = self.client.post(
        f"/api/v1/teacher/essay-corrections/{correction_id}/approve",
        json={"annotations_patch": [{
            "op": "add",
            "annotation": {
                "competency_code": "C1", "kind": "ACERTO",
                "anchor": {"start": 3, "end": 8, "quote": "texto"},
                "short_comment": "curto", "long_comment": "longo",
            },
        }]},
    )
    self.assertEqual(resp.status_code, 200, resp.text)
    self.assertEqual(len(resp.json()["ai_output"]["annotations"]), 1)

def test_edit_with_ai_output_patch_updates_a_structured_field(self):
    correction_id, _school_id = self._seed_pending_correction(
        "41", status="APPROVED", ai_output={"c2_tema": "antigo"},
    )
    self._as("teacher_41")
    resp = self.client.post(
        f"/api/v1/teacher/essay-corrections/{correction_id}/edit",
        json={"ai_output_patch": {"c2_tema": "novo"}},
    )
    self.assertEqual(resp.status_code, 200, resp.text)
    self.assertEqual(resp.json()["ai_output"]["c2_tema"], "novo")

def test_edit_with_annotations_patch_add_on_image_region_submission_is_422(self):
    correction_id, _school_id = self._seed_pending_correction(
        "42", status="APPROVED", anchor_mode="IMAGE_REGION",
    )
    self._as("teacher_42")
    resp = self.client.post(
        f"/api/v1/teacher/essay-corrections/{correction_id}/edit",
        json={"annotations_patch": [{
            "op": "add",
            "annotation": {
                "competency_code": "C1", "kind": "ACERTO",
                "anchor": {"start": 0, "end": 1, "quote": "x"},
                "short_comment": "c", "long_comment": "l",
            },
        }]},
    )
    self.assertEqual(resp.status_code, 422, resp.text)

def test_edit_with_unknown_annotation_letter_is_422_and_keeps_old_annotations(self):
    correction_id, _school_id = self._seed_pending_correction(
        "43", status="APPROVED", ai_output={"annotations": []},
    )
    self._as("teacher_43")
    resp = self.client.post(
        f"/api/v1/teacher/essay-corrections/{correction_id}/edit",
        json={"annotations_patch": [{"op": "remove", "letter": "Z"}]},
    )
    self.assertEqual(resp.status_code, 422, resp.text)

def test_edit_annotations_patch_writes_one_audit_log_entry(self):
    correction_id, _school_id = self._seed_pending_correction(
        "44", status="APPROVED", ai_output={"annotations": []}, canonical_text="Um texto qualquer.",
    )
    self._as("teacher_44")
    self.client.post(
        f"/api/v1/teacher/essay-corrections/{correction_id}/edit",
        json={"annotations_patch": [{
            "op": "add",
            "annotation": {
                "competency_code": "C1", "kind": "ACERTO",
                "anchor": {"start": 3, "end": 8, "quote": "texto"},
                "short_comment": "curto", "long_comment": "longo",
            },
        }]},
    )

    async def _fetch_log():
        async with self.factory() as session:
            from agente_ia_edu.db.models import AdminAuditLog
            from sqlalchemy import select
            rows = (await session.execute(
                select(AdminAuditLog).where(AdminAuditLog.action == "ESSAY_CORRECTION_EDITED")
            )).scalars().all()
            return rows

    logs = self.loop.run_until_complete(_fetch_log())
    self.assertEqual(len(logs), 1)
    self.assertIn("annotations_added", logs[0].metadata_)
```

Se `_seed_pending_correction` ainda não aceita `ai_output`/`canonical_text`/`anchor_mode` como parâmetros opcionais (com os defaults que já usa hoje quando omitidos), estenda a assinatura dela para aceitá-los - é o MESMO helper que Task 2 pediu para não duplicar em `test_r3_essay_correction_service.py`, mas aqui é a versão para os testes de rota (arquivo diferente, fixture parecida mas não compartilhada entre os dois arquivos de teste - são suítes independentes, cada uma com seu próprio helper).

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/bin/pytest tests/test_r3_essay_corrections_routes.py -k "annotations_patch or ai_output_patch" -v`
Expected: FAIL - `ApproveCorrectionRequest`/`EditCorrectionRequest` ainda não têm os campos, os testes devem falhar com 422 de "extra fields not permitted" ou aceitar mas o serviço não fazer nada com eles.

- [ ] **Step 3: Adicionar os campos nos schemas e passar adiante nas duas rotas**

```python
# essay_corrections.py linhas 39-46, substituir:
class ApproveCorrectionRequest(BaseModel):
    final_scores: Optional[dict] = None
    final_feedback: Optional[dict] = None
    ai_output_patch: Optional[dict] = None
    annotations_patch: Optional[list[dict]] = None


class EditCorrectionRequest(BaseModel):
    final_scores: Optional[dict] = None
    final_feedback: Optional[dict] = None
    ai_output_patch: Optional[dict] = None
    annotations_patch: Optional[list[dict]] = None
```

Na rota `approve_essay_correction` (chamada a `service.approve`, hoje só passa `final_scores`/`final_feedback`):

```python
            correction = await service.approve(
                essay_correction_id, reviewed_by_external_identity=identity.external_user_id,
                final_scores=request.final_scores, final_feedback=request.final_feedback,
                ai_output_patch=request.ai_output_patch, annotations_patch=request.annotations_patch,
            )
```

Substitua o corpo inteiro de `edit_essay_correction` (linhas 205-234, que hoje faz a validação/mutação inline) para delegar ao serviço, igual `approve_essay_correction` já faz:

```python
@essay_corrections_router.post(
    "/{essay_correction_id}/edit", response_model=EssayCorrectionResponse
)
async def edit_essay_correction(
    essay_correction_id: UUID,
    request: EditCorrectionRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayCorrectionResponse:
    """Lets a teacher revise the score/feedback/structured-feedback/
    annotations of a correction that was already approved (published) -
    unlike /approve, which only works while the correction is still
    PENDING_REVIEW."""
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        await _correction_for_own_school_or_403(
            session, essay_correction_id=essay_correction_id, school_id=school_id
        )
        service = EssayCorrectionService(session)
        try:
            correction = await service.edit(
                essay_correction_id, reviewed_by_external_identity=identity.external_user_id,
                final_scores=request.final_scores, final_feedback=request.final_feedback,
                ai_output_patch=request.ai_output_patch, annotations_patch=request.annotations_patch,
            )
        except ValueError as exc:
            status_code = 409 if "not APPROVED" in str(exc) else 422
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        # Build the response BEFORE commit: commit() expires `correction`
        # (expire_on_commit=True in production - see db/session.py), and
        # accessing its attributes afterwards triggers a synchronous
        # lazy-load that raises MissingGreenlet in an async context.
        response = _correction_to_response(correction)
        await session.commit()
        return response
```

`status_code = 409 if "not APPROVED" in str(exc) else 422` reproduz exatamente as duas mensagens de erro que `EssayCorrectionService.edit` levanta hoje ("... is {status}, not APPROVED - only an approved correction can be edited this way." vs. qualquer outro `ValueError` de validação de patch) - confirme lendo `edit()` (Task 2) antes de aplicar este passo, já que a substring exata precisa bater com a mensagem real do método.

`approve_essay_correction` já tem esse mesmo bloco `try/except ValueError` (linhas 190-191 hoje) apontando pra 422 sempre - **não mude isso**: hoje `approve()` já usa 422 pra "not PENDING_REVIEW" (não é 409 como `edit`) - comportamento pré-existente, fora do escopo desta task, não uniformizar.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/bin/pytest tests/test_r3_essay_corrections_routes.py -v`
Expected: PASS, incluindo os testes novos e todos os testes já existentes de `/edit`/`/approve` (linhas 233-410 do arquivo antes desta task) continuando verdes.

- [ ] **Step 5: Rodar a suíte ampla do subsistema pra checar regressão**

Run: `.venv/bin/pytest tests -k "essay_correction" -q` (ou `.venv/bin/python -m pytest` se o comando `pytest` direto for bloqueado)
Expected: todos passam, nenhuma regressão nas rotas/serviço de correção.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_corrections.py tests/test_r3_essay_corrections_routes.py
git commit -m "feat: rotas approve/edit de correcao aceitam ai_output_patch e annotations_patch"
```

---

### Task 4: `essay-annotations.js` ganha modo editável (retrocompatível)

**Files:**
- Modify: `src/agente_ia_edu/web/essay-annotations.js`
- Test: `tests/test_essay_annotations_editable.js` (novo)

**Interfaces:**
- Consumes: nada de outra task.
- Produces (usado pela Task 6):
  - `renderHighlightedText(canonicalText, annotations)` — assinatura **inalterada**, comportamento inalterado (usado hoje pelos dois portais).
  - `popoverHtml(markerNumber, annotation, options)` — nova função **pura** (exportada junto de `renderHighlightedText`/`wirePopovers`), devolve a string HTML do conteúdo do popover. Sem `options`/com `options = {}`, devolve exatamente o HTML que `showPopover` já monta hoje (somente leitura). Com `options = {editable: true}`, inclui dois botões a mais: `<button type="button" data-annotation-edit>Editar</button>` e `<button type="button" data-annotation-remove>Remover</button>`.
  - `wirePopovers(rootEl, annotations, options)` — novo 3º parâmetro **opcional**. Quando omitido (como os dois portais já chamam hoje), chama `popoverHtml` sem `options` - comportamento idêntico ao atual. Quando `options = {onEdit(annotation), onRemove(annotation)}` é passado, chama `popoverHtml(number, annotation, {editable: true})` e liga os dois botões aos callbacks.

Este projeto não usa jsdom em nenhum teste de frontend existente (confirmado: nenhum arquivo em `tests/*_frontend.js` toca `document`/`createElement` real) - `node --test` aqui testa só funções puras (string in, string/objeto out). Este task segue a mesma convenção: extrai a MONTAGEM do HTML do popover (o que hoje é só a parte de dentro de `showPopover` que monta `popover.innerHTML = ...`) para uma função pura testável sem DOM nenhum, e deixa a parte que realmente toca `document`/`addEventListener` (inalterada em estrutura, só parametrizada) sem teste automatizado - coberta pela verificação manual da Task 6 (que já testa o fluxo de popover completo no navegador).

- [ ] **Step 1: Ler o módulo inteiro antes de mexer**

Leia `src/agente_ia_edu/web/essay-annotations.js` de ponta a ponta (167 linhas) - em particular `showPopover` (a função que hoje monta E insere o HTML do popover, tudo junto) e `wirePopovers` (quem chama `showPopover`) - para separar a montagem do HTML (a extrair) da inserção/wiring (que fica como está, só ganha o parâmetro novo).

- [ ] **Step 2: Escrever o teste da função pura (falhando)**

```javascript
// tests/test_essay_annotations_editable.js
const test = require('node:test');
const assert = require('node:assert/strict');
const { popoverHtml } = require('../src/agente_ia_edu/web/essay-annotations.js');

const ANNOTATION = {
  letter: 'A', competency_code: 'C1', short_comment: 'curto', long_comment: 'longo',
  pedagogical_suggestion: null,
};

test('popoverHtml sem options nao inclui botoes de editar/remover (retrocompatibilidade)', () => {
  const html = popoverHtml(1, ANNOTATION);
  assert.doesNotMatch(html, /data-annotation-edit/);
  assert.doesNotMatch(html, /data-annotation-remove/);
  assert.match(html, /curto/);
  assert.match(html, /longo/);
});

test('popoverHtml com options.editable inclui os dois botoes', () => {
  const html = popoverHtml(1, ANNOTATION, { editable: true });
  assert.match(html, /data-annotation-edit/);
  assert.match(html, /data-annotation-remove/);
});
```

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `node --test tests/test_essay_annotations_editable.js`
Expected: FAIL - `popoverHtml` não existe ainda no módulo (não está no objeto devolvido por `createEssayAnnotations`).

- [ ] **Step 4: Extrair `popoverHtml` de `showPopover` e propagar `options` por `wirePopovers`**

Dentro de `showPopover`, a montagem de `popover.innerHTML = \`...\`` vira uma chamada a uma função nova `popoverHtml(markerEl.dataset.markerNumber, annotation, options)` (mesmo conteúdo de hoje, só que devolvido em vez de atribuído direto):

```javascript
function popoverHtml(markerNumber, annotation, options) {
  const editable = options && options.editable;
  return `
    <strong>${esc(markerNumber)} — ${esc(annotation.competency_code)}</strong>
    <p>${esc(annotation.short_comment)}</p>
    <p class="empty-text">${esc(annotation.long_comment)}</p>
    ${annotation.pedagogical_suggestion ? `<p class="essay-popover-suggestion">${esc(annotation.pedagogical_suggestion)}</p>` : ''}
    ${editable ? '<button type="button" data-annotation-edit>Editar</button>' : ''}
    ${editable ? '<button type="button" data-annotation-remove>Remover</button>' : ''}`;
}
```

`showPopover` passa a chamar `popover.innerHTML = popoverHtml(markerEl.dataset.markerNumber, annotation, options);` e, logo depois de `rootEl.appendChild(popover)`, liga os botões quando existem:

```javascript
if (options && options.onEdit) {
  popover.querySelector('[data-annotation-edit]').addEventListener('click', () => options.onEdit(annotation));
}
if (options && options.onRemove) {
  popover.querySelector('[data-annotation-remove]').addEventListener('click', () => options.onRemove(annotation));
}
```

Note que `popoverHtml` decide se inclui os BOTÕES a partir de `options.editable`, mas `showPopover` decide se LIGA os cliques a partir de `options.onEdit`/`options.onRemove` - Task 6 sempre passa os três juntos (`{editable: true, onEdit, onRemove}`), então na prática andam sempre juntos, mas manter as duas checagens separadas evita um botão sem handler nenhum se algum chamador futuro passar só um dos dois por engano.

Propague `options` por `wirePopovers` até a chamada de `showPopover` (hoje `wirePopovers` chama `showPopover(rootEl, markerEl, annotation)` a partir de um listener de clique/foco no marcador - adicione `options` como terceiro parâmetro de `wirePopovers` e repasse). Adicione `popoverHtml` ao objeto devolvido no fim do arquivo, junto de `renderHighlightedText`/`renderImageMarkers`/`wirePopovers`.

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `node --test tests/test_essay_annotations_editable.js`
Expected: PASS (2 testes)

- [ ] **Step 6: Rodar a suíte de frontend inteira pra checar retrocompatibilidade**

Run: `node --test tests/*_frontend.js tests/test_essay_annotations_editable.js`
Expected: todos passam - em especial qualquer teste que já exercita `essay-annotations.js` hoje (aluno e professor) continua verde sem tocar em `options`.

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/web/essay-annotations.js tests/test_essay_annotations_editable.js
git commit -m "feat: essay-annotations.js ganha modo editavel opcional no popover"
```

---

### Task 5: Painel do professor edita todo o texto de feedback

**Files:**
- Modify: `src/agente_ia_edu/web/essay-review.js` (dentro de `renderReviewPanel`, linhas ~1142-1420 - o bloco `scoresFeedbackHtml` e os handlers de `er-approve-btn`/`er-edit-btn`)
- Test: `tests/test_essay_review_feedback_editing_frontend.js` (novo)

**Interfaces:**
- Consumes: rotas `/approve` e `/edit` aceitando `ai_output_patch` (Task 3) - o corpo montado aqui usa exatamente essa forma.
- Produces: nada consumido por outra task - é o fim da cadeia de edição de TEXTO (a Task 6 cuida só de marcações, um bloco visualmente separado no mesmo painel).

- [ ] **Step 1: Ler o bloco atual inteiro antes de mexer**

Releia `renderReviewPanel` (linhas 1142-1420 de `essay-review.js`) e em particular como `originalScores`/`originalFeedbackText` são capturados no render (pra comparação de "mudou ou não") e como o handler de `er-approve-btn`/`er-edit-btn` monta `body` só com o que mudou - o padrão novo replica exatamente essa mesma ideia pros campos novos, não inventa um mecanismo diferente.

- [ ] **Step 2: Escrever o teste (falhando)**

```javascript
// tests/test_essay_review_feedback_editing_frontend.js
const test = require('node:test');
const assert = require('node:assert/strict');

// Este arquivo testa só a MONTAGEM do corpo da requisição a partir do
// estado do formulário - não o DOM inteiro (essay-review.js hoje não
// exporta módulo nenhum, é um script de página; a task de implementação
// deve extrair a função pura de "quais campos mudaram -> corpo do
// patch" para um pequeno objeto exportável, ex: um novo arquivo
// essay-review-edit-diff.js com module.exports, e essay-review.js
// importa/usa esse objeto em vez de reimplementar a lógica inline -
// mesma separação já usada por essay-report.js/essay-annotations.js).
const { buildAiOutputPatch } = require('../src/agente_ia_edu/web/essay-review-edit-diff.js');

test('buildAiOutputPatch so inclui campos que mudaram em relacao ao original', () => {
  const original = { c2_tema: 'antigo', intro_message: 'oi' };
  const current = { c2_tema: 'novo', intro_message: 'oi' };
  assert.deepEqual(buildAiOutputPatch(original, current), { c2_tema: 'novo' });
});

test('buildAiOutputPatch devolve null quando nada mudou', () => {
  const original = { c2_tema: 'igual' };
  const current = { c2_tema: 'igual' };
  assert.equal(buildAiOutputPatch(original, current), null);
});
```

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `node --test tests/test_essay_review_feedback_editing_frontend.js`
Expected: FAIL com `Cannot find module '../src/agente_ia_edu/web/essay-review-edit-diff.js'`

- [ ] **Step 4: Criar o módulo puro de diff e usá-lo em `essay-review.js`**

```javascript
// src/agente_ia_edu/web/essay-review-edit-diff.js
/* AGENTE IA EDU — função pura compartilhada pelos handlers de
   aprovar/editar do painel de revisão: compara o valor original (o que
   foi carregado no form) com o valor atual (o que está nos campos agora)
   e devolve só as chaves que mudaram, prontas pro corpo de
   ai_output_patch - ou null se nada mudou, pro chamador decidir não
   incluir a chave no corpo da requisição. */
(function essayReviewEditDiffModule(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.EssayReviewEditDiff = api;
})(typeof window !== 'undefined' ? window : null, function createEssayReviewEditDiff() {
  function buildAiOutputPatch(original, current) {
    const patch = {};
    let changed = false;
    Object.keys(current).forEach((field) => {
      if (current[field] !== (original[field] ?? '')) {
        patch[field] = current[field];
        changed = true;
      }
    });
    return changed ? patch : null;
  }

  return { buildAiOutputPatch };
});
```

Em `essay-review.js`, dentro de `renderReviewPanel`, adicione ao HTML já montado em `scoresFeedbackHtml` (logo depois do bloco de "Feedback"/antes de "Pré-visualização") os campos editáveis novos - mesmo padrão visual de `<textarea>` que "Próxima redação" já usa, um por campo de `AI_OUTPUT_EDITABLE_FIELDS` do Task 1 (os 8 de C2/C3 + intro_message + closing_message como texto simples; `mechanical_review` e `feedback.strengths`/`feedback.improvements` como um `<textarea>` de "uma linha por item", convertido pra lista via `.split('\n').map(s => s.trim()).filter(Boolean)` ao montar o corpo, e exibido via `.join('\n')` ao carregar):

```javascript
${['c2_tipologia_textual', 'c2_tema', 'c2_repertorio_sociocultural', 'c2_orientacao_melhoria',
   'c3_projeto_argumentativo', 'c3_fatos_informacoes_opinioes', 'c3_autoria', 'c3_orientacao_melhoria',
   'intro_message', 'closing_message'].map((field) => `
  <div class="form-group">
    <label ${isEditableNow ? `for="er-aiout-${field}"` : ''}>${field}</label>
    ${isEditableNow
      ? `<textarea id="er-aiout-${field}" class="textarea-input" rows="2">${tmEsc(aiOutput[field] || '')}</textarea>`
      : `<p class="empty-text">${tmEsc(aiOutput[field] || '—')}</p>`}
  </div>`).join('')}
<div class="form-group">
  <label ${isEditableNow ? 'for="er-aiout-mechanical_review"' : ''}>Revisão mecânica (uma ocorrência por linha, formato "CATEGORIA | trecho | forma sugerida | explicação")</label>
  ${isEditableNow
    ? `<textarea id="er-aiout-mechanical_review_raw" class="textarea-input" rows="3">${tmEsc((aiOutput.mechanical_review || []).map((m) => `${m.category} | ${m.excerpt} | ${m.suggested_form} | ${m.rule_explanation}`).join('\n'))}</textarea>`
    : ''}
</div>
```

Ao montar o corpo (dentro dos handlers de `er-approve-btn`/`er-edit-btn`, junto de `scoresEdited`/`feedbackEdited`), monte `currentAiOutput` lendo os campos de texto simples e `feedback.strengths`/`feedback.improvements` como listas a partir de dois `<textarea>` adicionais (mesmo padrão "uma linha por item"), depois:

```javascript
const currentAiOutput = {
  c2_tipologia_textual: container.querySelector('#er-aiout-c2_tipologia_textual').value.trim(),
  // ... os outros 7 campos de texto simples, mesma forma
};
const aiOutputPatch = window.EssayReviewEditDiff.buildAiOutputPatch(aiOutput, currentAiOutput);
if (aiOutputPatch) body.ai_output_patch = aiOutputPatch;
```

`feedback.strengths`/`feedback.improvements` (listas) continuam indo por `final_feedback` (já existe, só precisa incluir as duas chaves novas no objeto montado junto de `next_essay_strategy`, com o mesmo dirty-check por igualdade de array serializado como JSON string, já que são arrays e não strings simples).

**Importante:** o handler de `er-edit-btn` hoje tem um retorno antecipado quando nada mudou (`if (!scoresEdited && !feedbackEdited) { editBtn.disabled = false; return; }`) - estenda essa condição para `if (!scoresEdited && !feedbackEdited && !aiOutputPatch) { ... return; }`, senão editar só um campo estruturado novo (sem tocar nota nenhuma nem "Próxima redação") faz o clique em "Salvar alterações" não fazer nada. `er-approve-btn` não tem esse retorno antecipado (sempre envia, mesmo com corpo vazio, pra mover a correção pra APPROVED) - não mexer nesse comportamento.

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `node --test tests/test_essay_review_feedback_editing_frontend.js`
Expected: PASS (2 testes)

- [ ] **Step 6: Carregar `essay-review-edit-diff.js` no HTML do portal do professor**

`teacher.html` carrega `essay-review.js` hoje via `<script>` - adicione um `<script src="essay-review-edit-diff.js">` **antes** do script de `essay-review.js` (mesma ordem de carregamento que `essay-report.js`/`essay-annotations.js` já seguem - scripts compartilhados primeiro, o script da página por último). Confira `web/app.mount`/`StaticFiles` (`/teacher/assets`) já serve qualquer arquivo dentro de `web/` automaticamente - não precisa registrar rota nova, só adicionar a tag `<script>`.

- [ ] **Step 7: Verificação manual no navegador**

Suba o servidor de dev (`preview_start` com a config já existente em `.claude/launch.json`), entre no portal do professor como uma correção `APPROVED` de teste, edite um dos campos estruturados novos, clique "Salvar alterações", recarregue a página e confirme que o valor editado persistiu (bate com o que a rota `/edit` gravou). Isso cobre o que `node --test` não alcança (DOM real, fluxo de clique completo) - documente no relatório da task que essa verificação foi feita, com o que foi visto.

- [ ] **Step 8: Rodar a suíte de frontend inteira**

Run: `node --test tests/*_frontend.js tests/test_essay_review_feedback_editing_frontend.js tests/test_essay_annotations_editable.js`
Expected: todos passam.

- [ ] **Step 9: Commit**

```bash
git add src/agente_ia_edu/web/essay-review.js src/agente_ia_edu/web/essay-review-edit-diff.js \
        src/agente_ia_edu/web/teacher.html tests/test_essay_review_feedback_editing_frontend.js
git commit -m "feat: painel do professor edita os campos estruturados de feedback"
```

---

### Task 6: Adicionar/editar/remover marcações no painel do professor

**Files:**
- Modify: `src/agente_ia_edu/web/essay-review.js` (função `loadOriginalContent`, linhas ~1413-1452, e o painel de revisão pra incluir o botão "Adicionar marcação")
- Modify: `src/agente_ia_edu/web/styles.css` (estilo do popover de criação de marcação e do modo de seleção, reaproveitando classes já existentes de `.essay-popover` como base)
- Test: `tests/test_essay_review_annotation_offsets_frontend.js` (novo)

**Interfaces:**
- Consumes: `wirePopovers(rootEl, annotations, options)` (Task 4); rotas `/approve`/`/edit` aceitando `annotations_patch` (Task 3).
- Produces: nada consumido por outra task - última task do plano.

- [ ] **Step 1: Escrever o teste da função pura de cálculo de offset (falhando)**

```javascript
// tests/test_essay_review_annotation_offsets_frontend.js
const test = require('node:test');
const assert = require('node:assert/strict');
const { offsetsFromSelection } = require('../src/agente_ia_edu/web/essay-review-selection.js');

test('offsetsFromSelection calcula start/end a partir do texto antes da selecao', () => {
  const result = offsetsFromSelection({ precedingText: 'A ', selectedText: 'leitura' });
  assert.deepEqual(result, { start: 2, end: 9, quote: 'leitura' });
});

test('offsetsFromSelection com selecao vazia devolve null (nada selecionado)', () => {
  assert.equal(offsetsFromSelection({ precedingText: 'A ', selectedText: '' }), null);
});
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `node --test tests/test_essay_review_annotation_offsets_frontend.js`
Expected: FAIL com `Cannot find module '../src/agente_ia_edu/web/essay-review-selection.js'`

- [ ] **Step 3: Implementar o módulo puro + a integração com `Range`/`Selection` real**

```javascript
// src/agente_ia_edu/web/essay-review-selection.js
/* AGENTE IA EDU — cálculo de start/end de uma marcação nova a partir de
   uma seleção de texto do professor. A parte pura (testável sem DOM)
   é offsetsFromSelection; getSelectionOffsets faz a ponte com a
   Selection/Range real do navegador e SÓ funciona dentro de um elemento
   que renderiza o texto puro (sem marcações/tags extras) - ver
   essay-review.js's "modo seleção" em loadOriginalContent, que troca
   temporariamente pra essa renderização plana antes de habilitar a
   seleção, evitando que o "<sup>1</sup>" injetado por uma marcação já
   existente polua a contagem de caracteres. */
(function essayReviewSelectionModule(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.EssayReviewSelection = api;
})(typeof window !== 'undefined' ? window : null, function createEssayReviewSelection() {
  function offsetsFromSelection({ precedingText, selectedText }) {
    if (!selectedText) return null;
    const start = precedingText.length;
    return { start, end: start + selectedText.length, quote: selectedText };
  }

  function getSelectionOffsets(containerEl) {
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0) return null;
    const range = selection.getRangeAt(0);
    if (!containerEl.contains(range.commonAncestorContainer)) return null;
    const precedingRange = document.createRange();
    precedingRange.selectNodeContents(containerEl);
    precedingRange.setEnd(range.startContainer, range.startOffset);
    return offsetsFromSelection({
      precedingText: precedingRange.toString(), selectedText: range.toString(),
    });
  }

  return { offsetsFromSelection, getSelectionOffsets };
});
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `node --test tests/test_essay_review_annotation_offsets_frontend.js`
Expected: PASS (2 testes)

- [ ] **Step 5: Fila local de operações pendentes — por que não envia direto**

**Restrição do spec (Global Constraints/seção Frontend): "uma única chamada a `/edit` por vez, não uma por campo"** - isso cobre o bloco de marcações também, não só os campos de texto que a Task 5 já implementou. Adicionar/editar/remover uma marcação NÃO dispara uma requisição própria: acumula numa fila local (`pendingAnnotationsPatch`, um array em memória, vive só enquanto o painel desta correção está aberto) e só viaja pro servidor junto com tudo o mais, no PRÓXIMO clique em "Aprovar"/"Salvar alterações" - o mesmo botão e a mesma chamada que a Task 5 já monta com `final_scores`/`final_feedback`/`ai_output_patch`.

Dentro de `renderReviewPanel`, declare `let pendingAnnotationsPatch = [];` e `let displayedAnnotations = annotations.slice();` (cópia local da lista real, é o que `loadOriginalContent`/`renderHighlightedText`/`wirePopovers` passam a usar pra desenhar a tela - nunca `annotations` direto, pra refletir os pendentes sem esperar o servidor). Cada ação do professor muda as DUAS coisas juntas, sem nenhuma chamada de rede:

- **Adicionar**: `pendingAnnotationsPatch.push({op: 'add', annotation: {...}})`; em `displayedAnnotations`, acrescenta uma entrada com uma letra temporária só pra exibição (ex: `'_pending_' + pendingAnnotationsPatch.length`, nunca enviada ao servidor - o array `annotation` dentro da operação `add` no patch é o que de fato viaja, sem `letter` nenhum, igual ao brief da Task 1 já espera). Re-renderiza o texto destacado (`renderHighlightedText(canonicalText, displayedAnnotations)`) pra mostrar a marcação nova na hora.
- **Editar**: se a letra é `_pending_*` (uma marcação ainda não salva), muda o `annotation` dentro da PRÓPRIA operação `add` já na fila (não cria uma segunda operação) e a entrada correspondente em `displayedAnnotations`. Se é uma letra real (já existe no servidor, seja da IA ou de um save anterior), empilha `{op: 'edit', letter, changes: {...}}` em `pendingAnnotationsPatch` e atualiza a entrada em `displayedAnnotations` do mesmo jeito.
- **Remover**: se é `_pending_*`, tira a operação `add` correspondente da fila e a entrada de `displayedAnnotations` - cancela sem nunca ter tocado o servidor. Se é uma letra real, empilha `{op: 'remove', letter}` e tira a entrada de `displayedAnnotations`.

O botão "Adicionar marcação" (visível só quando `content.anchor_mode === 'TEXT_OFFSET'`) entra em modo de seleção: substitui temporariamente `target.innerHTML` por `<div id="er-plain-text-for-selection">${tmEsc(content.canonical_text)}</div>` (texto puro, sem `<mark>`/`<sup>` - é isso que garante que `getSelectionOffsets` do Step 3 nunca precisa lidar com caracteres injetados). Um listener de `mouseup` nesse `<div>` chama `window.EssayReviewSelection.getSelectionOffsets(...)`; se devolver `{start, end, quote}`, mostra um popover (reaproveite o CSS de `.essay-popover`) com select de competência (C1-C5), select de tipo (ACERTO/ATENCAO/MELHORIA), dois `<textarea>` (curto/longo), botão "Adicionar" - que executa a operação "Adicionar" descrita acima (sem rede) e volta pra visualização destacada normal (não mais o texto plano).

Ligue `onEdit`/`onRemove` na chamada de `window.EssayAnnotations.wirePopovers(target, displayedAnnotations, {...})` (Task 4) na visualização normal: `onEdit(annotation)` abre um popover só com os 4 campos editáveis (`competency_code`, `kind`, `short_comment`, `long_comment` - a posição/texto ancorado nunca muda numa edição, só remove+recria se o professor quiser mudar o trecho) e executa a operação "Editar" acima; `onRemove(annotation)` confirma (`window.confirm`, mesmo padrão que "Rejeitar" já usa em algum lugar deste projeto - grep por `confirm(` em `essay-review.js` pra achar o precedente exato) e executa a operação "Remover" acima.

- [ ] **Step 6: Ligar `pendingAnnotationsPatch` ao botão de salvar já existente (Task 5)**

Nos handlers de `er-approve-btn`/`er-edit-btn` que a Task 5 já estendeu (adicionando `ai_output_patch` ao `body` quando `aiOutputPatch` não é `null`), adicione mais uma condição: `if (pendingAnnotationsPatch.length) body.annotations_patch = pendingAnnotationsPatch;`. Isso entra na MESMA condição de "tem algo pra enviar" que já decide se o botão de aprovar dispara com corpo vazio ou não - para "Salvar alterações" (`er-edit-btn`), que hoje só envia a requisição se `scoresEdited || feedbackEdited` (Task 5 já estendeu pra incluir `aiOutputPatch`), estenda de novo pra incluir `pendingAnnotationsPatch.length > 0` na condição que decide se o clique faz alguma coisa.

Depois de uma chamada bem-sucedida, o código já existente chama `renderReviewQueue(returnStatus)` (sai do painel) - isso descarta `pendingAnnotationsPatch`/`displayedAnnotations` (variáveis locais da função `renderReviewPanel`) naturalmente, sem precisar de nenhuma limpeza explícita: a próxima vez que o professor abrir esta correção, `renderReviewPanel` roda de novo do zero com os dados frescos do servidor (já incluindo as marcações que acabaram de ser salvas, com as letras reais atribuídas por `next_annotation_letter`, Task 1).

Isto é HTML/DOM real (listeners, `window.getSelection`, popover dinâmico, estado local em closure) - não é razoável cobrir o fluxo inteiro com `node --test` sem jsdom (que este projeto não usa). O Step 7 cobre a verificação real.

- [ ] **Step 7: Verificação manual no navegador (obrigatória, documentar no relatório)**

Suba o servidor de dev, abra uma correção `APPROVED` de uma redação `TEXT_OFFSET` no portal do professor:
1. Clique "Adicionar marcação", selecione um trecho de texto, preencha o popover, clique "Adicionar" - confirme que a marcação aparece destacada na tela IMEDIATAMENTE, sem nenhuma requisição de rede ainda (verifique na aba de rede do navegador/`read_network_requests`).
2. Clique numa marcação existente (da IA), clique "Editar", mude o comentário curto, salve - confirme que o texto mudou na tela, ainda sem requisição.
3. Clique "Remover" numa marcação - confirme que ela some da tela, ainda sem requisição.
4. Clique "Salvar alterações" - confirme que UMA ÚNICA requisição a `/edit` sai, com `annotations_patch` contendo as 3 operações das etapas 1-3 juntas. Recarregue a página e confirme que as 3 mudanças persistiram (a nova tem uma letra real atribuída pelo servidor).
5. Repita o passo 1 com uma correção da mesma escola mas `anchor_mode = IMAGE_REGION` - confirme que o botão "Adicionar marcação" **não aparece**.
6. Confira no banco (ou reabrindo a tela) que existe **uma única** entrada nova de `AdminAuditLog` (`ESSAY_CORRECTION_EDITED`) cobrindo as 3 ações do passo 4, não três entradas separadas.

- [ ] **Step 8: Rodar a suíte de frontend inteira**

Run: `node --test tests/*_frontend.js`
Expected: todos passam (300+ testes já existentes + os novos desta leva).

- [ ] **Step 9: Rodar a suíte Python inteira do subsistema de redação**

Run: `.venv/bin/pytest tests -k "essay" --ignore=tests/test_r4_essay_batch_migration_postgresql.py -q`
Expected: todos passam (as 2 falhas conhecidas desse arquivo ignorado são pré-existentes e não relacionadas, já documentadas nesta sessão).

- [ ] **Step 10: Commit**

```bash
git add src/agente_ia_edu/web/essay-review.js src/agente_ia_edu/web/essay-review-selection.js \
        src/agente_ia_edu/web/styles.css tests/test_essay_review_annotation_offsets_frontend.js
git commit -m "feat: professor adiciona, edita e remove marcacoes no texto da redacao"
```
