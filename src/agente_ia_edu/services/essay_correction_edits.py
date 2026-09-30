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

_WATERMARK_KEY = "_annotation_letter_watermark"
_EDITABLE_ANNOTATION_FIELDS = ("competency_code", "kind", "short_comment", "long_comment")


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
            # A letra sai de `annotations` agora, mas next_annotation_letter
            # nunca pode reoferece-la - marca o watermark aqui, nao so no add
            # (a letra pode ter chegado nesta correcao vinda da IA, nunca
            # passando por mark_letter_used antes).
            mark_letter_used(ai_output, letter)
            diff["removed"].append(removed)
        else:
            raise ValueError(f"unknown annotations_patch op: {op!r}")

    return {key: value for key, value in diff.items() if value}
