import unittest
import uuid

from agente_ia_edu.db.models import EssayPrompt, EssaySubmission
from agente_ia_edu.services.essay_correction import _effective_essay_statement


def _prompt(statement="Disserte sobre o tema oficial."):
    return EssayPrompt(
        id=uuid.uuid4(), school_id=uuid.uuid4(), title="Tema", statement=statement,
        year=2026, created_by_external_identity="teacher:p",
    )


def _submission(*, student_declared_theme=None):
    return EssaySubmission(
        id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=uuid.uuid4(),
        prompt_assignment_id=uuid.uuid4(), student_id=uuid.uuid4(),
        mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
        canonical_text="Redacao.", normalized_text_hash="a" * 64,
        student_declared_theme=student_declared_theme,
    )


class EffectiveEssayStatementTests(unittest.TestCase):
    def test_returns_prompt_statement_when_no_declared_theme(self):
        prompt = _prompt("Disserte sobre o tema oficial.")
        submission = _submission(student_declared_theme=None)

        self.assertEqual(_effective_essay_statement(submission, prompt), "Disserte sobre o tema oficial.")

    def test_builds_custom_statement_from_declared_theme(self):
        prompt = _prompt("Disserte sobre o tema oficial.")
        submission = _submission(student_declared_theme="O impacto das redes sociais na juventude")

        result = _effective_essay_statement(submission, prompt)

        self.assertIn("O impacto das redes sociais na juventude", result)
        self.assertNotIn("Disserte sobre o tema oficial.", result)
        self.assertIn("tema livre", result.lower())
        self.assertIn("fuga ao tema", result.lower())


if __name__ == "__main__":
    unittest.main()
