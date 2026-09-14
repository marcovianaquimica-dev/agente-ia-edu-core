from __future__ import annotations

import logging
import math
import uuid
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from agente_ia_edu.services.discipline_gate import DisciplineGate
from agente_ia_edu.services.knowledge import KnowledgeService

logger = logging.getLogger(__name__)


def _as_node_id(raw: Any) -> uuid.UUID | None:
    """Coerce a projected catalog node id into the type the gate actually reads.

    ``DisciplineScope.permits`` raises ``TypeError`` on a ``str`` - it will not
    paper over a caller's type bug, and it will not deny anyone in silence
    either - and ``KnowledgeService`` stringifies every id it projects. The
    conversion therefore belongs here, explicit and visible, at the one place
    that knows both sides.

    A value that is not a usable id is reported as absent, not as a denial: the
    caller then falls back to the classification code and, failing that, to the
    absence rule. A malformed row must not make content silently disappear.
    """
    if isinstance(raw, uuid.UUID):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return uuid.UUID(raw)
        except ValueError:
            return None
    return None


def _as_node_ids(raw: Any) -> list[uuid.UUID]:
    """Every usable catalog node id a projected row carries.

    A row can be linked to more than one catalog node, so the projection hands
    over a list. Unusable entries are dropped rather than turned into denials,
    for the reason in ``_as_node_id``; a row left with no usable id falls back
    to the classification code, and failing that to the absence rule.
    """
    if not isinstance(raw, (list, tuple, set, frozenset)):
        raw = [raw]
    node_ids = []
    for entry in raw:
        node_id = _as_node_id(entry)
        if node_id is not None and node_id not in node_ids:
            node_ids.append(node_id)
    return node_ids


