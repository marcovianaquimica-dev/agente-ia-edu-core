import unittest
import uuid

from pydantic import ValidationError

from agente_ia_edu.essay_engine_contract.v5 import (
    CONTRACT_VERSION,
    STRUCTURED_FEEDBACK_FIELDS,
    EssayEngineOutput,
)

STRUCTURED = {
    "c2_tipologia_textual": "O texto é dissertativo-argumentativo.",
    "c2_tema": "Desenvolve o tema específico proposto.",
    "c2_repertorio_sociocultural": "Cita a Constituição de 1988 de forma produtiva.",
    "c2_orientacao_melhoria": "Articule o repertório ao argumento do 2º parágrafo.",
    "c3_projeto_argumentativo": "A tese é retomada na conclusão.",
    "c3_fatos_informacoes_opinioes": "Usa dados do IBGE citados no 2º parágrafo.",
    "c3_autoria": "Há ponto de vista próprio no 3º parágrafo.",
    "c3_orientacao_melhoria": "Desenvolva o segundo argumento com um exemplo concreto.",
}


def minimal_payload(**overrides):
    payload = {
        "identification": {
            "essay_id": str(uuid.uuid4()),
            "essay_version_id": str(uuid.uuid4()),
            "rubric_version": "ENEM_2025",
            "model_version": "fake-model-1",
            "prompt_version": "essay_correction_v15",
            "engine_version": "r3_correction_engine_v2",
            "contract_version": CONTRACT_VERSION,
            "anchor_mode": "IMAGE_REGION",
        },
        "scores": {
            "per_competency": {
                code: {"points": 160, "confidence": 0.8}
                for code in ("C1", "C2", "C3", "C4", "C5")
            },
            "total": 800,
        },
        "rationales": [
            {
                "competency_code": c, "summary": "resumo",
                "strengths": "pontos fortes", "growth_area": "onde avançar",
                "signal_keys": [],
            }
            for c in ("C1", "C4", "C5")
        ],
        "annotations": [
            {
                "letter": "A",
                "competency_code": "C1",
                "kind": "MELHORIA",
                "evidence_kind": "LOCALIZED",
                "anchor": {
                    "type": "IMAGE_REGION", "page": 1, "line": 2, "total_lines": 30,
                    "read_text": "Texto",
                },
                "short_comment": "curto",
                "long_comment": "longo",
            }
        ],
        "rewrites": [],
        "feedback": {"strengths": [], "improvements": [], "next_essay_strategy": "..."},
        "intervention": {
            "agente": "Ministério da Educação", "acao": "ampliar formação",
            "meio_modo": "por programa federal", "finalidade": "reduzir a evasão",
            "detalhamento": "com metas anuais", "respeita_direitos_humanos": True,
        },
        "alerts": [],
        "intro_message": "Olá! Vamos ver como foi sua redação.",
        "closing_message": "Continue praticando, você está no caminho certo.",
        **STRUCTURED,
    }
    payload.update(overrides)
    return payload


class EngineContractV5Tests(unittest.TestCase):
    def test_contract_version_is_v5(self):
        self.assertEqual(CONTRACT_VERSION, "essay_engine_output_v5")

    def test_rejects_the_v4_contract_version(self):
        payload = minimal_payload()
        payload["identification"]["contract_version"] = "essay_engine_output_v4"
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)

    def test_accepts_a_minimal_avaliativo_payload(self):
        EssayEngineOutput.model_validate(minimal_payload())

    def test_structured_feedback_fields_lists_the_eight_names_in_order(self):
        self.assertEqual(STRUCTURED_FEEDBACK_FIELDS, (
            "c2_tipologia_textual", "c2_tema", "c2_repertorio_sociocultural",
            "c2_orientacao_melhoria", "c3_projeto_argumentativo",
            "c3_fatos_informacoes_opinioes", "c3_autoria", "c3_orientacao_melhoria",
        ))

    def test_each_structured_field_is_required_when_scored(self):
        for field in STRUCTURED_FEEDBACK_FIELDS:
            with self.subTest(field=field):
                payload = minimal_payload()
                del payload[field]
                with self.assertRaises(ValidationError):
                    EssayEngineOutput.model_validate(payload)

    def test_each_structured_field_rejects_an_empty_string_when_scored(self):
        for field in STRUCTURED_FEEDBACK_FIELDS:
            with self.subTest(field=field):
                payload = minimal_payload(**{field: "   "})
                with self.assertRaises(ValidationError):
                    EssayEngineOutput.model_validate(payload)

    def test_structured_fields_are_optional_in_formativo(self):
        """FORMATIVO produces no grade at all - the same conditional shape
        `scores` already has. See the spec's §3 note."""
        payload = minimal_payload(scores=None)
        for field in STRUCTURED_FEEDBACK_FIELDS:
            del payload[field]
        output = EssayEngineOutput.model_validate(payload)
        self.assertIsNone(output.c2_tema)

    def test_rejects_a_c2_rationale_when_scored(self):
        payload = minimal_payload()
        payload["rationales"] = payload["rationales"] + [{
            "competency_code": "C2", "summary": "resumo",
            "strengths": "forças", "growth_area": "avançar", "signal_keys": [],
        }]
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)

    def test_rejects_a_c3_rationale_when_scored(self):
        payload = minimal_payload()
        payload["rationales"] = payload["rationales"] + [{
            "competency_code": "C3", "summary": "resumo",
            "strengths": "forças", "growth_area": "avançar", "signal_keys": [],
        }]
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)

    def test_still_accepts_rationales_for_c1_c4_c5(self):
        output = EssayEngineOutput.model_validate(minimal_payload())
        self.assertEqual(
            [r.competency_code for r in output.rationales], ["C1", "C4", "C5"]
        )

    def test_still_accepts_every_alert_code_v4_accepted(self):
        for code in (
            "FUGA_AO_TEMA", "TANGENCIAMENTO_AO_TEMA", "TIPO_TEXTUAL",
            "TIPO_TEXTUAL_PREDOMINANTE", "TEXTO_INSUFICIENTE",
            "ANULACAO_PROPOSITAL", "PARTE_DESCONECTADA_DO_TEMA",
            "IDENTIFICACAO_INDEVIDA", "LINGUA_ESTRANGEIRA", "TEXTO_ILEGIVEL",
            "OCR_DUVIDOSO", "POSSIVEL_DUPLICIDADE",
        ):
            with self.subTest(code=code):
                EssayEngineOutput.model_validate(
                    minimal_payload(alerts=[{"code": code, "detail": None}])
                )

    def test_still_accepts_the_seven_mechanical_categories(self):
        for category in (
            "ORTOGRAFIA", "ACENTUACAO", "CRASE", "PORQUES",
            "CONCORDANCIA", "REGENCIA", "PONTUACAO",
        ):
            with self.subTest(category=category):
                EssayEngineOutput.model_validate(minimal_payload(mechanical_review=[{
                    "category": category, "excerpt": "trecho",
                    "suggested_form": "forma", "rule_explanation": "regra",
                }]))

    def test_rejects_a_line_number_beyond_total_lines(self):
        payload = minimal_payload()
        payload["annotations"][0]["anchor"] = {
            "type": "IMAGE_REGION", "page": 1, "line": 31, "total_lines": 30,
            "read_text": "Texto",
        }
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)

    def test_rejects_an_anchor_type_that_disagrees_with_the_declared_mode(self):
        payload = minimal_payload()
        payload["identification"]["anchor_mode"] = "TEXT_OFFSET"
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)

    def test_total_must_be_the_sum_of_the_five_competencies(self):
        payload = minimal_payload()
        payload["scores"]["total"] = 999
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
