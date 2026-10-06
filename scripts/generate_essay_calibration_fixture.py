"""Gera a fixture PUBLICA e SINTETICA do benchmark de calibracao -
`tests/fixtures/essay_calibration_benchmark_v1.json`.

Este arquivo deliberadamente NAO contem nenhum texto de redacao real nem
nome de aluno real. Os dados reais de calibracao (30 redacoes reais de
alunos, usadas nos benchmarks completos deste projeto) vivem fora deste
repositorio, num armazenamento privado e protegido (ver
ESSAY_CALIBRATION_PRIVATE_DIR, usado por scripts/essay_calibration_benchmark.py
e scripts/essay_calibration_clean_text_experiment.py) - nunca neste arquivo
versionado.

Historico: este script originalmente inlineava os 30 textos reais (ver git
log se precisar do contexto). Isso foi revertido em 2026-10-08, depois que
uma tentativa de push revelou que pseudonimizar apenas os NOMES nao bastava -
os textos integrais das redacoes tambem precisavam ficar fora do historico
publicavel. A fixture sintetica abaixo serve exclusivamente para os testes
automatizados que validam a FORMA do contrato de dados
(tests/test_essay_calibration_fixture.py) - nenhuma correspondencia com
nenhum aluno real, nenhuma nota aqui tem significado pedagogico.
"""
from __future__ import annotations

import json
from pathlib import Path

# (tema_sintetico, texto_corpo_sintetico) - nenhuma correspondencia com
# nenhum aluno ou redacao real. Textos deliberadamente curtos e sobre temas
# diferentes do corpus real (uso de tecnologia, educacao financeira, etc.)
# para nunca serem confundidos com dados reais.
_SYNTHETIC_BODIES: list[str] = [
    "O uso excessivo de telas por criancas e adolescentes tem preocupado "
    "educadores e familias. E preciso equilibrar o acesso a tecnologia com "
    "atividades fisicas e sociais, para garantir um desenvolvimento saudavel.",
    "A educacao financeira nas escolas publicas ainda e insuficiente. "
    "Muitos jovens chegam a idade adulta sem saber administrar um "
    "orcamento basico, o que agrava problemas de endividamento no pais.",
    "A mobilidade urbana nas grandes cidades brasileiras enfrenta desafios "
    "historicos. O transporte publico de qualidade poderia reduzir o "
    "transito e a poluicao, mas exige investimento continuo do poder publico.",
    "O descarte inadequado de residuos eletronicos e um problema crescente. "
    "Campanhas de reciclagem e pontos de coleta especializados ajudariam a "
    "reduzir o impacto ambiental desses materiais.",
    "A valorizacao do professor e fundamental para melhorar a qualidade da "
    "educacao publica. Salarios dignos e formacao continuada atraem e "
    "mantem bons profissionais na carreira docente.",
    "O acesso a agua potavel ainda nao e universal em todas as regioes do "
    "pais. Investimentos em infraestrutura basica sao necessarios para "
    "garantir esse direito fundamental a toda a populacao.",
    "A desinformacao nas redes sociais cresce rapidamente e afeta decisoes "
    "importantes da sociedade. Programas de educacao midiatica nas escolas "
    "podem ajudar a formar cidadaos mais criticos.",
    "O empreendedorismo entre jovens tem crescido nos ultimos anos. "
    "Incentivos fiscais e programas de capacitacao poderiam ampliar essa "
    "tendencia e gerar mais empregos para a nova geracao.",
    "A preservacao de areas verdes nas cidades contribui para a qualidade "
    "do ar e o bem-estar da populacao. Parques urbanos bem planejados "
    "tambem incentivam a pratica de atividades fisicas ao ar livre.",
    "O acesso a saude mental ainda e limitado para grande parte da "
    "populacao brasileira. Ampliar o atendimento psicologico na rede "
    "publica e essencial para enfrentar esse desafio crescente.",
]

_SYNTHETIC_TOTALS: list[int] = [
    960, 760, 760, 280, 480, 560, 480, 640, 360, 360,
    920, 920, 840, 680, 600, 480, 440, 400, 400, 760,
]


def _synthetic_scores(total: int) -> dict[str, int]:
    base = total // 5
    remainder = total - base * 5
    scores = [base] * 5
    scores[0] += remainder
    return {
        "C1": scores[0], "C2": scores[1], "C3": scores[2],
        "C4": scores[3], "C5": scores[4], "total": total,
    }


def build_fixture() -> list[dict]:
    entries: list[dict] = []
    for index in range(30):
        student_ref = f"aluno_{index + 1:02d}"
        is_special = index >= 20
        if is_special:
            entry = {
                "student_ref": student_ref,
                "body_text": _SYNTHETIC_BODIES[index % len(_SYNTHETIC_BODIES)],
                "expected_scores": {"C1": 0, "C2": 0, "C3": 0, "C4": 0, "C5": 0, "total": 0},
                "expected_special_situation": {
                    "category": "SITUACAO_ESPECIAL_NAO_ESPECIFICADA",
                    "evidence_note": "entrada sintetica de teste - nao corresponde a nenhuma "
                                      "redacao real; usada apenas para validar a forma do "
                                      "contrato de dados.",
                },
                "normative_status": "UNVERIFIED",
                "normative_divergence": None,
                "reference_source": "essay_calibration_synthetic_fixture_v1",
                "reference_version": "2026-10-08",
            }
        else:
            entry = {
                "student_ref": student_ref,
                "body_text": _SYNTHETIC_BODIES[index % len(_SYNTHETIC_BODIES)],
                "expected_scores": _synthetic_scores(_SYNTHETIC_TOTALS[index]),
                "expected_special_situation": None,
                "normative_status": "CONFIRMED",
                "normative_divergence": None,
                "reference_source": "essay_calibration_synthetic_fixture_v1",
                "reference_version": "2026-10-08",
            }
        entries.append(entry)
    return entries


def main() -> None:
    entries = build_fixture()
    fixtures_dir = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    (fixtures_dir / "essay_calibration_benchmark_v1.json").write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"Gravadas {len(entries)} entradas sinteticas (sem correspondencia com alunos reais).")


if __name__ == "__main__":
    main()
