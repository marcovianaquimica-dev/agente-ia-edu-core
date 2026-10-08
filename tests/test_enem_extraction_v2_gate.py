"""MVP_IMPORT_GATE e politica de dependencia visual (prototipo experimental).

Portao conservador: alta confianca -> banco, duvida relevante -> revisao.
Ele NUNCA descarta: um item reprovado continua existindo, com os motivos
nomeados e auditaveis.
"""

from __future__ import annotations

import pytest

from agente_ia_edu.services.enem_extraction_v2 import gate
from agente_ia_edu.services.enem_extraction_v2.assets import (
    BURACO_MINIMO_DE_FIGURA,
    dependencia_visual_nao_resolvida,
)
from agente_ia_edu.services.enem_extraction_v2.contracts import (
    ASSOC_CONTIDO,
    ASSOC_MESMA_PAGINA,
    ATIVO_RASTER,
    AssetCandidate,
    Caixa,
    OptionCandidate,
    QuestionCandidate,
)


def questao(**kw) -> QuestionCandidate:
    base = dict(
        numero=42,
        enunciado="Um enunciado com tamanho suficiente para passar no piso "
                  "estrutural do portao de importacao do MVP.",
        opcoes=[OptionCandidate(le, f"alternativa {le}", i, "TAB")
                for i, le in enumerate("ABCDE")],
        gabarito="C",
    )
    base.update(kw)
    return QuestionCandidate(**base)


class TestPortaoDoMvp:
    def test_questao_integra_e_aprovada(self):
        d = gate.avaliar(questao())
        assert d.aprovado and d.motivos == () and d.estado == "MVP_IMPORT"

    def test_sem_gabarito_vai_para_revisao(self):
        d = gate.avaliar(questao(gabarito=None))
        assert not d.aprovado
        assert gate.GABARITO_AUSENTE in d.motivos
        assert d.estado == "REQUIRES_REVIEW"

    def test_enunciado_curto_vai_para_revisao(self):
        assert gate.ENUNCIADO_INCOMPLETO in gate.avaliar(
            questao(enunciado="curto")).motivos

    def test_quatro_alternativas_vao_para_revisao(self):
        poucas = [OptionCandidate(le, f"t {le}", i, "TAB")
                  for i, le in enumerate("ABCD")]
        assert gate.ALTERNATIVAS_INCOMPLETAS in gate.avaliar(
            questao(opcoes=poucas)).motivos

    def test_alternativa_vazia_vai_para_revisao(self):
        vazia = [OptionCandidate(le, "" if le == "D" else f"t {le}", i, "TAB")
                 for i, le in enumerate("ABCDE")]
        assert gate.ALTERNATIVAS_INCOMPLETAS in gate.avaliar(
            questao(opcoes=vazia)).motivos

    def test_alternativas_fora_de_ordem_vao_para_revisao(self):
        trocadas = [OptionCandidate(le, f"t {le}", i, "TAB")
                    for i, le in enumerate("ABCED")]
        assert gate.ORDEM_DAS_ALTERNATIVAS in gate.avaliar(
            questao(opcoes=trocadas)).motivos

    def test_alternativas_identicas_vao_para_revisao(self):
        iguais = [OptionCandidate(le, "mesmo texto", i, "TAB")
                  for i, le in enumerate("ABCDE")]
        assert gate.ALTERNATIVA_DUPLICADA in gate.avaliar(
            questao(opcoes=iguais)).motivos

    def test_numero_fora_da_faixa_do_caderno(self):
        assert gate.NUMERO_FORA_DA_FAIXA in gate.avaliar(
            questao(numero=200), faixa=(1, 90)).motivos

    def test_numero_ausente(self):
        assert gate.NUMERO_AUSENTE in gate.avaliar(questao(numero=None)).motivos

    def test_gabarito_fora_de_a_e(self):
        assert gate.GABARITO_FORA_DE_A_E in gate.avaliar(
            questao(gabarito="Z")).motivos

    @pytest.mark.parametrize("problema", [
        "TEXTO_ILEGIVEL",
        "ULTIMA_ALTERNATIVA_ANOMALA",
        "DEPENDENCIA_VISUAL_NAO_RESOLVIDA",
        "NUMERO_DUPLICADO",
        "GABARITO_AMBIGUO",
    ])
    def test_qualquer_problema_declarado_impede_a_importacao(self, problema):
        d = gate.avaliar(questao(problemas=[problema]))
        assert not d.aprovado
        assert gate.PROBLEMA_ESTRUTURAL in d.motivos
        assert problema in d.detalhes

    def test_o_portao_preserva_todos_os_motivos(self):
        d = gate.avaliar(questao(gabarito=None, enunciado="x"))
        assert set(d.motivos) == {gate.GABARITO_AUSENTE,
                                  gate.ENUNCIADO_INCOMPLETO}

    def test_todo_motivo_emitido_esta_classificado(self):
        """Exaustividade.

        No CEREBRO um motivo nasceu sem classificacao (EMPTY_PUBLIC_ANSWER) e
        caiu no default silenciosamente. Aqui a propria funcao afirma a
        inclusao, e este teste cobre os caminhos.
        """
        emitidos: set[str] = set()
        casos = [
            {"numero": None}, {"numero": 999}, {"enunciado": "x"},
            {"opcoes": []}, {"gabarito": None}, {"gabarito": "Z"},
            {"problemas": ["QUALQUER_COISA"]},
            {"opcoes": [OptionCandidate(le, f"t {le}", i, "TAB")
                        for i, le in enumerate("ABCED")]},
            {"opcoes": [OptionCandidate(le, "igual", i, "TAB")
                        for i, le in enumerate("ABCDE")]},
        ]
        for kw in casos:
            emitidos |= set(gate.avaliar(questao(**kw), faixa=(1, 90)).motivos)
        assert emitidos == gate.MOTIVOS_CONHECIDOS, (
            gate.MOTIVOS_CONHECIDOS - emitidos)

    def test_a_versao_do_portao_esta_declarada(self):
        assert gate.avaliar(questao()).versao == "MVP_IMPORT_GATE_V1"

    def test_o_portao_nao_altera_a_questao(self):
        q = questao()
        antes = (q.numero, q.enunciado, len(q.opcoes), q.gabarito, list(q.problemas))
        gate.avaliar(q)
        assert (q.numero, q.enunciado, len(q.opcoes), q.gabarito,
                list(q.problemas)) == antes


