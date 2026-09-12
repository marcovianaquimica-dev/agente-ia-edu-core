"""PHASE 26 - Authorial Material Ingestion Engine: curriculum-v2 matcher.

Deterministic, AI-agnostic suggestion of curriculum-v2 identifiers
(discipline_code/area_code/content_code/subcontent_codes) for an ingested
document, from its title + section titles. NEVER creates a CatalogNode.
NEVER forces a match - below the confidence floor (or with no CONTENT match
at all) the result is TAXONOMY_GAP, which the review screen surfaces
honestly rather than guessing.

This is intentionally the ONLY thing standing between "raw extracted text"
and "a curriculum-v2 code" in this phase. Spec s7 explicitly allows AI to
take over this exact role later (classification/curriculum association) -
this module is that future provider's deterministic placeholder, reachable
through the SAME return shape so swapping it in is additive, not a rewrite.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import CatalogNode

MIN_CONFIDENCE = 0.34  # below this: TAXONOMY_GAP, never a forced guess


def _normalize(text: str) -> set[str]:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    stop = {"de", "da", "do", "das", "dos", "e", "a", "o", "em", "para", "com", "um", "uma"}
    return {t for t in tokens if t not in stop and len(t) > 1}


def _score(query_tokens: set[str], node_tokens: set[str]) -> float:
    if not query_tokens or not node_tokens:
        return 0.0
    overlap = query_tokens & node_tokens
    if not overlap:
        return 0.0
    return len(overlap) / len(node_tokens)  # how much of the NODE name is covered


@dataclass(frozen=True)
class CurriculumMatch:
    classification_state: str  # MAPPED | TAXONOMY_GAP
    discipline_code: str | None = None
    area_code: str | None = None
    content_code: str | None = None
    subcontent_codes: list[str] = field(default_factory=list)
    confidence: float = 0.0
    matched_text: str | None = None

    def as_dict(self) -> dict:
        return {
            "classification_state": self.classification_state,
            "discipline_code": self.discipline_code,
            "area_code": self.area_code,
            "content_code": self.content_code,
            "subcontent_codes": list(self.subcontent_codes),
            "confidence": round(self.confidence, 3),
            "matched_text": self.matched_text,
        }


class AuthorialCurriculumMatcher:
    """Deterministic text-overlap matcher against the EXISTING curriculum-v2
    catalog_nodes table. Reads only - never writes a node."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def match(self, candidate_texts: list[str]) -> CurriculumMatch:
        candidates = [t for t in candidate_texts if t and t.strip()]
        if not candidates:
            return CurriculumMatch("TAXONOMY_GAP")

        nodes = (await self._session.execute(
            select(CatalogNode.id, CatalogNode.code, CatalogNode.name,
                  CatalogNode.node_type, CatalogNode.parent_id)
            .where(CatalogNode.active.is_(True))
        )).all()
        by_id = {n.id: n for n in nodes}
        contents = [n for n in nodes if (n.node_type or "").upper() in ("CONTENT", "SUBCONTENT")]

        query_tokens: set[str] = set()
        for text in candidates:
            query_tokens |= _normalize(text)

        best = None  # (score, node)
        for node in contents:
            score = _score(query_tokens, _normalize(node.name))
            if best is None or score > best[0]:
                best = (score, node)

        if best is None or best[0] < MIN_CONFIDENCE:
            return CurriculumMatch("TAXONOMY_GAP", confidence=best[0] if best else 0.0)

        score, matched = best
        # walk up to the CONTENT node (if the best match was a SUBCONTENT,
        # its parent is the content) and collect discipline/area codes.
        content_node = matched
        if (matched.node_type or "").upper() == "SUBCONTENT" and matched.parent_id in by_id:
            content_node = by_id[matched.parent_id]

        area_node = by_id.get(content_node.parent_id)
        discipline_node = by_id.get(area_node.parent_id) if area_node else None

        # subcontents: other high-scoring SUBCONTENT children of this content
        subcontents = []
        for n in nodes:
            if (n.node_type or "").upper() == "SUBCONTENT" and n.parent_id == content_node.id:
                s = _score(query_tokens, _normalize(n.name))
                if s >= MIN_CONFIDENCE:
                    subcontents.append(n.code)

        return CurriculumMatch(
            "MAPPED",
            discipline_code=discipline_node.code if discipline_node else None,
            area_code=area_node.code if area_node else None,
            content_code=content_node.code,
            subcontent_codes=sorted(subcontents),
            confidence=score,
            matched_text=matched.name,
        )
