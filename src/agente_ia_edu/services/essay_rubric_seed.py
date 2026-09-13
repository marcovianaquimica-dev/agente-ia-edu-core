"""Load a reviewed rubric file into the database, idempotently.

Idempotence matters because the seed runs on every environment and every fresh
database, and because a rubric that is already seeded must never be silently
rewritten - a correction persisted under ENEM_2025 has to keep meaning exactly
what it meant when it was produced (spec §19). Re-seeding an existing version is
therefore a no-op, not an update: ``seed()`` treats an existing rubric version
as authoritative and never rewrites it.

That authority is conditional on completeness. ``seed()`` requires an existing
rubric version to already have its children (competencies, levels, signals,
scoring rules) written; if it finds a header row for the version without them,
it raises ``IncompleteRubricSeedError`` instead of silently returning the bare
row or silently re-seeding under it. A half-seeded rubric that reports itself
complete would let every downstream reader (in particular the correction
validator) believe an empty rubric is a valid one, which is worse than failing
loudly.

Superseding a rubric flips its status and stamps the time; it never deletes a
level, so historical corrections stay readable.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import (
    EssayRubric,
    EssayRubricCompetency,
    EssayRubricLevel,
    EssayRubricScoringRule,
    EssayRubricSignal,
)
from agente_ia_edu.rubrics.loader import RubricFile


class IncompleteRubricSeedError(RuntimeError):
    """Raised when a rubric version's header row exists without its children.

    This should not arise from the seeder's own normal use: a failure partway
    through ``seed()`` leaves an uncommitted transaction, which the caller
    rolls back in full. It guards against the row arriving some other way -
    a manual insert, a future migration, a different caller convention, a bug
    elsewhere - since a half-seeded rubric that ``seed()`` treated as already
    complete would silently expose zero competencies to every reader.
    """


class EssayRubricSeeder:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def seed(self, rubric_file: RubricFile, *, activate: bool = True) -> EssayRubric:
        """Insert ``rubric_file`` if its version is not present yet.

        Returns the stored rubric either way. An already-seeded version is
        treated as authoritative and returned untouched - it is never
        rewritten, even if the file that produced it has since changed.

        That authority requires the existing row to be complete. If a rubric
        header row exists for this version but its competencies were never
        written (for example a bare row inserted outside this seeder), this
        raises ``IncompleteRubricSeedError`` rather than returning the partial
        row or re-seeding children under it - re-seeding risks writing new
        children under a header row whose own columns may be stale or wrong,
        and deciding what to do about a corrupted seed is a human call, not
        this method's.
        """
        existing = await self.session.scalar(
            select(EssayRubric).where(
                EssayRubric.rubric_version == rubric_file.rubric_version
            )
        )
        if existing is not None:
            existing_competency_count = await self.session.scalar(
                select(func.count())
                .select_from(EssayRubricCompetency)
                .where(EssayRubricCompetency.rubric_id == existing.id)
            )
            expected_competency_count = len(rubric_file.competencies)
            if existing_competency_count != expected_competency_count:
                raise IncompleteRubricSeedError(
                    f"Rubric version {rubric_file.rubric_version!r} exists "
                    f"(id={existing.id}) but is incomplete: found "
                    f"{existing_competency_count} competencies, but the "
                    f"rubric file declares {expected_competency_count}. "
                    "Refusing to silently return this partial rubric or "
                    "re-seed children under it - this indicates the row was "
                    "written outside a full seed() transaction and needs "
                    "manual investigation."
                )
            return existing

        rubric = EssayRubric(
            rubric_version=rubric_file.rubric_version,
            label=rubric_file.label,
            effective_year=rubric_file.effective_year,
            max_total_points=rubric_file.max_total_points,
            official_source_title=rubric_file.official_source_title,
            official_source_url=rubric_file.official_source_url,
            official_source_sha256=rubric_file.official_source_sha256,
            status="ACTIVE" if activate else "DRAFT",
            published_at=datetime.now(timezone.utc) if activate else None,
        )
        self.session.add(rubric)
        await self.session.flush()

        for entry in rubric_file.competencies:
            competency = EssayRubricCompetency(
                rubric_id=rubric.id,
                code=entry.code,
                ordinal=entry.ordinal,
                official_title=entry.official_title,
                source_page=entry.source_page,
            )
            self.session.add(competency)
            await self.session.flush()

            for level in entry.levels:
                self.session.add(
                    EssayRubricLevel(
                        competency_id=competency.id,
                        points=level.points,
                        descriptor=level.descriptor,
                        source_page=level.source_page,
                        provenance=level.provenance,
                    )
                )

            for signal in entry.signals:
                self.session.add(
                    EssayRubricSignal(
                        competency_id=competency.id,
                        key=signal.key,
                        label=signal.label,
                        description=signal.description,
                        provenance=signal.provenance,
                        source_ref=signal.source_ref,
                        rationale=signal.rationale,
                        active=True,
                    )
                )

        for rule in rubric_file.scoring_rules:
            self.session.add(
                EssayRubricScoringRule(
                    rubric_id=rubric.id,
                    key=rule.key,
                    label=rule.label,
                    description=rule.description,
                    effect=rule.effect,
                    competency_code=rule.competency_code,
                    max_points=rule.max_points,
                    source_page=rule.source_page,
                    provenance=rule.provenance,
                )
            )

        await self.session.flush()
        return rubric

    async def supersede(self, rubric_version: str) -> EssayRubric:
        """Mark a rubric superseded. Nothing is deleted."""
        rubric = await self.session.scalar(
            select(EssayRubric).where(EssayRubric.rubric_version == rubric_version)
        )
        if rubric is None:
            raise ValueError(f"Unknown rubric version {rubric_version!r}")
        rubric.status = "SUPERSEDED"
        rubric.superseded_at = datetime.now(timezone.utc)
        await self.session.flush()
        return rubric


__all__ = ["EssayRubricSeeder", "IncompleteRubricSeedError"]
