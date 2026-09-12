"""PHASE 9U.2-H4 — STEP 1: the ASSISTED HUMAN REVIEW packet builder.

READ-ONLY. Builds a deterministic review packet for the six residual questions
(official numbers 95, 104, 105, 112, 129, 134) so a human curator can decide,
per question, whether the deterministic candidate CONTENT is CONFIRM or REJECT.

It does NOT classify anything, does NOT write to the database, does NOT call a
provider or OpenAI, does NOT run Alembic, does NOT touch the catalog or any
vocabulary. It reuses the unchanged
``ClassificationProposalService.match_retrieval_vocabulary`` and the registered
``curriculum-v2`` bindings only to *report* the deterministic lexical situation.

Sources
-------
* Question text  : ``scratchpad_question_text_9u2.txt``  (STATEMENT / CANONICAL_TEXT /
                   ALTERNATIVAS / QUESTION_VERSION_ID / CONTENT_HASH per ``QUESTÃO #N`` block).
* G0 fiche       : ``docs/phase9u2_g0_curation.md`` §15.1 (``**#N** — qv ...`` block).
* Candidate      : the G0 content assignment (``_H4_CANDIDATE_CONTENT``), validated against
                   the registered curriculum-v2 bindings + ``_curriculum_v2_bindings.NEW_CONTENTS``.

If ``DATABASE_URL`` is set, ``augment_from_db(...)`` may add the live
question_version_id / content_hash under a READ-ONLY transaction; without it the
builder runs fully offline (local Docker is never a production substitute).

``build_packet()`` returns the packet dict; ``build_decision_template()`` returns
the empty (all-PENDING) curator decision template. ``main()`` prints the text
report and writes ``phase9u2h4_review_packet.json`` + ``phase9u2h4_decisions.json``
next to this file (artifacts, not code).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
for _cand in (Path.cwd(), _HERE.parents[1]):
    if (_cand / "src" / "agente_ia_edu").is_dir():
        sys.path.insert(0, str(_cand / "src"))
        _REPO_ROOT = _cand
        break
else:  # pragma: no cover
    _REPO_ROOT = _HERE.parents[1]

from agente_ia_edu.services._curriculum_v2_bindings import (  # noqa: E402
    DEFERRED_CONTENTS,
    NEW_AREAS,
    NEW_CONTENTS,
    TAXONOMY_VERSION,
)
from agente_ia_edu.services.curriculum_classification import (  # noqa: E402
    ClassificationProposalService,
    resolve_registered_initial_binding,
)

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

PHASE = "9U.2-H4"
TAXONOMY = TAXONOMY_VERSION  # "curriculum-v2"

H4_REVIEW_OFFICIAL_NUMBERS: tuple[int, ...] = (95, 104, 105, 112, 129, 134)

# The G0 content assignment for each residual question (docs/phase9u2_g0_curation.md
# §15.1 / §15.4). This is the *candidate* the curator confirms or rejects — it is
# NEVER used to bypass the deterministic probe.
_H4_CANDIDATE_CONTENT: dict[int, str] = {
    95: "CHEMISTRY-PHYSICAL-EQUILIBRIUM",
    104: "CHEMISTRY-PHYSICAL-ACID-BASE",
    105: "BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS",
    112: "PHYSICS-EM-ELECTROSTATICS",
    129: "BIOLOGY-ECOLOGY-BIOGEOCHEMICAL-CYCLES",
    134: "PHYSICS-MECHANICS-HYDROSTATICS",
}

# Never valid as a review target.
PROTECTED_OFFICIAL_NUMBERS = frozenset({91, 93, 107, 128})
ALREADY_CLASSIFIED_OFFICIAL_NUMBERS = frozenset(
    {92, 94, 103, 111, 113, 125, 126, 127, 130, 133, 135, 152, 153, 172}
)
DEFERRED_CONTENT_CODE = "CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS"

_QUESTION_TEXT_FILE = _REPO_ROOT / "scratchpad_question_text_9u2.txt"
_G0_DOC_FILE = _REPO_ROOT / "docs" / "phase9u2_g0_curation.md"

_CONTENT_TO_AREA = {code: area for code, _name, area in NEW_CONTENTS}
_CONTENT_TO_NAME = {code: name for code, name, _area in NEW_CONTENTS}
_AREA_TO_NAME = {code: name for code, name, _disc in NEW_AREAS}
_AREA_TO_DISCIPLINE = {code: disc for code, _name, disc in NEW_AREAS}


class PacketError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def validate_targets() -> None:
    """Pure. Raises PacketError on any target / candidate defect."""
    targets = set(H4_REVIEW_OFFICIAL_NUMBERS)
    if len(H4_REVIEW_OFFICIAL_NUMBERS) != 6 or targets != {95, 104, 105, 112, 129, 134}:
        raise PacketError(f"target set must be exactly the 6 residual questions, got {sorted(targets)}")
    if targets & PROTECTED_OFFICIAL_NUMBERS:
        raise PacketError("a protected official number is present in the review set")
    if targets & ALREADY_CLASSIFIED_OFFICIAL_NUMBERS:
        raise PacketError("an already-classified official number is present in the review set")
    if 133 in targets:
        raise PacketError("Q133 (already classified in H3) must not be a review target")
    for number in H4_REVIEW_OFFICIAL_NUMBERS:
        code = _H4_CANDIDATE_CONTENT.get(number)
        if code is None:
            raise PacketError(f"#{number} has no candidate content_code")
        if code == DEFERRED_CONTENT_CODE or code in DEFERRED_CONTENTS:
            raise PacketError(f"#{number} candidate is a DEFERRED content ({code})")
        binding = resolve_registered_initial_binding(TAXONOMY, code)
        if binding is None or binding.canonical_code != code:
            raise PacketError(f"#{number} -> {code!r} has no registered {TAXONOMY} binding")
        if code not in _CONTENT_TO_AREA:
            raise PacketError(f"#{number} -> {code!r} is not in NEW_CONTENTS")


# --------------------------------------------------------------------------- #
# Sources — question text + G0 fiche (offline, deterministic)
# --------------------------------------------------------------------------- #

_BLOCK_RE = re.compile(r"={80}\nQUEST(?:AO|ÃO) #(\d+)\n={80}\n(.*?)(?=\n={80}\nQUEST|\nQUESTIONS_REQUESTED)", re.S)


def _parse_question_text(raw: str) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for m in _BLOCK_RE.finditer(raw):
        num = int(m.group(1))
        body = m.group(2)

        def _field(name: str) -> str | None:
            fm = re.search(rf"^{name}:\s*(.+?)$", body, re.M)
            return fm.group(1).strip() if fm else None

        def _multiline(name: str, until: str) -> str | None:
            fm = re.search(rf"{name}:\n(.*?)\n\n{until}", body, re.S)
            return fm.group(1).strip() if fm else None

        statement = _multiline("STATEMENT", "CANONICAL_TEXT")
        canonical = _multiline("CANONICAL_TEXT", "ALTERNATIVAS")
        alt_block = re.search(r"ALTERNATIVAS:\n(.*?)\n\n(?:DIFFICULTY|CONTENT_HASH)", body, re.S)
        alternatives: list[str] = []
        if alt_block:
            for line in alt_block.group(1).splitlines():
                lm = re.match(r"^([A-E])\)\s*(.+)$", line.strip())
                if lm:
                    alternatives.append(f"{lm.group(1)}) {lm.group(2).strip()}")
        out[num] = {
            "question_version_id": _field("QUESTION_VERSION_ID"),
            "content_hash": _field("CONTENT_HASH"),
            "statement": statement,
            "canonical_text": canonical,
            "alternatives": alternatives,
        }
    return out


def load_question_text() -> dict[int, dict]:
    if not _QUESTION_TEXT_FILE.exists():
        raise PacketError(f"question text file not found: {_QUESTION_TEXT_FILE}")
    return _parse_question_text(_QUESTION_TEXT_FILE.read_text(encoding="utf-8"))


_G0_BLOCK_RE = re.compile(r"\*\*#(\d+)\*\*[^\n]*\n(.*?)(?=\n\*\*#\d+\*\*|\n## |\Z)", re.S)


def load_g0_fiches() -> dict[int, dict]:
    if not _G0_DOC_FILE.exists():
        return {}
    text = _G0_DOC_FILE.read_text(encoding="utf-8")
    out: dict[int, dict] = {}
    for m in _G0_BLOCK_RE.finditer(text):
        num = int(m.group(1))
        block = m.group(2).strip()
        header = m.group(0).splitlines()[0]

        def _line(prefix: str) -> str | None:
            lm = re.search(rf"^{re.escape(prefix)}:?\s*(.+?)$", block, re.M)
            return lm.group(1).strip() if lm else None

        out[num] = {
            "raw": (header + "\n" + block).strip(),
            "evidence": _line("EVIDENCE"),
            "rationale": _line("RATIONALE"),
            "next_state": _line("NEXT_STATE"),
        }
    return out


# --------------------------------------------------------------------------- #
# Deterministic lexical probe (report only — statement vs alternatives kept apart)
# --------------------------------------------------------------------------- #


def _normalizes_empty(term: str) -> bool:
    return ClassificationProposalService._normalize_vocabulary_phrase(term) == ""


def _match_terms(text: str, vocab) -> dict[str, list[str]]:
    match = ClassificationProposalService.match_retrieval_vocabulary(text, vocab, known_codes=None)
    if match is None:
        return {"primary": [], "specific": [], "contextual": []}
    return {
        "primary": list(match.matched_primary_terms),
        "specific": list(match.matched_specific_terms),
        "contextual": list(match.matched_contextual_expressions),
    }


def _probe_question(number: int, qtext: dict) -> dict:
    code = _H4_CANDIDATE_CONTENT[number]
    binding = resolve_registered_initial_binding(TAXONOMY, code)
    vocab = binding.vocabulary
    statement = qtext.get("statement") or qtext.get("canonical_text") or ""

    stmt_hits = _match_terms(statement, vocab)
    stmt_all = stmt_hits["primary"] + stmt_hits["specific"] + stmt_hits["contextual"]
    stmt_real = [t for t in stmt_all if not _normalizes_empty(t)]
    stmt_degenerate = [t for t in stmt_all if _normalizes_empty(t)]

    option_hits: list[dict] = []
    for opt in qtext.get("alternatives", []):
        oh = _match_terms(opt, vocab)
        oh_real = [t for t in (oh["primary"] + oh["specific"] + oh["contextual"]) if not _normalizes_empty(t)]
        if oh_real:
            option_hits.append({"option": opt, "terms": oh_real})

    if stmt_real:
        binding_status = "BOUND"
        match_source = "STATEMENT"
        current_decision = "READY_FOR_INITIAL"
        matched_term = stmt_real[0]
    elif stmt_degenerate:
        binding_status = "SPURIOUS_BOUND_DEGENERATE_TERM"
        match_source = "NONE_GENUINE"
        current_decision = "NEEDS_REVIEW"
        matched_term = None
    else:
        binding_status = "NONE"
        match_source = "NONE"
        current_decision = "NEEDS_REVIEW"
        matched_term = None

    warnings: list[str] = []
    if stmt_degenerate:
        warnings.append(
            f"vocabulary defect: term(s) {stmt_degenerate} normalize to the empty string "
            f"and match every statement (spurious BOUND)"
        )
    if not stmt_real and option_hits:
        warnings.append(
            "the only genuine vocabulary evidence is in the ALTERNATIVES, not the statement — "
            "the deterministic engine reads statement/canonical_text only; OPTION_ONLY_EVIDENCE"
        )
    if not stmt_real and not option_hits:
        warnings.append("no genuine vocabulary term in the statement or any alternative")

    reason = {
        "BOUND": "deterministic lexical match on the statement — would already be READY_FOR_INITIAL",
        "SPURIOUS_BOUND_DEGENERATE_TERM": "match only via an empty-normalizing vocab term; no genuine statement evidence",
        "NONE": "statement carries no vocabulary term for the candidate content",
    }[binding_status]

    return {
        "candidate_content_code": code,
        "candidate_content_name": _CONTENT_TO_NAME.get(code),
        "candidate_content_path": (
            f"{_AREA_TO_DISCIPLINE.get(_CONTENT_TO_AREA[code], '?')}"
            f" / {_AREA_TO_NAME.get(_CONTENT_TO_AREA[code], _CONTENT_TO_AREA[code])}"
            f" / {_CONTENT_TO_NAME.get(code, code)}"
        ),
        "candidate_area_code": _CONTENT_TO_AREA.get(code),
        "taxonomy_version": TAXONOMY,
        "binding_registered": binding is not None,
        "vocabulary_version": vocab.version,
        "deterministic_vocabulary_probe": {
            "statement_primary_terms": stmt_hits["primary"],
            "statement_specific_terms": stmt_hits["specific"],
            "statement_contextual_terms": stmt_hits["contextual"],
            "statement_real_terms": stmt_real,
            "statement_degenerate_terms": stmt_degenerate,
        },
        "matched_term": matched_term,
        "match_source": match_source,
        "binding_status": binding_status,
        "current_decision": current_decision,
        "reason_for_human_review": reason,
        "statement_evidence": stmt_real,
        "option_only_evidence": option_hits,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- #
# Packet + decision template
# --------------------------------------------------------------------------- #


def build_packet() -> dict:
    validate_targets()
    qtext = load_question_text()
    fiches = load_g0_fiches()

    missing = [n for n in H4_REVIEW_OFFICIAL_NUMBERS if n not in qtext]
    if missing:
        raise PacketError(f"question text missing for {missing} in {_QUESTION_TEXT_FILE.name}")

    questions = []
    for number in H4_REVIEW_OFFICIAL_NUMBERS:
        qt = qtext[number]
        probe = _probe_question(number, qt)
        g0 = fiches.get(number, {})
        questions.append(
            {
                "official_number": number,
                "question_version_id": qt["question_version_id"],
                "content_hash": qt["content_hash"],
                "statement": qt["statement"],
                "canonical_text": qt["canonical_text"],
                "alternatives": qt["alternatives"],
                "g0_fiche": {
                    "found": bool(g0),
                    "raw": g0.get("raw"),
                    "evidence": g0.get("evidence"),
                    "rationale": g0.get("rationale"),
                    "next_state": g0.get("next_state"),
                },
                **probe,
                "curator_decision": "PENDING",
                "curator_evidence_note": "",
            }
        )

    return {
        "phase": PHASE,
        "taxonomy_version": TAXONOMY,
        "generated_by": "phase9u2h4_review_packet.py",
        "review_official_numbers": list(H4_REVIEW_OFFICIAL_NUMBERS),
        "protected_official_numbers": sorted(PROTECTED_OFFICIAL_NUMBERS),
        "already_classified_official_numbers": sorted(ALREADY_CLASSIFIED_OFFICIAL_NUMBERS),
        "n05_deferred_content": DEFERRED_CONTENT_CODE,
        "database_writes": 0,
        "openai_calls": 0,
        "alembic_execution": 0,
        "questions": questions,
    }


def build_decision_template() -> dict:
    validate_targets()
    return {
        "phase": PHASE,
        "taxonomy_version": TAXONOMY,
        "instructions": (
            "For each question set decision to CONFIRM or REJECT (leave PENDING to skip). "
            "On CONFIRM, content_code MUST equal the packet's candidate_content_code for that "
            "question; fill curator_id and evidence_note (a literal excerpt or an explicit "
            "human rationale). No decision is pre-filled."
        ),
        "decisions": [
            {
                "official_number": number,
                "decision": "PENDING",
                "content_code": None,
                "candidate_content_code": _H4_CANDIDATE_CONTENT[number],
                "curator_id": "",
                "evidence_note": "",
            }
            for number in H4_REVIEW_OFFICIAL_NUMBERS
        ],
    }


# --------------------------------------------------------------------------- #
# Optional READ-ONLY DB augmentation
# --------------------------------------------------------------------------- #


async def augment_from_db(packet: dict) -> dict:  # pragma: no cover - needs DATABASE_URL
    """READ-ONLY. Overlay live question_version_id / content_hash from production.
    Never writes. Only called by main() when DATABASE_URL resolves."""
    from sqlalchemy import select
    from agente_ia_edu.db.models import BookletQuestion, QuestionVersion
    from agente_ia_edu.db.session import create_engine, create_session_factory
    from phase9u2_batch0 import _begin_read_only

    engine = create_engine()
    factory = create_session_factory(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _begin_read_only(session)
            for q in packet["questions"]:
                bqs = list(
                    (
                        await session.scalars(
                            select(BookletQuestion).where(
                                BookletQuestion.official_number == q["official_number"]
                            )
                        )
                    ).all()
                )
                qv_ids = {str(b.question_version_id) for b in bqs}
                if len(qv_ids) == 1:
                    qv_id = next(iter(qv_ids))
                    version = await session.get(QuestionVersion, qv_id)
                    q["db_question_version_id"] = qv_id
                    q["db_content_hash"] = getattr(version, "content_hash", None)
                    q["db_matches_offline"] = qv_id == q["question_version_id"]
    finally:
        await engine.dispose()
    return packet


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def render_report(packet: dict) -> str:
    lines = [f"PHASE {packet['phase']} — REVIEW PACKET", ""]
    for q in packet["questions"]:
        lines += [
            "=" * 72,
            f"Q{q['official_number']}   candidate = {q['candidate_content_code']}   "
            f"({q['candidate_content_path']})",
            "=" * 72,
            f"  question_version_id : {q['question_version_id']}",
            f"  content_hash        : {q['content_hash']}",
            f"  taxonomy_version    : {q['taxonomy_version']}   binding_registered: {q['binding_registered']}",
            f"  current_decision    : {q['current_decision']}   binding_status: {q['binding_status']}",
            f"  match_source        : {q['match_source']}   matched_term: {q['matched_term']!r}",
            f"  reason_for_review   : {q['reason_for_human_review']}",
            "",
            "  STATEMENT:",
            f"    {q['statement']}",
            "",
            "  ALTERNATIVES:",
        ]
        for opt in q["alternatives"]:
            lines.append(f"    {opt}")
        lines += [
            "",
            f"  STATEMENT_EVIDENCE (genuine vocab terms in the statement): {q['statement_evidence'] or 'NONE'}",
            "  OPTION_ONLY_EVIDENCE (genuine vocab terms only in an alternative — NOT deterministic):",
        ]
        if q["option_only_evidence"]:
            for oh in q["option_only_evidence"]:
                lines.append(f"    {oh['terms']}  in  «{oh['option']}»")
        else:
            lines.append("    NONE")
        lines += ["", "  WARNINGS:"]
        for w in q["warnings"] or ["none"]:
            lines.append(f"    - {w}")
        g0 = q["g0_fiche"]
        lines += [
            "",
            f"  G0_FICHE ({'found' if g0['found'] else 'NOT FOUND in docs/phase9u2_g0_curation.md'}):",
        ]
        if g0["found"]:
            for gl in (g0["raw"] or "").splitlines():
                lines.append(f"    {gl}")
        lines += [
            "",
            f"  CURATOR_DECISION   : {q['curator_decision']}   (CONFIRM / REJECT)",
            "  CURATOR_EVIDENCE_NOTE : \"\"",
            "",
        ]
    lines += [
        "SUMMARY",
        f"  QUESTIONS: {[q['official_number'] for q in packet['questions']]}",
        f"  ALL current_decision == NEEDS_REVIEW: "
        f"{all(q['current_decision'] == 'NEEDS_REVIEW' for q in packet['questions'])}",
        f"  DATABASE_WRITES: {packet['database_writes']}   OPENAI_CALLS: {packet['openai_calls']}   "
        f"ALEMBIC_EXECUTION: {packet['alembic_execution']}",
        "",
        "FINAL_DECISION:",
        "PHASE_9U2_H4_REVIEW_PACKET_COMPLETE",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - manual entrypoint
    parser = argparse.ArgumentParser(description="PHASE 9U.2-H4 review-packet builder (read-only)")
    parser.add_argument("--out-dir", default=str(_HERE), help="where to write the JSON artifacts")
    parser.add_argument("--no-write", action="store_true", help="print only, do not write JSON files")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    packet = build_packet()
    template = build_decision_template()

    db_url_present = False
    try:
        from agente_ia_edu.db.session import get_database_url

        get_database_url()
        db_url_present = True
    except Exception:
        db_url_present = False

    if db_url_present:
        import asyncio

        try:
            packet = asyncio.run(augment_from_db(packet))
        except Exception as exc:  # never fatal — the offline packet still stands
            packet["db_augmentation_error"] = type(exc).__name__

    print(render_report(packet))

    if not args.no_write:
        out = Path(args.out_dir)
        (out / "phase9u2h4_review_packet.json").write_text(
            json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out / "phase9u2h4_decisions.json").write_text(
            json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nwrote {out / 'phase9u2h4_review_packet.json'}")
        print(f"wrote {out / 'phase9u2h4_decisions.json'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
