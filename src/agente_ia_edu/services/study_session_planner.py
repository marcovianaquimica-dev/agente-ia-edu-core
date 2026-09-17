"""PHASE 24 - StudySessionPlanner.

A PURE, deterministic, AI-agnostic function: given a time budget, break config,
optional target contents, and the ALREADY-COMPUTED PHASE 21 Adaptive Learning
Path + PHASE 23 material availability, it produces one ordered study plan.

It does NOT: correct questions, recompute the Domain Map, build a second
learning path, or select practice questions (that is PHASE 22, invoked lazily
when a PRACTICE block starts). It only ORCHESTRATES.

Determinism: every ordering has an explicit tiebreaker (priority_score desc,
then content_code asc); integer/float arithmetic only; no randomness; no clock
in the output except generated_at.

Block types (extensible): STUDY / PRACTICE / REVIEW / BREAK (+ DIAGNOSE reserved).
A block with no executable experience yet is emitted with action_available=False
and a clear note - never a fake action.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

PLANNER_VERSION = "phase24-planner-v1"

BLOCK_STUDY = "STUDY"
BLOCK_PRACTICE = "PRACTICE"
BLOCK_REVIEW = "REVIEW"
BLOCK_BREAK = "BREAK"
BLOCK_DIAGNOSE = "DIAGNOSE"

# PHASE 21 content states we consume (kept as plain strings - no import coupling).
_S_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
_S_BLOCKED = "BLOCKED_BY_PREREQUISITE"
_S_RECOMMENDED = "RECOMMENDED"
_S_READY = "READY"
_S_NEEDS_REVIEW = "NEEDS_REVIEW"
_S_MASTERED = "MASTERED"


@dataclass(frozen=True)
class StudySessionPlanPolicy:
    """Every tunable in ONE place (spec s12 - not hardcoded across the code)."""

    # session-level safe limits (minutes)
    session_min_minutes: int = 10
    session_max_minutes: int = 300
    untimed_target_minutes: int = 45
    break_default_minutes: int = 10

    # content selection
    max_contents: int = 4
    min_content_minutes: int = 15

    # duration estimates
    minutes_per_question: float = 2.5
    practice_min_questions: int = 5
    practice_max_questions: int = 20
    study_minutes_floor: int = 10
    review_minutes_floor: int = 8

    # which blocks currently have a real executable experience
    review_action_available: bool = False      # no "redo my errors" flow yet (s9)
    diagnose_action_available: bool = False     # initial diagnostic is out of scope (s32)
    study_requires_material: bool = True        # STUDY is executable only if material exists (s10)

    # time mix per PHASE 21 state: fractions of the content's minutes going to
    # STUDY / PRACTICE / REVIEW (must sum to ~1). Pedagogy of spec s8.
    state_mix: dict = field(default_factory=lambda: {
        _S_INSUFFICIENT: {"STUDY": 0.60, "PRACTICE": 0.40, "REVIEW": 0.0},
        _S_BLOCKED:      {"STUDY": 0.80, "PRACTICE": 0.20, "REVIEW": 0.0},
        _S_RECOMMENDED:  {"STUDY": 0.15, "PRACTICE": 0.50, "REVIEW": 0.35},
        _S_READY:        {"STUDY": 0.0,  "PRACTICE": 0.70, "REVIEW": 0.30},
        _S_NEEDS_REVIEW: {"STUDY": 0.0,  "PRACTICE": 0.40, "REVIEW": 0.60},
        _S_MASTERED:     {"STUDY": 0.0,  "PRACTICE": 0.35, "REVIEW": 0.65},
    })
    default_mix: dict = field(default_factory=lambda: {"STUDY": 0.4, "PRACTICE": 0.4, "REVIEW": 0.2})

    @classmethod
    def default(cls) -> "StudySessionPlanPolicy":
        return cls()

    def as_dict(self) -> dict:
        return {
            "session_min_minutes": self.session_min_minutes,
            "session_max_minutes": self.session_max_minutes,
            "untimed_target_minutes": self.untimed_target_minutes,
            "break_default_minutes": self.break_default_minutes,
            "max_contents": self.max_contents,
            "min_content_minutes": self.min_content_minutes,
            "minutes_per_question": self.minutes_per_question,
            "practice_min_questions": self.practice_min_questions,
            "practice_max_questions": self.practice_max_questions,
            "study_minutes_floor": self.study_minutes_floor,
            "review_minutes_floor": self.review_minutes_floor,
            "review_action_available": self.review_action_available,
            "diagnose_action_available": self.diagnose_action_available,
            "study_requires_material": self.study_requires_material,
            "state_mix": self.state_mix,
            "default_mix": self.default_mix,
        }

    def with_overrides(self, overrides: dict | None) -> "StudySessionPlanPolicy":
        if not overrides:
            return self
        allowed = {k: v for k, v in overrides.items()
                   if k in self.as_dict() and k not in ("state_mix", "default_mix")}
        return replace(self, **allowed) if allowed else self


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class _Content:
    code: str
    name: str
    state: str
    priority: float
    accuracy: float | None
    questions_answered: int
    unsatisfied_prerequisites: list  # [{code, name}]
    from_target: bool                # explicitly requested by coordination / student


class StudySessionPlanner:
    def __init__(self, policy: StudySessionPlanPolicy | None = None) -> None:
        self.policy = policy or StudySessionPlanPolicy.default()

    # ---- public -------------------------------------------------------

    def plan(
        self,
        *,
        effective_minutes: int,
        timer_mode: str,
        breaks: list[dict] | None,
        target_content_codes: list[str] | None,
        path: dict,
        material_availability: dict[str, dict],
        catalog_names: dict[str, str] | None = None,
    ) -> dict:
        pol = self.policy
        notes: list[str] = []
        breaks = list(breaks or [])

        if timer_mode == "UNTIMED":
            effective_minutes = pol.untimed_target_minutes
            notes.append("Sessão sem cronômetro rígido: plano organizado para "
                         f"~{effective_minutes} min de referência.")

        contents = self._pick_contents(target_content_codes, path, notes, catalog_names or {})
        if not contents:
            return self._empty_plan(effective_minutes, timer_mode, material_availability, notes)

        alloc = self._allocate_minutes(contents, effective_minutes, notes)

        blocks: list[dict] = []
        for c in contents:
            minutes = alloc.get(c.code, 0)
            if minutes <= 0:
                continue
            blocks.extend(self._content_blocks(c, minutes, material_availability, notes))

        blocks = self._insert_breaks(blocks, breaks, notes)
        blocks = self._finalize(blocks)

        eff = sum(b["estimated_minutes"] for b in blocks if b["block_type"] != BLOCK_BREAK)
        brk = sum(b["estimated_minutes"] for b in blocks if b["block_type"] == BLOCK_BREAK)
        return {
            "planner_version": PLANNER_VERSION,
            "generated_at": _now_iso(),
            "timer_mode": timer_mode,
            "effective_study_minutes": eff,
            "break_minutes": brk,
            "total_minutes": eff + brk,
            "target_content_codes": [c.code for c in contents],
            "target_content_names": [c.name for c in contents],
            "blocks": blocks,
            "notes": notes,
            "policy": pol.as_dict(),
            "ai_used": False,
        }

    # ---- content selection -----------------------------------------

    def _pick_contents(self, target_codes, path, notes, catalog_names: dict[str, str]) -> list[_Content]:
        steps = {s["content_code"]: s for s in path.get("steps", [])}
        mastered = {m["content_code"]: m for m in path.get("mastered", [])}

        def mk(code: str, from_target: bool) -> _Content:
            s = steps.get(code) or mastered.get(code)
            if s is None:
                # Not in this student's learning path yet (e.g. a coordinator
                # just assigned it with zero prior evidence) - fall back to the
                # real catalog name rather than the raw code (spec s25).
                return _Content(code=code, name=catalog_names.get(code, code), state=_S_INSUFFICIENT, priority=0.5,
                                accuracy=None, questions_answered=0,
                                unsatisfied_prerequisites=[], from_target=from_target)
            state = s.get("content_state") or (_S_MASTERED if code in mastered else _S_INSUFFICIENT)
            return _Content(
                code=code, name=s.get("content_name") or code, state=state,
                priority=float(s.get("priority_score") or (0.2 if code in mastered else 0.5)),
                accuracy=s.get("accuracy"), questions_answered=int(s.get("questions_answered") or 0),
                unsatisfied_prerequisites=list(s.get("unsatisfied_prerequisites") or []),
                from_target=from_target,
            )

        if target_codes:
            picked = [mk(code, True) for code in target_codes]
        else:
            ordered = sorted(
                path.get("steps", []),
                key=lambda s: (-float(s.get("priority_score") or 0.0), s.get("content_code") or ""),
            )
            picked = [mk(s["content_code"], False) for s in ordered[: self.policy.max_contents]]
            if not picked and mastered:
                first = sorted(mastered.values(), key=lambda m: m["content_code"])[0]
                picked = [mk(first["content_code"], False)]

        # BLOCKED -> substitute the first unsatisfied prerequisite (spec s8)
        out: list[_Content] = []
        for c in picked:
            if c.state == _S_BLOCKED and c.unsatisfied_prerequisites:
                pre = c.unsatisfied_prerequisites[0]
                notes.append(f"{c.name}: bloqueado por pré-requisito — priorizando "
                             f"{pre.get('name') or pre.get('code')} primeiro.")
                out.append(_Content(code=pre.get("code"), name=pre.get("name") or pre.get("code"),
                                    state=_S_INSUFFICIENT, priority=max(c.priority, 0.6),
                                    accuracy=None, questions_answered=0,
                                    unsatisfied_prerequisites=[], from_target=c.from_target))
            else:
                out.append(c)
        # de-dup by code, keep first (stable), deterministic
        seen: set[str] = set()
        dedup: list[_Content] = []
        for c in out:
            if c.code and c.code not in seen:
                seen.add(c.code)
                dedup.append(c)
        return dedup

    # ---- time allocation -----------------------------------------

    def _allocate_minutes(self, contents: list[_Content], effective: int, notes) -> dict[str, int]:
        pol = self.policy
        weights = {c.code: max(c.priority, 0.05) for c in contents}
        # drop lowest-priority contents until each remaining can get the minimum
        ordered = sorted(contents, key=lambda c: (-weights[c.code], c.code))
        chosen = list(ordered)
        while len(chosen) > 1 and effective < pol.min_content_minutes * len(chosen):
            dropped = chosen.pop()
            notes.append(f"Tempo insuficiente para todos os conteúdos — "
                         f"{dropped.name} ficará para outra sessão.")
        wsum = sum(weights[c.code] for c in chosen) or 1.0
        alloc: dict[str, int] = {}
        running = 0
        for i, c in enumerate(chosen):
            if i == len(chosen) - 1:
                alloc[c.code] = max(0, effective - running)
            else:
                m = int(round(effective * weights[c.code] / wsum))
                m = max(m, pol.min_content_minutes if effective >= pol.min_content_minutes else m)
                alloc[c.code] = m
                running += m
        return alloc

    # ---- per-content blocks -------------------------------------

    def _content_blocks(self, c: _Content, minutes: int, material_avail, notes) -> list[dict]:
        pol = self.policy
        mix = pol.state_mix.get(c.state, pol.default_mix)
        raw = {
            BLOCK_STUDY: minutes * mix.get("STUDY", 0.0),
            BLOCK_PRACTICE: minutes * mix.get("PRACTICE", 0.0),
            BLOCK_REVIEW: minutes * mix.get("REVIEW", 0.0),
        }
        blocks: list[dict] = []

        study_m = int(round(raw[BLOCK_STUDY]))
        if study_m >= pol.study_minutes_floor:
            avail = material_avail.get(c.code) or {}
            has_material = bool(avail.get("material_available"))
            action = has_material or not pol.study_requires_material
            note = None
            if not action:
                note = "Nenhum material publicado para este conteúdo ainda."
            blocks.append(self._block(
                BLOCK_STUDY, c, study_m, action_available=action, action_note=note,
                extra={"material_count": int(avail.get("material_count") or 0),
                       "material_id": avail.get("material_id"),
                       "material_title": avail.get("material_title"),
                       "reason": f"Construção de base em {c.name}."}))

        practice_m = int(round(raw[BLOCK_PRACTICE]))
        if practice_m >= pol.minutes_per_question:
            q = int(round(practice_m / pol.minutes_per_question))
            q = max(pol.practice_min_questions, min(pol.practice_max_questions, q))
            est = int(round(q * pol.minutes_per_question))
            blocks.append(self._block(
                BLOCK_PRACTICE, c, est, action_available=True, action_note=None,
                extra={"requested_questions": q,
                       "reason": f"Prática adaptativa em {c.name} "
                                 f"(~{q} questões, ~{pol.minutes_per_question:g} min/questão)."}))

        review_m = int(round(raw[BLOCK_REVIEW]))
        if review_m >= pol.review_minutes_floor:
            blocks.append(self._block(
                BLOCK_REVIEW, c, review_m,
                action_available=pol.review_action_available,
                action_note=(None if pol.review_action_available
                             else "Revisão guiada de erros estará disponível em uma próxima fase."),
                extra={"reason": f"Revisão de {c.name} "
                                 f"({'desempenho ' + _pct(c.accuracy) if c.accuracy is not None else 'evidência recente'})."}))
        return blocks

    def _block(self, block_type: str, c: _Content, minutes: int, *,
               action_available: bool, action_note: str | None, extra: dict) -> dict:
        icons = {BLOCK_STUDY: "📘", BLOCK_PRACTICE: "📝", BLOCK_REVIEW: "🔄",
                 BLOCK_BREAK: "☕", BLOCK_DIAGNOSE: "🧭"}
        labels = {BLOCK_STUDY: f"Estudar {c.name}", BLOCK_PRACTICE: f"Praticar {c.name}",
                  BLOCK_REVIEW: f"Revisar {c.name}", BLOCK_DIAGNOSE: f"Diagnosticar {c.name}"}
        return {
            "block_type": block_type,
            "content_code": c.code,
            "content_name": c.name,
            "content_state": c.state,
            "title": labels.get(block_type, block_type),
            "icon": icons.get(block_type, "•"),
            "estimated_minutes": max(1, int(minutes)),
            "action_available": bool(action_available),
            "action_note": action_note,
            "status": "PENDING",
            "practice_id": None,
            **extra,
        }

    # ---- breaks -----------------------------------------------------

    def _insert_breaks(self, blocks: list[dict], breaks: list[dict], notes) -> list[dict]:
        pol = self.policy
        if not breaks or not blocks:
            return blocks
        study_blocks = [b for b in blocks if b["block_type"] != BLOCK_BREAK]
        n = len(study_blocks)
        out: list[dict] = []
        # position-based: after the k-th study block (1-indexed). Default: spread evenly.
        placements: list[tuple[int, dict]] = []
        for i, br in enumerate(breaks):
            dur = int(br.get("duration_minutes") or pol.break_default_minutes)
            if dur <= 0:
                notes.append("Intervalo com duração inválida foi ignorado.")
                continue
            after = br.get("after_block")
            if after is None:
                after = int(round((i + 1) * n / (len(breaks) + 1)))
            after = max(1, min(n, int(after)))
            placements.append((after, {
                "block_type": BLOCK_BREAK, "content_code": None, "content_name": None,
                "content_state": None, "title": "Intervalo", "icon": "☕",
                "estimated_minutes": dur, "action_available": True, "action_note": None,
                "status": "PENDING", "practice_id": None,
                "start_at": br.get("start_at"), "end_at": br.get("end_at"),
                "reason": "Pausa programada — não conta como tempo de estudo.",
            }))
        placements.sort(key=lambda p: p[0])
        pi = 0
        study_seen = 0
        for b in blocks:
            out.append(b)
            if b["block_type"] != BLOCK_BREAK:
                study_seen += 1
                while pi < len(placements) and placements[pi][0] == study_seen:
                    out.append(placements[pi][1])
                    pi += 1
        while pi < len(placements):
            out.append(placements[pi][1])
            pi += 1
        return out

    # ---- finalize -------------------------------------------------

    def _finalize(self, blocks: list[dict]) -> list[dict]:
        for i, b in enumerate(blocks):
            b["index"] = i
        return blocks

    def _empty_plan(self, effective, timer_mode, material_avail, notes) -> dict:
        notes.append("Ainda não há evidências para personalizar seu momento de aprendizado — "
                     "conclua uma atividade com classificação curricular ou escolha um conteúdo.")
        minutes = max(self.policy.study_minutes_floor,
                      int(effective) if timer_mode == "TIMED" else self.policy.untimed_target_minutes)
        blocks = self._finalize([{
            "block_type": BLOCK_STUDY, "content_code": None, "content_name": None,
            "content_state": None, "title": "Explorar um conteúdo", "icon": "📘",
            "estimated_minutes": max(1, minutes), "action_available": False,
            "action_note": "Escolha um conteúdo ou faça uma atividade para liberar este bloco.",
            "status": "PENDING", "practice_id": None,
            "reason": "Sem evidências e sem conteúdo definido — bloco informativo.",
        }])
        return {
            "planner_version": PLANNER_VERSION, "generated_at": _now_iso(),
            "timer_mode": timer_mode, "effective_study_minutes": blocks[0]["estimated_minutes"],
            "break_minutes": 0, "total_minutes": blocks[0]["estimated_minutes"],
            "target_content_codes": [], "target_content_names": [], "blocks": blocks, "notes": notes,
            "policy": self.policy.as_dict(), "ai_used": False,
        }


def _pct(a: float | None) -> str:
    return "—" if a is None else f"{round(a * 1000) / 10:g}%"


__all__ = ["StudySessionPlanner", "StudySessionPlanPolicy", "PLANNER_VERSION",
           "BLOCK_STUDY", "BLOCK_PRACTICE", "BLOCK_REVIEW", "BLOCK_BREAK", "BLOCK_DIAGNOSE"]
