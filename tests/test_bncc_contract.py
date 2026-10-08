"""CEREBRO - Fase 4: o contrato de referencia BNCC.

A decisao normativa que este modulo fixa (spec 22.3): **``EM13CNT301``
isolado NAO e identidade eterna**. O mesmo codigo pode ter enunciado
diferente em versoes diferentes da BNCC, entao toda referencia normativa ou
auditavel carrega a tripla ``taxonomy_code + taxonomy_version + node_code``.

``knowledge_chunks.bncc_node_codes`` continua guardando so o codigo, porque
sua funcao e recuperacao. Quem audita nunca se contenta com isso.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.bncc_contract.v1 import (
    BNCC_TAXONOMY_CODE,
    BNCC_VERSION_EM_2018,
    CNT_AREA_CODE,
    CONTRACT_VERSION,
    RELATION_TYPES,
    BnccNodeRef,
    BnccReference,
)


class ConstantsTests(unittest.TestCase):
    def test_the_taxonomy_identity_is_the_approved_one(self):
        self.assertEqual(BNCC_TAXONOMY_CODE, "bncc")
        self.assertEqual(BNCC_VERSION_EM_2018, "EM-2018")

    def test_the_contract_is_versioned(self):
        self.assertEqual(CONTRACT_VERSION, "v1")

    def test_relation_types_are_only_primary_and_supporting(self):
        """PREREQUISITE fica fora: pre-requisito ja pertence ao grafo
        curricular (CatalogNodePrerequisite), e um tipo novo de relacao BNCC
        so entra com necessidade pedagogica concreta."""
        self.assertEqual(set(RELATION_TYPES), {"PRIMARY", "SUPPORTING"})

    def test_the_cnt_area_has_a_stable_code(self):
        self.assertEqual(CNT_AREA_CODE, "EM13CNT")


class BnccNodeRefTests(unittest.TestCase):
    def _ref(self, **overrides) -> BnccNodeRef:
        defaults = dict(
            taxonomy_code=BNCC_TAXONOMY_CODE,
            taxonomy_version=BNCC_VERSION_EM_2018,
            node_code="EM13CNT301",
        )
        defaults.update(overrides)
        return BnccNodeRef(**defaults)

    def test_the_urn_carries_all_three_parts(self):
        self.assertEqual(self._ref().as_urn(), "bncc:EM-2018:EM13CNT301")

    def test_the_same_code_in_two_versions_is_two_different_references(self):
        """O ponto inteiro do ajuste 4."""
        self.assertNotEqual(
            self._ref(taxonomy_version="EM-2018"),
            self._ref(taxonomy_version="EM-2026"),
        )
        self.assertNotEqual(
            self._ref(taxonomy_version="EM-2018").as_urn(),
            self._ref(taxonomy_version="EM-2026").as_urn(),
        )

    def test_the_reference_is_immutable(self):
        ref = self._ref()
        with self.assertRaises(Exception):
            ref.node_code = "EM13CNT302"  # type: ignore[misc]

    def test_it_round_trips_through_the_urn(self):
        ref = self._ref()
        self.assertEqual(BnccNodeRef.from_urn(ref.as_urn()), ref)

    def test_a_malformed_urn_is_refused_not_guessed(self):
        for bad in ("EM13CNT301", "bncc:EM13CNT301", "", "a:b:c:d"):
            with self.assertRaises(ValueError, msg=bad):
                BnccNodeRef.from_urn(bad)

    def test_a_reference_without_a_version_cannot_be_built(self):
        """Falha FECHADA: nao ha default de versao. Uma referencia sem versao
        nao e auditavel, e deixar passar seria pior que recusar."""
        with self.assertRaises(ValueError):
            BnccNodeRef(
                taxonomy_code="bncc", taxonomy_version="", node_code="EM13CNT301"
            )


class BnccReferenceTests(unittest.TestCase):
    """O que o Knowledge Pack ve em ``topic.bncc[]``."""

    def _reference(self, **overrides) -> BnccReference:
        defaults = dict(
            code="EM13CNT301",
            statement="Construir questoes, elaborar hipoteses, previsoes e estimativas.",
            competency_code="CNT-CE3",
            competency_statement="Analisar situacoes-problema e avaliar aplicacoes.",
            taxonomy_code=BNCC_TAXONOMY_CODE,
            taxonomy_version=BNCC_VERSION_EM_2018,
            relation_type="PRIMARY",
        )
        defaults.update(overrides)
        return BnccReference(**defaults)

    def test_it_carries_the_version_explicitly(self):
        self.assertEqual(self._reference().taxonomy_version, "EM-2018")

    def test_it_exposes_a_node_ref(self):
        self.assertEqual(self._reference().node_ref.as_urn(), "bncc:EM-2018:EM13CNT301")

    def test_it_never_exposes_a_database_identifier(self):
        """O Pack nao pode depender de detalhe de armazenamento (spec 22.8)."""
        fields = set(BnccReference.__dataclass_fields__)
        for leaked in ("id", "node_id", "taxonomy_id", "link_id", "taxonomy_node_id"):
            self.assertNotIn(leaked, fields)

    def test_an_unknown_relation_type_is_refused(self):
        with self.assertRaises(ValueError):
            self._reference(relation_type="PREREQUISITE")

    def test_it_is_immutable(self):
        with self.assertRaises(Exception):
            self._reference().code = "EM13CNT302"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
