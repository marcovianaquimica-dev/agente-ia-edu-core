"""NUCLEO DIAGNOSTIC BANK - Estequiometria.

Mesmo banco do Balanceamento, outro conteudo. Nenhuma tabela nova, nenhum
"segundo banco": Question / QuestionVersion / QuestionOption,
`origin_type='GENERATED'`, edicao propria do Nucleo, `provenance='AI_VERIFIED'`,
e a mesma selecao (`QuestionBankService` + `PracticeSelectionPolicy`) que o
MicroDiagnosticService ja usa.

O QUE MUDA E A VERIFICACAO DETERMINISTICA
==========================================
Em Balanceamento, o que Python conferia era conservacao de atomos. Aqui e
mais: massa molar, proporcao molar e O VALOR NUMERICO DA RESPOSTA. O item
declara os dados da conta (equacao, especie de origem, especie de destino,
quantidade); `estequiometria` refaz a conta; e so entao comparamos com a
alternativa que o gerador marcou.

O ERRO QUE ISTO EXISTE PARA PEGAR
==================================
Equacao nao balanceada. A regra de tres fecha perfeitamente sobre
coeficientes errados, o numero que sai parece correto, e um SEGUNDO LLM
refaria a mesma conta sobre a mesma equacao errada - os dois concordariam, e
os dois estariam errados. `proporcao_molar` recusa equacao desbalanceada antes
de qualquer conta.

TOLERANCIA, E POR QUE ELA E RELATIVA
=====================================
O livro escolar usa H=1 e O=16; a IUPAC diz 1,008 e 15,999. Para 4 g de H2 o
livro escreve 36 g e a aritmetica da 35,74 - 0,7% de diferenca. Comparar por
igualdade rejeitaria itens escolares CORRETOS. Por isso a comparacao e por
tolerancia relativa, declarada em `TOLERANCIA_RELATIVA` e nao espalhada pelo
codigo.

FAIL-CLOSED
===========
O que este modulo nao consegue recalcular vai para REQUIRES_REVIEW, nunca para
AI_VERIFIED. Alternativa sem numero legivel, especie fora da equacao, dado
ausente: todos rejeitam. Num diagnostico a saida nao e uma nota - e "o aluno
esta pronto" - e um item que ninguem conferiu contamina essa conclusao.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agente_ia_edu.services.chemistry_balance import FormulaInvalida
from agente_ia_edu.services.estequiometria import (
    DadosInsuficientes,
    massa_molar,
    proporcao_molar,
    resolver_massa_massa,
    resolver_mol_mol,
)

CONTENT_CODE = "CHEMISTRY-PHYSICAL-STOICHIOMETRY"
ORIGIN_TYPE = "GENERATED"
BANK_TAG = "nucleo-diagnostic-bank-v1"
LETRAS = ("A", "B", "C", "D", "E")

# 1% - o bastante para absorver H=1 vs H=1,008, e pouco o bastante para que
# duas alternativas distintas nao colidam. Um numero so, declarado aqui.
TOLERANCIA_RELATIVA = 0.01

# -------------------------------------------------------------- a matriz ---
# Auditada contra a taxonomia real antes de ser escrita: `CHEMISTRY-PHYSICAL-
# STOICHIOMETRY` e o unico no de Estequiometria do catalogo, e NAO ha
# SUBCONTENT sob ele. As micro-habilidades abaixo NAO viram CatalogNode - elas
# nao participam do grafo curricular (nada e pre-requisito de "relacao
# mol-mol"), entao vivem em `diagnostic_skill`, no metadata do item, como ja
# acontece no banco de Balanceamento.
SKILL_PROPORCAO = "PROPORCAO_ESTEQUIOMETRICA"
SKILL_MOL_MOL = "RELACAO_MOL_MOL"
SKILL_MASSA_MOL = "RELACAO_MASSA_MOL"
SKILL_MASSA_MASSA = "RELACAO_MASSA_MASSA"

HABILIDADES = {
    SKILL_PROPORCAO: "O aluno le a proporcao entre as especies a partir dos "
                     "coeficientes da equacao?",
    SKILL_MOL_MOL: "O aluno converte quantidade de materia de uma especie em "
                   "quantidade de materia de outra?",
    SKILL_MASSA_MOL: "O aluno converte massa em quantidade de materia usando a "
                     "massa molar?",
    SKILL_MASSA_MASSA: "O aluno percorre massa -> mol -> proporcao -> mol -> "
                       "massa ate o fim?",
}

# Reagente limitante, rendimento e pureza FICARAM DE FORA desta primeira
# matriz, de proposito: as tres exigem um segundo dado de entrada (duas massas,
# ou um percentual) e portanto um contrato de item diferente do que a
# verificacao abaixo sabe refazer. Entrar com elas agora significaria aprovar
# por opiniao de modelo o que nao consigo recalcular - que e exatamente o que
# este banco existe para nao fazer. Ficam registradas como proxima matriz.

_DEPENDENCIA_VISUAL = re.compile(
    r"\b(gr[áa]fico|figura|imagem|tabela abaixo|esquema|diagrama|ilustra)\w*\b",
    re.IGNORECASE)


@dataclass(frozen=True)
class ItemEstequiometria:
    """O que o gerador produziu, com os DADOS DA CONTA declarados.

    `rationale` existe para o aluno ver depois; ele NAO chega ao verificador,
    pelo mesmo motivo do contrato de classificacao: perguntar "este gabarito
    esta certo?" convida a concordar, perguntar "qual e a resposta?" obriga a
    calcular. Aqui quem calcula e Python.
    """

    diagnostic_skill: str
    diagnostic_objective: str
    difficulty: str
    stem: str
    options: dict[str, str]
    correct_answer: str
    rationale: str
    equacao: str
    especie_de: str
    especie_para: str
    quantidade: float | None
    unidade_entrada: str          # "g" ou "mol"
    unidade_saida: str            # "g" ou "mol"
    generator_version: str = ""
    massas_molares: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class VerificacaoDeterministica:
    conferiu: bool
    problemas: list[str] = field(default_factory=list)
    detalhes: dict = field(default_factory=dict)


def _numero(texto: str) -> float | None:
    """Extrai o numero de "35,7 g". Devolve None quando nao ha um so numero.

    Virgula decimal e o padrao em portugues. Um texto como "mais de 30 g" tem
    numero mas nao e um VALOR - e o `fullmatch` abaixo recusa, que e o
    comportamento desejado: o que nao da para ler nao da para conferir.
    """
    t = (texto or "").strip()
    if not t:
        return None
    # Antes do numero so cabem simbolos e espaco - NUNCA letras. `[^\d\-+]*`
    # aceitava "mais de 30 g", que tem numero mas nao e um VALOR: o item fica
    # irrespondivel e o verificador precisa recusar, nao adivinhar.
    m = re.fullmatch(r"[\s~=≈<>]*([-+]?\d+(?:[.,]\d+)?)\s*(?:g|mol|gramas?|mols?)?\.?",
                     t, re.IGNORECASE)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def _proximo(a: float, b: float) -> bool:
    if a == b:
        return True
    escala = max(abs(a), abs(b))
    if escala == 0:
        return True
    return abs(a - b) / escala <= TOLERANCIA_RELATIVA


def conferir_estrutura(item: ItemEstequiometria) -> list[str]:
    """O que da para dizer sem fazer conta nenhuma."""
    problemas: list[str] = []
    if item.diagnostic_skill not in HABILIDADES:
        problemas.append(f"habilidade desconhecida: {item.diagnostic_skill!r}")
    if not (item.stem or "").strip():
        problemas.append("enunciado vazio")
    if set(item.options or {}) != set(LETRAS):
        problemas.append(f"alternativas devem ser exatamente {LETRAS}")
    if item.correct_answer not in LETRAS:
        problemas.append(f"gabarito fora das letras: {item.correct_answer!r}")
    if any(not (t or "").strip() for t in (item.options or {}).values()):
        problemas.append("ha alternativa vazia")
    if _DEPENDENCIA_VISUAL.search(item.stem or ""):
        # O banco e de texto. Um item que manda "observar o grafico" sem
        # grafico e irrespondivel - e o aluno conclui que nao sabe o conteudo.
        problemas.append("dependencia visual: o enunciado cita um recurso que "
                         "o item nao tem")
    if item.unidade_entrada not in ("g", "mol"):
        problemas.append(f"unidade de entrada nao suportada: {item.unidade_entrada!r}")
    if item.unidade_saida not in ("g", "mol"):
        problemas.append(f"unidade de saida nao suportada: {item.unidade_saida!r}")
    return problemas


def conferir_calculo(item: ItemEstequiometria) -> VerificacaoDeterministica:
    """Refaz a conta e confronta com as alternativas. Sem opiniao de modelo."""
    problemas: list[str] = []
    detalhes: dict = {}

    try:
        proporcao = proporcao_molar(item.equacao)
        detalhes["proporcao_molar"] = proporcao
    except (FormulaInvalida, DadosInsuficientes) as exc:
        return VerificacaoDeterministica(False, [f"equacao: {exc}"], detalhes)

    try:
        if item.unidade_entrada == "g" and item.unidade_saida == "g":
            valor = resolver_massa_massa(item.equacao, de=item.especie_de,
                                         para=item.especie_para,
                                         massa=item.quantidade)
        elif item.unidade_entrada == "mol" and item.unidade_saida == "mol":
            valor = resolver_mol_mol(item.equacao, de=item.especie_de,
                                     para=item.especie_para, mols=item.quantidade)
        elif item.unidade_entrada == "g" and item.unidade_saida == "mol":
            mm = massa_molar(item.especie_de)
            valor = resolver_mol_mol(item.equacao, de=item.especie_de,
                                     para=item.especie_para,
                                     mols=item.quantidade / mm)
        elif item.unidade_entrada == "mol" and item.unidade_saida == "g":
            mols = resolver_mol_mol(item.equacao, de=item.especie_de,
                                    para=item.especie_para, mols=item.quantidade)
            valor = mols * massa_molar(item.especie_para)
        else:
            return VerificacaoDeterministica(
                False, [f"par de unidades nao suportado: "
                        f"{item.unidade_entrada!r} -> {item.unidade_saida!r}"],
                detalhes)
    except (FormulaInvalida, DadosInsuficientes) as exc:
        return VerificacaoDeterministica(False, [f"calculo: {exc}"], detalhes)
    except TypeError as exc:  # quantidade None
        return VerificacaoDeterministica(
            False, [f"dado ausente para o calculo: {exc}"], detalhes)

    detalhes["valor_calculado"] = valor

    valores = {letra: _numero(texto) for letra, texto in (item.options or {}).items()}
    detalhes["valores_das_alternativas"] = valores
    ilegiveis = [letra for letra, v in valores.items() if v is None]
    if ilegiveis:
        problemas.append(
            f"alternativa sem numero legivel: {sorted(ilegiveis)} - "
            f"sem isso nao da para conferir qual esta certa")
        return VerificacaoDeterministica(False, problemas, detalhes)

    # Alternativas distintas entre si: duas iguais tornam o item irrespondivel
    # mesmo que a correta esteja la.
    vistos: list[float] = []
    for letra in LETRAS:
        v = valores[letra]
        if any(_proximo(v, outro) for outro in vistos):
            problemas.append(f"alternativas numericamente iguais em {letra!r}")
        vistos.append(v)

    batem = [letra for letra in LETRAS if _proximo(valores[letra], valor)]
    detalhes["alternativas_corretas"] = batem
    if not batem:
        problemas.append(
            f"nenhuma alternativa bate com o calculo ({valor:.4g})")
    elif len(batem) > 1:
        problemas.append(
            f"mais de uma alternativa correta: {batem} (calculo = {valor:.4g})")
    elif batem[0] != item.correct_answer:
        problemas.append(
            f"gabarito declarado {item.correct_answer!r}, mas o calculo da "
            f"{batem[0]!r} ({valor:.4g})")

    return VerificacaoDeterministica(not problemas, problemas, detalhes)


__all__ = [
    "ItemEstequiometria",
    "VerificacaoDeterministica",
    "conferir_calculo",
    "conferir_estrutura",
    "HABILIDADES",
    "CONTENT_CODE",
    "ORIGIN_TYPE",
    "BANK_TAG",
    "TOLERANCIA_RELATIVA",
    "SKILL_PROPORCAO",
    "SKILL_MOL_MOL",
    "SKILL_MASSA_MOL",
    "SKILL_MASSA_MASSA",
]
