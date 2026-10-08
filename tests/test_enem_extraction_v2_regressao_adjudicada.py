"""Regressao permanente dos 10 erros silenciosos adjudicados por humano.

Origem: adjudicacao cega de 58 linhas, 2026-10-03. Destes 58 julgamentos, 10
linhas eram itens que a V2 da primeira medicao IMPORTARIA e que o adjudicador
reprovou - erro silencioso, o pior tipo.

Nenhum destes pode voltar a ser aprovado em silencio. Duas saidas sao aceitas:
  (a) o defeito foi corrigido e o item esta integro;
  (b) o item declara o problema e vai para REQUIRES_REVIEW.

A fixture ``tests/fixtures/enem_v2_erros_silenciosos_adjudicados.json`` guarda
apenas COORDENADAS (ano, dia, numero) e o estado esperado. **Nenhum literal de
prova.** O texto e lido do PDF na hora, quando ele esta presente.

``var/inep-pilot/`` esta no .gitignore, entao estes testes PULAM quando os
PDFs nao estao na maquina. Isso e proposital: um teste que falha por arquivo
ausente mente sobre o estado do codigo.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
FIXTURE = RAIZ / "tests/fixtures/enem_v2_erros_silenciosos_adjudicados.json"
MANIFESTO = RAIZ / "tests/manual/phase10_enem_manifest_2016_2025.json"

CASOS = json.loads(FIXTURE.read_text())["casos"]
MANIFEST = {(e["exam_year"], e["exam_day"]): e
            for e in json.loads(MANIFESTO.read_text())["booklets"]}

# assinaturas de mobilia que NAO podem reaparecer no texto de um item.
# Sao formas, nao conteudo de prova: marca d'agua colada e codigo de barras.
ASSINATURAS_DE_MOBILIA = ("ENEM2024ENEM2024", "ENEM2025ENEM2025",
                          "ENEM 2022 ENEM 2022", "ENEM 2023 ENEM 2023")


def _pdfs_presentes(ano: int, dia: int) -> bool:
    e = MANIFEST[(ano, dia)]
    return (RAIZ / e["proof_pdf"]).exists() and (RAIZ / e["answer_key_pdf"]).exists()


@pytest.fixture(scope="module")
def extraidos():
    """Extrai uma vez cada caderno citado na fixture."""
    from agente_ia_edu.services.enem_extraction_v2 import extrair_caderno

    cache = {}
    for caso in CASOS:
        chave = (caso["ano"], caso["dia"])
        if chave in cache or not _pdfs_presentes(*chave):
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
    r = extraidos[chave]
    q = next((x for x in r.questoes if x.numero == caso["numero"]), None)
    assert q is not None, f"item {caso['numero']} deixou de ser detectado"
    return q


@pytest.mark.parametrize("caso", CASOS, ids=[c["id_adjudicacao"] for c in CASOS])
def test_nenhum_erro_silencioso_adjudicado_volta_a_passar_calado(extraidos, caso):
    """O invariante central: corrigido, ou declarado. Nunca silencioso."""
    from agente_ia_edu.services.enem_extraction_v2 import gate

    q = _item(extraidos, caso)
    faixa = (1, 90) if caso["dia"] == 1 else (91, 180)
    decisao = gate.avaliar(q, faixa=faixa)

    if caso["esperado_aprovado_pelo_gate"]:
        # (a) corrigido: aprovado E sem vestigio do defeito
        assert decisao.aprovado, (
            f"{caso['id_adjudicacao']} era aprovado e corrigido; agora o portao "
            f"reprova por {decisao.motivos}. Se isso for proposital, atualize a "
            f"fixture explicando o porque.")
    else:
        # (b) declarado
        assert not decisao.aprovado, (
            f"{caso['id_adjudicacao']} voltou a ser importado em silencio")
        assert set(caso["esperado_problemas"]) & set(q.problemas), (
            f"{caso['id_adjudicacao']} e reprovado, mas por outro motivo: "
            f"esperado {caso['esperado_problemas']}, obtido {q.problemas}")


@pytest.mark.parametrize("caso", [c for c in CASOS if c["modo"] == "mobilia"],
                         ids=[c["id_adjudicacao"] for c in CASOS
                              if c["modo"] == "mobilia"])
def test_mobilia_nao_reaparece_no_texto_do_item(extraidos, caso):
    """Criterio E da adjudicacao: nenhum texto estranho agregado."""
    q = _item(extraidos, caso)
    inteiro = " ".join([q.enunciado] + [o.texto for o in q.opcoes])
    sujas = [a for a in ASSINATURAS_DE_MOBILIA if a in inteiro]
    assert not sujas, f"{caso['id_adjudicacao']} voltou a carregar {sujas}"


@pytest.mark.parametrize("caso", [c for c in CASOS if c["modo"] == "mobilia"
                                  and c["esperado_aprovado_pelo_gate"]],
                         ids=[c["id_adjudicacao"] for c in CASOS
                              if c["modo"] == "mobilia"
                              and c["esperado_aprovado_pelo_gate"]])
def test_a_ultima_alternativa_voltou_ao_tamanho_das_irmas(extraidos, caso):
    """A mobilia inflava a alternativa E. Guarda contra reincidencia.

    Compara com o comprimento medido DEPOIS do endurecimento, com folga de
    20%: o objetivo e pegar reincidencia de mobilia, nao congelar o texto.
    """
    q = _item(extraidos, caso)
    assert len(q.opcoes) == 5
    limite = int(caso["comprimento_da_ultima_alternativa"] * 1.2) + 10
    assert len(q.opcoes[4].texto) <= limite, (
        f"{caso['id_adjudicacao']}: ultima alternativa cresceu de "
        f"{caso['comprimento_da_ultima_alternativa']} para "
        f"{len(q.opcoes[4].texto)} caracteres")


def test_a_fixture_cobre_os_dez_casos_adjudicados():
    assert len(CASOS) == 10
    assert sum(1 for c in CASOS if c["modo"] == "mobilia") == 7
    assert sum(1 for c in CASOS if c["modo"] == "ativo") == 3


def test_a_fixture_nao_carrega_literal_de_prova():
    """Guarda de direitos: so coordenadas e estado, nunca texto do item."""
    bruto = FIXTURE.read_text()
    for caso in CASOS:
        assert set(caso) <= {
            "id_adjudicacao", "criterio_reprovado", "modo", "ano", "dia",
            "numero", "esperado_aprovado_pelo_gate", "esperado_problemas",
            "comprimento_da_ultima_alternativa", "ativos_associados",
        }, caso
    # nenhum campo de texto livre longo
    assert len(bruto) < 6000
