# tests/test_r0_phase1_gate.py
"""Proves the phase's central claim: everything here is additive.

Each earlier task tested what it created. Nothing tested that the rest of the
system is unaffected - and "purely additive" is exactly the kind of claim that
is believed rather than checked.
"""

import pathlib
import re
import unittest

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School, UserSchoolLink

R0_TABLES = {
    "persons",
    "users",
    "academic_years",
    "school_units",
    "segments",
    "grade_levels",
    "classes",
    "students",
    "student_enrollments",
    "enrollment_transitions",
    "school_settings",
    "school_identity_versions",
}

FORBIDDEN_COLUMN_FRAGMENTS = ("password", "token", "secret", "credential", "senha")

# Named exceptions to the credential-column scan below. Every hit the scan finds
# must be listed here with a reason, or the test fails. This is a record, not a
# blind spot: a new column that matches a forbidden fragment breaks the test and
# forces someone to explain it here, one way or the other.
#
# These four are not the same kind of thing:
#   - pedagogical_classifications.{input,output,total}_tokens are LLM usage
#     counters (Integer, nullable) written from provider responses - they only
#     match the "token" fragment because of the plural "tokens". Not secrets.
#   - user_invitations.token is a real credential (32 random bytes, base64url
#     encoded via secrets.token_urlsafe, String(255), nullable=False, unique,
#     compared in plaintext by validate_token in
#     src/agente_ia_edu/services/invitation.py). It predates R0 entirely and
#     is a known, accepted gap - not something declared fine. Fixing it means
#     storing a hash and looking up by hash, a behaviour change with consumer
#     migration that is out of scope for this phase.
CREDENTIAL_COLUMN_EXCEPTIONS = {
    ("pedagogical_classifications", "input_tokens"): (
        "LLM usage counter (prompt token count), not a secret - false positive "
        "of the 'token' fragment."
    ),
    ("pedagogical_classifications", "output_tokens"): (
        "LLM usage counter (completion token count), not a secret - false "
        "positive of the 'token' fragment."
    ),
    ("pedagogical_classifications", "total_tokens"): (
        "LLM usage counter (total token count), not a secret - false positive "
        "of the 'token' fragment."
    ),
    ("user_invitations", "token"): (
        "KNOWN GAP, predates R0: invitation-activation secret stored and "
        "compared in plaintext (see validate_token in "
        "src/agente_ia_edu/services/invitation.py). Not a false positive and "
        "not fine - just out of scope to fix here, since fixing it means "
        "switching to a hashed lookup with consumer migration."
    ),
    ("essay_submission_pages", "ocr_tokens"): (
        "OCR transcription output (R2): a JSONB list of "
        "{text, confidence, start, end} per recognized word/token in a "
        "submitted essay page image - not a secret, not an auth token. "
        "False positive of the 'token' fragment, same class as the "
        "pedagogical_classifications.*_tokens entries above."
    ),
}


