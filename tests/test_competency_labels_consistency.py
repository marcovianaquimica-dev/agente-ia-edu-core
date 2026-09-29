"""Os rótulos curtos das competências estão duplicados em quatro arquivos
(dois JS, dois Python), sem registro central. Este teste é o registro: ele
falha se um dos quatro sair de sincronia com os outros, e trava o texto novo
de C2 e C3 pedido na leva de feedback estruturado (spec §2).

O dicionário COMPETENCY_DESCRIPTIONS de web/essay-report.js NÃO entra nessa
sincronia: é a citação verbatim da Matriz de Referência do ENEM e continua
exatamente como estava - ver as "Decisões de implementação" do plano."""

import pathlib
import re
import unittest

from agente_ia_edu.services.essay_pdf_export import _COMPETENCY_LABELS as PDF_LABELS
from agente_ia_edu.services.essay_teacher_dashboard import (
    _COMPETENCY_LABELS as DASHBOARD_LABELS,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "agente_ia_edu" / "web"

EXPECTED_LABELS = {
    "C1": "Domínio da norma padrão",
    "C2": "Tipologia, tema e repertório",
    "C3": "Projeto argumentativo e autoria",
    "C4": "Coesão textual",
    "C5": "Proposta de intervenção",
}


def _js_labels(filename: str, const_name: str) -> dict[str, str]:
    source = (WEB / filename).read_text(encoding="utf-8")
    match = re.search(rf"{const_name}\s*=\s*\{{(.*?)\}};", source, re.S)
    assert match, f"{const_name} não encontrado em {filename}"
    return dict(re.findall(r"(C[1-5]):\s*'([^']*)'", match.group(1)))


class CompetencyLabelsTests(unittest.TestCase):
    def test_essay_report_js_labels(self):
        self.assertEqual(
            _js_labels("essay-report.js", "COMPETENCY_LABELS"), EXPECTED_LABELS
        )

    def test_essay_evolution_js_labels(self):
        self.assertEqual(
            _js_labels("essay-evolution.js", "COMPETENCY_LABELS"), EXPECTED_LABELS
        )

    def test_pdf_export_labels(self):
        self.assertEqual(PDF_LABELS, EXPECTED_LABELS)

    def test_teacher_dashboard_labels(self):
        self.assertEqual(DASHBOARD_LABELS, EXPECTED_LABELS)

    def test_c1_c4_c5_were_not_renamed(self):
        """Só C2 e C3 mudam nesta leva (spec §2)."""
        self.assertEqual(EXPECTED_LABELS["C1"], "Domínio da norma padrão")
        self.assertEqual(EXPECTED_LABELS["C4"], "Coesão textual")
        self.assertEqual(EXPECTED_LABELS["C5"], "Proposta de intervenção")

    def test_official_matriz_descriptions_are_untouched(self):
        """COMPETENCY_DESCRIPTIONS cita a Matriz de Referência do ENEM
        verbatim. Renomear a competência na interface não reescreve a citação
        oficial - ver as Decisões de implementação do plano."""
        source = (WEB / "essay-report.js").read_text(encoding="utf-8")
        self.assertIn(
            "Compreensão do tema e aplicação de áreas do conhecimento na "
            "estrutura dissertativo-argumentativa.",
            source,
        )
        self.assertIn(
            "Seleção e organização de argumentos em defesa de um ponto de vista.",
            source,
        )


if __name__ == "__main__":
    unittest.main()
