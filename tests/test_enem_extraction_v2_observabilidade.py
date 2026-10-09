"""Observabilidade: todo motivo de revisao e nomeado, conhecido e auditavel.

Existe porque no CEREBRO um motivo nasceu sem classificacao
(``EMPTY_PUBLIC_ANSWER``) e caiu no default em silencio. Aqui a regra e:
um codigo novo so entra se alguem atualizar esta lista de proposito.

A verificacao do pipeline e por ARVORE SINTATICA, nao por busca de texto:
buscar o nome no arquivo encontraria a propria docstring que o explica -
erro ja cometido nesta base de codigo.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agente_ia_edu.services.enem_extraction_v2 import contracts, gate

PACOTE = Path(contracts.__file__).parent

# Todo codigo de problema que o extrator pode anexar a um item.
# Acrescentar um codigo SEM acrescentar aqui quebra a suite, de proposito.
CODIGOS_DE_PROBLEMA_CONHECIDOS = {
    "ALTERNATIVAS_INCOMPLETAS",
    "DEPENDENCIA_VISUAL_NAO_RESOLVIDA",
    "ENUNCIADO_VAZIO",
    "FRAGMENTACAO_DE_PALAVRA_SUSPEITA",
    "GABARITO_AMBIGUO",
    "GABARITO_AUSENTE",
    "NUMERO_DUPLICADO",
    "TEXTO_ILEGIVEL",
    "ULTIMA_ALTERNATIVA_ANOMALA",
}


def _constantes_de_problema() -> dict[str, str]:
    """Constantes de ``contracts`` cujo valor e um codigo de problema."""
    nao_sao_problema = {
        "MOTOR_PYPDF", "MOTOR_PYMUPDF", "ATIVO_RASTER", "ATIVO_VETORIAL",
        "ASSOC_CONTIDO", "ASSOC_SOBREPOSTO", "ASSOC_MESMA_PAGINA",
        "ASSOC_NENHUMA",
    }
    saida = {}
    for nome in dir(contracts):
        if nome.startswith("_") or nome in nao_sao_problema:
            continue
        valor = getattr(contracts, nome)
        if isinstance(valor, str) and nome.isupper():
            saida[nome] = valor
    return saida


def test_todo_codigo_de_problema_esta_na_lista_conhecida():
    valores = set(_constantes_de_problema().values())
    assert valores == CODIGOS_DE_PROBLEMA_CONHECIDOS, {
        "novos": valores - CODIGOS_DE_PROBLEMA_CONHECIDOS,
        "sumiram": CODIGOS_DE_PROBLEMA_CONHECIDOS - valores,
    }


def test_a_constante_e_o_valor_coincidem():
    """Evita ``FOO = "BAR"``, que torna o log impossivel de rastrear."""
    divergentes = {n: v for n, v in _constantes_de_problema().items() if n != v}
    assert divergentes == {}, divergentes


def test_o_pipeline_so_anexa_codigos_conhecidos():
    """Por AST: todo nome anexado a ``problemas`` vem de ``contracts``."""
    arvore = ast.parse((PACOTE / "pipeline.py").read_text(encoding="utf-8"))
    anexados: set[str] = set()
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call):
            continue
        alvo = no.func
        if not (isinstance(alvo, ast.Attribute) and alvo.attr == "append"):
            continue
        if not (isinstance(alvo.value, ast.Name) and alvo.value.id == "problemas"):
            continue
        for argumento in no.args:
            if isinstance(argumento, ast.Name):
                anexados.add(argumento.id)
            elif isinstance(argumento, ast.Constant):
                anexados.add(f"LITERAL:{argumento.value}")
    assert anexados, "nenhum append a `problemas` encontrado - teste quebrado?"
    literais = {a for a in anexados if a.startswith("LITERAL:")}
    assert literais == set(), (
        f"codigo escrito como literal no pipeline, fora de contracts: {literais}")
    desconhecidos = {a for a in anexados
                     if getattr(contracts, a, None) not in CODIGOS_DE_PROBLEMA_CONHECIDOS}
    assert desconhecidos == set(), desconhecidos


def test_todo_problema_declarado_chega_ao_portao_como_detalhe():
    """O portao nao pode engolir o motivo: ele vai para `detalhes`."""
    from agente_ia_edu.services.enem_extraction_v2.contracts import (
        OptionCandidate,
        QuestionCandidate,
    )

    for codigo in sorted(CODIGOS_DE_PROBLEMA_CONHECIDOS):
        q = QuestionCandidate(
            numero=42,
            enunciado="Um enunciado com tamanho suficiente para o piso do portao.",
            opcoes=[OptionCandidate(le, f"t {le}", i, "TAB")
                    for i, le in enumerate("ABCDE")],
            gabarito="C", problemas=[codigo])
        decisao = gate.avaliar(q)
        assert not decisao.aprovado, codigo
        assert codigo in decisao.detalhes, codigo


def test_o_portao_declara_a_sua_versao():
    """Sem versao nao da para comparar duas medicoes."""
    assert gate.VERSAO_DO_PORTAO.startswith("MVP_IMPORT_GATE_V")


def test_nenhum_motivo_do_portao_e_vazio_ou_duplicado():
    assert all(m and m.isupper() for m in gate.MOTIVOS_CONHECIDOS)
    assert len(gate.MOTIVOS_CONHECIDOS) == len(set(gate.MOTIVOS_CONHECIDOS))