class TestPhase1IsAdditive(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    def test_every_r0_table_exists(self):
        declared = set(Base.metadata.tables)
        missing = R0_TABLES - declared
        self.assertEqual(missing, set(), f"tabelas de R0 ausentes: {sorted(missing)}")

    def test_no_table_anywhere_has_a_credential_column(self):
        """Not just `users`: the rule is that this system stores no credential
        at all, so the guard covers every table in the schema.

        The scan itself stays broad and generic - it must never be narrowed to
        dodge a specific hit. What it finds is only allowed through via the
        named, reasoned exceptions in CREDENTIAL_COLUMN_EXCEPTIONS above."""
        offenders = []
        for table_name, table in Base.metadata.tables.items():
            for column in table.columns:
                # Use column.key (not column.name) so this matches assertion 3
                # below, which checks membership against table.columns and
                # therefore matches on key too - keeping both assertions
                # keyed the same way so no mismatch can hide a real column.
                lowered = column.key.lower()
                if any(fragment in lowered for fragment in FORBIDDEN_COLUMN_FRAGMENTS):
                    offenders.append((table_name, column.key))

        # 1. Every hit must be an explicitly named, reasoned exception.
        unexplained = [hit for hit in offenders if hit not in CREDENTIAL_COLUMN_EXCEPTIONS]
        self.assertEqual(
            unexplained,
            [],
            f"colunas de credencial sem excecao registrada: {unexplained}",
        )

        # 2. No R0 table may appear in the exception list, period. This keeps
        # the phase's claim ("R0 introduces no credential column") absolute
        # and independent of whatever the pre-existing schema carries - it is
        # not satisfied merely by assertion 1 passing, because assertion 1
        # would also pass if someone "explained away" a credential column on
        # an R0 table by adding it to the exception list. Built from the same
        # R0_TABLES the first test uses, so the two definitions cannot drift.
        r0_exceptions = [
            (table_name, column_name)
            for table_name, column_name in CREDENTIAL_COLUMN_EXCEPTIONS
            if table_name in R0_TABLES
        ]
        self.assertEqual(
            r0_exceptions,
            [],
            f"tabela de R0 na lista de excecoes de credencial: {r0_exceptions}",
        )

        # 3. Every exception must point at a column that actually exists,
        # so the list cannot rot into standing permission for a column that
        # was removed years earlier.
        declared_tables = Base.metadata.tables
        stale_exceptions = [
            (table_name, column_name)
            for table_name, column_name in CREDENTIAL_COLUMN_EXCEPTIONS
            if table_name not in declared_tables
            or column_name not in declared_tables[table_name].columns
        ]
        self.assertEqual(
            stale_exceptions,
            [],
            f"excecao de credencial aponta para coluna inexistente: {stale_exceptions}",
        )

    def test_the_new_link_columns_are_all_nullable(self):
        """The bridge only works if every existing row stays valid without it."""
        columns = {c.name: c for c in inspect(UserSchoolLink).columns}
        for name in (
            "user_id",
            "school_unit_id",
            "segment_id",
            "grade_level_id",
            "class_id",
        ):
            with self.subTest(column=name):
                self.assertIn(name, columns)
                self.assertTrue(columns[name].nullable)

    def test_the_migration_declares_those_columns_nullable_too(self):
        """The test above reads the MODEL. The claim of this phase is about what
        the migration does to a production database, and a NOT NULL present only
        in the DDL would pass a model-only check while breaking every existing
        row on deploy.

        Deliberately a textual assertion and nothing more: it reads the one
        statement that matters - ``sa.Column(column, sa.Uuid(), nullable=True)``
        - without knowing anything else about how the migration is written."""
        migration = (
            pathlib.Path(__file__).resolve().parent.parent
            / "migrations"
            / "versions"
            / "043_user_school_link_entities.py"
        )
        source = migration.read_text(encoding="utf-8")

        self.assertIn(
            'op.add_column("user_school_links", sa.Column(column, sa.Uuid(), nullable=True))',
            source,
            "043 must add the bridge columns as NULLABLE",
        )
        self.assertNotIn("nullable=False", source)

        declared = set(re.findall(r'\("(\w+)", "\w+", "fk_user_school_links_\w+"\)', source))
        self.assertEqual(
            declared,
            {"user_id", "school_unit_id", "segment_id", "grade_level_id", "class_id"},
            "the five bridge columns the model declares must be the five 043 adds",
        )

    async def test_a_school_still_works_with_no_r0_rows_at_all(self):
        """Every school in production is in this state today: it exists, it has
        links, and it has nothing from R0. That must keep working."""
        async with self.session_factory() as session:
            school = School(code="ESCOLA_LEGADA", name="Escola Legada")
            session.add(school)
            await session.flush()

            link = UserSchoolLink(
                external_user_id="prof_mendes",
                school_id=school.id,
                role="TEACHER",
                scope_type="CLASSROOM",
                scope_external_id="TURMA_3A",
            )
            session.add(link)
            await session.flush()

            self.assertIsNone(link.class_id)
            self.assertEqual(link.scope_external_id, "TURMA_3A")
