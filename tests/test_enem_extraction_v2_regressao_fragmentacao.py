"""Regressao permanente dos 2 erros silenciosos da terceira adjudicacao.

Origem: amostra ALEATORIA SIMPLES de 60 entre as 698 que o MVP_IMPORT_GATE
aprovava. 58 passaram; as 2 reprovas tinham a mesma observacao do
adjudicador - fragmentacao de palavra por espaco espurio apos a primeira
letra.

Invariante: enquanto esses itens apresentarem fragmentacao, eles NAO podem
voltar a ser aprovados em silencio.

A fixture guarda apenas COORDENADAS e CONTAGENS - nenhum literal de prova.
Os testes PULAM quando os PDFs nao estao na maquina (``var/`` e gitignored):
um teste que falha por arquivo ausente mente sobre o estado do codigo.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
FIXTURE = RAIZ / "tests/fixtures/enem_v2_fragmentacao_adjudicada.json"
MANIFESTO = RAIZ / "tests/manual/phase10_enem_manifest_2016_2025.json"

CASOS = json.loads(FIXTURE.read_text())["casos"]
MANIFEST = {(e["exam_year"], e["exam_day"]): e
            for e in json.loads(MANIFESTO.read_text())["booklets"]}


def _presentes(ano: int, dia: int) -> bool:
    e = MANIFEST[(ano, dia)]
    return (RAIZ / e["proof_pdf"]).exists() and (RAIZ / e["answer_key_pdf"]).exists()


@pytest.fixture(scope="module")
def extraidos():
    from agente_ia_edu.services.enem_extraction_v2 import extrair_caderno

    cache = {}
    for caso in CASOS:
        chave = (caso["ano"], caso["dia"])
        if chave in cache or not _presentes(*chave):
            continue
        e = MANIFEST[chave]
        cache[chave] = extrair_caderno(
            RAIZ / e["proof_pdf"], RAIZ / e["answer_key_pdf"],
            ano=chave[0], dia=chave[1], caderno=e["booklet"])
    return cache


def _item(extraidos, caso):
    chave = (caso["ano"], caso["dia"])
    if chave not in extraidos:
        pytest.skip(f"PDF de {chave[0]} D{chave[1]} ausente (var/ e gitignored)")
    q = next((x for x in extraidos[chave].questoes
              if x.numero == caso["numero"]), None)
    assert q is not None, f"item {caso['numero']} deixou de ser detectado"
    return q


@pytest.mark.parametrize("caso", CASOS, ids=[c["id_adjudicacao"] for c in CASOS])
def test_item_fragmentado_nao_e_aprovado_em_silencio(extraidos, caso):
    from agente_ia_edu.services.enem_extraction_v2 import gate

    q = _item(extraidos, caso)
    faixa = (1, 90) if caso["dia"] == 1 else (91, 180)
    decisao = gate.avaliar(q, faixa=faixa)
    assert not decisao.aprovado, (
        f"{caso['id_adjudicacao']} voltou a ser importado em silencio")
    assert gate.PROBLEMA_ESTRUTURAL in decisao.motivos
    assert "FRAGMENTACAO_DE_PALAVRA_SUSPEITA" in decisao.detalhes, (
        f"{caso['id_adjudicacao']} e reprovado, mas por outro motivo: "
        f"{decisao.detalhes}")


@pytest.mark.parametrize("caso", CASOS, ids=[c["id_adjudicacao"] for c in CASOS])
def test_o_detector_ainda_enxerga_o_padrao(extraidos, caso):
    """Guarda contra o detector virar no-op e o item cair por outro motivo."""
    from agente_ia_edu.services.enem_extraction_v2 import fragmentation
    from agente_ia_edu.services.enem_extraction_v2.text_layer import ler

    q = _item(extraidos, caso)
    r = extraidos[(caso["ano"], caso["dia"])]
    e = MANIFEST[(caso["ano"], caso["dia"])]
    vocabulario = fragmentation.vocabulario_limpo(
        ler(RAIZ / e["proof_pdf"], r.motor_escolhido).documento)
    achados = fragmentation.ocorrencias(
        [q.enunciado] + [o.texto for o in q.opcoes], vocabulario)
    assert len(achados) >= caso["ocorrencias_suspeitas_minimo"], (
        f"{caso['id_adjudicacao']}: o detector enxergava "
        f"{caso['ocorrencias_suspeitas_minimo']} ocorrencias e agora ve "
        f"{len(achados)}")
    letras = {letra.lower() for letra, _ in achados}
    assert len(letras) >= caso["letras_distintas_minimo"]


def test_a_fixture_cobre_os_dois_casos():
    assert len(CASOS) == 2
    assert {c["id_adjudicacao"] for c in CASOS} == {"R010", "R019"}
    assert all(c["modo"] == "fragmentacao" for c in CASOS)
    assert all(not c["esperado_aprovado_pelo_gate"] for c in CASOS)


def test_a_fixture_nao_carrega_literal_de_prova():
    permitido = {
        "id_adjudicacao", "criterio_reprovado", "modo", "ano", "dia", "numero",
        "esperado_aprovado_pelo_gate", "esperado_problemas",
        "ocorrencias_suspeitas_minimo", "letras_distintas_minimo",
    }
    for caso in CASOS:
        assert set(caso) <= permitido, set(caso) - permitido
    assert len(FIXTURE.read_text()) < 2500
