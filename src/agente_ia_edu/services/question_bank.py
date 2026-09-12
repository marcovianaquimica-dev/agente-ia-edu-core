"""Question Bank core - AI-agnostic read/query layer over the official question bank.

PHASE 12. Turns the existing structure

    Institution -> Exam -> ExamApplication(year, day) -> ExamBooklet
      -> BookletQuestion(official_number, position)
      -> QuestionVersion(version_kind='official_original', canonical_text, statement)
      -> QuestionOption(option_key, position, text)

plus the curriculum-v2 classifications stored in ``pedagogical_classifications``
(``metadata->>'taxonomy_version' = 'curriculum-v2'``, ``lifecycle = 'ACTIVE'``,
``content`` = the ``catalog_nodes.code`` of the primary CONTENT node) into a
reusable, paginated Question Bank query service.

Design rules (PHASE 12):
  * No IA. This module imports no provider, no OpenAI SDK, nothing under
    ``agente_ia_edu.providers`` and nothing under ``classification_prompts`` /
    ``ai_classification_service`` / ``classification_consensus``.
  * The ENEM area/prova is derived DETERMINISTICALLY from ``booklet_questions
    .official_number`` (Day 1: Q1-45 Linguagens, Q46-90 Ciencias Humanas;
    Day 2: Q91-135 Ciencias da Natureza, Q136-180 Matematica). No redundant
    column is added.
  * Read-only: this service never writes. It never modifies questions, versions,
    options, classifications, catalog nodes or bindings.
  * Only ACTIVE curriculum-v2 classifications are surfaced. Historical metadata
    is passed through untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Sequence
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from agente_ia_edu.db.models import (
    BookletQuestion,
    CatalogNode,
    Exam,
    ExamApplication,
    ExamBooklet,
    PedagogicalClassification,
    Question,
    QuestionOption,
    QuestionVersion,
)

CURRICULUM_TAXONOMY_VERSION = "curriculum-v2"
OFFICIAL_VERSION_KIND = "official_original"

# 2020 questions whose classifications are protected (PHASE 11): never rewritten,
# never forced. Surfaced as a question property, never a blocker.
PROTECTED_QUESTIONS: frozenset[tuple[int, int]] = frozenset(
    {(2020, 91), (2020, 93), (2020, 107), (2020, 128), (2020, 133)}
)

# ---------------------------------------------------------------------------
# Deterministic ENEM area derivation
# ---------------------------------------------------------------------------

_ENEM_AREAS: tuple[tuple[int, int, str, str], ...] = (
    (1, 45, "LC", "Linguagens, Codigos e suas Tecnologias"),
    (46, 90, "CH", "Ciencias Humanas e suas Tecnologias"),
    (91, 135, "CN", "Ciencias da Natureza e suas Tecnologias"),
    (136, 180, "MT", "Matematica e suas Tecnologias"),
)
ENEM_AREA_CODES: tuple[str, ...] = tuple(code for _, _, code, _ in _ENEM_AREAS)


def derive_enem_area(official_number: int | None) -> tuple[str | None, str | None]:
    """Return ``(area_code, area_label)`` for an ENEM official number.

    Purely structural - no database, no LLM. Returns ``(None, None)`` when the
    number is missing or outside the 1..180 ENEM range.
    """
    if official_number is None:
        return (None, None)
    for lo, hi, code, label in _ENEM_AREAS:
        if lo <= official_number <= hi:
            return (code, label)
    return (None, None)


def _official_numbers_for_area(area_code: str) -> tuple[int, int] | None:
    for lo, hi, code, _ in _ENEM_AREAS:
        if code == area_code:
            return (lo, hi)
    return None


# ---------------------------------------------------------------------------
# Read DTOs (plain dataclasses - the API layer maps these to Pydantic)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OptionView:
    id: UUID
    key: str
    position: int
    text: str
    is_valid_option: bool


@dataclass(frozen=True)
class CurriculumClassificationView:
    taxonomy_version: str
    discipline_code: str | None
    area_code: str | None
    content_code: str | None
    subcontent_code: str | None
    status: str  # CLASSIFIED | NEEDS_REVIEW | DRAFT
    lifecycle: str  # ACTIVE
    source: str | None  # rule | ai | human | hybrid
    provider_name: str | None
    model_version: str | None
    prompt_version: str | None
    classification_mode: str | None  # DETERMINISTIC | AI_ASSISTED | FORCED_CLOSURE | ...
    confidence: str | None  # HIGH | MEDIUM | LOW (band, from metadata) - may be None
    numeric_confidence: float | None  # classification_confidence column, if any
    review_reason: str | None
    closure_phase: str | None
    visual_dependency: bool
    evidence: list[dict[str, Any]]
    context: str | None
    created_at: str | None


@dataclass(frozen=True)
class AssetRef:
    """A reference to an associated asset. The store is not populated yet
    (PHASE 12 only prepares the shape); ``present`` is always ``False`` for now.
    """

    kind: str
    reference: str | None
    present: bool


@dataclass(frozen=True)
class QuestionBankItem:
    question_id: UUID
    question_version_id: UUID
    version_kind: str
    year: int | None
    day: int | None
    enem_area: str | None
    enem_area_label: str | None
    booklet_code: str | None
    official_number: int | None
    position: int | None
    canonical_text: str
    statement: str | None
    recommended_difficulty: str | None
    options: list[OptionView]
    classification: CurriculumClassificationView | None
    classification_state: str  # UNCLASSIFIED | CLASSIFIED | NEEDS_REVIEW | FORCED_CLOSURE
    is_protected: bool
    has_visual_dependency: bool
    assets: list[AssetRef]
    evidence_uri: str | None  # booklet provenance pointer (not a visual asset)


@dataclass(frozen=True)
class QuestionBankPage:
    items: list[QuestionBankItem]
    page: int
    page_size: int
    total: int

    @property
    def total_pages(self) -> int:
        if self.page_size <= 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size


@dataclass(frozen=True)
class QuestionBankFilters:
    year: int | None = None
    day: int | None = None
    enem_area: str | None = None  # LC | CH | CN | MT
    discipline_code: str | None = None
    area_code: str | None = None
    content_code: str | None = None
    subcontent_code: str | None = None
    official_number: int | None = None
    booklet_code: str | None = None
    classification_state: str | None = None  # UNCLASSIFIED|CLASSIFIED|NEEDS_REVIEW|FORCED_CLOSURE|ANY_CLASSIFIED
    classification_source: str | None = None  # rule | ai | human | hybrid
    classification_mode: str | None = None  # DETERMINISTIC | FORCED_CLOSURE | ...
    has_classification: bool | None = None
    provisional_only: bool | None = None  # NEEDS_REVIEW or FORCED_CLOSURE
    visual_dependency: bool | None = None
    difficulty: str | None = None  # EASY | MEDIUM | HARD (recommended_difficulty)
    protected_only: bool | None = None


_ORDERABLE = {
    "official_number": (BookletQuestion.official_number,),
    "year": (ExamApplication.year, BookletQuestion.official_number),
    "created_at": (QuestionVersion.created_at, BookletQuestion.official_number),
    "position": (BookletQuestion.position,),
}
_CLASSIFICATION_STATES = {
    "UNCLASSIFIED",
    "CLASSIFIED",
    "NEEDS_REVIEW",
    "FORCED_CLOSURE",
    "ANY_CLASSIFIED",
}


# ---------------------------------------------------------------------------
# Selection abstraction (for the future list generator - no PDF, no persistence)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuestionSelectionEntry:
    position: int
    question_version_id: UUID
    question_id: UUID
    year: int | None
    official_number: int | None
    enem_area: str | None
    content_code: str | None


@dataclass(frozen=True)
class QuestionSelection:
    entries: list[QuestionSelectionEntry]
    source: str = "manual"

    @property
    def question_version_ids(self) -> list[UUID]:
        return [entry.question_version_id for entry in self.entries]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class QuestionBankService:
    """Reusable, AI-agnostic Question Bank query service.

    All reads go through one ``AsyncSession``. Every listing is paginated and
    deterministically ordered. Nothing here knows about OpenAI or any provider.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._catalog_cache: dict[str, CatalogNode] | None = None
        self._catalog_by_id: dict[UUID, CatalogNode] | None = None

    # -- catalog helpers (curriculum-v2 tree; 64 nodes -> load once) ---------

    async def _load_catalog(self) -> None:
        if self._catalog_cache is not None:
            return
        rows = list(
            (await self._session.scalars(select(CatalogNode))).all()
        )
        self._catalog_by_id = {node.id: node for node in rows}
        self._catalog_cache = {node.code: node for node in rows if node.code}

    def _resolve_curriculum_path(self, content_code: str | None) -> dict[str, str | None]:
        """Walk the curriculum-v2 catalog tree from a CONTENT/SUBCONTENT code
        up to its DISCIPLINE, returning the code at each level. Deterministic,
        in-memory (the 64-node tree is loaded once).
        """
        empty = {"discipline_code": None, "area_code": None,
                 "content_code": content_code, "subcontent_code": None}
        if not content_code or self._catalog_cache is None:
            return empty
        node = self._catalog_cache.get(content_code)
        if node is None:
            return empty
        levels: dict[str, str | None] = {"discipline_code": None, "area_code": None,
                                         "content_code": None, "subcontent_code": None}
        seen: set[UUID] = set()
        cur: CatalogNode | None = node
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            key = {
                "DISCIPLINE": "discipline_code",
                "AREA": "area_code",
                "CONTENT": "content_code",
                "SUBCONTENT": "subcontent_code",
            }.get((cur.node_type or "").upper())
            if key and levels.get(key) is None:
                levels[key] = cur.code
            cur = self._catalog_by_id.get(cur.parent_id) if cur.parent_id else None
        if levels["content_code"] is None:
            levels["content_code"] = content_code
        return levels

    # -- query building ----------------------------------------------------

    def _base_query(self) -> Select:
        return (
            select(Question, QuestionVersion, BookletQuestion, ExamBooklet, ExamApplication)
            .join(QuestionVersion, QuestionVersion.question_id == Question.id)
            .join(BookletQuestion, BookletQuestion.question_version_id == QuestionVersion.id)
            .join(ExamBooklet, ExamBooklet.id == BookletQuestion.exam_booklet_id)
            .join(ExamApplication, ExamApplication.id == ExamBooklet.exam_application_id)
            .where(QuestionVersion.version_kind == OFFICIAL_VERSION_KIND)
        )

    def _apply_filters(self, query: Select, f: QuestionBankFilters, pc) -> Select:
        _active = (pc.lifecycle == "ACTIVE") & (
            pc.metadata_["taxonomy_version"].as_string() == CURRICULUM_TAXONOMY_VERSION
        )
        if f.year is not None:
            query = query.where(ExamApplication.year == f.year)
        if f.day is not None:
            query = query.where(ExamApplication.day == f.day)
        if f.official_number is not None:
            query = query.where(BookletQuestion.official_number == f.official_number)
        if f.booklet_code:
            query = query.where(ExamBooklet.code == f.booklet_code)
        if f.enem_area:
            span = _official_numbers_for_area(f.enem_area.upper())
            if span is None:
                query = query.where(func.coalesce(BookletQuestion.official_number, -1) == -1)
            else:
                query = query.where(
                    BookletQuestion.official_number.between(span[0], span[1])
                )
        if f.difficulty:
            query = query.where(
                func.upper(QuestionVersion.recommended_difficulty) == f.difficulty.upper()
            )
        # -- curriculum-v2 classification filters --
        curriculum_filters = any(
            v is not None
            for v in (
                f.discipline_code, f.area_code, f.content_code, f.subcontent_code,
                f.classification_source, f.classification_mode,
            )
        )
        state = (f.classification_state or "").upper() or None
        wants_classified = (
            f.has_classification is True
            or f.provisional_only is True
            or curriculum_filters
            or (state in {"CLASSIFIED", "NEEDS_REVIEW", "FORCED_CLOSURE", "ANY_CLASSIFIED"})
        )
        wants_unclassified = f.has_classification is False or state == "UNCLASSIFIED"
        if not (wants_classified or wants_unclassified or f.visual_dependency is True):
            return query

        on_clause = (pc.question_version_id == QuestionVersion.id) & _active
        if wants_unclassified and not wants_classified:
            query = query.outerjoin(pc, on_clause).where(pc.id.is_(None))
        else:
            query = query.join(pc, on_clause)

        if f.content_code:
            query = query.where(pc.content == f.content_code)
        if f.subcontent_code:
            query = query.where(
                pc.metadata_["primary_content_code"].as_string() == f.subcontent_code
            )
        if f.discipline_code:
            query = query.where(pc.content.in_(self._codes_under(f.discipline_code)))
        if f.area_code:
            query = query.where(pc.content.in_(self._codes_under(f.area_code)))
        if f.classification_source:
            query = query.where(pc.source == f.classification_source)
        if f.classification_mode:
            query = query.where(
                pc.metadata_["classification_mode"].as_string() == f.classification_mode
            )
        if state == "CLASSIFIED":
            query = query.where(pc.status == "CLASSIFIED")
        elif state == "NEEDS_REVIEW":
            query = query.where(pc.status == "NEEDS_REVIEW")
        elif state == "FORCED_CLOSURE":
            query = query.where(
                pc.metadata_["classification_mode"].as_string() == "FORCED_CLOSURE"
            )
        if f.provisional_only is True:
            query = query.where(
                (pc.status == "NEEDS_REVIEW")
                | (pc.metadata_["classification_mode"].as_string() == "FORCED_CLOSURE")
            )
        if f.visual_dependency is True:
            query = query.where(pc.metadata_["visual_dependency"].as_boolean().is_(True))
        return query

    def _codes_under(self, ancestor_code: str) -> list[str]:
        """Every CONTENT/SUBCONTENT code whose ancestry includes ``ancestor_code``.
        In-memory over the 64-node curriculum-v2 tree.
        """
        if not self._catalog_cache:
            return [ancestor_code]
        target = self._catalog_cache.get(ancestor_code)
        if target is None:
            return [ancestor_code]
        out: list[str] = []
        for node in self._catalog_cache.values():
            if node.node_type and node.node_type.upper() not in {"CONTENT", "SUBCONTENT"}:
                continue
            cur: CatalogNode | None = node
            seen: set[UUID] = set()
            while cur is not None and cur.id not in seen:
                seen.add(cur.id)
                if cur.id == target.id and node.code:
                    out.append(node.code)
                    break
                cur = self._catalog_by_id.get(cur.parent_id) if cur.parent_id else None
        return out or [ancestor_code]

    # -- public API ------------------------------------------------------

    async def list_questions(
        self,
        filters: QuestionBankFilters | None = None,
        *,
        page: int = 1,
        page_size: int = 20,
        order_by: str = "official_number",
        order_direction: str = "asc",
    ) -> QuestionBankPage:
        filters = filters or QuestionBankFilters()
        if filters.classification_state and filters.classification_state.upper() not in _CLASSIFICATION_STATES:
            raise ValueError(f"unknown classification_state: {filters.classification_state!r}")
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        order_by = order_by if order_by in _ORDERABLE else "official_number"
        descending = str(order_direction).lower() == "desc"

        await self._load_catalog()
        pc = aliased(PedagogicalClassification)

        count_base = (
            select(func.count(func.distinct(QuestionVersion.id)))
            .select_from(Question)
            .join(QuestionVersion, QuestionVersion.question_id == Question.id)
            .join(BookletQuestion, BookletQuestion.question_version_id == QuestionVersion.id)
            .join(ExamBooklet, ExamBooklet.id == BookletQuestion.exam_booklet_id)
            .join(ExamApplication, ExamApplication.id == ExamBooklet.exam_application_id)
            .where(QuestionVersion.version_kind == OFFICIAL_VERSION_KIND)
        )
        count_q = self._apply_filters(count_base, filters, pc)
        total = int((await self._session.scalar(count_q)) or 0)

        rows_q = self._apply_filters(self._base_query(), filters, pc)
        order_cols = list(_ORDERABLE[order_by])
        order_cols = [c.desc() if descending else c.asc() for c in order_cols]
        # deterministic tiebreaker
        rows_q = rows_q.order_by(*order_cols, QuestionVersion.id.asc())
        rows_q = rows_q.offset((page - 1) * page_size).limit(page_size)
        # NOTE: list rows are light - options are NOT eager-loaded here. Callers
        # that need the full question (preview) use get_question/get_by_official_number.

        result = await self._session.execute(rows_q)
        items: list[QuestionBankItem] = []
        seen_versions: set[UUID] = set()
        version_ids: list[UUID] = []
        raw: list[tuple[Any, ...]] = []
        for question, version, bq, booklet, application in result.unique().all():
            if version.id in seen_versions:
                continue
            seen_versions.add(version.id)
            version_ids.append(version.id)
            raw.append((question, version, bq, booklet, application))

        classifications = await self._active_classifications(version_ids)
        for question, version, bq, booklet, application in raw:
            items.append(
                self._to_item(
                    question, version, bq, booklet, application,
                    classifications.get(version.id), with_options=False,
                )
            )
        return QuestionBankPage(items=items, page=page, page_size=page_size, total=total)

    async def get_question(self, question_id: UUID) -> QuestionBankItem | None:
        await self._load_catalog()
        q = (
            self._base_query()
            .where(Question.id == question_id)
            .options(selectinload(QuestionVersion.options))
        )
        row = (await self._session.execute(q)).unique().first()
        if row is None:
            return None
        question, version, bq, booklet, application = row
        classification = (await self._active_classifications([version.id])).get(version.id)
        return self._to_item(question, version, bq, booklet, application, classification)

    async def get_by_official_number(
        self, *, year: int, official_number: int, booklet_code: str | None = None
    ) -> QuestionBankItem | None:
        await self._load_catalog()
        q = (
            self._base_query()
            .where(
                ExamApplication.year == year,
                BookletQuestion.official_number == official_number,
            )
            .options(selectinload(QuestionVersion.options))
        )
        if booklet_code:
            q = q.where(ExamBooklet.code == booklet_code)
        row = (await self._session.execute(q)).unique().first()
        if row is None:
            return None
        question, version, bq, booklet, application = row
        classification = (await self._active_classifications([version.id])).get(version.id)
        return self._to_item(question, version, bq, booklet, application, classification)

    async def get_questions_by_version_ids(
        self, question_version_ids: Sequence[UUID]
    ) -> list[QuestionBankItem]:
        """Batch-load full :class:`QuestionBankItem` for a list of official
        ``question_version_id`` values, preserving the caller's order.

        Bounded query count (independent of N): 1 catalog load (cached), 1 rows
        query (``IN``), 1 options ``selectinload`` batch, 1 active-classifications
        batch. Unknown or non-official ids are silently skipped - the caller
        (e.g. the list generator) validates membership first via
        :meth:`build_selection`.
        """
        ids = list(dict.fromkeys(question_version_ids))  # de-dupe, keep order
        if not ids:
            return []
        await self._load_catalog()
        rows_q = (
            self._base_query()
            .where(QuestionVersion.id.in_(ids))
            .options(selectinload(QuestionVersion.options))
        )
        by_version: dict[UUID, tuple[Any, ...]] = {}
        for question, version, bq, booklet, application in (
            await self._session.execute(rows_q)
        ).unique().all():
            by_version.setdefault(version.id, (question, version, bq, booklet, application))
        classifications = await self._active_classifications(list(by_version))
        out: list[QuestionBankItem] = []
        for vid in ids:
            row = by_version.get(vid)
            if row is None:
                continue
            question, version, bq, booklet, application = row
            out.append(
                self._to_item(question, version, bq, booklet, application,
                              classifications.get(version.id), with_options=True)
            )
        return out

    async def build_selection(
        self, question_version_ids: Sequence[UUID], *, source: str = "manual"
    ) -> QuestionSelection:
        """Validate a list of official ``question_version_id`` values and return
        an ordered :class:`QuestionSelection` (input order preserved). No
        persistence, no IA, no PDF. Raises ``ValueError`` on any unknown or
        non-official version id or on a duplicate.
        """
        ids = list(question_version_ids)
        if len(set(ids)) != len(ids):
            raise ValueError("QuestionSelection cannot contain duplicate question_version_id values")
        if not ids:
            return QuestionSelection(entries=[], source=source)
        q = (
            select(QuestionVersion.id, QuestionVersion.question_id, ExamApplication.year,
                   BookletQuestion.official_number)
            .join(BookletQuestion, BookletQuestion.question_version_id == QuestionVersion.id)
            .join(ExamBooklet, ExamBooklet.id == BookletQuestion.exam_booklet_id)
            .join(ExamApplication, ExamApplication.id == ExamBooklet.exam_application_id)
            .where(
                QuestionVersion.id.in_(ids),
                QuestionVersion.version_kind == OFFICIAL_VERSION_KIND,
            )
        )
        found = {r[0]: r for r in (await self._session.execute(q)).all()}
        missing = [str(i) for i in ids if i not in found]
        if missing:
            raise ValueError(f"unknown or non-official question_version_id(s): {missing}")
        classifications = await self._active_classifications(ids)
        entries: list[QuestionSelectionEntry] = []
        for position, vid in enumerate(ids, start=1):
            _, question_id, year, official_number = found[vid]
            area_code, _ = derive_enem_area(official_number)
            classification = classifications.get(vid)
            entries.append(
                QuestionSelectionEntry(
                    position=position,
                    question_version_id=vid,
                    question_id=question_id,
                    year=year,
                    official_number=official_number,
                    enem_area=area_code,
                    content_code=(classification.content if classification else None),
                )
            )
        return QuestionSelection(entries=entries, source=source)

    # -- internal --------------------------------------------------------

    async def _active_classifications(
        self, version_ids: Iterable[UUID]
    ) -> dict[UUID, PedagogicalClassification]:
        ids = list(version_ids)
        if not ids:
            return {}
        q = select(PedagogicalClassification).where(
            PedagogicalClassification.question_version_id.in_(ids),
            PedagogicalClassification.lifecycle == "ACTIVE",
            PedagogicalClassification.metadata_["taxonomy_version"].as_string()
            == CURRICULUM_TAXONOMY_VERSION,
        )
        out: dict[UUID, PedagogicalClassification] = {}
        for row in (await self._session.scalars(q)).all():
            out[row.question_version_id] = row
        return out

    def _to_item(
        self, question, version, bq, booklet, application,
        classification: PedagogicalClassification | None,
        *, with_options: bool = True,
    ) -> QuestionBankItem:
        area_code, area_label = derive_enem_area(bq.official_number)
        is_protected = (application.year, bq.official_number) in PROTECTED_QUESTIONS
        options = [
            OptionView(
                id=o.id, key=o.option_key, position=o.position, text=o.text,
                is_valid_option=bool(o.is_valid_option),
            )
            for o in sorted(version.options, key=lambda x: x.position)
        ] if with_options else []
        view: CurriculumClassificationView | None = None
        state = "UNCLASSIFIED"
        visual = False
        if classification is not None:
            md = classification.metadata_ or {}
            path = self._resolve_curriculum_path(classification.content)
            mode = md.get("classification_mode")
            visual = bool(md.get("visual_dependency"))
            view = CurriculumClassificationView(
                taxonomy_version=md.get("taxonomy_version") or CURRICULUM_TAXONOMY_VERSION,
                discipline_code=path["discipline_code"],
                area_code=path["area_code"],
                content_code=path["content_code"],
                subcontent_code=path["subcontent_code"],
                status=classification.status,
                lifecycle=classification.lifecycle,
                source=classification.source,
                provider_name=classification.provider_name,
                model_version=classification.model_version,
                prompt_version=classification.prompt_version,
                classification_mode=mode,
                confidence=md.get("confidence"),
                numeric_confidence=_to_float(classification.classification_confidence),
                review_reason=md.get("review_reason"),
                closure_phase=md.get("closure_phase"),
                visual_dependency=visual,
                evidence=list(md.get("evidence") or []),
                context=md.get("context"),
                created_at=classification.created_at.isoformat()
                if classification.created_at else None,
            )
            if mode == "FORCED_CLOSURE":
                state = "FORCED_CLOSURE"
            elif classification.status == "NEEDS_REVIEW":
                state = "NEEDS_REVIEW"
            else:
                state = "CLASSIFIED"
        return QuestionBankItem(
            question_id=question.id,
            question_version_id=version.id,
            version_kind=version.version_kind,
            year=application.year,
            day=application.day,
            enem_area=area_code,
            enem_area_label=area_label,
            booklet_code=booklet.code,
            official_number=bq.official_number,
            position=bq.position,
            canonical_text=version.canonical_text,
            statement=version.statement,
            recommended_difficulty=version.recommended_difficulty,
            options=options,
            classification=view,
            classification_state=state,
            is_protected=is_protected,
            has_visual_dependency=visual,
            assets=[],  # asset store not populated yet; shape is ready
            evidence_uri=bq.evidence_uri,
        )


def _to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "CURRICULUM_TAXONOMY_VERSION",
    "ENEM_AREA_CODES",
    "PROTECTED_QUESTIONS",
    "AssetRef",
    "CurriculumClassificationView",
    "OptionView",
    "QuestionBankFilters",
    "QuestionBankItem",
    "QuestionBankPage",
    "QuestionBankService",
    "QuestionSelection",
    "QuestionSelectionEntry",
    "derive_enem_area",
]
