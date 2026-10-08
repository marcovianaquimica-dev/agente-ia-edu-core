"""CEREBRO / Knowledge Engine - Fase 2: a politica de direitos, pura.

Sem sessao, sem I/O, sem banco. A politica e o que a Fase 2 existe para
fixar, entao ela nasce como funcao pura e testavel isoladamente - nao como
um `if` espalhado dentro de um service.

Dois eixos que nunca se colapsam num campo so:

- ``rights_class``    - o que podemos FAZER com a fonte;
- ``authority_level`` - quanto CONFIAMOS na fonte.

Nenhum dos dois se deriva do outro.
"""

from __future__ import annotations

import unittest
import uuid

from agente_ia_edu.services.knowledge_engine.rights import (
    AUTHORITY_LEVELS,
    NON_COMMERCIAL_EXCERPT_LIMIT,
    RIGHTS_CLASSES,
    SOURCE_KINDS,
    KnowledgeRightsViolation,
    max_excerpt_chars,
    may_expose_literal_text,
    validate_source_rights,
)


class VocabularyTests(unittest.TestCase):
    def test_rights_classes_are_exactly_the_five_the_schema_constrains(self):
        self.assertEqual(
            set(RIGHTS_CLASSES),
            {"COMMERCIAL_REFERENCE", "LICENSED", "OWN", "PUBLIC_DOMAIN", "OFFICIAL_PUBLIC"},
        )

    def test_authority_levels_are_the_documented_set(self):
        self.assertEqual(
            set(AUTHORITY_LEVELS),
            {"OFFICIAL", "ACADEMIC", "COMMERCIAL_TEXTBOOK", "OWN", "OTHER"},
        )

    def test_source_kinds_are_the_documented_set(self):
        self.assertEqual(
            set(SOURCE_KINDS),
            {"TEXTBOOK", "CURRICULUM_FRAMEWORK", "OWN_MATERIAL", "ARTICLE", "OTHER"},
        )

    def test_an_unknown_rights_class_is_refused(self):
        with self.assertRaises(KnowledgeRightsViolation) as caught:
            validate_source_rights(
                rights_class="FAIR_USE_MAYBE",
                authority_level="OWN",
                source_kind="TEXTBOOK",
            )
        self.assertIn("rights_class", str(caught.exception))

    def test_an_unknown_authority_level_is_refused(self):
        with self.assertRaises(KnowledgeRightsViolation) as caught:
            validate_source_rights(
                rights_class="OWN", authority_level="VERY_TRUSTWORTHY", source_kind="TEXTBOOK"
            )
        self.assertIn("authority_level", str(caught.exception))

    def test_an_unknown_source_kind_is_refused(self):
        with self.assertRaises(KnowledgeRightsViolation) as caught:
            validate_source_rights(
                rights_class="OWN", authority_level="OWN", source_kind="PODCAST"
            )
        self.assertIn("source_kind", str(caught.exception))


class CommercialSourceRuleTests(unittest.TestCase):
    """A trava central, aplicada ANTES do banco.

    O CheckConstraint continua sendo a ultima linha de defesa; esta validacao
    existe para que o chamador receba um motivo nomeado em vez de um
    IntegrityError cru.
    """

    def test_commercial_source_may_not_carry_an_educational_resource(self):
        with self.assertRaises(KnowledgeRightsViolation) as caught:
            validate_source_rights(
                rights_class="COMMERCIAL_REFERENCE",
                authority_level="COMMERCIAL_TEXTBOOK",
                source_kind="TEXTBOOK",
                educational_resource_id=uuid.uuid4(),
            )
        message = str(caught.exception)
        self.assertIn("COMMERCIAL_REFERENCE", message)
        self.assertIn("educational_resource_id", message)

    def test_commercial_source_without_a_resource_is_fine(self):
        validate_source_rights(
            rights_class="COMMERCIAL_REFERENCE",
            authority_level="COMMERCIAL_TEXTBOOK",
            source_kind="TEXTBOOK",
            educational_resource_id=None,
        )

    def test_the_rule_is_surgical_and_only_binds_commercial_sources(self):
        """A BNCC e OFFICIAL_PUBLIC e pode, sim, existir como recurso."""
        for rights_class in ("LICENSED", "OWN", "PUBLIC_DOMAIN", "OFFICIAL_PUBLIC"):
            validate_source_rights(
                rights_class=rights_class,
                authority_level="OFFICIAL",
                source_kind="CURRICULUM_FRAMEWORK",
                educational_resource_id=uuid.uuid4(),
            )


class IndependentAxesTests(unittest.TestCase):
    def test_the_same_rights_class_accepts_different_authority_levels(self):
        for authority_level in AUTHORITY_LEVELS:
            validate_source_rights(
                rights_class="COMMERCIAL_REFERENCE",
                authority_level=authority_level,
                source_kind="TEXTBOOK",
            )

    def test_own_material_may_have_lower_authority_than_a_commercial_book(self):
        """Nao ha regra que force autoridade a seguir direitos."""
        validate_source_rights(
            rights_class="OWN", authority_level="OTHER", source_kind="OWN_MATERIAL"
        )
        validate_source_rights(
            rights_class="COMMERCIAL_REFERENCE",
            authority_level="ACADEMIC",
            source_kind="ARTICLE",
        )


class LiteralTextExposureTests(unittest.TestCase):
    """Nao usado na Fase 2 - nao existe chunk nem Pack ainda.

    Esta aqui porque e a definicao da politica, e a politica e o que esta fase
    fixa. Fica coberta por teste e ociosa ate a Fase 8, quando o validador do
    Knowledge Pack passa a consulta-la.
    """

    def test_commercial_source_never_exposes_literal_text(self):
        self.assertFalse(may_expose_literal_text("COMMERCIAL_REFERENCE"))
        self.assertIsNone(max_excerpt_chars("COMMERCIAL_REFERENCE"))

    def test_every_other_class_may_be_quoted_within_the_limit(self):
        for rights_class in ("LICENSED", "OWN", "PUBLIC_DOMAIN", "OFFICIAL_PUBLIC"):
            self.assertTrue(may_expose_literal_text(rights_class))
            self.assertEqual(max_excerpt_chars(rights_class), NON_COMMERCIAL_EXCERPT_LIMIT)

    def test_the_limit_is_the_three_hundred_chars_the_spec_fixes(self):
        self.assertEqual(NON_COMMERCIAL_EXCERPT_LIMIT, 300)

    def test_an_unknown_class_fails_closed(self):
        """Classe desconhecida nunca pode virar permissao por omissao."""
        self.assertFalse(may_expose_literal_text("WHATEVER"))
        self.assertIsNone(max_excerpt_chars("WHATEVER"))


if __name__ == "__main__":
    unittest.main()