class StudySearchService:
    """Generic natural-language discovery service for student study requests.

    The service intentionally keeps intent parsing deterministic and domain-agnostic:
    it identifies the underlying user intent, resolves the most likely learning context,
    then delegates to the existing knowledge layer for actual data retrieval.
    """

    DISCIPLINES = {
        "Matemática": ("matematica", "matemática", "álgebra", "algebra", "equação", "equações", "fração", "frações", "função", "funções", "geometria", "derivada", "derivadas"),
        "Química": ("quimica", "química", "solução", "soluções", "diluição", "reação", "reações", "mol", "molaridade", "concentração"),
        "História": ("historia", "história", "revolução", "revolução industrial", "imperialismo", "colonialismo"),
        "Biologia": ("biologia", "genética", "ecologia", "celula", "célula"),
        "Física": ("fisica", "física", "mecânica", "movimento", "energia"),
        "Geografia": ("geografia", "mapa", "território", "urbanização"),
        "Português": ("portugues", "português", "literatura", "texto", "interpretação"),
        "Inglês": ("ingles", "inglês", "grammar", "vocabulary", "reading"),
        "Artes": ("arte", "artes", "música", "musica"),
        "Filosofia": ("filosofia", "ética", "pensamento"),
        "Sociologia": ("sociologia", "sociedade", "trabalho"),
    }

    DIFFICULTY_WORDS = {
        "EASY": ("fácil", "facil", "fáceis", "facis", "simples", "iniciante", "básico", "basico"),
        "MEDIUM": ("médio", "media", "média", "intermediário", "intermediario", "nivel medio", "nível médio", "nível medio"),
        "HARD": ("difícil", "dificeis", "difíceis", "desafio", "avançado", "complexo"),
    }

    INTENT_PATTERNS = {
        "STUDY": ("quero estudar", "quero aprender"),
        "PRACTICE": ("quero praticar",),
        "REVIEW": ("quero revisar",),
    }

    @staticmethod
    def normalize(value: str | None) -> str:
        return " ".join((value or "").strip().lower().split())

    @classmethod
    def detect_intent(cls, query: str | None) -> str:
        text = cls.normalize(query)
        if not text:
            return "SEARCH"

        for intent, markers in cls.INTENT_PATTERNS.items():
            if any(text.startswith(marker) for marker in markers):
                return intent
            if any(f" {marker}" in text for marker in markers):
                return intent
        return "SEARCH"

    @classmethod
    def _strip_noise(cls, query: str | None) -> str:
        text = cls.normalize(query or "")
        if not text:
            return ""

        for marker in (
            "quero estudar ", "quero aprender ", "quero praticar ", "quero revisar ",
            "estudar ", "aprender ", "praticar ", "revisar ",
            "questões de ", "questoes de ", "questões ", "questoes ",
            "de ", "sobre ", "para ", "do ", "da ", "dos ", "das ", "uma ", "um ",
        ):
            if text.startswith(marker):
                text = text[len(marker):]
                break
        return text.strip()

    @classmethod
    def _match_discipline(cls, query: str) -> str:
        text = cls.normalize(query)
        for discipline, markers in cls.DISCIPLINES.items():
            if any(marker in text for marker in markers):
                return discipline
        return "Geral"

    @classmethod
    def _match_grade_level(cls, query: str) -> str:
        text = cls.normalize(query)
        if any(marker in text for marker in ("ensino médio", "ensino medio", "ensino-medio", "2º ano do ensino médio", "2 ano do ensino medio", "3º ano do ensino médio", "3 ano do ensino medio")):
            return "Ensino Médio"
        if any(marker in text for marker in ("9º ano", "9 ano", "9o ano", "3ª série", "3a serie", "3ª serie", "terceira série", "terceira serie")):
            return "Ensino Fundamental"
        return "Ensino Fundamental"

    @classmethod
    def _match_difficulty(cls, query: str) -> str | None:
        text = cls.normalize(query)
        for level, markers in cls.DIFFICULTY_WORDS.items():
            if any(marker in text for marker in markers):
                return level
        return None

    @classmethod
    def _guess_content(cls, query: str) -> str:
        text = cls._strip_noise(query)
        if not text:
            return ""

        for marker in (" de ", " sobre ", " para ", " do ", " da ", " dos ", " das "):
            if marker in text:
                left, right = text.split(marker, 1)
                text = left.strip() if left.strip() else right.strip()
                break

        for grade_marker in ("9º ano", "9 ano", "9o ano", "3ª série", "3a serie", "3ª serie", "ensino médio", "ensino medio", "ensino-medio"):
            if grade_marker in text:
                text = text.replace(grade_marker, "").strip()
                break

        for level, markers in cls.DIFFICULTY_WORDS.items():
            for marker in markers:
                if marker in text:
                    text = text.replace(marker, "").strip()
                    break

        text = text.replace("  ", " ").strip()
        return text or query.strip()

    @classmethod
    def _guess_subcontent(cls, query: str) -> str | None:
        text = cls.normalize(query)
        subcontent_map = {
            "concentração comum": "concentração comum",
            "diluição": "diluição de soluções",
            "equações": "equações",
            "revolução industrial": "revolução industrial",
        }
        for key, value in subcontent_map.items():
            if key in text:
                return value
        return None

    @classmethod
    def resolve_context(cls, query: str | None, **extra: Any) -> dict[str, Any]:
        text = (query or "").strip()
        intent = cls.detect_intent(text)
        discipline = cls._match_discipline(text)
        grade_level = cls._match_grade_level(text)
        difficulty = cls._match_difficulty(text)
        content = cls._guess_content(text)
        subcontent = cls._guess_subcontent(text)
        if intent == "PRACTICE":
            resource_type = "QUESTION"
        elif intent in {"STUDY", "REVIEW"}:
            resource_type = "MATERIAL"
        else:
            resource_type = "ALL"

        if "questões" in cls.normalize(text) or "questoes" in cls.normalize(text):
            resource_type = "QUESTION"
        if "video" in cls.normalize(text) or "vídeo" in cls.normalize(text):
            resource_type = "VIDEO"

        return {
            "discipline": discipline,
            "content": content,
            "subcontent": subcontent or content,
            "grade_level": grade_level,
            "difficulty": difficulty or "MEDIUM",
            "resource_type": resource_type,
            "intent": intent,
            "tenant": extra.get("tenant", "school"),
            "status": extra.get("status", "PUBLISHED"),
            "visibility": extra.get("visibility", "PUBLIC"),
            "quantity": extra.get("quantity", 10),
            "origin": extra.get("origin", "student_search"),
            "author": extra.get("author"),
            "segment": extra.get("segment"),
            "date_range": extra.get("date_range"),
        }

    @classmethod
    def build_query(cls, query: str | None, **kwargs: Any) -> dict[str, Any]:
        text = (query or "").strip()
        resolved_context = cls.resolve_context(text, **kwargs)
        intent = resolved_context["intent"]
        payload = {
            "query": text,
            "intent": intent,
            "resolved_context": resolved_context,
            "results": {"questions": [], "materials": []},
            "pagination": {
                "page": int(kwargs.get("page", 1) or 1),
                "limit": int(kwargs.get("limit", 10) or 10),
                "total": 0,
                "total_pages": 0,
            },
        }
        return payload

    @classmethod
    async def search(
        cls,
        query: str | None,
        *,
        session=None,
        difficulty: str | None = None,
        resource_type: str | None = None,
        page: int = 1,
        limit: int = 10,
        institution_id: str | None = None,
        requester_scope_type: str | None = None,
        requester_scope_external_id: str | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        payload = cls.build_query(
            query,
            page=page,
            limit=limit,
            difficulty=difficulty,
            resource_type=resource_type,
        )

        resolved = payload["resolved_context"]
        if difficulty:
            resolved["difficulty"] = difficulty.upper()
        if resource_type:
            resolved["resource_type"] = resource_type.upper()

        if session is None:
            return payload

        content = (resolved.get("content") or "").strip()
        if not content:
            return payload

        # The gate reads the institution_id this method already took and never
        # used. A school with no universe, no active universe, or an active
        # universe declaring no catalog scope resolves as unrestricted, which is
        # every school that exists today.
        scope = await DisciplineGate(session).scope_for_school(institution_id)

        def _permitted(item: dict[str, Any]) -> bool:
            # The catalog node ids are the stronger evidence and the only one a
            # catalog-linked row or a material carries: those have no
            # ``classification`` block at all, and reading that absence as
            # "unclassified" let another discipline's content straight through.
            #
            # ``any``, not ``all``: a row linked to several nodes belongs to all
            # of them. Content that genuinely is mathematics must reach a
            # mathematics school; that it is also filed under biology is not a
            # reason to hide it.
            node_ids = _as_node_ids(item.get("gate_content_node_ids"))
            if node_ids:
                return any(scope.permits(node_id) for node_id in node_ids)
            classification = item.get("classification") or {}
            return scope.permits_code(classification.get("content"))

        knowledge = KnowledgeService(session)
        questions: list[dict[str, Any]] = []
        materials: list[dict[str, Any]] = []

        requested_type = (resource_type or resolved.get("resource_type") or "ALL").upper()
        requested_type = "ALL" if requested_type == "SEARCH" else requested_type

        if requested_type in {"ALL", "QUESTION"}:
            try:
                results = await knowledge.find_questions_by_content(
                    content,
                    difficulty=resolved.get("difficulty"),
                    institution_id=institution_id,
                    requester_institution_id=institution_id,
                    requester_scope_type=requester_scope_type,
                    requester_scope_external_id=requester_scope_external_id,
                )
                if not scope.unrestricted:
                    # Filtered on the raw rows, which carry the ``classification``
                    # block: the projection below collapses a missing
                    # classification into the search term itself, so filtering
                    # after it would compare the query against catalog codes.
                    results = [item for item in results if _permitted(item)]
                questions = [
                    {
                        "id": item.get("question_version_id"),
                        "title": (item.get("statement") or "Questão de estudo")[:180],
                        # ``classification`` is present but null on catalog-link
                        # rows, so ``.get("classification", {})`` returns None
                        # and raises - swallowed by the except below, which
                        # emptied the whole question list whenever a single
                        # catalog-linked question matched.
                        "content": (item.get("classification") or {}).get("content") or content,
                        "discipline": (item.get("classification") or {}).get("discipline") or resolved.get("discipline"),
                        "difficulty": item.get("difficulty_learning_level") or item.get("difficulty_ai") or resolved.get("difficulty"),
                        "resource_type": "QUESTION",
                    }
                    for item in results
                ]
            # Only the database errors this lookup can actually raise degrade
            # into an empty list. This branch now carries a discipline
            # restriction, and a swallowed bug is indistinguishable from a
            # correct empty result: that is exactly how a plain AttributeError
            # in the projection below passed for "no results" for an unknown
            # period. Anything else is logged and allowed to propagate.
            except SQLAlchemyError:
                logger.warning(
                    "study_search: question lookup failed for content=%r", content,
                    exc_info=True,
                )
                questions = []
            except Exception:
                logger.exception(
                    "study_search: unexpected error building questions for content=%r",
                    content,
                )
                raise

        if requested_type in {"ALL", "MATERIAL", "VIDEO"}:
            # Materials are gated by the catalog node their link was selected by.
            # The projection below echoes the search term back as ``content``, so
            # the filter has to run on the raw rows - matching the query text
            # against catalog codes would hide materials from schools entitled
            # to them.
            try:
                material_results = await knowledge.find_resources_by_content(
                    content,
                    resource_type="VIDEO" if requested_type == "VIDEO" else None,
                    requester_institution_id=institution_id,
                    requester_scope_type=requester_scope_type,
                    requester_scope_external_id=requester_scope_external_id,
                )
                if not scope.unrestricted:
                    material_results = [
                        item for item in material_results if _permitted(item)
                    ]
                materials = [
                    {
                        "id": item.get("resource_id"),
                        "title": item.get("title") or "Material complementar",
                        "content": content,
                        "discipline": resolved.get("discipline"),
                        "resource_type": item.get("resource_type") or "MATERIAL",
                    }
                    for item in material_results
                ]
            # Same rule as the question branch above, and the same reason.
            except SQLAlchemyError:
                logger.warning(
                    "study_search: material lookup failed for content=%r", content,
                    exc_info=True,
                )
                materials = []
            except Exception:
                logger.exception(
                    "study_search: unexpected error building materials for content=%r",
                    content,
                )
                raise

        combined = questions if requested_type == "QUESTION" else materials if requested_type in {"MATERIAL", "VIDEO"} else questions + materials
        total = len(combined)
        total_pages = max(1, math.ceil(total / max(1, limit))) if total else 0
        start = (page - 1) * limit
        page_items = combined[start:start + limit]

        payload["results"] = {"questions": questions if requested_type in {"ALL", "QUESTION"} else [], "materials": materials if requested_type in {"ALL", "MATERIAL", "VIDEO"} else []}
        payload["pagination"] = {
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": total_pages,
        }

        if requested_type in {"ALL", "QUESTION", "MATERIAL", "VIDEO"}:
            if requested_type == "QUESTION":
                payload["results"]["questions"] = page_items
            elif requested_type in {"MATERIAL", "VIDEO"}:
                payload["results"]["materials"] = page_items
            else:
                payload["results"]["questions"] = questions[:limit]
                payload["results"]["materials"] = materials[:limit]

        return payload