class TestDependenciaVisual:
    """Indicio de figura sem associacao segura -> revisao, nao importacao."""

    def test_buraco_grande_sem_ativo_e_dependencia_nao_resolvida(self):
        assert dependencia_visual_nao_resolvida(154.0, [])

    def test_buraco_grande_com_ativo_confiavel_esta_resolvido(self):
        forte = AssetCandidate(pagina=1, caixa=Caixa(0, 0, 100, 100),
                               tipo=ATIVO_RASTER, metodo_deteccao="teste",
                               metodo_associacao=ASSOC_CONTIDO,
                               confianca_associacao=0.9)
        assert not dependencia_visual_nao_resolvida(154.0, [forte])

    def test_ativo_de_confianca_baixa_nao_resolve(self):
        fraco = AssetCandidate(pagina=1, caixa=Caixa(0, 0, 100, 100),
                               tipo=ATIVO_RASTER, metodo_deteccao="teste",
                               metodo_associacao=ASSOC_MESMA_PAGINA,
                               confianca_associacao=0.3)
        assert dependencia_visual_nao_resolvida(154.0, [fraco])

    def test_questao_sem_buraco_nao_e_marcada(self):
        assert not dependencia_visual_nao_resolvida(12.0, [])

    def test_o_piso_e_o_declarado(self):
        assert BURACO_MINIMO_DE_FIGURA == 60.0

    def test_limitacao_conhecida_buraco_pequeno_escapa(self):
        """Caracteriza o que a regra NAO pega, para nao criar ilusao.

        Um dos tres casos adjudicados, 2022 D2 item 135, tem buraco de 46 pt,
        abaixo da mediana do caderno. Nenhum limiar razoavel o alcanca por
        este sinal. Ele e pego por ULTIMA_ALTERNATIVA_ANOMALA, por
        coincidencia, nao por desenho.
        """
        assert not dependencia_visual_nao_resolvida(46.0, [])
