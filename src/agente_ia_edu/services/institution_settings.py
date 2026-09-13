"""R0 - the only sanctioned way to read and change a school's configuration.

Every write here records who did it, when, and from what value to which, because
"who put this school in evaluative mode?" has to be answerable (spec §5.3). A
direct UPDATE bypasses that, so nothing outside this service writes to
``school_settings`` or ``school_identity_versions``.

Publishing a visual identity never edits a published one. February's devolutiva
keeps February's mark; editing in place would rewrite a document a family
already received (spec §5.2).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import AdminAuditLog, SchoolIdentityVersion, SchoolSetting
from agente_ia_edu.db.models.institution import CORRECTION_MODES, VALIDATION_MODES

_POLICY_FIELDS = ("validation_default", "validation_threshold_points")


class IdentityVersionImmutableError(RuntimeError):
    """A published visual identity cannot be edited; publish a new version."""


class InstitutionSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_settings(self, school_id: uuid.UUID) -> SchoolSetting:
        """Return the school's settings, creating a formative default if absent.

        Formative is the default because it produces no score at all, which is
        the configuration that assumes the least about what the school intends.
        """
        existing = await self.session.scalar(
            select(SchoolSetting).where(SchoolSetting.school_id == school_id)
        )
        if existing is not None:
            return existing

        created = SchoolSetting(school_id=school_id, correction_mode="FORMATIVO")
        self.session.add(created)
        await self.session.flush()
        return created

    async def configure(
        self,
        school_id: uuid.UUID,
        *,
        performed_by_external_id: str,
        **changes: Any,
    ) -> SchoolSetting:
        """Apply ``changes`` and record who made them.

        Raises ``ValueError`` when the resulting configuration is incoherent, so
        the caller learns why rather than meeting a database constraint error.
        """
        settings = await self.get_settings(school_id)

        # Return early if no changes requested, without writing an audit row.
        if not changes:
            return settings

        unknown = set(changes) - {
            "correction_mode",
            "validation_default",
            "validation_teacher_can_disable",
            "validation_threshold_points",
        }
        if unknown:
            raise ValueError(f"Unknown setting(s): {sorted(unknown)}")

        mode = changes.get("correction_mode", settings.correction_mode)
        if mode not in CORRECTION_MODES:
            raise ValueError(f"Unknown correction_mode {mode!r}")

        default = changes.get("validation_default", settings.validation_default)
        if default is not None and default not in VALIDATION_MODES:
            raise ValueError(f"Unknown validation_default {default!r}")

        threshold = changes.get("validation_threshold_points", settings.validation_threshold_points)
        if threshold is not None and not isinstance(threshold, int):
            raise ValueError(f"validation_threshold_points must be an integer, got {type(threshold).__name__!r}")
        if threshold is not None and not (0 <= threshold <= 1000):
            raise ValueError(f"validation_threshold_points must be between 0 and 1000 inclusive, got {threshold!r}")

        if mode == "FORMATIVO":
            for field in _POLICY_FIELDS:
                value = changes.get(field, getattr(settings, field))
                if value is not None:
                    raise ValueError(
                        f"{field} has no meaning in FORMATIVO mode, where no score "
                        f"exists to validate; got {value!r}"
                    )

        before = {field: getattr(settings, field) for field in changes}
        for field, value in changes.items():
            setattr(settings, field, value)

        self.session.add(AdminAuditLog(
            school_id=school_id,
            performed_by_external_id=performed_by_external_id,
            action="SCHOOL_SETTINGS_UPDATED",
            entity_type="SCHOOL_SETTINGS",
            entity_id=str(settings.id),
            metadata_={"before": _stringify(before), "after": _stringify(changes)},
        ))
        await self.session.flush()
        return settings

    async def publish_identity(
        self,
        school_id: uuid.UUID,
        *,
        performed_by_external_id: str,
        display_name: str,
        logo_asset_uri: str | None = None,
        primary_color: str | None = None,
        secondary_color: str | None = None,
    ) -> SchoolIdentityVersion:
        """Publish a new visual identity and point the school at it."""
        highest = await self.session.scalar(
            select(func.max(SchoolIdentityVersion.version)).where(
                SchoolIdentityVersion.school_id == school_id
            )
        )
        version = SchoolIdentityVersion(
            school_id=school_id,
            version=(highest or 0) + 1,
            display_name=display_name,
            logo_asset_uri=logo_asset_uri,
            primary_color=primary_color,
            secondary_color=secondary_color,
            published_at=datetime.now(timezone.utc),
        )
        self.session.add(version)
        await self.session.flush()

        settings = await self.get_settings(school_id)
        settings.current_identity_version_id = version.id

        self.session.add(AdminAuditLog(
            school_id=school_id,
            performed_by_external_id=performed_by_external_id,
            action="SCHOOL_IDENTITY_PUBLISHED",
            entity_type="SCHOOL_IDENTITY_VERSION",
            entity_id=str(version.id),
            metadata_={"version": version.version, "display_name": display_name},
        ))
        await self.session.flush()
        return version

    async def amend_identity(self, identity_version_id: uuid.UUID, **_: Any) -> None:
        """Always raises. A published identity is immutable by design.

        This method exists so the refusal is explicit and discoverable, rather
        than a rule someone has to remember not to break.
        """
        raise IdentityVersionImmutableError(
            f"Identity version {identity_version_id} is published and cannot be edited. "
            "Publish a new version instead - a devolutiva already delivered must keep "
            "the identity it was delivered with."
        )


def _stringify(values: dict[str, Any]) -> dict[str, str | None]:
    return {key: (None if value is None else str(value)) for key, value in values.items()}


__all__ = ["IdentityVersionImmutableError", "InstitutionSettingsService"]
