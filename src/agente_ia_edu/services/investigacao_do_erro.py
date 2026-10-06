"""INVESTIGAR ANTES DE RESOLVER - uma pergunta curta no lugar do despejo.

O QUE ESTE MODULO MUDA
=======================
Ate aqui, errar levava a uma explicacao inteira. Para quem errou so a ultima
etapa, isso e ouvir de novo o que ja sabia; para quem errou a primeira, e
ouvir tres etapas construidas sobre a que falhou. Nos dois casos o sistema
nao fica sabendo NADA de novo sobre o aluno - ele falou, nao perguntou.

A investigacao troca o despejo por uma pergunta de cada vez:

    ERRO -> hipotese -> micropergunta -> resposta -> gargalo localizado

E o gargalo localizado e evidencia DIAGNOSTICA de verdade: o aluno respondeu
uma pergunta que isola uma etapa, e nao uma questao que mistura quatro.

A HIPOTESE E O PONTO DELICADO
==============================
O distrator SUGERE um raciocinio. Ele nao o prova. Quem marcou 8,50 g pode
ter pulado a proporcao, pode ter errado a massa molar, pode ter chutado -
8,50 e o que sai de `0,5 mol x 17 g/mol`, e isso e uma hipotese boa, nao um
fato sobre a cabeca de alguem.

Por isso toda hipotese aqui e escrita como hipotese, e ha teste varrendo os
textos atras de "voce fez", "voce esqueceu", "seu erro foi". A micropergunta
e que confirma ou refuta - e e justamente por isso que ela existe.

O QUE ESTE MODULO NAO E
========================
Nao e mastery, nao escreve em lugar nenhum e nao consulta modelo de IA. Nao
ha sessao de banco aqui, e ha teste lendo o arquivo - mesma garantia
estrutural de `sinal_diagnostico` e `conversa_do_assessor`.

A micropergunta serve ao DIAGNOSTICO e ao FADING. Ela nao entra no mapa de
dominio: acertar uma etapa isolada, logo depois de o sistema dizer qual
etapa e, nao e a mesma coisa que resolver o problema inteiro sozinho.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agente_ia_edu.services.grafo_estequiometria import (
    CONTEUDO,
    LEITURA_FORMULA,
    MASSA_MOL,
    MASSA_MOLAR,
    PROPORCAO,
)


@dataclass(frozen=True)
class EtapaDaInvestigacao:
    """Uma pergunta que isola UMA etapa - e o que dizer dos dois desfechos.

    `se_errar` nao e a resposta com outras palavras: e o ensino daquela etapa.
    Dizer "a resposta e C" encerra a pergunta sem ensinar nada, e ha teste
    exigindo substancia aqui.
    """

    ordem: int
    habilidade: str
    pergunta: str
    alternativas: dict[str, str]
    correta: str
    conferencia: str
    se_acertar: str
    se_errar: str


@dataclass(frozen=True)
class Investigacao:
    key: str
    content_code: str
    habilidade_alvo: str
    hipotese: str
    etapas: tuple[EtapaDaInvestigacao, ...]


# ---------------------------------------------------------------------------
# AS CADEIAS CURADAS
#
# Cada uma comeca na etapa mais BASICA da cadeia, e nao na que o aluno errou.
# Perguntar a contribuicao dos hidrogenios a quem le 1 H no NH3 nao localiza
# nada - ele vai errar a segunda pergunta pelo motivo da primeira, e o
# sistema vai concluir a coisa errada.
# ---------------------------------------------------------------------------

_LEITURA = Investigacao(
    key="INV-EST-LEITURA-FORMULA",
    content_code=CONTEUDO,
    habilidade_alvo=LEITURA_FORMULA,
    hipotese="Esse resultado sugere que a contagem de átomos da fórmula pode "
             "estar escapando em algum ponto. Vamos conferir com três "
             "fórmulas rápidas.",
    etapas=(
        EtapaDaInvestigacao(
            ordem=1,
            habilidade=LEITURA_FORMULA,
            pergunta="Na fórmula H₂O, quantos átomos de hidrogênio estão "
                     "representados?",
            alternativas={"A": "1", "B": "2", "C": "3", "D": "4"},
            correta="B",
            conferencia="indice de H em H2O",
            se_acertar="Isso. O número pequeno depois do símbolo diz quantos "
                       "átomos daquele elemento a fórmula tem.",
            se_errar="O número pequeno que vem depois do símbolo é o índice, e "
                     "ele conta os átomos daquele elemento. Em H₂O o 2 está "
                     "colado no H, então são dois hidrogênios; o oxigênio não "
                     "tem índice, e isso significa um só."),
        EtapaDaInvestigacao(
            ordem=2,
            habilidade=LEITURA_FORMULA,
            pergunta="E na fórmula NH₃, quantos átomos de hidrogênio?",
            alternativas={"A": "1", "B": "2", "C": "3", "D": "4"},
            correta="C",
            conferencia="indice de H em NH3",
            se_acertar="Exato: três hidrogênios, e um nitrogênio.",
            se_errar="O 3 está colado no H, então ele conta hidrogênios: são "
                     "três. O nitrogênio aparece sem índice nenhum, e símbolo "
                     "sem índice quer dizer um átomo."),
        EtapaDaInvestigacao(
            ordem=3,
            habilidade=LEITURA_FORMULA,
            pergunta="Na fórmula Ca(OH)₂, quantos átomos de oxigênio?",
            alternativas={"A": "1", "B": "2", "C": "3", "D": "4"},
            correta="B",
            conferencia="indice de O em Ca(OH)2",
            se_acertar="Isso mesmo. O índice fora do parêntese multiplica "
                       "tudo o que está dentro dele.",
            se_errar="O índice que vem depois do parêntese multiplica tudo o "
                     "que está lá dentro. Dentro de (OH) há um oxigênio e um "
                     "hidrogênio; com o 2 do lado de fora, a fórmula passa a "
                     "ter dois de cada um."),
    ),
)

_MASSA_MOLAR = Investigacao(
    key="INV-EST-MASSA-MOLAR",
    content_code=CONTEUDO,
    habilidade_alvo=MASSA_MOLAR,
    hipotese="Esse resultado sugere que a soma das massas pode ter ficado "
             "incompleta em algum ponto — às vezes na leitura da fórmula, às "
             "vezes na multiplicação pelo índice. Vamos conferir por partes.",
    etapas=(
        EtapaDaInvestigacao(
            ordem=1,
            habilidade=LEITURA_FORMULA,
            pergunta="Na fórmula NH₃, quantos átomos de hidrogênio estão "
                     "representados?",
            alternativas={"A": "1", "B": "2", "C": "3", "D": "4"},
            correta="C",
            conferencia="indice de H em NH3",
            se_acertar="Isso. Então a massa molar vai precisar contar o "
                       "hidrogênio três vezes.",
            se_errar="O número pequeno colado no símbolo conta os átomos "
                     "daquele elemento: em NH₃ são três hidrogênios. O "
                     "nitrogênio vem sem índice, e isso quer dizer um átomo."),
        EtapaDaInvestigacao(
            ordem=2,
            habilidade=MASSA_MOLAR,
            pergunta="Se cada hidrogênio contribui com 1 g/mol, qual é a "
                     "contribuição dos três hidrogênios juntos?",
            alternativas={"A": "1 g/mol", "B": "3 g/mol", "C": "4 g/mol",
                          "D": "13 g/mol"},
            correta="B",
            conferencia="3 * 1",
            se_acertar="Isso. Cada elemento entra tantas vezes quanto o "
                       "índice manda.",
            se_errar="A contribuição de um elemento é a massa atômica dele "
                     "multiplicada pelo índice. Com três hidrogênios de 1 "
                     "g/mol cada, a conta é uma multiplicação simples, e o "
                     "resultado entra inteiro na soma final."),
        EtapaDaInvestigacao(
            ordem=3,
            habilidade=MASSA_MOLAR,
            pergunta="O nitrogênio entra com 14 g/mol e os hidrogênios com 3 "
                     "g/mol. Qual é a massa molar do NH₃?",
            alternativas={"A": "15 g/mol", "B": "16 g/mol", "C": "17 g/mol",
                          "D": "18 g/mol"},
            correta="C",
            conferencia="14 + 3",
            se_acertar="É isso: massa molar é a soma das contribuições de "
                       "cada elemento da fórmula.",
            se_errar="A massa molar é a soma das contribuições de todos os "
                     "elementos, nenhum de fora. Some a parte do nitrogênio "
                     "com a parte dos hidrogênios e o total é a massa de um "
                     "mol da substância."),
    ),
)

_MASSA_MOL = Investigacao(
    key="INV-EST-MASSA-MOL",
    content_code=CONTEUDO,
    habilidade_alvo=MASSA_MOL,
    hipotese="Esse resultado sugere que a passagem entre massa e quantidade "
             "de matéria pode estar invertida em algum momento. Vamos olhar "
             "os dois sentidos.",
    etapas=(
        EtapaDaInvestigacao(
            ordem=1,
            habilidade=MASSA_MOLAR,
            pergunta="Com H = 1 g/mol e O = 16 g/mol, qual é a massa molar da "
                     "água (H₂O)?",
            alternativas={"A": "17 g/mol", "B": "18 g/mol", "C": "20 g/mol",
                          "D": "34 g/mol"},
            correta="B",
            conferencia="2*1 + 16",
            se_acertar="Isso. Dois hidrogênios valem 2, mais 16 do oxigênio.",
            se_errar="São dois hidrogênios de 1 g/mol e um oxigênio de 16 "
                     "g/mol. Multiplique cada massa atômica pelo índice e "
                     "some as duas partes para chegar à massa de um mol."),
        EtapaDaInvestigacao(
            ordem=2,
            habilidade=MASSA_MOL,
            pergunta="Quantos mol de água existem em 36 g?",
            alternativas={"A": "0,5 mol", "B": "1 mol", "C": "2 mol",
                          "D": "18 mol"},
            correta="C",
            conferencia="36 / 18",
            se_acertar="Exato. A massa molar diz quanto pesa um mol, então "
                       "dividir pela massa molar conta quantos mol cabem.",
            se_errar="A massa molar diz quanto pesa um mol da substância. "
                     "Para descobrir quantos mol cabem numa massa, divida a "
                     "massa pela massa molar — e repare que o resultado sai "
                     "em mol, não em gramas."),
        EtapaDaInvestigacao(
            ordem=3,
            habilidade=MASSA_MOL,
            pergunta="E quanto pesam 0,5 mol de água?",
            alternativas={"A": "0,5 g", "B": "9 g", "C": "18 g", "D": "36 g"},
            correta="B",
            conferencia="0.5 * 18",
            se_acertar="Isso. No sentido contrário a conta é multiplicar.",
            se_errar="Indo de mol para massa o caminho se inverte: em vez de "
                     "dividir, multiplique a quantidade de matéria pela massa "
                     "molar. Metade de um mol pesa metade do que pesa um mol "
                     "inteiro."),
    ),
)

# O CASO OBSERVADO NO NAVEGADOR, em 2026-10-06.
#
#   N2 + 3 H2 -> 2 NH3, 14,0 g de N2, M(N2) = 28,0, M(NH3) = 17,0
#   resposta correta 17,0 g; o aluno marcou 8,50 g
#
# 8,50 e exatamente `0,5 mol x 17 g/mol` - o que sai de levar o mol de N2
# direto para a massa de NH3, sem a proporcao 1:2 do meio. Isso e uma
# hipotese BOA justamente porque e TESTAVEL: a segunda micropergunta a
# confirma ou a derruba, e ate la ninguem afirma nada.
_PROPORCAO = Investigacao(
    key="INV-EST-PROPORCAO",
    content_code=CONTEUDO,
    habilidade_alvo=PROPORCAO,
    hipotese="Essa resposta sugere que a conta pode ter ido do mol de N₂ "
             "direto para a massa de NH₃, sem a etapa da proporção entre as "
             "duas substâncias. Vamos conferir uma etapa de cada vez.",
    etapas=(
        EtapaDaInvestigacao(
            ordem=1,
            habilidade=MASSA_MOL,
            pergunta="A massa molar do N₂ é 28,0 g/mol. 14,0 g de N₂ "
                     "correspondem a quantos mol?",
            alternativas={"A": "0,25 mol", "B": "0,5 mol", "C": "1,0 mol",
                          "D": "2,0 mol"},
            correta="B",
            conferencia="14 / 28",
            se_acertar="Isso. Meio mol de N₂ — essa é a quantidade com que "
                       "vamos trabalhar.",
            se_errar="Para ir de massa para quantidade de matéria, divida a "
                     "massa pela massa molar. Como 14,0 g é metade dos 28,0 g "
                     "que um mol inteiro pesaria, o resultado também é "
                     "metade de um mol."),
        EtapaDaInvestigacao(
            ordem=2,
            habilidade=PROPORCAO,
            pergunta="Na equação N₂ + 3 H₂ → 2 NH₃, cada 1 mol de N₂ produz "
                     "2 mol de NH₃. Então 0,5 mol de N₂ produzem quantos mol "
                     "de NH₃?",
            alternativas={"A": "0,25 mol", "B": "0,5 mol", "C": "1,0 mol",
                          "D": "2,0 mol"},
            correta="C",
            conferencia="0.5 * 2/1",
            se_acertar="Exato. Os coeficientes da equação são a proporção "
                       "entre as quantidades — e é essa etapa que muda o "
                       "número.",
            se_errar="Os números na frente das fórmulas são a proporção entre "
                     "as quantidades de matéria. Como saem 2 mol de NH₃ para "
                     "cada 1 mol de N₂, a quantidade de amônia é o dobro da "
                     "quantidade de nitrogênio que reagiu."),
        EtapaDaInvestigacao(
            ordem=3,
            habilidade=MASSA_MOL,
            pergunta="A massa molar do NH₃ é 17,0 g/mol. Quanto pesam 1,0 mol "
                     "de NH₃?",
            # "8,50 g" entra como distrator de proposito: e o valor que o
            # aluno marcou na questao original. Ve-lo aqui, lado a lado com
            # 17,0 g depois de ter feito a etapa da proporcao, e o momento em
            # que a hipotese se fecha para ele proprio.
            alternativas={"A": "8,50 g", "B": "17,0 g", "C": "28,0 g",
                          "D": "34,0 g"},
            correta="B",
            conferencia="1.0 * 17",
            se_acertar="É isso: 17,0 g de amônia. Repare que a etapa da "
                       "proporção é a que separa esse valor de 8,50 g.",
            se_errar="No último passo o caminho é multiplicar a quantidade de "
                     "matéria pela massa molar. Um mol da substância pesa "
                     "exatamente a massa molar dela, então o resultado vem "
                     "direto desse valor."),
    ),
)


INVESTIGACOES: tuple[Investigacao, ...] = (
    _LEITURA, _MASSA_MOLAR, _MASSA_MOL, _PROPORCAO,
)


def investigacao_para(content_code: str, skill: str | None) -> Investigacao | None:
    """A cadeia daquela lacuna, ou None quando nao ha uma escrita para ela.

    Nao ha cadeia generica, de proposito: uma investigacao que nao isola
    etapas do conteudo nao localiza nada, e o aluno responderia perguntas
    cujo resultado o sistema nao saberia interpretar.
    """
    if not skill:
        return None
    for inv in INVESTIGACOES:
        if inv.content_code == content_code and inv.habilidade_alvo == skill:
            return inv
    return None


def proxima_etapa(inv: Investigacao,
                  respostas: Mapping[int, str] | None) -> EtapaDaInvestigacao | None:
    """A primeira etapa ainda nao RESOLVIDA.

    Errar nao avanca: quem errou a etapa 1 precisa da etapa 1. Avancar ali
    seria perguntar a etapa 2 a quem acabou de mostrar que a 1 nao esta de pe
    - e a resposta da 2 nao significaria nada.
    """
    dadas = dict(respostas or {})
    for e in inv.etapas:
        marcada = (dadas.get(e.ordem) or "").strip().upper()
        if marcada != e.correta:
            return e
    return None


def gargalo(inv: Investigacao,
            respostas: Mapping[int, str] | None) -> str | None:
    """A habilidade da PRIMEIRA etapa que falhou, ou None.

    Errar duas etapas nao quer dizer duas lacunas: a segunda se apoia na
    primeira, e comecar pela mais alta e ensinar o telhado a quem ainda nao
    tem parede. Etapa nao respondida nao conta - falta de resposta nao e erro.
    """
    dadas = dict(respostas or {})
    for e in inv.etapas:
        marcada = dadas.get(e.ordem)
        if marcada is None:
            return None
        if (marcada or "").strip().upper() != e.correta:
            return e.habilidade
    return None


def conferir_resposta(etapa: EtapaDaInvestigacao, escolha: str) -> bool:
    """Acertou? A conferencia e aqui, nunca no cliente."""
    return (escolha or "").strip().upper() == etapa.correta


def para_o_aluno(inv: Investigacao,
                 respostas: Mapping[int, str] | None) -> dict:
    """A investigacao como ela pode chegar ao cliente.

    O QUE NAO ESTA AQUI NAO VAZA. A letra correta de uma etapa ABERTA nao sai
    em campo nenhum - nem marcada na alternativa, nem solta. Ela so aparece
    depois que o aluno acertou aquela etapa, e ai e para ele conferir o
    raciocinio, nao para descobrir a resposta.

    Ha teste serializando esta saida e procurando o campo `correta` nela.
    """
    dadas = {int(k): (v or "").strip().upper()
             for k, v in dict(respostas or {}).items()}
    atual = proxima_etapa(inv, dadas)

    concluidas = []
    for e in inv.etapas:
        if dadas.get(e.ordem) == e.correta:
            concluidas.append({
                "ordem": e.ordem,
                "question": e.pergunta,
                "selected": dadas[e.ordem],
                "correct_option": e.correta,
                "comentario": e.se_acertar,
            })

    # O desfecho da ULTIMA tentativa errada da etapa aberta - o ensino
    # daquela etapa, sem a letra.
    ultima_errada = None
    if atual is not None and dadas.get(atual.ordem):
        ultima_errada = {"ordem": atual.ordem, "comentario": atual.se_errar}

    return {
        "key": inv.key,
        "content_code": inv.content_code,
        "skill": inv.habilidade_alvo,
        "hipotese": inv.hipotese,
        "total_etapas": len(inv.etapas),
        "completed": atual is None,
        "bottleneck_skill": gargalo(inv, dadas),
        "etapa": None if atual is None else {
            "ordem": atual.ordem,
            "skill": atual.habilidade,
            "question": atual.pergunta,
            "options": [{"key": k, "text": t}
                        for k, t in sorted(atual.alternativas.items())],
        },
        "concluidas": concluidas,
        "retorno": ultima_errada,
    }


def conferir() -> list[str]:
    """Refaz as contas de todas as etapas. Devolve os problemas, ou vazio.

    Mesmo papel de `sondagem_estequiometria.conferir`: um erro de digitacao
    em "17 g/mol" ensinaria quimica errada a quem confiou no sistema. As
    contas vem de `massa_molar` e `chemistry_balance`, nunca de um literal
    repetido aqui.
    """
    from agente_ia_edu.services.chemistry_balance import atomos_da_formula
    from agente_ia_edu.services.massa_molar import (
        contribuicoes,
        massa_molar,
        massa_para_mol,
        mol_para_massa,
        por_proporcao,
    )

    problemas: list[str] = []
    etapas = {(inv.key, e.ordem): e for inv in INVESTIGACOES for e in inv.etapas}

    def marcada(key: str, ordem: int) -> str:
        e = etapas[(key, ordem)]
        return e.alternativas[e.correta]

    def exige(key: str, ordem: int, esperado: str) -> None:
        valor = marcada(key, ordem)
        if not valor.startswith(esperado):
            problemas.append(
                f"{key} etapa {ordem}: gabarito {valor!r} nao bate com "
                f"{esperado!r}")

    # Leitura de formula - contagem real de atomos.
    exige("INV-EST-LEITURA-FORMULA", 1, str(atomos_da_formula("H2O")["H"]))
    exige("INV-EST-LEITURA-FORMULA", 2, str(atomos_da_formula("NH3")["H"]))
    exige("INV-EST-LEITURA-FORMULA", 3, str(atomos_da_formula("Ca(OH)2")["O"]))

    # Massa molar do NH3, por partes e no total.
    exige("INV-EST-MASSA-MOLAR", 1, str(atomos_da_formula("NH3")["H"]))
    contrib_h = next(c[3] for c in contribuicoes("NH3") if c[0] == "H")
    exige("INV-EST-MASSA-MOLAR", 2, _numero(contrib_h))
    exige("INV-EST-MASSA-MOLAR", 3, _numero(massa_molar("NH3")))

    # Massa <-> mol, nos dois sentidos.
    exige("INV-EST-MASSA-MOL", 1, _numero(massa_molar("H2O")))
    exige("INV-EST-MASSA-MOL", 2, _numero(massa_para_mol(36.0, "H2O")))
    exige("INV-EST-MASSA-MOL", 3, _numero(mol_para_massa(0.5, "H2O")))

    # A cadeia do caso 8,50 g.
    mols_n2 = massa_para_mol(14.0, "N2")
    exige("INV-EST-PROPORCAO", 1, _virgula(mols_n2))
    mols_nh3 = por_proporcao(mols_n2, 1, 2)
    exige("INV-EST-PROPORCAO", 2, _virgula(mols_nh3))
    exige("INV-EST-PROPORCAO", 3, _virgula(mol_para_massa(mols_nh3, "NH3")))

    return problemas


def _numero(valor: float) -> str:
    """18.0 -> "18"; 0.5 -> "0,5". A grafia que o enunciado usa."""
    if float(valor).is_integer():
        return str(int(valor))
    return f"{valor:g}".replace(".", ",")


def _virgula(valor: float) -> str:
    """Uma casa decimal, com virgula - a grafia do caso N2/NH3."""
    return f"{valor:.1f}".replace(".", ",")


__all__ = [
    "INVESTIGACOES",
    "EtapaDaInvestigacao",
    "Investigacao",
    "conferir",
    "conferir_resposta",
    "gargalo",
    "investigacao_para",
    "para_o_aluno",
    "proxima_etapa",
]
